try:
    from Parser import parser
except ImportError:          # parser.py sitting next to this file
    import parser
import dag
import argparse
from pathlib import Path
arg = argparse.ArgumentParser(description="Read a text file from command line arguments.")
arg.add_argument("--file_path", type=str, help="Path to the Input TXT file")
arg.add_argument("--export-ssa", action='store_true', help="Export the SSA to TXT file")
# CUSTOM scheduler: resources per operator type
arg.add_argument("--max-add", type=int, default=1, help="Number of adders (max '+' issued per step)")
arg.add_argument("--max-sub", type=int, default=1, help="Number of subtractors (max '-' per step)")
arg.add_argument("--max-mul", type=int, default=1, help="Number of multipliers (max '*' per step)")
arg.add_argument("--lat-add", type=int, default=3, help="Adder NUM_PIPE_CYCLES")
arg.add_argument("--lat-sub", type=int, default=3, help="Subtractor NUM_PIPE_CYCLES")
arg.add_argument("--lat-mul", type=int, default=3, help="Multiplier NUM_PIPE_CYCLES")
# RTL generation
arg.add_argument("--rtl", action='store_true', help="Generate datapath + FSM Verilog and a testbench")
arg.add_argument("--rtl-dir", type=str, default="Outputs/RTL", help="Where to write the RTL")
arg.add_argument("--top", type=str, default=None, help="Top module name (default: <input>_datapath)")
arg.add_argument("--width", type=int, default=32, help="Datapath width")
arg.add_argument("--fpga", action='store_true',
                 help="Generate the RTL (implies --rtl) plus the board top <top>.v, which wraps <top>_core.v with debouncers, step mode and "
                      "seven-segment display (instantiates debouncer.v and seven_seg.v)")
arg.add_argument("--outputs", type=str, default=None,
                 help="Comma-separated variables to expose as outputs (default: every assigned variable)")
args = arg.parse_args()
if args.fpga:
    args.rtl = True          # the board top is built from the generated datapath

max_ops = {'+': args.max_add, '-': args.max_sub, '*': args.max_mul}
latency = {'+': args.lat_add, '-': args.lat_sub, '*': args.lat_mul}

content = None
try:
    with open(args.file_path, "r", encoding="utf-8") as file:
        content = file
        algo = parser.parse(content)
        ssa_algo = parser.ssa(algo)
        
        node_dict = dag.algo2tree(ssa_algo)

        print("\n************ASAP*****************\n")
        dag.schedule(node_dict, method="ASAP")
        for node in node_dict.values():
            print(node)

        print("\n************ALAP*****************\n")
        dag.schedule(node_dict, method="ALAP")
        for node in node_dict.values():
            print(node)
        
        print("\n************ASAP-CONST*****************\n")
        dag.schedule(node_dict, method="ASAP_CONST", max_op=2)
        for node in node_dict.values():
            print(node)

        print("\n************ALAP-CONST*****************\n")
        dag.schedule(node_dict, method="ALAP_CONST", max_op=2)
        for node in node_dict.values():
            print(node)

        print(f"\n************CUSTOM (per-type limits {max_ops}, latency {latency})*****************\n")
        dag.schedule(node_dict, method="CUSTOM", max_ops=max_ops, latency=latency)
        for node in node_dict.values():
            print(node)


        if(args.export_ssa):
            export = parser.export_ssa(ssa_algo)
            filename = Path(args.file_path).name
            with open("Outputs/SSA/"+filename[:-4] + '_ssa.txt' ,"w")as ssa_file_out:
                ssa_file_out.write(export)

        if(args.rtl):
            import datapath_gen
            outputs = [o.strip() for o in args.outputs.split(',')] if args.outputs else None
            live_algo, out_map, removed = datapath_gen.dead_code_eliminate(algo, ssa_algo, outputs)
            hw_dict = dag.algo2tree(live_algo)          # fresh DAG: only what reaches an output
            dag.schedule(hw_dict, method="CUSTOM", max_ops=max_ops, latency=latency)

            stem = Path(args.file_path).stem
            top = args.top or datapath_gen.ident(stem) + "_datapath"
            # With --fpga the board-facing module takes the top name and the generated
            # datapath becomes <top>_core; without it the datapath itself is the top.
            core = f"{top}_core" if args.fpga else top
            dp = datapath_gen.Datapath(hw_dict, out_map, latency, width=args.width, top=core)

            out_dir = Path(args.rtl_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{core}.v").write_text(dp.verilog())
            (out_dir / f"tb_{core}.v").write_text(dp.testbench(algo))
            report = dp.report(removed)
            written = [f"{core}.v", f"tb_{core}.v"]
            if args.fpga:
                (out_dir / f"{top}.v").write_text(dp.board_top(top))
                (out_dir / f"tb_{top}.v").write_text(dp.board_testbench(algo, top))
                written += [f"{top}.v", f"tb_{top}.v"]
                try:
                    xdc_text = dp.xdc()
                    with open(out_dir / f"{top}_constraints.xdc", "w", newline="") as xdc_out:
                        xdc_out.write(xdc_text)
                    written.append(f"{top}_constraints.xdc")
                except ValueError as e:
                    print(f"WARNING: no constraints file written: {e}")
                    report += f"FPGA note: no constraints file written: {e}\n"
                for note in dp.board_notes():
                    report += f"FPGA note: {note}\n"
                report += "\nSwitches: " + dp.switch_summary() + "\n"
                report += (f"FPGA top module: {top}. Board files needed: debouncer.v, seven_seg.v, "
                           f"clk_divider.v, counter_3bit_sync_reset.v\n")
            (out_dir / f"{top}_report.txt").write_text(report)
            print("\n************RTL*****************\n")
            print(report)
            print(f"Wrote {out_dir}/: " + ", ".join(written + [f"{top}_report.txt"]))
        
except FileNotFoundError:
    print(f"Error: The file at '{args.file_path}' was not found.")
