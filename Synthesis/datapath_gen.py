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
 
 
def dead_code_eliminate(algo, ssa_algo, outputs=None):
    final = {}
    for orig, step in zip(algo, ssa_algo):
        final[orig[0]] = step[0]
    if outputs:
        missing = [o for o in outputs if o not in final]
        if missing:
            raise ValueError(f"--outputs names variables that are never assigned: {missing}")
        final = {o: final[o] for o in outputs}
 
    live, keep = set(final.values()), []
    for step in reversed(ssa_algo): 
        if step[0] in live:
            keep.append(step)
            live.update((step[1], step[3]))
    keep.reverse()
    removed = [s[0] for s in ssa_algo if s not in keep]
    return keep, final, removed
 

class Value:
    def __init__(self, kind, name, src, write_step):
        self.kind = kind
        self.name = name
        self.src = src 
        self.w = write_step 
        self.reg_reads = []
        self.is_output = False
        self.reg = None
 
    @property
    def needs_reg(self):
        return self.is_output or bool(self.reg_reads)
 
    @property
    def start(self):
        return self.w + 1
 
    @property
    def end(self):
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
 
    def unit_name(self, n):
        return f"{FU_TYPES[n.op]['prefix']}{n.unit}"
 
    def units(self):
        seen = {}
        for n in self.nodes:
            seen[self.unit_name(n)] = n.op
        return sorted(seen.items(), key=lambda kv: kv[0])
 
    def _build_values(self):
        self.values = {} 
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
                 "    input  wire             start,"]
        ports += [f"    input  wire [WIDTH-1:0] {v.src}," for v in self.inputs]
        ports += [f"    output wire [WIDTH-1:0] out_{ident(o)}," for o in self.outputs]
        ports += ["    output wire             busy,", "    output wire             done"]
        ports += [f"    input wire [{clog2(len(units))}-1:0] function_probe_sel,"]
        ports += [f"    output reg [WIDTH-1:0] function_probe"]
        emit("// ---------------------------------------------------------------------------")
        emit(f"// {self.top}: auto-generated by datapath_gen.py -- do not edit by hand")
        emit("//")
        emit("// Protocol: pulse `start` (1 cycle) with inputs valid; inputs are latched,")
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
        emit("        else case (state)")
        emit("            S_IDLE: if (start)  state <= 1;")
        emit("            S_DONE:             state <= S_IDLE;")
        emit("            default:            state <= state + 1'b1;   // control steps 1..%d" % T)
        emit("        endcase")
        emit("    end")
        emit("    assign busy = (state != S_IDLE);")
        emit("    assign done = (state == S_DONE);")
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
            emit(f"        .clk(clk), .a({u}_a), .b({u}_b), .out({u}_out));")
            emit("")
            
        emit(f"    always @(*) begin")
        emit(f"        case (function_probe)")
        last = None
        for index, (u, op) in enumerate(units):
            emit(f"            {clog2(len(units))}'d{index}: function_probe = {u}_out;")
            last = u
        emit(f"            default: function_probe = {last}_out;")
        emit(f"        endcase")
        emit(f"    end")
        
        # ---------------- registers
        emit("    // ------------------------------------------------------------ registers")
        for r, srcs in enumerate(self.reg_sources):
            names = ", ".join(v.name for v in self.registers[r])
            emit(f"    // r{r} holds: {names}")
            mux(f"r{r}_d", srcs, f"r{r}_sel")
            emit(f"    reg  [WIDTH-1:0] r{r};")
            emit(f"    always @(posedge clk) if (r{r}_we) r{r} <= r{r}_d;")
            emit("")
 
        emit("    // ------------------------------------------------------------ outputs")
        for o, n in self.outputs.items():
            emit(f"    assign out_{ident(o)} = r{self.values[id(n)].reg};   // {n.name}")
        emit("")
        emit("endmodule")
        return "\n".join(L) + "\n"
    # ======================================================================
    #                               Constraints
    # ======================================================================
    
    def constraints(self,template):
        with open(template) as f:
            lines = f.readlines()
        # We edit the IOs we need
        for index,v in enumerate(self.inputs):
            for j in range(self.width):
                lines[index*self.width + j] = lines[index*self.width + j][1:]
                lines[index*self.width + j] = lines[index*self.width + j].replace(f"SW[{index*self.width + j}]", f"{v.src}[{j}]")
        for index,o in enumerate(self.outputs):
            for j in range(self.width):
                lines[16+index*self.width + j] = lines[16+index*self.width + j][1:]
                lines[16+index*self.width + j] = lines[16+index*self.width + j].replace(f"LED[{index*self.width + j}]", f"out_{ident(o)}[{j}]")
        # We write back
        return "\n".join(lines)
    # ======================================================================
    #                               Testbench
    # ======================================================================
    
    #Claude helped with clean spacing and formatting, making RTL debugging of generated verilog cleaner and simpler.
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
        emit("    reg clk = 1'b0, rst = 1'b1, start = 1'b0;")
        for v in self.inputs:
            emit(f"    reg  [WIDTH-1:0] {v.src};")
        for o in self.outputs:
            emit(f"    wire [WIDTH-1:0] out_{ident(o)};")
        emit("    wire busy, done;")
        emit("")
        conns = [".clk(clk)", ".rst(rst)", ".start(start)"]
        conns += [f".{v.src}({v.src})" for v in self.inputs]
        conns += [f".out_{ident(o)}(out_{ident(o)})" for o in self.outputs]
        conns += [".busy(busy)", ".done(done)"]
        emit(f"    {self.top} #(.WIDTH(WIDTH)) dut (")
        emit("        " + ",\n        ".join(conns))
        emit("    );")
        emit("    always #5 clk = ~clk;")
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
        emit("        for (t = 0; t < N_TESTS; t = t + 1) begin")
        emit("            @(negedge clk);")
        emit("            randomize_inputs(t < 2 ? t : 2);")
        emit("            run_reference;")
        emit("            start = 1'b1;")
        emit("            @(negedge clk);")
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
        emit("        if (errors == 0) $display(\"PASS: %0d tests, %0d cycles start->done\", N_TESTS, cycles);")
        emit("        else             $display(\"FAIL: %0d mismatches\", errors);")
        emit("        $finish;")
        emit("    end")
        emit("endmodule")
        return "\n".join(L) + "\n"
 
    # ======================================================================
    #                               Report
    # ======================================================================
    
    #Claude helped with the report visualizations.
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
