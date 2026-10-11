"""Lightweight deterministic regression harness.

Exercises the pipeline's NON-LLM, NON-Vivado components in-process so a
developer can validate core logic in seconds:

  * category classification (coverage_engine.classify_from_files)
  * evidence-based coverage verdicts (coverage_engine.evaluate_coverage)
  * deterministic expected-value repair and its decline rules
    (targeted_fix.attempt_targeted_fix)
  * deterministic combinational coverage completion
    (complete_coverage._complete_combinational)

No Ollama model and no Vivado run are required.  Exit code 0 = all pass.

Run:  .venv\\Scripts\\python.exe app\\regression.py
"""

import os
import sys
import tempfile
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))

# targeted_fix / complete_coverage bind their file paths from AI_WORKSPACE
# at import time, so point it at a scratch workspace BEFORE importing them.
WS = Path(tempfile.mkdtemp(prefix="regression_ws_"))
os.environ["AI_WORKSPACE"] = str(WS)

from coverage_engine import (          # noqa: E402
    classify_from_files,
    evaluate_coverage,
    parse_samples,
    _eval_seq_reset_asserted,
)
import targeted_fix                     # noqa: E402
import complete_coverage                # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    ok = bool(condition)
    RESULTS.append((name, ok))
    tag = "PASS" if ok else "FAIL"
    suffix = f" -> {detail}" if detail else ""
    print(f"  [{tag}] {name}{suffix}")


# ============================================================
# 1. CATEGORY CLASSIFICATION
# ============================================================

print("\n[1] category classification")

AND_TB = "module t;\nreg a; reg b; wire y;\ninitial begin a = 0; b = 1; end\nendmodule\n"
AND_RTL = "module and_gate(input wire a, input wire b, output wire y);\nassign y = a & b;\nendmodule\n"
CNT_TB = "module t;\nreg clk; reg rst; reg en; wire [3:0] count;\ninitial begin clk = 0; end\nendmodule\n"
CNT_RTL = "module counter(input wire clk, input wire rst, input wire en, output reg [3:0] count);\nalways @(posedge clk) count <= count + 1;\nendmodule\n"
FIFO_TB = "module t;\nreg clk; reg wr_en; reg rd_en; wire full; wire empty;\ninitial begin clk = 0; end\nendmodule\n"
FIFO_RTL = "module fifo(input wire clk, input wire wr_en, input wire rd_en, output wire full, output wire empty);\nreg [7:0] mem [0:3];\nendmodule\n"

check("combinational classified", classify_from_files("", AND_TB, AND_RTL) == "COMBINATIONAL",
      classify_from_files("", AND_TB, AND_RTL))
check("sequential classified", classify_from_files("", CNT_TB, CNT_RTL) == "SEQUENTIAL",
      classify_from_files("", CNT_TB, CNT_RTL))
check("memory classified", classify_from_files("", FIFO_TB, FIFO_RTL) == "MEMORY",
      classify_from_files("", FIFO_TB, FIFO_RTL))


# ============================================================
# 2. COVERAGE ENGINE
# ============================================================

print("\n[2] coverage engine")

COMB_LOG = (
    "At time 0, a=0 b=0 y=0\n"
    "At time 10000, a=0 b=1 y=0\n"
    "At time 20000, a=1 b=0 y=0\n"
    "At time 30000, a=1 b=1 y=1\n"
)
samples = parse_samples(COMB_LOG)

cov_pass = evaluate_coverage(
    category="COMBINATIONAL", samples=samples, log_text=COMB_LOG,
    tb_text=AND_TB, design_text=AND_RTL, input_signals=["a", "b"],
    exhaustive_limit=8, exhaustive_ok=True,
)
check("exhaustive hit -> 100%", cov_pass["coverage_pct"] == 100.0,
      f"{cov_pass['coverage_pct']}%")

cov_fail = evaluate_coverage(
    category="COMBINATIONAL", samples=samples[:2], log_text=COMB_LOG,
    tb_text=AND_TB, design_text=AND_RTL, input_signals=["a", "b"],
    exhaustive_limit=8, exhaustive_ok=False,
)
check("exhaustive miss -> not 100%", cov_fail["coverage_pct"] < 100.0,
      f"{cov_fail['coverage_pct']}%")

seq_samples = parse_samples(
    "At time 0, clk=0 rst=1 en=0 count=0\n"
    "At time 10000, clk=1 rst=1 en=0 count=0\n"
)
check("reset-asserted evidence detected",
      _eval_seq_reset_asserted(["clk", "rst", "en", "count"], seq_samples, "", "") is True)


