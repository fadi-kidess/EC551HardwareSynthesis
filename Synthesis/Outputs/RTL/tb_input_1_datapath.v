`timescale 1ns/1ps
// Self-checking testbench for input_1_datapath (auto-generated)
module tb_input_1_datapath;
    localparam WIDTH = 32;
    localparam N_TESTS = 200;
    reg clk = 1'b0, rst = 1'b1, start = 1'b0;
    reg  [WIDTH-1:0] in_A;
    reg  [WIDTH-1:0] in_C;
    reg  [WIDTH-1:0] in_B;
    wire [WIDTH-1:0] out_Q;
    wire [WIDTH-1:0] out_Z;
    wire [WIDTH-1:0] out_Y;
    wire [WIDTH-1:0] out_X;
    wire busy, done;

    input_1_datapath #(.WIDTH(WIDTH)) dut (
        .clk(clk),
        .rst(rst),
        .start(start),
        .in_A(in_A),
        .in_C(in_C),
        .in_B(in_B),
        .out_Q(out_Q),
        .out_Z(out_Z),
        .out_Y(out_Y),
        .out_X(out_X),
        .busy(busy),
        .done(done)
    );
    always #5 clk = ~clk;

    // reference model: the original program, executed in order
    reg [WIDTH-1:0] ref_x, ref_y, ref_a, ref_b, ref_c, ref_A, ref_B, ref_X, ref_C, ref_Y, ref_Z, ref_Q;
    task run_reference; begin
        ref_A = in_A;
        ref_C = in_C;
        ref_B = in_B;
        ref_a = ref_x + ref_y;
        ref_b = ref_a + ref_y;
        ref_a = ref_b + ref_c;
        ref_X = ref_A + ref_B;
        ref_X = ref_A + ref_C;
        ref_Y = ref_X + ref_A;
        ref_Z = ref_X - ref_B;
        ref_Q = ref_B * ref_Z;
    end endtask

    integer t, errors = 0, cycles;
    task randomize_inputs(input integer mode); begin
        in_A = (mode == 0) ? {WIDTH{1'b0}} : (mode == 1) ? {WIDTH{1'b1}} : {$random};
        in_C = (mode == 0) ? {WIDTH{1'b0}} : (mode == 1) ? {WIDTH{1'b1}} : {$random};
        in_B = (mode == 0) ? {WIDTH{1'b0}} : (mode == 1) ? {WIDTH{1'b1}} : {$random};
    end endtask

    initial begin
        randomize_inputs(0);
        repeat (3) @(negedge clk);
        rst = 1'b0;
        for (t = 0; t < N_TESTS; t = t + 1) begin
            @(negedge clk);
            randomize_inputs(t < 2 ? t : 2);
            run_reference;
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;
            randomize_inputs(2);          // inputs must have been latched
            cycles = 1;
            while (!done) begin @(negedge clk); cycles = cycles + 1; end
            if (out_Q !== ref_Q) begin
                errors = errors + 1;
                $display("MISMATCH test %0d: Q = %0d, expected %0d", t, out_Q, ref_Q);
            end
            if (out_Z !== ref_Z) begin
                errors = errors + 1;
                $display("MISMATCH test %0d: Z = %0d, expected %0d", t, out_Z, ref_Z);
            end
            if (out_Y !== ref_Y) begin
                errors = errors + 1;
                $display("MISMATCH test %0d: Y = %0d, expected %0d", t, out_Y, ref_Y);
            end
            if (out_X !== ref_X) begin
                errors = errors + 1;
                $display("MISMATCH test %0d: X = %0d, expected %0d", t, out_X, ref_X);
            end
        end
        if (errors == 0) $display("PASS: %0d tests, %0d cycles start->done", N_TESTS, cycles);
        else             $display("FAIL: %0d mismatches", errors);
        $finish;
    end
endmodule
