// Small combinational example for the optional Verilog/Yosys entry point.
module alu(input [7:0] a, b, input [1:0] op, output reg [7:0] y);
always @* begin
    case(op)
        2'b00: y = a + b;
        2'b01: y = a ^ b;
        2'b10: y = (a < b) ? a : b;
        default: y = {a[3:0], b[3:0]};
    endcase
end
endmodule
