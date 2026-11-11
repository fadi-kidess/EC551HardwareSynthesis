"""
Datapath + FSM generator.

Takes a scheduled DAG (dag.CUSTOM sets node.step / node.unit / node.latency) and emits:
  * <top>.v       the datapath: functional-unit instances, operand muxes, registers with
                  input muxes, and the FSM controller that drives every select/enable
  * tb_<top>.v    a self-checking testbench whose reference model is the ORIGINAL
                  (pre-SSA) program, executed sequentially
  * <top>_report.txt  schedule, binding, register lifetimes, mux sizes, control table

Timing model (matches adder.v / subtractor.v / multiplier.v):
  - An op issued in control step s drives its unit's a/b inputs during step s.
  - With NUM_PIPE_CYCLES = L >= 1 the result is on the unit's `out` during step s+L only
    (the pipeline keeps shifting). A consumer issued in exactly step s+L reads `out`
    directly (bypass); anything later reads a register loaded at the end of step s+L.
  - With L = 0 the unit is combinational; the result is registered at the end of step s
    and consumers start at s+1 (no operation chaining).
  - `ce` (clock enable) freezes the FSM, every register and every unit pipeline together,
    so holding ce low is exactly equivalent to stopping the clock (used for single-step).
  - Step 0 is IDLE: when `start` is high, primary inputs are latched into registers.
    Inputs may change once `start` has been sampled. Outputs are valid when `done` is
    high and stay valid until the next `start`.

Registers are shared between values whose lifetimes do not overlap (left-edge
algorithm); a value read only through the bypass gets no register at all.
"""
import math
import re

from dag import Node, collect_nodes

FU_TYPES = {
    '+': {'module': 'adder',      'prefix': 'add', 'commutative': True},
    '-': {'module': 'subtractor', 'prefix': 'sub', 'commutative': False},
    '*': {'module': 'multiplier', 'prefix': 'mul', 'commutative': True},
}
CONST_RE = re.compile(r'^\d+$')


def is_const(x):
    return isinstance(x, str) and bool(CONST_RE.match(x))


def ident(name):
    s = re.sub(r'\W', '_', name)
    return s if not s[0].isdigit() else '_' + s


def clog2(n):
    return max(1, math.ceil(math.log2(n))) if n > 1 else 1


# --------------------------------------------------------------------------- DCE
def dead_code_eliminate(algo, ssa_algo, outputs=None):
    """
    Program outputs are the final definition of each assigned variable (or only the ones
    named in `outputs`). Steps that cannot reach an output are dropped.
    Returns (live_ssa_algo, {original_name: ssa_name}, [removed ssa names]).
    """
    final = {}
    for orig, step in zip(algo, ssa_algo):
        final[orig[0]] = step[0]
    if outputs:
        missing = [o for o in outputs if o not in final]
        if missing:
            raise ValueError(f"--outputs names variables that are never assigned: {missing}")
        final = {o: final[o] for o in outputs}

    live, keep = set(final.values()), []
    for step in reversed(ssa_algo):          # reverse order resolves names correctly
        if step[0] in live:
            keep.append(step)
            live.update((step[1], step[3]))
    keep.reverse()
    removed = [s[0] for s in ssa_algo if s not in keep]
    return keep, final, removed


# --------------------------------------------------------------------------- model
class Value:
    """Anything that may need to live in a register: a primary input or an op result."""
    def __init__(self, kind, name, src, write_step):
        self.kind = kind            # 'in' | 'op'
        self.name = name
        self.src = src              # signal that is written into the register
        self.w = write_step         # register (if any) is loaded at the END of this step
        self.reg_reads = []         # steps in which a consumer reads it from a register
        self.is_output = False
        self.reg = None

    @property
    def needs_reg(self):
        return self.is_output or bool(self.reg_reads)

    @property
    def start(self):                # first step the register holds this value
        return self.w + 1

    @property
    def end(self):                  # last step the register must hold it
        return math.inf if self.is_output else max(self.reg_reads)


