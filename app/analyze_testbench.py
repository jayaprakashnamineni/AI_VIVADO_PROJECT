from pathlib import Path
import re
import sys
import json
from itertools import product

from coverage_engine import (
    parse_samples,
    evaluate_coverage,
    classify_from_files,
)

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent
generated_dir = project_dir / "generated"
output_dir = project_dir / "output"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    generated_dir = Path(_ws)
    output_dir = Path(_ws)

tb_file = generated_dir / "tb.v"
design_file = generated_dir / "design.v"
log_file = output_dir / "simulation.log"
report_file = output_dir / "verification_report.txt"
analysis_file = output_dir / "requirement_analysis.json"

output_dir.mkdir(exist_ok=True)


# ============================================================
# CHECK REQUIRED FILES
# ============================================================

for file in (tb_file, log_file):
    if not file.exists():
        print(f"ERROR: Required file not found: {file}")
        sys.exit(1)


# ============================================================
# READ FILES
# ============================================================

tb_text = tb_file.read_text(
    encoding="utf-8", errors="ignore"
)

log_text = log_file.read_text(
    encoding="utf-8", errors="ignore"
)

design_text = ""
if design_file.exists():
    design_text = design_file.read_text(
        encoding="utf-8", errors="ignore"
    )


# ============================================================
# START ANALYSIS
# ============================================================

print("=" * 60)
print("AUTOMATIC TESTBENCH ANALYSIS")
print("=" * 60)


# ============================================================
# ANALYZE TESTBENCH STRUCTURE
# ============================================================

module_match = re.search(
    r"module\s+(\w+)", tb_text
)

testbench_name = (
    module_match.group(1)
    if module_match
    else "Unknown"
)

initial_blocks = len(re.findall(r"\binitial\b", tb_text))
always_blocks = len(re.findall(r"\balways\b", tb_text))
if_statements = len(re.findall(r"\bif\s*\(", tb_text))
monitor_statements = len(re.findall(r"\$monitor\b", tb_text))
display_statements = len(re.findall(r"\$display\b", tb_text))
finish_statements = len(re.findall(r"\$finish\b", tb_text))


# ============================================================
# EXTRACT INPUT SIGNALS FROM MONITOR
# ============================================================

monitor_match = re.search(
    r'\$monitor\s*\(\s*"([^"]+)"',
    tb_text,
    re.IGNORECASE
)

input_signals = []

if monitor_match:

    monitor_format = monitor_match.group(1)

    detected_signals = re.findall(
        r"(\w+)\s*=\s*%b",
        monitor_format
    )

    output_names = {
        "sum", "cout", "carry", "out", "output", "result",
        "count", "cnt", "q", "dout", "y", "state", "data",
    }

    for signal in detected_signals:
        if signal.lower() not in output_names:
            input_signals.append(signal)


# ============================================================
# FALLBACK FOR INPUT DETECTION
# ============================================================

if not input_signals:

    reg_match = re.search(
        r"\breg\s+([^;]+);",
        tb_text
    )

    if reg_match:

        for signal in reg_match.group(1).split(","):
            signal = re.sub(
                r"\[[^\]]+\]", "", signal
            ).strip()
            if signal:
                input_signals.append(signal)


input_signals = list(dict.fromkeys(input_signals))


# ============================================================
# PARSE SIMULATION SAMPLES
# ============================================================

simulation_samples = parse_samples(log_text)


# ============================================================
# EXTRACT UNIQUE INPUT COMBINATIONS (BINARY CONTROL VIEW)
# ============================================================

tested_combinations = set()

for sample in simulation_samples:

    values = sample["values"]

    if all(signal in values for signal in input_signals):

        combination = tuple(
            values[signal]
            for signal in input_signals
        )

        tested_combinations.add(combination)

valid_binary_inputs = []

for combination in tested_combinations:
    if all(value in ("0", "1") for value in combination):
        valid_binary_inputs.append(combination)

tested_combinations = set(valid_binary_inputs)

total_possible_combinations = (
    (2 ** len(input_signals))
    if input_signals else 0
)

tested_count = len(tested_combinations)

