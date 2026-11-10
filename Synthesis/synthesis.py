from Parser import parser
import dag
import argparse
from pathlib import Path
arg = argparse.ArgumentParser(description="Read a text file from command line arguments.")
arg.add_argument("--file_path", type=str, help="Path to the Input TXT file")
arg.add_argument("--export-ssa", action='store_true', help="Export the SSA to TXT file")
arg.add_argument("--outputs", type=str, default=None,
                 help="Comma-separated variables to expose as outputs (default: every assigned variable)")
arg.add_argument("--max-add", type=int, default=1, help="Number of adders (max '+' issued per step)")
arg.add_argument("--max-sub", type=int, default=1, help="Number of subtractors (max '-' per step)")
arg.add_argument("--max-mul", type=int, default=1, help="Number of multipliers (max '*' per step)")
arg.add_argument("--lat-add", type=int, default=1, help="Adder NUM_PIPE_CYCLES")
arg.add_argument("--lat-sub", type=int, default=1, help="Subtractor NUM_PIPE_CYCLES")
arg.add_argument("--lat-mul", type=int, default=1, help="Multiplier NUM_PIPE_CYCLES")
arg.add_argument("--rtl-dir", type=str, default="Outputs/RTL", help="Where to write the RTL")
arg.add_argument("--top", type=str, default=None, help="Top module name (default: <input>_datapath)")
arg.add_argument("--width", type=int, default=32, help="Datapath width")
args = arg.parse_args()


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


        if(args.export_ssa):
            export = parser.export_ssa(ssa_algo)
            filename = Path(args.file_path).name
            with open("Outputs/SSA/"+filename[:-4] + '_ssa.txt' ,"w")as ssa_file_out:
                ssa_file_out.write(export)
        import datapath_gen
        outputs = [o.strip() for o in args.outputs.split(',')] if args.outputs else None
        live_algo, out_map, removed = datapath_gen.dead_code_eliminate(algo, ssa_algo, outputs)
        hw_dict = dag.algo2tree(live_algo)          # fresh DAG: only what reaches an output
        dag.schedule(hw_dict, method="CUSTOM", max_ops=max_ops, latency=latency)

        stem = Path(args.file_path).stem
        top = args.top or datapath_gen.ident(stem) + "_datapath"
        dp = datapath_gen.Datapath(hw_dict, out_map, latency, width=args.width, top=top)

        out_dir = Path(args.rtl_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{top}.v").write_text(dp.verilog())
        (out_dir / f"tb_{top}.v").write_text(dp.testbench(algo))
        report = dp.report(removed)
        (out_dir / f"{top}_report.txt").write_text(report)
        print("\n************RTL*****************\n")
        print(report)
        print(f"Wrote {out_dir}/{top}.v, tb_{top}.v, {top}_report.txt")
        print(f"Simulate: iverilog -o sim {out_dir}/{top}.v {out_dir}/tb_{top}.v "
              f"adder.v subtractor.v multiplier.v && vvp sim")
except FileNotFoundError:
    print(f"Error: The file at '{args.file_path}' was not found.")