# ============================================================
# 3. TARGETED FIX (expected-value repair + decline rules)
# ============================================================

print("\n[3] targeted fix")

TB_WRONG = (
    "module t;\n"
    "reg y;\n"
    "initial begin\n"
    "    if (y !== 1'b1) begin\n"
    '        $display("ASSERTION FAILED");\n'
    '        $display("CHECK: a=0 b=1");\n'
    '        $display("EXPECTED: 1");\n'
    '        $display("ACTUAL: %b", y);\n'
    '        $display("SIMULATION FAILED");\n'
    "        $finish;\n"
    "    end\n"
    "end\n"
    "endmodule\n"
)

LOG_WRONG = (
    "ASSERTION FAILED\n"
    "CHECK: a=0 b=1\n"
    "EXPECTED: 1\n"
    "ACTUAL: 0\n"
)
REVIEW_FUNC = "STATUS: FAIL\nFAILURE_TYPE: FUNCTIONAL_SIMULATION_ERROR\n"


def _write_ws(tb, log, review, design="module d; endmodule\n"):
    (WS / "tb.v").write_text(tb, encoding="utf-8")
    (WS / "design.v").write_text(design, encoding="utf-8")
    (WS / "simulation.log").write_text(log, encoding="utf-8")
    (WS / "review_report.txt").write_text(review, encoding="utf-8")


_write_ws(TB_WRONG, LOG_WRONG, REVIEW_FUNC)
res = targeted_fix.attempt_targeted_fix()
fixed_tb = (WS / "tb.v").read_text(encoding="utf-8")
check("single wrong expected -> applied",
      res["applied"] is True,
      res.get("fix_type", ""))
check("single wrong expected -> literal corrected",
      "1'b0" in fixed_tb and "EXPECTED: 0" in fixed_tb)

LOG_MULTI = (
    "ASSERTION FAILED\nCHECK: a=0 b=1\nEXPECTED: 1\nACTUAL: 0\n"
    "ASSERTION FAILED\nCHECK: a=1 b=0\nEXPECTED: 1\nACTUAL: 0\n"
)
_write_ws(TB_WRONG, LOG_MULTI, REVIEW_FUNC)
res = targeted_fix.attempt_targeted_fix()
check("multiple failures -> declined",
      res["applied"] is False and "Multiple" in res.get("reason", ""),
      res.get("reason", ""))

_write_ws(TB_WRONG, LOG_WRONG, "STATUS: FAIL\nFAILURE_TYPE: VIVADO_COMPILATION_ERROR\n")
res = targeted_fix.attempt_targeted_fix()
check("non-functional failure -> declined", res["applied"] is False,
      res.get("reason", ""))


# ============================================================
# 4. DETERMINISTIC COVERAGE COMPLETION
# ============================================================

print("\n[4] deterministic coverage completion")

TB_COMB = (
    "module t;\n"
    "reg a;\nreg b;\nwire y;\n"
    "initial begin\n"
    '    $monitor("At time %0t, a=%b b=%b y=%b", $time, a, b, y);\n'
    "    a = 0; b = 0;\n    #10;\n"
    "    a = 0; b = 1;\n    #10;\n"
    "    a = 1; b = 0;\n    #10;\n"
    '    $display("SIMULATION SUCCESSFUL");\n'
    "    $finish;\n"
    "end\n"
    "endmodule\n"
)
(WS / "tb.v").write_text(TB_COMB, encoding="utf-8")

COMB_LOG_PARTIAL = (
    "At time 0, a=0 b=0 y=0\n"
    "At time 10000, a=0 b=1 y=0\n"
    "At time 20000, a=1 b=0 y=0\n"
)
rc = complete_coverage._complete_combinational(TB_COMB, COMB_LOG_PARTIAL)
updated = (WS / "tb.v").read_text(encoding="utf-8")
check("missing combo added", rc == 0 and "ALL COVERAGE COMBINATIONS APPLIED" in updated,
      f"rc={rc}")


# ============================================================
# SUMMARY
# ============================================================

shutil.rmtree(WS, ignore_errors=True)

passed = sum(1 for _n, ok in RESULTS if ok)
total = len(RESULTS)

print("\n" + "=" * 60)
print(f"REGRESSION RESULT: {passed}/{total} passed")
print("=" * 60)

sys.exit(0 if passed == total else 1)