all_combinations = set(
    product(["0", "1"], repeat=len(input_signals))
) if input_signals else set()

missing_combinations = sorted(
    all_combinations - tested_combinations
)

if total_possible_combinations > 0:
    combo_coverage = (
        tested_count / total_possible_combinations
    ) * 100
else:
    combo_coverage = 0.0


# ============================================================
# VERIFICATION COVERAGE (REQUIRED-CASE COVERAGE)
# ============================================================

analysis = None

if analysis_file.exists():
    try:
        analysis = json.loads(
            analysis_file.read_text(encoding="utf-8")
        )
    except Exception:
        analysis = None

category = ""

if analysis:
    category = analysis.get("category", "")

category_key = category.strip().upper()

valid_categories = {
    "COMBINATIONAL", "SEQUENTIAL", "MEMORY",
    "PROTOCOL", "COMPLEX",
}

if category_key not in valid_categories:

    request = (
        analysis.get("user_request", "")
        if analysis else None
    )

    category_guess = classify_from_files(
        request, tb_text, design_text
    )

    print()
    print(
        "COVERAGE ANALYSIS NOTE"
    )
    print("-" * 60)
    print(
        f"Category inferred from source     : "
        f"{category_guess}"
    )
    print(
        "(requirement_analysis.json missing "
        "or not applicable)"
    )

    category_key = category_guess


if coverage_block := (analysis or {}).get("coverage"):
    coverage_threshold = coverage_block.get(
        "threshold", 100
    )
    exhaustive_limit = coverage_block.get(
        "exhaustive_input_limit", 8
    )
else:
    coverage_threshold = 100
    exhaustive_limit = 8

exhaustive_ok = (
    input_signals
    and len(input_signals) <= exhaustive_limit
    and tested_count == total_possible_combinations
)

coverage = evaluate_coverage(
    category=category_key,
    samples=simulation_samples,
    log_text=log_text,
    tb_text=tb_text,
    design_text=design_text,
    input_signals=input_signals,
    exhaustive_limit=exhaustive_limit,
    exhaustive_ok=exhaustive_ok,
)

coverage_pct = coverage["coverage_pct"]
coverage_ok = coverage_pct >= coverage_threshold
coverage_status = "PASS" if coverage_ok else "FAIL"

missing_cases = coverage["missing"]


# ============================================================
# ANALYZE SIMULATION RESULT
# ============================================================

simulation_success = bool(
    re.search(
        r"SIMULATION SUCCESSFUL",
        log_text,
        re.IGNORECASE
    )
)

simulation_failed = bool(
    re.search(
        r"SIMULATION FAILED",
        log_text,
        re.IGNORECASE
    )
)

vivado_errors = len(
    re.findall(r"\bERROR:", log_text)
)


# ============================================================
# DETERMINE FINAL STATUS
# ============================================================

simulation_ok = (
    simulation_success
    and not simulation_failed
    and vivado_errors == 0
)

if simulation_ok and coverage_ok:
    final_status = "PASS"
else:
    final_status = "FAIL"


# ============================================================
# PRINT RESULTS
# ============================================================

print()
print("TESTBENCH STRUCTURE")
print("-" * 60)

print(f"Testbench Name       : {testbench_name}")
print(f"Initial Blocks       : {initial_blocks}")
print(f"Always Blocks        : {always_blocks}")
print(f"IF Checks            : {if_statements}")
print(f"Monitor Statements   : {monitor_statements}")
print(f"Display Statements   : {display_statements}")
print(f"Finish Statements    : {finish_statements}")

print()
print("INPUT COVERAGE ANALYSIS")
print("-" * 60)

print(
    "Input Signals        : "
    + (
        ", ".join(input_signals)
        if input_signals
        else "Not detected"
    )
)

print(
    f"Total Combinations   : "
    f"{total_possible_combinations}"
)

print(
    f"Tested Combinations  : "
    f"{tested_count}"
)

print(
    f"Actual Coverage      : "
    f"{combo_coverage:.2f}%"
)

print()
print("VERIFICATION COVERAGE")
print("-" * 60)
print(f"Category             : {category_key}")
print(f"Required Tests       : {coverage['applicable']}")
print(f"Executed Tests       : {coverage['verified']}")

