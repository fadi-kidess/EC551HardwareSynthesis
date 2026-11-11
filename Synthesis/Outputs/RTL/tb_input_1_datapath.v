`timescale 1ns/1ps
// Board-level testbench for input_1_datapath (auto-generated)
module tb_input_1_datapath;
    localparam WIDTH = 4, DB = 20, S_DONE = 11, N_TESTS = 20;
    reg clk = 1'b0, rst = 1'b1, start = 1'b0, step = 1'b0, step_switch = 1'b0;
    reg  [WIDTH-1:0] in_A = 0;
    reg  [WIDTH-1:0] in_C = 0;
    reg  [WIDTH-1:0] in_B = 0;
    reg  [1:0] function_probe_sel = 0;
    wire [7:0] an; wire [6:0] cn; wire busy, done;
    input_1_datapath #(.WIDTH(WIDTH), .DEBOUNCE_CYCLES(DB)) dut (
        .clk(clk),
        .rst(rst),
        .start(start),
        .step(step),
        .step_switch(step_switch),
        .in_A(in_A),
        .in_C(in_C),
        .in_B(in_B),
        .an(an),
        .cn(cn),
        .busy(busy),
        .done(done),
        .function_probe_sel(function_probe_sel)
    );
    always #5 clk = ~clk;
    // seven_seg's digit counter runs on the divided clock, which does not tick during
    // reset, so in simulation it starts at x (FPGA flops power up at 0 -- hardware is fine)
    initial dut.u_display.anode_counter.count = 3'd0;

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
    task set_switches; begin
        in_A = {$random};
        in_C = {$random};
        in_B = {$random};
    end endtask

    // a press: a few bounces, held for `hold` cycles, a few bounces on release
    task press(input integer which, input integer hold); integer k; begin
        for (k = 0; k < 3; k = k + 1) begin
            if (which) step = 1; else start = 1; repeat (3) @(negedge clk);
            if (which) step = 0; else start = 0; repeat (2) @(negedge clk);
        end
        if (which) step = 1; else start = 1;
        repeat (hold) @(negedge clk);
        for (k = 0; k < 2; k = k + 1) begin
            if (which) step = 0; else start = 0; repeat (2) @(negedge clk);
            if (which) step = 1; else start = 1; repeat (2) @(negedge clk);
        end
        if (which) step = 0; else start = 0;
        repeat (DB * 3) @(negedge clk);
    end endtask

    integer t, k, errors = 0;
    task check_outputs(input integer t); begin
        if (dut.out_Q !== ref_Q) begin errors = errors + 1;
            $display("MISMATCH test %0d: Q = %0d, expected %0d", t, dut.out_Q, ref_Q); end
        if (dut.out_Z !== ref_Z) begin errors = errors + 1;
            $display("MISMATCH test %0d: Z = %0d, expected %0d", t, dut.out_Z, ref_Z); end
        if (dut.out_Y !== ref_Y) begin errors = errors + 1;
            $display("MISMATCH test %0d: Y = %0d, expected %0d", t, dut.out_Y, ref_Y); end
        if (dut.out_X !== ref_X) begin errors = errors + 1;
            $display("MISMATCH test %0d: X = %0d, expected %0d", t, dut.out_X, ref_X); end
        if (done !== 1'b1) begin errors = errors + 1;
            $display("test %0d: done LED not lit", t); end
    end endtask
    task expect_state(input integer s, input integer t); begin
        if (dut.dbg_state !== s) begin errors = errors + 1;
            $display("test %0d: state = %0d, expected %0d", t, dut.dbg_state, s); end
    end endtask

    initial begin
        repeat (5) @(negedge clk); rst = 0; repeat (5) @(negedge clk);
        // ---------------- run mode: long press, results must survive the release
        for (t = 0; t < N_TESTS; t = t + 1) begin
            set_switches; run_reference;
            press(0, DB * 10);
            set_switches;                    // must not disturb latched inputs
            repeat (DB * 5) @(negedge clk);
            check_outputs(t);
        end
        // ---------------- step mode
        step_switch = 1; repeat (10) @(negedge clk);
        for (t = N_TESTS; t < 2 * N_TESTS; t = t + 1) begin
            set_switches; run_reference;
            repeat (DB * 5) @(negedge clk);
            expect_state(0, t);               // nothing moves without a press
            press(0, DB * 4);
            set_switches;
            expect_state(1, t);               // start = cycle 1, inputs latched
            for (k = 2; k <= S_DONE; k = k + 1) begin
                press(1, DB * (k == 2 ? 40 : 4));   // one very long hold included
                expect_state(k, t);
                if (k < S_DONE && done) begin errors = errors + 1;
                    $display("test %0d: done LED early", t); end
            end
            check_outputs(t);
            press(1, DB * 4);                 // DONE -> IDLE
            expect_state(0, t);
            check_outputs(t);                 // results still shown afterwards
        end
        // ---------------- back to run mode
        step_switch = 0; repeat (10) @(negedge clk);
        set_switches; run_reference; press(0, DB * 4);
        repeat (DB * 2) @(negedge clk); check_outputs(2 * N_TESTS);
        if (errors == 0) $display("PASS: %0d run-mode + %0d step-mode tests", N_TESTS + 1, N_TESTS);
        else             $display("FAIL: %0d errors", errors);
        $finish;
    end
endmodule
