module adder #(
    parameter WIDTH = 32,
    parameter NUM_PIPE_CYCLES = 3
)(
    input  wire                 clk,
    input  wire [WIDTH-1:0]     a,
    input  wire [WIDTH-1:0]     b,
    output wire [WIDTH-1:0]     out
);

    // Attribute to force Vivado to retime this specific block 
    // even if global retiming is turned off
    (* RETIMING_FORWARD = "yes" *) reg [WIDTH-1:0] pipe_regs [0:NUM_PIPE_CYCLES-1];
    
    integer i;

    always @(posedge clk) begin
        if (NUM_PIPE_CYCLES > 0) begin
            pipe_regs[0] <= data_in;
            for (i = 1; i < NUM_PIPE_CYCLES; i = i + 1) begin
                pipe_regs[i] <= pipe_regs[i-1];
            end
        end
    end

    assign out = (NUM_PIPE_CYCLES > 0) ? pipe_regs[NUM_PIPE_CYCLES-1] : a+b;

endmodule

