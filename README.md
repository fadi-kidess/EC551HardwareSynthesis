README File:
o Explanation of repository organization and directory structure.
o Short description of your synthesis tool architecture.
o Instructions on how to read, build, and run the code.
o Your complete 30-minute demo plan.

## Repo Organization and Directory Structure
```text
.
├── EC551_Program2_Rev2.pdf			<- Assignment description
├── README.md						<- This File
└── Synthesis						<- Project Root
    ├── Inputs						<- Input files (lists of equations)
    │   └── input_1.txt
    │
	├── Outputs						<- Output files
    │   └── SSA						<- Single Static Assignment files
    │       └── input_1_ssa.txt
    │
	├── Parser						<- <in>.txt to algo. (list of steps)
    │   └── parser.py				
    │
	├── VerilogFunctionalBlocks		<- Parameterized primitive blocks
    │   ├── adder.v
    │   ├── multiplier.v
    │   └── subtractor.v
    │
	├── dag.py						<- algo. to DAG graph w\schedulers
    └── synthesis.py				<- main func. (implements CLI tool)
```

## Tool Architecture

## Instructions

## Demo Plan