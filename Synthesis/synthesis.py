from Parser import parser
import dag
import argparse
from pathlib import Path
arg = argparse.ArgumentParser(description="Read a text file from command line arguments.")
arg.add_argument("--file_path", type=str, help="Path to the Input TXT file")
arg.add_argument("--export-ssa", action='store_true', help="Export the SSA to TXT file")
args = arg.parse_args()
content = None
try:
    with open(args.file_path, "r", encoding="utf-8") as file:
        content = file
        algo = parser.parse(content)
        ssa_algo = parser.ssa(algo)
        
        node_dict = dag.algo2tree(ssa_algo)

        for node in node_dict.values():
            print(node)

        if(args.export_ssa):
            export = parser.export_ssa(ssa_algo)
            filename = Path(args.file_path).name
            with open("Outputs/SSA/"+filename[:-4] + '_ssa.txt' ,"w")as ssa_file_out:
                ssa_file_out.write(export)
        
except FileNotFoundError:
    print(f"Error: The file at '{args.file_path}' was not found.")


