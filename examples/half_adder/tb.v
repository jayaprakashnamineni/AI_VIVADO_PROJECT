module half_adder_tb;

reg a;
reg b;
wire sum;
wire carry;

half_adder uut (
    .a(a),
    .b(b),
    .sum(sum),
    .carry(carry)
);

initial begin
    $monitor("At time %0t, a=%b, b=%b, sum=%b, carry=%b", $time, a, b, sum, carry);

    a = 0; b = 0; #10;
    if (sum !== 0 || carry !== 0) begin
        $display("ASSERTION FAILED");
        $display("CHECK: 0+0 => sum=0 carry=0");
        $display("TIME: %0t", $time);
        $display("EXPECTED: sum=0 carry=0");
        $display("ACTUAL: sum=%b carry=%b", sum, carry);
        $display("SIMULATION FAILED");
        $finish;
    end

    a = 0; b = 1; #10;
    if (sum !== 1 || carry !== 0) begin
        $display("ASSERTION FAILED");
        $display("CHECK: 0+1 => sum=1 carry=0");
        $display("TIME: %0t", $time);
        $display("EXPECTED: sum=1 carry=0");
        $display("ACTUAL: sum=%b carry=%b", sum, carry);
        $display("SIMULATION FAILED");
        $finish;
    end

    a = 1; b = 0; #10;
    if (sum !== 1 || carry !== 0) begin
        $display("ASSERTION FAILED");
        $display("CHECK: 1+0 => sum=1 carry=0");
        $display("TIME: %0t", $time);
        $display("EXPECTED: sum=1 carry=0");
        $display("ACTUAL: sum=%b carry=%b", sum, carry);
        $display("SIMULATION FAILED");
        $finish;
    end

    a = 1; b = 1; #10;
    if (sum !== 0 || carry !== 1) begin
        $display("ASSERTION FAILED");
        $display("CHECK: 1+1 => sum=0 carry=1");
        $display("TIME: %0t", $time);
        $display("EXPECTED: sum=0 carry=1");
        $display("ACTUAL: sum=%b carry=%b", sum, carry);
        $display("SIMULATION FAILED");
        $finish;
    end

    $display("SIMULATION SUCCESSFUL");
    $finish;
end

endmodule