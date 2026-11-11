# EC551 Hardware Synthesis — Simplified HLS Tool

A small High-Level Synthesis (HLS) tool written in Python. It reads a list of arithmetic
equations, converts them to Static Single Assignment (SSA) form, builds a dependency DAG,
schedules it (ASAP, ALAP, both resource-constrained variants, and a custom list scheduler),
and generates synthesizable Verilog: a mux-and-register datapath with a control FSM, a
self-checking testbench and a human-readable report. You can then simulate the generated
design with Icarus Verilog and build it for a Nexys A7 FPGA in Vivado.

Built for EC551 Programming Assignment 2 (see `EC551_Program2_Rev2.pdf`).

## Contents
1. [Repo Organization and Directory Structure](#repo-organization-and-directory-structure)
2. [Tool Architecture](#tool-architecture)
3. [Instructions](#instructions)
4. [30-Minute Demo Plan](#30-minute-demo-plan)

---

## Repo Organization and Directory Structure

```text
.
├── EC551_Program2_Rev2.pdf          <- Assignment description
├── README.md                        <- This file
└── Synthesis                        <- Project root (run every command from here)
    ├── Inputs                       <- Input files (lists of equations)
    │   └── <input>.txt
    │
    ├── Outputs                      <- Generated files
    │   ├── SSA                      <- Static Single Assignment exports
    │   │   └── <input>_ssa.txt
    │   │
    │   └── RTL                      <- Generated datapath + testbench + report
    │       ├── <input>_datapath.v
    │       ├── tb_<input>_datapath.v
    │       └── <input>_datapath_report.txt
    │
    ├── Parser                       <- <input>.txt -> algorithm (list of steps) -> SSA
    │   └── parser.py
    │
    ├── VerilogFunctionalBlocks      <- Parameterized primitive blocks (WIDTH, NUM_PIPE_CYCLES)
    │   ├── adder.v
    │   ├── multiplier.v
    │   └── subtractor.v
    │
    ├── DAG
    │   └── dag.py                   <- Earlier DAG prototype (not used by synthesis.py)
    │
    ├── dag.py                       <- Algorithm -> DAG, plus all schedulers
    ├── datapath_gen.py              <- Scheduled DAG -> Verilog datapath, FSM, testbench, report
    └── synthesis.py                 <- Entry point (command-line tool)
```

`synthesis.py` uses relative paths (`Outputs/SSA`, `Outputs/RTL`, `from Parser import parser`),
so run it from inside the `Synthesis/` directory.

---

## Tool Architecture

```text
 Inputs/<input>.txt
        │
        ▼
 ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
 │ 1. Parse     │──▶│ 2. SSA       │──▶│ 3. DAG       │──▶│ 4. Schedule      │
 │ parser.parse │   │ parser.ssa   │   │ algo2tree    │   │ ASAP / ALAP /    │
 └──────────────┘   └──────┬───────┘   └──────────────┘   │ *_CONST / CUSTOM │
                           │ --export-ssa                 └────────┬─────────┘
                           ▼                                       │
                 Outputs/SSA/<input>_ssa.txt                       ▼
                                          ┌─────────────────────────────────────┐
                                          │ 5. Dead-code elimination            │
                                          │ 6. Datapath + FSM generation        │
                                          │    (binding, left-edge registers,   │
                                          │     muxes, control FSM, probe mux)  │
                                          └──────────────────┬──────────────────┘
                                                             ▼
                        Outputs/RTL/<top>.v, tb_<top>.v, <top>_report.txt
```

1. **Parse** (`Parser/parser.py: parse()`): each line `out = in1 op in2` becomes a step
   `[out, in1, op, in2]`. Supported operators are `+`, `-` and `*`. Blank lines and `#` comments
   are ignored. An operand can be a variable name or a numeric constant.
2. **SSA** (`parser.ssa()`): renames any variable that is assigned more than once to a uniquely
   indexed version (`a` becomes `a1`, then `a2`) and rewrites later reads to use the latest
   version. `parser.export_ssa()` writes the result when you pass `--export-ssa`.
3. **DAG** (`dag.py: algo2tree()`): builds one `Node` per operation. Edges are data dependencies
   (an operand produced by an earlier operation). Primary inputs and constants are leaves.
4. **Scheduling** (`dag.schedule(node_dict, method=...)`):
   - `ASAP`: each operation runs as soon as its operands are ready (unconstrained).
   - `ALAP`: each operation runs as late as possible within the ASAP latency bound.
   - `ASAP_CONST` / `ALAP_CONST`: the same, but no more than `max_op` operations of a type per step.
   - `CUSTOM` (our scheduler, and the one used to generate hardware): a list scheduler.
     Priority is the longest remaining path to the end of the program, and it respects a
     per-operator unit count (`--max-add/sub/mul`) and a per-operator pipeline latency
     (`--lat-add/sub/mul`).
   The command-line tool prints the ASAP, ALAP and constrained schedules as ASCII trees. In the
   CLI the constrained variants are fixed at `max_op=2`. The CUSTOM schedule drives the RTL.
5. **Dead-code elimination** (`datapath_gen.dead_code_eliminate()`): removes operations that
   never reach an output, then rebuilds and reschedules a fresh DAG. The outputs default to the
   last version of every assigned variable. Pass `--outputs` to choose them.
6. **Datapath + FSM generation** (`datapath_gen.Datapath`):
   - **Binding**: each scheduled operation is assigned to a functional unit instance
     (`add0`, `add1`, `sub0`, `mul0`, ...). The tool instantiates exactly as many units as the
     schedule uses.
   - **Registers**: a left-edge register allocator (`_allocate_registers`) packs value lifetimes
     into the fewest registers. Registers sit on the control-step boundaries.
   - **Muxes**: when a unit or register has more than one source, the tool builds operand muxes
     and register-input muxes for it.
   - **Control FSM**: a step counter `S_IDLE → 1 … T → S_DONE` drives the mux selects and the
     register write-enables. The interface protocol uses `start`, `busy` and `done`, and inputs
     are latched on `start`.
   - **Observability**: `function_probe_sel` selects which functional unit's output appears on
     `function_probe`.
7. **Generated files**:
   - `<top>.v`: the datapath and controller module.
   - `tb_<top>.v`: a self-checking testbench. It runs 200 random input vectors and compares the
     results with a reference model of the original (pre-SSA) program, so it checks the parser,
     SSA and dead-code elimination as well as the RTL.
   - `<top>_report.txt`: the schedule, binding, functional units, mux sizes, register
     allocation, output mapping and a per-step usage table.
8. **Functional blocks** (`VerilogFunctionalBlocks/`): `adder`, `subtractor` and `multiplier`,
   each parameterized by `WIDTH` and `NUM_PIPE_CYCLES`.

### Example (`Inputs/input_1.txt`)

| Input | SSA |
|---|---|
| `a = x+y` | `a1 = x + y` |
| `b = a+y` | `b = a1 + y` |
| `a = b+c` | `a2 = b + c` |
| `X = A + B` | `X1 = A + B` |
| `X = A + C` | `X2 = A + C` |
| `Y = X + A` | `Y = X2 + A` |
| `Z = X - B` | `Z = X2 - B` |
| `Q = B * Z` | `Q = B * Z` |

Excerpt from the generated report, with one adder, one subtractor and one multiplier, each with latency 1:

```text
Dead code removed (never reaches an output): a1, b, a2, X1

  step  1  add0      X2 = A + C          result @ step 2   -> r0
  step  2  add0       Y = X2 + A         result @ step 3   -> r1
  step  2  sub0       Z = X2 - B         result @ step 3   -> r2
  step  3  mul0       Q = B * Z          result @ step 4   -> r3

  total: 4 registers for 7 stored values
```

---

## Instructions

### Requirements
- Python 3.6+ (standard library only, no packages to install)
- [Icarus Verilog](https://steveicarus.github.io/iverilog/) (`iverilog`, `vvp`) for simulation; GTKWave is optional
- Xilinx Vivado for the FPGA build (target: **Digilent Nexys A7**, part `xc7a100tcsg324-1`)

### Input format
- One equation per line: `out = in1 op in2`, where `op` is `+`, `-` or `*`
- At most 20 equations
- Operands are variable names or non-negative integer constants
- Blank lines and `#` comments are allowed

```text
# Inputs/example.txt
X = A + B
Y = X + A
Z = X - B
Q = B * Z
```

### Run the tool

```bash
cd Synthesis
python3 synthesis.py --file_path Inputs/input_1.txt --export-ssa
```

This prints the ASAP, ALAP and constrained schedules and the RTL report, then writes these files:
- `Outputs/SSA/input_1_ssa.txt` (only with `--export-ssa`)
- `Outputs/RTL/input_1_datapath.v`
- `Outputs/RTL/tb_input_1_datapath.v`
- `Outputs/RTL/input_1_datapath_report.txt`

### Command-line options

| Flag | Default | Meaning |
|---|---|---|
| `--file_path PATH` | — | Input equations file (required) |
| `--export-ssa` | off | Write the SSA form to `Outputs/SSA/<input>_ssa.txt` |
| `--outputs X,Y` | every assigned variable | Comma-separated variables to expose as outputs. Everything else is dead-code eliminated |
| `--max-add N` | 1 | Number of adders (maximum `+` operations issued per step) |
| `--max-sub N` | 1 | Number of subtractors |
| `--max-mul N` | 1 | Number of multipliers |
| `--lat-add N` | 1 | Adder pipeline depth (`NUM_PIPE_CYCLES`) |
| `--lat-sub N` | 1 | Subtractor pipeline depth |
| `--lat-mul N` | 1 | Multiplier pipeline depth |
| `--rtl-dir DIR` | `Outputs/RTL` | Directory for the generated RTL |
| `--top NAME` | `<input>_datapath` | Name of the generated top module |
| `--width N` | 32 | Datapath bit width |

Example with a constrained schedule: two adders, a 2-cycle multiplier and an 8-bit datapath.

```bash
python3 synthesis.py --file_path Inputs/input_1.txt --max-add 2 --lat-mul 2 --width 8
```

### Reading the report
`<top>_report.txt` contains the following sections:
- **Schedule / binding**: each operation's control step, the unit that runs it, when its result
  is ready and the register that stores it.
- **Functional units**: the unit instances and the size of each operand mux.
- **Registers**: each register's input-mux size and the lifetimes of the values it holds
  (`name[first..last step]`).
- **Outputs**: which register drives each `out_*` port.
- **Usage per step**: a table showing what every unit computes and which registers are written
  at each step.

### Simulate

```bash
cd Synthesis
iverilog -o sim Outputs/RTL/input_1_datapath.v Outputs/RTL/tb_input_1_datapath.v VerilogFunctionalBlocks/*.v
vvp sim
```

A passing run prints `PASS: 200 tests, <n> cycles start->done`. Mismatches are printed per test.

> The simulate command that `synthesis.py` prints at the end lists `adder.v` etc. without the
> `VerilogFunctionalBlocks/` prefix. Use the command above.

### Build and program the Nexys A7 (Vivado)
1. Generate the design with a width that fits the board's I/O. With `--width 4`, three inputs fit
   on the 16 slide switches:
   `python3 synthesis.py --file_path Inputs/<input>.txt --width 4`
2. In Vivado, create an RTL project for part `xc7a100tcsg324-1` (Nexys A7-100T).
3. Add these design sources: `Outputs/RTL/<input>_datapath.v` and the three files in
   `VerilogFunctionalBlocks/`. Add sources **by reference** (do not copy them into the project) so
   that the project picks up newly generated RTL without being re-created.
4. Add the Nexys A7 constraints file (`.xdc`) and set the top module.
5. Run Synthesis, then Implementation, then Generate Bitstream. In Hardware Manager, open the
   target and program the device.

Planned board I/O mapping. **TODO: confirm against the committed top-level wrapper and XDC.**

| Board resource | Signal |
|---|---|
| Slide switches `SW[15:0]` | Primary inputs (`in_*`). Remaining variables are hard-coded |
| `BTNC` | Step clock (debounced): advances the FSM one control step per press |
| `BTNU` | `start` |
| `BTND` / `CPU_RESETN` | `rst` |
| Upper switches | `function_probe_sel`: chooses which functional unit is shown |
| 7-segment display | Selected output value / `function_probe` |
| LEDs | `busy`, `done`, current FSM state |

---

## 30-Minute Demo Plan

**Setup before the demo starts** (not part of the 30 minutes):
- [ ] Nexys A7 connected and visible in Hardware Manager
- [ ] Vivado project open, with sources added by reference to `Synthesis/Outputs/RTL` and `VerilogFunctionalBlocks`
- [ ] Bitstream for `input_1` already built
- [ ] Terminal open in `Synthesis/`, with `python3` and `iverilog` working
- [ ] Empty files `Inputs/live_1.txt`, `Inputs/live_2.txt` and `Inputs/live_3.txt` open in an editor
- [ ] Calculator (or Python REPL) ready for hand-checking results

| Time | Segment | What we show |
|---|---|---|
| 0:00–3:00 | **Intro** | Goal of the project, the architecture diagram above, a quick tour of the repo layout |
| 3:00–8:00 | **Software walkthrough** (`input_1.txt`) | Run with `--export-ssa` and show the SSA renaming (`a1`/`a2`, `X1`/`X2`). Compare the ASAP, ALAP and constrained schedules. Re-run with `--max-add 2` and `--lat-mul 2` to show the CUSTOM schedule changing. Walk through the report: dead-code elimination, binding, left-edge registers, mux sizes and the per-step table |
| 8:00–11:00 | **Simulation** | Run the self-checking testbench (`PASS: 200 tests`). Optionally show the FSM stepping and register writes in GTKWave |
| 11:00–14:00 | **Hardware bring-up** | Program the pre-built `input_1` bitstream. Set the inputs on the switches, press `start`, then step the clock with the button and watch the state and `busy`/`done` LEDs. Flip the probe-select switches to show each adder, subtractor and multiplier output. Read the results on the 7-segment display |
| 14:00–18:00 | **Live run 1** (professor's equations) | Professor writes the equations into `Inputs/live_1.txt`, then: run the tool, simulate (PASS), synthesize and program, set the switches, step, and compare with a hand calculation. *Suggested focus: basic `+`/`-` chain* |
| 18:00–22:00 | **Live run 2** (professor's equations) | Same flow with `live_2.txt`. *Suggested focus: variable re-assignment (SSA) plus `*`* |
| 22:00–26:00 | **Live run 3** (professor's constraints) | Same flow with `live_3.txt`, using the professor's resource limits and latencies (`--max-*`, `--lat-*`). *Suggested focus: a tighter resource limit produces a longer schedule and more mux sharing* |
| 26:00–30:00 | **Q&A / buffer** | Questions, plus spare time if a build runs over |

**Steps for each live run** (about 4 minutes):
1. `python3 synthesis.py --file_path Inputs/live_N.txt --width 4 [--max-add/--max-sub/--max-mul ...] [--outputs ...]`
2. `iverilog -o sim Outputs/RTL/live_N_datapath.v Outputs/RTL/tb_live_N_datapath.v VerilogFunctionalBlocks/*.v && vvp sim`
3. In Vivado: set the new top, then Generate Bitstream and Program Device.
4. Enter the inputs on the switches, press `start`, step through the FSM, check each functional
   unit with the probe switches, and read the outputs.
5. Compare the outputs with the hand or REPL calculation and with the schedule in the report.

**Contingencies:**
- **Vivado build time** is the bottleneck. Keep the project open with sources added by reference,
  and use a small `--width` to keep synthesis fast. If a build overruns its slot, show the PASS
  result from simulation and the report, then move on. Program the board during the buffer.
- **Unsupported input** (for example, more than 2 operands on a line): rewrite it on the spot
  as a chain of 2-operand equations, which also shows SSA and the DAG at work.
- **Negative results** are out of scope per the assignment. Pick input values that keep
  subtraction results non-negative.