if missing_cases:
    missing_text = ", ".join(
        f"{case['id']} ({case['name']})"
        for case in missing_cases
    )
    print(f"Missing Tests        : {missing_text}")
else:
    missing_text = "None"
    print(f"Missing Tests        : None")

print(
    f"Coverage             : {coverage_pct:.2f}%"
)
print(
    f"Required Coverage    : {coverage_threshold}%"
)
print(
    f"Coverage Status      : {coverage_status}"
)

print()
print("COVERAGE DETAIL")

for case in coverage["results"]:

    detail = case.get("detail", "")

    if detail:
        print(
            f"  {case['id']:<26} -> {case['status']} "
            f"({detail})"
        )
    else:
        print(
            f"  {case['id']:<26} -> {case['status']}"
        )

print()
print("SIMULATION ANALYSIS")
print("-" * 60)

print(
    f"Simulation Samples   : "
    f"{len(simulation_samples)}"
)

print(
    f"Vivado Errors        : "
    f"{vivado_errors}"
)

print(
    f"Simulation Success   : "
    f"{simulation_success}"
)

print(
    f"Simulation Failed    : "
    f"{simulation_failed}"
)

print()
print("=" * 60)
print(f"FINAL VERIFICATION STATUS: {final_status}")
print("=" * 60)


# ============================================================
# CREATE VERIFICATION REPORT
# ============================================================

def _combos_text(combinations, limit=20):

    if not combinations:
        return "  None\n"

    lines = []

    for combination in combinations:

        values = [
            f"{signal}={value}"
            for signal, value in zip(
                input_signals, combination
            )
        ]

        lines.append("  " + ", ".join(values))

    text = "\n".join(lines) + "\n"

    if len(combinations) > limit:
        text += (
            f"  ... and "
            f"{len(combinations) - limit} more\n"
        )

    return text


tested_text = _combos_text(sorted(tested_combinations))
missing_combo_text = _combos_text(missing_combinations)

coverage_detail_lines = []

for case in coverage["results"]:

    detail = case.get("detail", "")

    line = (
        f"{case['id']:<26} : {case['status']}"
    )

    if detail:
        line += f" ({detail})"

    coverage_detail_lines.append(line)

report = f"""
============================================================
AI VIVADO PROJECT - VERIFICATION REPORT
============================================================

TESTBENCH STRUCTURE

Testbench Name       : {testbench_name}
Initial Blocks       : {initial_blocks}
Always Blocks        : {always_blocks}
IF Checks            : {if_statements}
Monitor Statements   : {monitor_statements}
Display Statements   : {display_statements}
Finish Statements    : {finish_statements}

INPUT COVERAGE ANALYSIS

Input Signals        : {", ".join(input_signals) if input_signals else "Not detected"}
Total Combinations   : {total_possible_combinations}
Tested Combinations  : {tested_count}
Actual Coverage      : {combo_coverage:.2f}%

TESTED COMBINATIONS

{tested_text}MISSING INPUT COMBINATIONS

{missing_combo_text}VERIFICATION COVERAGE

Category             : {category_key}
Required Tests       : {coverage['applicable']}
Executed Tests       : {coverage['verified']}
Missing Tests        : {missing_text}
Coverage             : {coverage_pct:.2f}%
Required Coverage    : {coverage_threshold}%
Coverage Status      : {coverage_status}

COVERAGE DETAIL

{chr(10).join(coverage_detail_lines)}

SIMULATION ANALYSIS

Simulation Samples   : {len(simulation_samples)}
Vivado Errors        : {vivado_errors}
Simulation Success   : {simulation_success}
Simulation Failed    : {simulation_failed}

FINAL RESULT

STATUS               : {final_status}

============================================================
"""

report_file.write_text(
    report.strip() + "\n",
    encoding="utf-8"
)

print()
print("Verification report saved to:")
print(report_file)


# ============================================================
# EXIT CODE (used by main.py for the coverage gate)
# ============================================================

if final_status == "PASS":
    sys.exit(0)
else:
    sys.exit(1)