class Datapath:
    def __init__(self, node_dict, out_map, latency, width=32, top='datapath'):
        self.top, self.width = top, width
        self.latency = {op: latency.get(op, 1) for op in FU_TYPES}
        self.nodes = sorted(collect_nodes(node_dict), key=lambda n: (n.step, n.op, n.unit))
        for n in self.nodes:
            if n.op not in FU_TYPES:
                raise ValueError(f"No functional unit for operator '{n.op}'")
            if not hasattr(n, 'unit'):
                raise ValueError("DAG is not bound: schedule it with method='CUSTOM' first")
        by_name = {n.name: n for n in self.nodes}
        self.out_map = out_map
        self.outputs = {o: by_name[s] for o, s in out_map.items()}

        self._build_values()
        self._check_and_collect_reads()
        self._allocate_registers()
        self._bind_operands()
        self._build_register_writes()

    # ---- functional units ------------------------------------------------
    def unit_name(self, n):
        return f"{FU_TYPES[n.op]['prefix']}{n.unit}"

    def units(self):
        seen = {}
        for n in self.nodes:
            seen[self.unit_name(n)] = n.op
        return sorted(seen.items(), key=lambda kv: kv[0])

    # ---- values and lifetimes -------------------------------------------
    def _build_values(self):
        self.values = {}                     # key -> Value ; key is ('in', name) or id(node)
        for n in self.nodes:
            for c in n.children:
                if isinstance(c, str) and not is_const(c) and ('in', c) not in self.values:
                    self.values[('in', c)] = Value('in', c, f"in_{ident(c)}", 0)
        for n in self.nodes:
            L = self.latency[n.op]
            self.values[id(n)] = Value('op', n.name, f"{self.unit_name(n)}_out", n.step + L)
        for n in self.outputs.values():
            self.values[id(n)].is_output = True
        self.inputs = [v for v in self.values.values() if v.kind == 'in']

    def operand_source(self, child, step):
        """('const', literal) | ('fu', signal) | ('reg', Value)"""
        if is_const(child):
            return ('const', f"{self.width}'d{child}")
        if isinstance(child, str):
            return ('reg', self.values[('in', child)])
        v = self.values[id(child)]
        if self.latency[child.op] >= 1 and step == v.w:
            return ('fu', v.src)
        return ('reg', v)

    def _check_and_collect_reads(self):
        for n in self.nodes:
            for c in n.children:
                if isinstance(c, Node):
                    earliest = c.step + max(self.latency[c.op], 1)
                    if n.step < earliest:
                        raise ValueError(f"Schedule violates latency: {n.name}@{n.step} "
                                         f"reads {c.name} which is ready at {earliest}")
                kind, v = self.operand_source(c, n.step)
                if kind == 'reg':
                    v.reg_reads.append(n.step)
        self.last_step = max([v.w for v in self.values.values()] + [n.step for n in self.nodes])

    def _allocate_registers(self):
        """Left-edge: sort lifetimes by start, reuse the first register that is free."""
        need = sorted((v for v in self.values.values() if v.needs_reg),
                      key=lambda v: (v.start, v.end))
        self.registers = []                  # list of lists of Values
        reg_end = []
        for v in need:
            for r, e in enumerate(reg_end):
                if e < v.start:
                    break
            else:
                r = len(reg_end)
                reg_end.append(-1)
                self.registers.append([])
            v.reg = r
            reg_end[r] = v.end
            self.registers[r].append(v)

    # ---- operand muxes ---------------------------------------------------
    def _signal(self, src):
        kind, x = src
        return f"r{x.reg}" if kind == 'reg' else x

    def _bind_operands(self):
        """Per unit port, the list of distinct sources and which one each op selects.
        For commutative ops the operand order that adds fewer new mux inputs is chosen."""
        self.port_sources = {}               # (unit, 'a'|'b') -> [signal, ...]
        self.issue = {}                      # step -> [(unit, sig_a, sig_b, node)]
        for n in self.nodes:
            u = self.unit_name(n)
            sa = self._signal(self.operand_source(n.children[0], n.step))
            sb = self._signal(self.operand_source(n.children[1], n.step))
            pa = self.port_sources.setdefault((u, 'a'), [])
            pb = self.port_sources.setdefault((u, 'b'), [])
            if FU_TYPES[n.op]['commutative']:
                cost = lambda x, y: (x not in pa) + (y not in pb)
                if cost(sb, sa) < cost(sa, sb):
                    sa, sb = sb, sa
            for p, s in ((pa, sa), (pb, sb)):
                if s not in p:
                    p.append(s)
            self.issue.setdefault(n.step, []).append((u, sa, sb, n))

    # ---- register writes -------------------------------------------------
    def _build_register_writes(self):
        self.reg_sources = [[] for _ in self.registers]
        self.writes = {}                     # step -> [(reg, signal, Value)]
        for r, vals in enumerate(self.registers):
            for v in vals:
                if v.src not in self.reg_sources[r]:
                    self.reg_sources[r].append(v.src)
                self.writes.setdefault(v.w, []).append((r, v.src, v))

    # ======================================================================
    #                               Verilog
    # ======================================================================
    def verilog(self):
        W, T = self.width, self.last_step
        S_DONE = T + 1
        SW = clog2(S_DONE + 1)
        L = []
        emit = L.append

        def mux(name, sources, sel):
            if len(sources) == 1:
                emit(f"    wire [WIDTH-1:0] {name} = {sources[0]};")
                return
            emit(f"    reg  [WIDTH-1:0] {name};")
            emit(f"    always @(*) begin")
            emit(f"        case ({sel})")
            for i, s in enumerate(sources):
                emit(f"            {clog2(len(sources))}'d{i}: {name} = {s};")
            emit(f"            default: {name} = {{WIDTH{{1'b0}}}};")
            emit(f"        endcase")
            emit(f"    end")

        units = self.units()
        ports = ["    input  wire             clk,", "    input  wire             rst,",
                 "    input  wire             ce,      // 1 = run; 0 = freeze everything",
                 "    input  wire             start,"]
        ports += [f"    input  wire [WIDTH-1:0] {v.src}," for v in self.inputs]
        ports += [f"    output wire [WIDTH-1:0] out_{ident(o)}," for o in self.outputs]
        ports += [f"    output wire [WIDTH-1:0] dbg_{u}_out,   // debug: raw unit output" for u, _ in units]
        ports += [f"    output wire [{SW-1}:0]       dbg_state,"]
        ports += ["    output wire             busy,", "    output wire             done"]

        emit("// ---------------------------------------------------------------------------")
        emit(f"// {self.top}: auto-generated by datapath_gen.py -- do not edit by hand")
        emit("//")
        emit("// Protocol: hold `start` for a cycle with ce=1 while idle; inputs are latched,")
        emit("// so they may change afterwards. Outputs are valid while `done` is high and")
        emit("// stay valid until the next `start`.")
        emit(f"// Latency: {S_DONE} cycles from the cycle `start` is sampled to `done`.")
        emit("// ---------------------------------------------------------------------------")
        emit("`timescale 1ns/1ps")
        emit(f"module {self.top} #(")
        emit(f"    parameter WIDTH = {W}")
        emit(")(")
        emit("\n".join(ports))
        emit(");")
        emit("")
        for op, info in FU_TYPES.items():
            emit(f"    localparam LAT_{info['prefix'].upper()} = {self.latency[op]};")
        emit("")

        # ---------------- FSM
        emit("    // ------------------------------------------------------------ FSM")
        emit(f"    localparam [{SW-1}:0] S_IDLE = {SW}'d0;")
        emit(f"    localparam [{SW-1}:0] S_DONE = {SW}'d{S_DONE};")
        emit(f"    reg [{SW-1}:0] state;")
        emit("    always @(posedge clk) begin")
        emit("        if (rst)                state <= S_IDLE;")
        emit("        else if (ce) case (state)")
        emit("            S_IDLE: if (start)  state <= 1;")
        emit("            S_DONE:             state <= S_IDLE;")
        emit("            default:            state <= state + 1'b1;   // control steps 1..%d" % T)
        emit("        endcase")
        emit("    end")
        emit("    assign busy = (state != S_IDLE);")
        emit("    assign done = (state == S_DONE);")
        emit("    assign dbg_state = state;")
        emit("")

        # ---------------- control signal declarations
        emit("    // ------------------------------------------------------------ control signals")
        sels = []
        for (u, p), srcs in self.port_sources.items():
            if len(srcs) > 1:
                sels.append((f"{u}_{p}_sel", clog2(len(srcs))))
        for r, srcs in enumerate(self.reg_sources):
            emit(f"    reg r{r}_we;")
            if len(srcs) > 1:
                sels.append((f"r{r}_sel", clog2(len(srcs))))
        for s, w in sels:
            emit(f"    reg [{w-1}:0] {s};")
        emit("")
        emit("    always @(*) begin")
        for r in range(len(self.registers)):
            emit(f"        r{r}_we = 1'b0;")
        for s, w in sels:
            emit(f"        {s} = {w}'d0;")
        emit("        case (state)")
        for step in range(0, T + 1):
            lines = []
            for (u, sa, sb, n) in self.issue.get(step, []):
                lines.append(f"// issue {n.name} = {n.children_names()} on {u}  (a <- {sa}, b <- {sb})")
                for p, s in (('a', sa), ('b', sb)):
                    srcs = self.port_sources[(u, p)]
                    if len(srcs) > 1:
                        lines.append(f"{u}_{p}_sel = {clog2(len(srcs))}'d{srcs.index(s)};")
            for (r, s, v) in self.writes.get(step, []):
                lines.append(f"r{r}_we = 1'b1;  // r{r} <= {v.name}")
                if len(self.reg_sources[r]) > 1:
                    lines.append(f"r{r}_sel = {clog2(len(self.reg_sources[r]))}'d"
                                 f"{self.reg_sources[r].index(s)};")
            if not lines:
                continue
            label = "S_IDLE" if step == 0 else f"{SW}'d{step}"
            emit(f"            {label}: begin")
            if step == 0:
                emit("                if (start) begin   // latch primary inputs")
                for ln in lines:
                    emit(f"                    {ln}")
                emit("                end")
            else:
                for ln in lines:
                    emit(f"                {ln}")
            emit("            end")
        emit("            default: ;")
        emit("        endcase")
        emit("    end")
        emit("")

        # ---------------- functional units + operand muxes
        emit("    // ------------------------------------------------------------ functional units")
        for u, op in units:
            info = FU_TYPES[op]
            mux(f"{u}_a", self.port_sources[(u, 'a')], f"{u}_a_sel")
            mux(f"{u}_b", self.port_sources[(u, 'b')], f"{u}_b_sel")
            emit(f"    wire [WIDTH-1:0] {u}_out;")
            emit(f"    {info['module']} #(.WIDTH(WIDTH), .NUM_PIPE_CYCLES(LAT_{info['prefix'].upper()})) "
                 f"u_{u} (")
            emit(f"        .clk(clk), .ce(ce), .a({u}_a), .b({u}_b), .out({u}_out));")
            emit(f"    assign dbg_{u}_out = {u}_out;")
            emit("")

        # ---------------- registers
        emit("    // ------------------------------------------------------------ registers")
        for r, srcs in enumerate(self.reg_sources):
            names = ", ".join(v.name for v in self.registers[r])
            emit(f"    // r{r} holds: {names}")
            mux(f"r{r}_d", srcs, f"r{r}_sel")
            emit(f"    reg  [WIDTH-1:0] r{r};")
            emit(f"    always @(posedge clk) if (ce && r{r}_we) r{r} <= r{r}_d;")
            emit("")

        emit("    // ------------------------------------------------------------ outputs")
        for o, n in self.outputs.items():
            emit(f"    assign out_{ident(o)} = r{self.values[id(n)].reg};   // {n.name}")
        emit("")
        emit("endmodule")
        return "\n".join(L) + "\n"

    # ======================================================================
    #                               Testbench
    # ======================================================================
    def testbench(self, algo, n_tests=200):
        """Reference model = the original program run sequentially, so it checks the
        parser/SSA/DCE as well as the scheduling and RTL."""
        W = self.width
        names = []
        for out, a, _, b in algo:
            for x in (a, b, out):
                if not is_const(x) and x not in names:
                    names.append(x)
        ref = lambda x: (f"{W}'d{x}" if is_const(x) else f"ref_{ident(x)}")
        rand = "{" + ", ".join(["$random"] * math.ceil(W / 32)) + "}"
        L = []
        emit = L.append
        emit("`timescale 1ns/1ps")
        emit(f"// Self-checking testbench for {self.top} (auto-generated)")
        emit(f"module tb_{self.top};")
        emit(f"    localparam WIDTH = {W};")
        emit(f"    localparam N_TESTS = {n_tests};")
        emit("    reg clk = 1'b0, rst = 1'b1, start = 1'b0, ce = 1'b1;")
        emit("    reg random_ce = 1'b0;   // phase 2 stalls the datapath at random")
        for v in self.inputs:
            emit(f"    reg  [WIDTH-1:0] {v.src};")
        for o in self.outputs:
            emit(f"    wire [WIDTH-1:0] out_{ident(o)};")
        emit("    wire busy, done;")
        emit("")
        conns = [".clk(clk)", ".rst(rst)", ".ce(ce)", ".start(start)"]
        conns += [f".{v.src}({v.src})" for v in self.inputs]
        conns += [f".out_{ident(o)}(out_{ident(o)})" for o in self.outputs]
        conns += [".busy(busy)", ".done(done)"]
        emit(f"    {self.top} #(.WIDTH(WIDTH)) dut (")
        emit("        " + ",\n        ".join(conns))
        emit("    );")
        emit("    always #5 clk = ~clk;")
        emit("    always @(negedge clk) ce = random_ce ? $random : 1'b1;")
        emit("")
        emit("    // reference model: the original program, executed in order")
        emit("    reg [WIDTH-1:0] " + ", ".join(f"ref_{ident(n)}" for n in names) + ";")
        emit("    task run_reference; begin")
        for v in self.inputs:
            emit(f"        ref_{ident(v.name)} = {v.src};")
        for out, a, op, b in algo:
            emit(f"        {ref(out)} = {ref(a)} {op} {ref(b)};")
        emit("    end endtask")
        emit("")
        emit("    integer t, errors = 0, cycles;")
        emit("    task randomize_inputs(input integer mode); begin")
        for v in self.inputs:
            emit(f"        {v.src} = (mode == 0) ? {{WIDTH{{1'b0}}}} : (mode == 1) ? {{WIDTH{{1'b1}}}} : {rand};")
        emit("    end endtask")
        emit("")
        emit("    initial begin")
        emit("        randomize_inputs(0);")
        emit("        repeat (3) @(negedge clk);")
        emit("        rst = 1'b0;")
        emit("        for (t = 0; t < 2 * N_TESTS; t = t + 1) begin")
        emit("            random_ce = (t >= N_TESTS);")
        emit("            @(negedge clk);")
        emit("            randomize_inputs(t < 2 ? t : 2);")
        emit("            run_reference;")
        emit("            while (busy) @(negedge clk);    // previous run may still be stalled in DONE")
        emit("            start = 1'b1;")
        emit("            @(negedge clk);")
        emit("            while (!busy) @(negedge clk);   // held until a ce cycle takes it")
        emit("            start = 1'b0;")
        emit("            randomize_inputs(2);          // inputs must have been latched")
        emit("            cycles = 1;")
        emit("            while (!done) begin @(negedge clk); cycles = cycles + 1; end")
        for o in self.outputs:
            emit(f"            if (out_{ident(o)} !== ref_{ident(o)}) begin")
            emit(f"                errors = errors + 1;")
            emit(f"                $display(\"MISMATCH test %0d: {o} = %0d, expected %0d\", "
                 f"t, out_{ident(o)}, ref_{ident(o)});")
            emit("            end")
        emit("        end")
        emit("        if (errors == 0) $display(\"PASS: %0d tests (half with random ce stalls)\", 2 * N_TESTS);")
        emit("        else             $display(\"FAIL: %0d mismatches\", errors);")
        emit("        $finish;")
        emit("    end")
        emit("endmodule")
        return "\n".join(L) + "\n"

    # ======================================================================
    #                    FPGA board wrapper (Nexys A7 style)
    # ======================================================================
    # Instantiates the user's modules -- debouncer #(COUNT), seven_seg -- it does not
    # generate them. Port names match constraints.xdc.
    def board_notes(self):
        notes = []
        if len(self.outputs) > 4:
            notes.append(f"{len(self.outputs)} outputs but only 4 display digits: showing the "
                         f"first 4 ({', '.join(list(self.outputs)[:4])}); use --outputs to choose")
        if self.width > 4:
            notes.append(f"WIDTH={self.width}: each digit shows only the low hex nibble")
        return notes

    def board_top(self, name=None):
        name = name or f"{self.top}_fpga"
        W, S_DONE = self.width, self.last_step + 1
        SW = clog2(S_DONE + 1)
        units = [u for u, _ in self.units()]
        PW = clog2(len(units))
        shown = list(self.outputs)[:4]
        digits = []                              # digit3 (leftmost) .. digit0
        for i in range(4):
            if i < len(shown):
                o = f"out_{ident(shown[i])}"
                digits.append(f"{o}[3:0]" if W >= 4 else f"{{{{{4 - W}{{1'b0}}}}, {o}}}")
            else:
                digits.append("4'h0")
        L = []
        emit = L.append
        emit("`timescale 1ns/1ps")
        emit("// ---------------------------------------------------------------------------")
        emit(f"// {name}: board top level around {self.top} -- auto-generated by datapath_gen.py")
        emit("//")
        emit("//   start        button: run the computation (inputs are latched on the press)")
        emit("//   step_switch  0 = run at full speed")
        emit("//                1 = single-step: the start press executes cycle 1 (IDLE -> step 1,")
        emit("//                    latching the inputs); each `step` press then executes exactly")
        emit("//                    ONE more clock cycle of the whole datapath (FSM, registers and")
        emit("//                    unit pipelines advance together), however long it is held")
        emit("//   function_probe_sel  selects which unit output is shown on the leftmost digit: " +
             ", ".join(f"{i}={u}" for i, u in enumerate(units)))
        emit("//   display      digits 3..0 (left to right) = " +
             ", ".join(shown) + "; leftmost digit = unit probe")
        emit("//   busy LED     computation in progress   done LED  results valid (stays lit)")
        emit("//   switches     " + self.switch_summary())
        emit("// ---------------------------------------------------------------------------")
        emit(f"module {name} #(")
        emit(f"    parameter WIDTH = {W},")
        emit("    parameter DEBOUNCE_CYCLES = 32'd1_000_000     // ~10 ms at 100 MHz")
        emit(")(")
        ports = ["    input  wire             clk,", "    input  wire             rst,",
                 "    input  wire             start,", "    input  wire             step,",
                 "    input  wire             step_switch,"]
        ports += [f"    input  wire [WIDTH-1:0] {v.src}," for v in self.inputs]
        ports += ["    output wire [7:0]       an,", "    output wire [6:0]       cn,",
                  "    output wire             busy,", "    output wire             done,",
                  f"    input  wire [{PW-1}:0]       function_probe_sel"]
        emit("\n".join(ports))
        emit(");")
        emit("")
        emit("    // ---- 2-FF synchronizers: buttons and the mode switch are asynchronous to clk")
        emit("    reg [1:0] start_sync, step_sync, mode_sync;")
        emit("    always @(posedge clk) begin")
        emit("        start_sync <= {start_sync[0], start};")
        emit("        step_sync  <= {step_sync[0],  step};")
        emit("        mode_sync  <= {mode_sync[0],  step_switch};")
        emit("    end")
        emit("    wire step_mode = mode_sync[1];")
        emit("")
        emit("    // ---- debounce, then turn each press into a ONE-cycle pulse")
        emit("    wire start_db, step_db;")
        emit("    debouncer #(.COUNT(DEBOUNCE_CYCLES)) u_start_db (")
        emit("        .clk(clk), .rst(rst), .button(start_sync[1]), .debounced_button(start_db));")
        emit("    debouncer #(.COUNT(DEBOUNCE_CYCLES)) u_step_db (")
        emit("        .clk(clk), .rst(rst), .button(step_sync[1]),  .debounced_button(step_db));")
        emit("    reg start_db_q, step_db_q;")
        emit("    always @(posedge clk) begin")
        emit("        if (rst) begin start_db_q <= 1'b0; step_db_q <= 1'b0; end")
        emit("        else     begin start_db_q <= start_db; step_db_q <= step_db; end")
        emit("    end")
        emit("    wire start_pulse = start_db & ~start_db_q;")
        emit("    wire step_pulse  = step_db  & ~step_db_q;")
        emit("")
        emit("    // ---- clock enable for the whole datapath")
        emit("    wire core_busy, core_done;")
        emit("    wire start_go = start_pulse & ~core_busy;          // accepted start")
        emit("    wire ce = ~step_mode | step_pulse | start_go;")
        emit("")
        for o in self.outputs:
            emit(f"    wire [WIDTH-1:0] out_{ident(o)};")
        for u in units:
            emit(f"    wire [WIDTH-1:0] dbg_{u}_out;")
        emit(f"    wire [{SW-1}:0] dbg_state;")
        conns = [".clk(clk)", ".rst(rst)", ".ce(ce)", ".start(start_go)"]
        conns += [f".{v.src}({v.src})" for v in self.inputs]
        conns += [f".out_{ident(o)}(out_{ident(o)})" for o in self.outputs]
        conns += [f".dbg_{u}_out(dbg_{u}_out)" for u in units]
        conns += [".dbg_state(dbg_state)", ".busy(core_busy)", ".done(core_done)"]
        emit(f"    {self.top} #(.WIDTH(WIDTH)) u_core (")
        emit("        " + ",\n        ".join(conns))
        emit("    );")
        emit("")
        emit("    // ---- status LEDs: `done` stays lit until the next accepted start")
        emit("    reg done_led;")
        emit("    always @(posedge clk) begin")
        emit("        if (rst || start_go)  done_led <= 1'b0;")
        emit("        else if (core_done)   done_led <= 1'b1;")
        emit("    end")
        emit("    assign busy = core_busy;")
        emit("    assign done = done_led;")
        emit("")
        emit("    // ---- unit probe for the leftmost digit")
        emit("    reg [WIDTH-1:0] function_probe;")
        emit("    always @(*) begin")
        emit("        case (function_probe_sel)")
        for i, u in enumerate(units):
            emit(f"            {PW}'d{i}: function_probe = dbg_{u}_out;")
        emit(f"            default: function_probe = dbg_{units[-1]}_out;")
        emit("        endcase")
        emit("    end")
        emit("")
        emit("    seven_seg u_display (")
        emit("        .clk(clk), .rst(rst),")
        emit(f"        .digit3({digits[0]}), .digit2({digits[1]}), "
             f".digit1({digits[2]}), .digit0({digits[3]}),")
        fp = "function_probe[3:0]" if W >= 4 else f"{{{{{4 - W}{{1'b0}}}}, function_probe}}"
        emit(f"        .digit_function({fp}),")
        emit("        .an(an), .seg(cn));")
        emit("")
        emit("endmodule")
        return "\n".join(L) + "\n"

    def board_testbench(self, algo, name=None, n_tests=20, debounce=20):
        """Presses the (bouncing) buttons like a person would, in both modes, with a short
        debounce so it simulates quickly. Checks results, the done LED, and that each step
        press advances exactly one cycle no matter how long it is held."""
        W, S_DONE = self.width, self.last_step + 1
        top = name or f"{self.top}_fpga"
        names = []
        for out, a, _, b in algo:
            for x in (a, b, out):
                if not is_const(x) and x not in names:
                    names.append(x)
        ref = lambda x: (f"{W}'d{x}" if is_const(x) else f"ref_{ident(x)}")
        rand = "{" + ", ".join(["$random"] * math.ceil(W / 32)) + "}"
        PW = clog2(len(self.units()))
        L = []
        emit = L.append
        emit("`timescale 1ns/1ps")
        emit(f"// Board-level testbench for {top} (auto-generated)")
        emit(f"module tb_{top};")
        emit(f"    localparam WIDTH = {W}, DB = {debounce}, S_DONE = {S_DONE}, N_TESTS = {n_tests};")
        emit("    reg clk = 1'b0, rst = 1'b1, start = 1'b0, step = 1'b0, step_switch = 1'b0;")
        for v in self.inputs:
            emit(f"    reg  [WIDTH-1:0] {v.src} = 0;")
        emit(f"    reg  [{PW-1}:0] function_probe_sel = 0;")
        emit("    wire [7:0] an; wire [6:0] cn; wire busy, done;")
        conns = [".clk(clk)", ".rst(rst)", ".start(start)", ".step(step)", ".step_switch(step_switch)"]
        conns += [f".{v.src}({v.src})" for v in self.inputs]
        conns += [".an(an)", ".cn(cn)", ".busy(busy)", ".done(done)",
                  ".function_probe_sel(function_probe_sel)"]
        emit(f"    {top} #(.WIDTH(WIDTH), .DEBOUNCE_CYCLES(DB)) dut (")
        emit("        " + ",\n        ".join(conns))
        emit("    );")
        emit("    always #5 clk = ~clk;")
        emit("    // seven_seg's digit counter runs on the divided clock, which does not tick during")
        emit("    // reset, so in simulation it starts at x (FPGA flops power up at 0 -- hardware is fine)")
        emit("    initial dut.u_display.anode_counter.count = 3'd0;")
        emit("")
        emit("    reg [WIDTH-1:0] " + ", ".join(f"ref_{ident(n)}" for n in names) + ";")
        emit("    task run_reference; begin")
        for v in self.inputs:
            emit(f"        ref_{ident(v.name)} = {v.src};")
        for out, a, op, b in algo:
            emit(f"        {ref(out)} = {ref(a)} {op} {ref(b)};")
        emit("    end endtask")
        emit("    task set_switches; begin")
        for v in self.inputs:
            emit(f"        {v.src} = {rand};")
        emit("    end endtask")
        emit("")
        emit("    // a press: a few bounces, held for `hold` cycles, a few bounces on release")
        emit("    task press(input integer which, input integer hold); integer k; begin")
        emit("        for (k = 0; k < 3; k = k + 1) begin")
        emit("            if (which) step = 1; else start = 1; repeat (3) @(negedge clk);")
        emit("            if (which) step = 0; else start = 0; repeat (2) @(negedge clk);")
        emit("        end")
        emit("        if (which) step = 1; else start = 1;")
        emit("        repeat (hold) @(negedge clk);")
        emit("        for (k = 0; k < 2; k = k + 1) begin")
        emit("            if (which) step = 0; else start = 0; repeat (2) @(negedge clk);")
        emit("            if (which) step = 1; else start = 1; repeat (2) @(negedge clk);")
        emit("        end")
        emit("        if (which) step = 0; else start = 0;")
        emit("        repeat (DB * 3) @(negedge clk);")
        emit("    end endtask")
        emit("")
        emit("    integer t, k, errors = 0;")
        emit("    task check_outputs(input integer t); begin")
        for o in self.outputs:
            emit(f"        if (dut.out_{ident(o)} !== ref_{ident(o)}) begin errors = errors + 1;")
            emit(f"            $display(\"MISMATCH test %0d: {o} = %0d, expected %0d\", t, "
                 f"dut.out_{ident(o)}, ref_{ident(o)}); end")
        emit("        if (done !== 1'b1) begin errors = errors + 1;")
        emit("            $display(\"test %0d: done LED not lit\", t); end")
        emit("    end endtask")
        emit("    task expect_state(input integer s, input integer t); begin")
        emit("        if (dut.dbg_state !== s) begin errors = errors + 1;")
        emit("            $display(\"test %0d: state = %0d, expected %0d\", t, dut.dbg_state, s); end")
        emit("    end endtask")
        emit("")
        emit("    initial begin")
        emit("        repeat (5) @(negedge clk); rst = 0; repeat (5) @(negedge clk);")
        emit("        // ---------------- run mode: long press, results must survive the release")
        emit("        for (t = 0; t < N_TESTS; t = t + 1) begin")
        emit("            set_switches; run_reference;")
        emit("            press(0, DB * 10);")
        emit("            set_switches;                    // must not disturb latched inputs")
        emit("            repeat (DB * 5) @(negedge clk);")
        emit("            check_outputs(t);")
        emit("        end")
        emit("        // ---------------- step mode")
        emit("        step_switch = 1; repeat (10) @(negedge clk);")
        emit("        for (t = N_TESTS; t < 2 * N_TESTS; t = t + 1) begin")
        emit("            set_switches; run_reference;")
        emit("            repeat (DB * 5) @(negedge clk);")
        emit("            expect_state(0, t);               // nothing moves without a press")
        emit("            press(0, DB * 4);")
        emit("            set_switches;")
        emit("            expect_state(1, t);               // start = cycle 1, inputs latched")
        emit("            for (k = 2; k <= S_DONE; k = k + 1) begin")
        emit("                press(1, DB * (k == 2 ? 40 : 4));   // one very long hold included")
        emit("                expect_state(k, t);")
        emit("                if (k < S_DONE && done) begin errors = errors + 1;")
        emit("                    $display(\"test %0d: done LED early\", t); end")
        emit("            end")
        emit("            check_outputs(t);")
        emit("            press(1, DB * 4);                 // DONE -> IDLE")
        emit("            expect_state(0, t);")
        emit("            check_outputs(t);                 // results still shown afterwards")
        emit("        end")
        emit("        // ---------------- back to run mode")
        emit("        step_switch = 0; repeat (10) @(negedge clk);")
        emit("        set_switches; run_reference; press(0, DB * 4);")
        emit("        repeat (DB * 2) @(negedge clk); check_outputs(2 * N_TESTS);")
        emit("        if (errors == 0) $display(\"PASS: %0d run-mode + %0d step-mode tests\", N_TESTS + 1, N_TESTS);")
        emit("        else             $display(\"FAIL: %0d errors\", errors);")
        emit("        $finish;")
        emit("    end")
        emit("endmodule")
        return "\n".join(L) + "\n"

    # ======================================================================
    #             Constraints (Nexys A7 / Nexys 4 DDR), same style as constraints.xdc
    # ======================================================================
    #  switches : inputs packed from sw[0] upward in port order; function_probe_sel in the
    #             switches just below sw[15]; step_switch = sw[15]
    #  LEDs     : busy = led[14], done = led[15]
    #  buttons  : rst = btnc, start = btnd, step = btnl
    NEXYS_SW = [("J15", "LVCMOS33", "IO_L24N_T3_RS0_15"), ("L16", "LVCMOS33", "IO_L3N_T0_DQS_EMCCLK_14"),
                ("M13", "LVCMOS33", "IO_L6N_T0_D08_VREF_14"), ("R15", "LVCMOS33", "IO_L13N_T2_MRCC_14"),
                ("R17", "LVCMOS33", "IO_L12N_T1_MRCC_14"), ("T18", "LVCMOS33", "IO_L7N_T1_D10_14"),
                ("U18", "LVCMOS33", "IO_L17N_T2_A13_D29_14"), ("R13", "LVCMOS33", "IO_L5N_T0_D07_14"),
                ("T8", "LVCMOS18", "IO_L24N_T3_34"), ("U8", "LVCMOS18", "IO_25_34"),
                ("R16", "LVCMOS33", "IO_L15P_T2_DQS_RDWR_B_14"), ("T13", "LVCMOS33", "IO_L23P_T3_A03_D19_14"),
                ("H6", "LVCMOS33", "IO_L24P_T3_35"), ("U12", "LVCMOS33", "IO_L20P_T3_A08_D24_14"),
                ("U11", "LVCMOS33", "IO_L19N_T3_A09_D25_VREF_14"), ("V10", "LVCMOS33", "IO_L21P_T3_DQS_14")]
    NEXYS_LED = [("H17", "IO_L18P_T2_A24_15"), ("K15", "IO_L24P_T3_RS1_15"), ("J13", "IO_L17N_T2_A25_15"),
                 ("N14", "IO_L8P_T1_D11_14"), ("R18", "IO_L7P_T1_D09_14"), ("V17", "IO_L18N_T2_A11_D27_14"),
                 ("U17", "IO_L17P_T2_A14_D30_14"), ("U16", "IO_L18P_T2_A12_D28_14"),
                 ("V16", "IO_L16N_T2_A15_D31_14"), ("T15", "IO_L14N_T2_SRCC_14"),
                 ("U14", "IO_L22P_T3_A05_D21_14"), ("T16", "IO_L15N_T2_DQS_DOUT_CSO_B_14"),
                 ("V15", "IO_L16P_T2_CSI_B_14"), ("V14", "IO_L22N_T3_A04_D20_14"),
                 ("V12", "IO_L20N_T3_A07_D23_14"), ("V11", "IO_L21N_T3_DQS_A06_D22_14")]
    NEXYS_BTN = [("rst", "N17", "IO_L9P_T1_DQS_14", "btnc"), ("BTNU", "M18", "IO_L4N_T0_D05_14", "btnu"),
                 ("start", "P18", "IO_L9N_T1_DQS_D13_14", "btnd"), ("step", "P17", "IO_L12P_T1_MRCC_14", "btnl"),
                 ("BTNR", "M17", "IO_L10N_T1_D15_14", "btnr")]
    NEXYS_SEG = [("T10", "IO_L24N_T3_A00_D16_14", "ca"), ("R10", "IO_25_14", "cb"), ("K16", "IO_25_15", "cc"),
                 ("K13", "IO_L17P_T2_A26_15", "cd"), ("P15", "IO_L13P_T2_MRCC_14", "ce"),
                 ("T11", "IO_L19P_T3_A10_D26_14", "cf"), ("L18", "IO_L4P_T0_D04_14", "cg")]
    NEXYS_AN = [("J17", "IO_L23P_T3_FOE_B_15"), ("J18", "IO_L23N_T3_FWE_B_15"), ("T9", "IO_L24P_T3_A01_D17_14"),
                ("J14", "IO_L19P_T3_A22_15"), ("P14", "IO_L8N_T1_D12_14"), ("T14", "IO_L14P_T2_SRCC_14"),
                ("K2", "IO_L23P_T3_35"), ("U13", "IO_L23N_T3_A02_D18_14")]

    def switch_map(self):
        PW = clog2(len(self.units()))
        sw = [None] * 16
        bits = [f"{v.src}[{i}]" for v in self.inputs for i in range(self.width)]
        if len(bits) + PW + 1 > 16:
            raise ValueError(f"{len(bits)} input bits + {PW} probe-select bits + step_switch need "
                             f"{len(bits) + PW + 1} switches; the board has 16. Reduce --width or inputs.")
        for i, b in enumerate(bits):
            sw[i] = b
        for i in range(PW):
            sw[15 - PW + i] = f"function_probe_sel[{i}]"
        sw[15] = "step_switch"
        return sw

    def switch_summary(self):
        """e.g. 'in_A=sw[3:0], in_C=sw[7:4], ..., step_switch=sw[15]'"""
        try:
            sw = self.switch_map()
        except ValueError as e:
            return f"(do not fit on the board: {e})"
        spans = {}
        for i, p in enumerate(sw):
            if p:
                spans.setdefault(p.split("[")[0], []).append(i)
        return ", ".join(f"{n}=sw[{ix[-1]}:{ix[0]}]" if len(ix) > 1 else f"{n}=sw[{ix[0]}]"
                         for n, ix in spans.items())

    def xdc(self):
        def line(pin, std, port, comment, active=True, end=True):
            s = (f"set_property -dict {{ PACKAGE_PIN {pin:<6}IOSTANDARD {std} }} "
                 f"[get_ports {{ {port} }}]; #{comment}")
            return s if active else "#" + s
        L = []
        for i, (port, (pin, std, bank)) in enumerate(zip(self.switch_map(), self.NEXYS_SW)):
            L.append(line(pin, std, port or f"SW[{i}]", f"{bank} Sch=sw[{i}]", port is not None))
            L.append("")
        leds = {14: "busy", 15: "done"}
        for i, (pin, bank) in enumerate(self.NEXYS_LED):
            L.append(line(pin, "LVCMOS33", leds.get(i, f"LED[{i}]"), f"{bank} Sch=led[{i}]", i in leds))
            L.append("")
        L.append("")
        L.append(line("E3", "LVCMOS33", "clk", "IO_L12P_T1_MRCC_35 Sch=clk100mhz"))
        L.append("create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports {clk}];")
        for port, pin, bank, sch in self.NEXYS_BTN:
            L.append(line(pin, "LVCMOS33", port, f"{bank} Sch={sch}", port.islower()))
        for i, (pin, bank, sch) in enumerate(self.NEXYS_SEG):
            L.append(line(pin, "LVCMOS33", f"cn[{i}]", f"{bank} Sch={sch}"))
        L.append(line("H15", "LVCMOS33", "DP", "IO_L19N_T3_A21_VREF_15 Sch=dp", False))
        L.append("")
        for i, (pin, bank) in enumerate(self.NEXYS_AN):
            L.append(line(pin, "LVCMOS33", f"an[{i}]", f"{bank} Sch=an[{i}]"))
        return "\r\n".join(L)          # CRLF, like the original file

    # ======================================================================
    #                               Report
    # ======================================================================
    def report(self, removed=()):
        T = self.last_step
        out = [f"Datapath report: {self.top}", "=" * 60, ""]
        out.append("Latency (NUM_PIPE_CYCLES): " +
                   ", ".join(f"{FU_TYPES[o]['module']}={l}" for o, l in self.latency.items()))
        out.append(f"Control steps: 1..{T}   total latency start->done: {T + 1} cycles")
        if removed:
            out.append(f"Dead code removed (never reaches an output): {', '.join(removed)}")
        out.append("")
        out.append("Schedule / binding")
        out.append("-" * 60)
        for n in self.nodes:
            v = self.values[id(n)]
            dest = f"r{v.reg}" if v.reg is not None else "bypass only"
            out.append(f"  step {n.step:>2}  {self.unit_name(n):<5} {n.name:>6} = "
                       f"{n.children_names():<14} result @ step {v.w:<3} -> {dest}")
        out.append("")
        out.append("Functional units")
        out.append("-" * 60)
        for u, op in self.units():
            a, b = len(self.port_sources[(u, 'a')]), len(self.port_sources[(u, 'b')])
            out.append(f"  {u:<6} {FU_TYPES[op]['module']:<11} operand muxes {a}:1 / {b}:1")
        out.append("")
        out.append("Registers (left-edge allocation; lifetime = steps the value must be held)")
        out.append("-" * 60)
        for r, vals in enumerate(self.registers):
            life = ", ".join(f"{v.name}[{v.start}..{'end' if v.end == math.inf else v.end}]"
                             for v in vals)
            out.append(f"  r{r:<3} input mux {len(self.reg_sources[r])}:1   {life}")
        out.append(f"  total: {len(self.registers)} registers for "
                   f"{sum(len(v) for v in self.registers)} stored values")
        out.append("")
        out.append("Outputs")
        out.append("-" * 60)
        for o, n in self.outputs.items():
            out.append(f"  out_{ident(o)} <- r{self.values[id(n)].reg} ({n.name})")
        out.append("")
        out.append("Usage per step")
        out.append("-" * 60)
        hdr = "  step | " + " | ".join(f"{u:^5}" for u, _ in self.units()) + " | writes"
        out.append(hdr)
        for s in range(0, T + 1):
            busy = {u: "" for u, _ in self.units()}
            for (u, _, _, n) in self.issue.get(s, []):
                busy[u] = n.name
            wr = ", ".join(f"r{r}<-{v.name}" for r, _, v in self.writes.get(s, []))
            out.append(f"  {s:>4} | " + " | ".join(f"{busy[u]:^5}" for u, _ in self.units())
                       + f" | {wr}")
        return "\n".join(out) + "\n"


def _children_names(self):
    nm = lambda c: c.name if isinstance(c, Node) else c
    return f"{nm(self.children[0])} {self.op} {nm(self.children[1])}"


Node.children_names = _children_names
