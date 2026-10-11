from pathlib import Path
from llm_client import chat
import difflib
import re
import sys
import json
import shutil
import os
import subprocess

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent

generated_dir = project_dir / "generated"
output_dir = project_dir / "output"
prompts_path = project_dir / "prompts"

_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    generated_dir = Path(_ws)
    output_dir = Path(_ws)

design_file = generated_dir / "design.v"
tb_file = generated_dir / "tb.v"
top_file = generated_dir / "top_module.txt"

verification_file = output_dir / "verification_report.txt"
analysis_file = output_dir / "requirement_analysis.json"

config_file = project_dir / "config.json"
prompt_file = prompts_path / "enhance_tb.txt"


# ============================================================
# LOAD CONFIG
# ============================================================

if config_file.exists():
    config = json.loads(
        config_file.read_text(encoding="utf-8")
    )
else:
    config = {}

model_name = (
    config.get("ollama_fix_model")
    or config.get("ollama_generate_model")
    or config.get("ollama_model", "qwen2.5-coder:7b")
)
max_fix_retries = config.get("max_fix_retries", 2)

coverage_config = config.get("coverage", {})

coverage_improve_attempts = coverage_config.get(
    "max_coverage_improve_attempts", 2
)

relaxed_tb_pct = config.get(
    "fix_change_relaxed_tb_pct", 60
)


# ============================================================
# PARSE MISSING COVERAGE CASES FROM REPORT
# ============================================================

def parse_missing_cases(report_text):
    """Return a list of missing case ids from COVERAGE DETAIL."""
    cases = []

    lines = report_text.splitlines()

    in_detail = False

    for line in lines:
        stripped = line.strip()

        if stripped == "COVERAGE DETAIL":
            in_detail = True
            continue

        if in_detail and not stripped:
            continue

        if in_detail:

            match = re.match(
                r"^([A-Z][A-Z0-9_]+)\s*:\s*(.+)$",
                stripped,
            )

            if match and "NOT_VERIFIED" in match.group(2):
                cases.append(match.group(1))

            elif match and match.group(2) not in (
                "VERIFIED", "NOT_APPLICABLE"
            ):
                continue

            elif not match:
                break

    if cases:
        return cases

    # Fallback: parse the "Missing Tests" line
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("Missing Tests"):

            value = stripped.split(":", 1)[1]

            found = re.findall(
                r"\b[D][A-Z][A-Z0-9_]+|[A-Z][A-Z0-9_]{3,}\b",
                value,
            )

            for case_id in found:
                if case_id not in cases:
                    cases.append(case_id)

            return cases

    return cases


# ============================================================
# CHANGE-SIZE VALIDATION (testbench only; RTL must be identical)
# ============================================================

def validate_tb_change(original, corrected, label, threshold_pct):
    """Return (accepted, report_lines)."""
    orig_lines = original.splitlines()
    corr_lines = corrected.splitlines()

    diff = list(difflib.unified_diff(
        orig_lines, corr_lines, lineterm="", n=0
    ))

    added = sum(
        1 for l in diff
        if l.startswith("+") and not l.startswith("+++")
    )
    removed = sum(
        1 for l in diff
        if l.startswith("-") and not l.startswith("---")
    )
    changed = added + removed

    orig_count = len(orig_lines)
    max_count = max(orig_count, len(corr_lines), 1)

    change_pct = (changed / max_count) * 100

    orig_mod = re.search(r"\bmodule\s+(\w+)", original)
    corr_mod = re.search(r"\bmodule\s+(\w+)", corrected)

    module_changed = (
        orig_mod is not None
        and corr_mod is not None
        and orig_mod.group(1) != corr_mod.group(1)
    )

    report = []

    if module_changed:
        report.append("Validation result: REJECTED")
        report.append("Reason: testbench module renamed")
        return False, "\n".join(report)

    if changed == 0:
        report.append(
            "Validation result: REJECTED"
        )
        report.append(
            "Reason: testbench unchanged - "
            "no missing scenarios added"
        )
        return False, "\n".join(report)

    if change_pct > threshold_pct:
        report.append("Validation result: REJECTED")
        report.append(
            f"Reason: {label} changed {change_pct:.1f}% "
            f"(threshold {threshold_pct}%)"
        )
        return False, "\n".join(report)

    report.append(
        f"  {label} change percentage: {change_pct:.1f}%"
    )
    report.append(
        f"  {label} lines added: {added}"
    )
    report.append(
        f"  {label} lines removed: {removed}"
    )
    report.append("Validation result: ACCEPTED")

    return True, "\n".join(report)


# ============================================================
# VALIDATE TESTBENCH BY ACTUAL SIMULATION
# ============================================================

def validate_by_simulation():
    """Run the improved testbench end-to-end.

    Returns (ok, reason). Simulation must PASS with the
    COVERAGE_HIT evidence and no errors.
    """
    sim_script = project_dir / "app" / "run_simulation.py"
    review_script = project_dir / "app" / "review_simulation.py"

    env = os.environ.copy()
    env["AI_WORKSPACE"] = str(generated_dir)
    env["PYTHONUNBUFFERED"] = "1"

    for script in (sim_script, review_script):
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(project_dir),
                env=env,
            )
            if result.returncode != 0:
                return False, f"{script.name} exited {result.returncode}"
        except Exception as error:
            return False, f"{script.name} failed: {error}"

    log_path = generated_dir / "simulation.log"
    review_path = generated_dir / "review_report.txt"

    log_text = ""
    if log_path.exists():
        log_text = log_path.read_text(
            encoding="utf-8", errors="ignore"
        )

    review_text = ""
    if review_path.exists():
        review_text = review_path.read_text(
            encoding="utf-8", errors="ignore"
        )

    if "SIMULATION FAILED" in log_text:
        detail = (
            "SIMULATION FAILED detected in log: "
            + _extract_failure_context(log_text)
        )
        return False, detail

    if "SIMULATION SUCCESSFUL" not in log_text:
        return False, "SIMULATION SUCCESSFUL not found in log"

    if "STATUS: PASS" not in review_text:
        return False, _review_failure_reason(review_text)

    echo_ok = True

    for case_id in missing_cases:
        if f"COVERAGE_HIT: {case_id}" not in log_text:
            echo_ok = False

    if not echo_ok:
        return False, (
            "COVERAGE_HIT lines never executed for all missing "
            "cases"
        )

    return True, "Simulation PASS with multiple clock cycles"


# ============================================================
# FAILURE-CONTEXT HELPERS
# ============================================================

def _extract_failure_context(log_text):
    """Return the assertion failure details from the log."""
    for marker in (
        "ASSERTION FAILED",
        "EXPECTED:",
        "ACTUAL:",
        "CHECK:",
    ):
        pass

    tail = log_text.strip().splitlines()

    keep = []
    capture = False

    for line in tail:
        if "ASSERTION FAILED" in line:
            capture = True

        if capture:
            keep.append(line.strip())

        if "$finish" in line:
            break

    context = " | ".join(keep)

    if not context:
        context = tail[-1].strip() if tail else "unknown"

    return context[:1200]


def _review_failure_reason(review_text):
    """Build a concise reason from the review report."""
    details = [
        line.strip()
        for line in review_text.splitlines()
        if "ERROR_DETAILS" in line
        or "FAILURE_TYPE" in line
        or line.startswith("At time ")
    ]

    reason_lines = []
    added = 0

    for line in details:
        if line.startswith("At time "):
            if added < 12:
                reason_lines.append(line)
                added += 1
        else:
            reason_lines.append(line)

    return " | ".join(reason_lines)[:1500]


# ============================================================
# CHECK REQUIRED FILES
# ============================================================

for file in (design_file, tb_file, top_file):
    if not file.exists():
        print(f"ERROR: Required file not found: {file}")
        sys.exit(1)

if not verification_file.exists():
    print("ERROR: verification_report.txt not found.")
    print("Run coverage analysis before testbench improvement.")
    sys.exit(1)

if not prompt_file.exists():
    print(f"ERROR: Prompt file not found: {prompt_file}")
    sys.exit(1)


# ============================================================
# READ FILES
# ============================================================

design_code = design_file.read_text(
    encoding="utf-8", errors="ignore"
)

tb_code = tb_file.read_text(
    encoding="utf-8", errors="ignore"
)

top_module = top_file.read_text(
    encoding="utf-8", errors="ignore"
).strip()

verification_text = verification_file.read_text(
    encoding="utf-8", errors="ignore"
)

missing_cases = parse_missing_cases(verification_text)


# ============================================================
# NOTHING TO IMPROVE
# ============================================================

if not missing_cases:

    print()
    print("=" * 60)
    print("TESTBENCH IMPROVEMENT")
    print("=" * 60)
    print("No missing coverage cases to add.")
    print("Testbench coverage is sufficient.")
    sys.exit(0)


print()
print("=" * 60)
print("TESTBENCH COVERAGE IMPROVEMENT AGENT")
print("=" * 60)

print(f"Missing cases ({len(missing_cases)}):")
for case_id in missing_cases:
    print(f"  - {case_id}")

print(f"Using model: {model_name}")
print(f"Max attempts: {coverage_improve_attempts}")
print()


# ============================================================
# BUILD CASE DESCRIPTIONS
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

case_lines = []

if analysis:

    category = analysis.get("category", "")

    cases = (
        analysis.get("coverage", {})
        .get("cases", [])
    )

    for case in cases:
        if case.get("id", "").upper() in missing_cases:
            case_lines.append(
                f"- {case['id']}: {case['name']} - "
                f"{case.get('description', '')}"
            )

for case_id in missing_cases:

    if not any(
        line.startswith(f"- {case_id}:")
        for line in case_lines
    ):
        case_lines.append(
            f"- {case_id}: Add a scenario that exercises "
            f"this required condition"
        )

missing_text = "\n".join(
    f"{line}" for line in case_lines
)


# ============================================================
# PROMT TEMPLATE
# ============================================================

prompt_template = prompt_file.read_text(
    encoding="utf-8"
)

rejection_notice = ""

for fix_attempt in range(1, coverage_improve_attempts + 1):

    print("-" * 60)
    print(
        f"IMPROVEMENT ATTEMPT "
        f"{fix_attempt}/{coverage_improve_attempts}"
    )
    print("-" * 60)

    prompt = prompt_template.replace(
        "{design_code}", design_code
    )
    prompt = prompt.replace("{tb_code}", tb_code)
    prompt = prompt.replace(
        "{missing_cases}", missing_text
    )
    prompt = prompt.replace("{category}", category or "Custom")
    prompt = prompt.replace("{top_module}", top_module)

    if rejection_notice:
        prompt = rejection_notice + "\n\n" + prompt

    # --------------------------------------------------------
    # CALL AI
    # --------------------------------------------------------

    try:

        response = chat(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            options={
                "num_predict": 1024,
                "temperature": 0.2
            },
        )

    except Exception as error:

        print("ERROR: AI model call failed.")
        print(f"Reason: {error}")

        if fix_attempt >= coverage_improve_attempts:
            sys.exit(1)

        print("Retrying...")
        continue

    ai_output = response["message"]["content"]

    # --------------------------------------------------------
    # EXTRACT TESTBENCH CODE BLOCK (testbench-only contract)
    # --------------------------------------------------------

    code_blocks = re.findall(
        r"```(?:verilog|text)?\s*\n?(.*?)```",
        ai_output,
        re.DOTALL | re.IGNORECASE,
    )

    corrected_tb = None

    for block in code_blocks:
        stripped = block.strip()

        if re.search(r"\bmodule\s+\w+", stripped):
            corrected_tb = stripped
            break

    if corrected_tb is None:

        print(
            "ERROR: AI did not return the corrected testbench."
        )

        if fix_attempt >= coverage_improve_attempts:
            sys.exit(1)

        print("Retrying...")
        continue

    # --------------------------------------------------------
    # VALIDATE: TESTBENCH MODULE NAME UNCHANGED
    # --------------------------------------------------------

    tb_module_match = re.search(
        r"\bmodule\s+(\w+)", corrected_tb
    )

    tb_module = (
        tb_module_match.group(1)
        if tb_module_match
        else ""
    )

    if tb_module != top_module:

        print()
        print("TESTBENCH VALIDATION")
        print("-" * 60)
        print("Testbench module name was renamed.")
        print("Validation result: REJECTED")

        if fix_attempt >= coverage_improve_attempts:
            print("ERROR: Module name changed.")
            sys.exit(1)

        rejection_notice = (
            "PREVIOUS FIX WAS REJECTED.\n"
            f"The testbench module must stay exactly {top_module}.\n"
        )

        print("Retrying with stricter instructions...")
        continue

    # --------------------------------------------------------
    # VALIDATE: TESTBENCH CHANGE SIZE
    # --------------------------------------------------------

    tb_ok, tb_report = validate_tb_change(
        tb_code, corrected_tb, "Testbench", relaxed_tb_pct
    )

    print()
    print("TESTBENCH VALIDATION REPORT")
    print("-" * 60)
    print(tb_report)

    if not tb_ok:

        if fix_attempt >= coverage_improve_attempts:

            print()
            print("ERROR: Max improvement attempts reached.")
            sys.exit(1)

        rejection_notice = (
            "PREVIOUS FIX WAS REJECTED BY CHANGE-SIZE"
            " VALIDATOR.\n"
            f"{tb_report}\n\n"
            "Make only surgical additions to the testbench: "
            "add the missing scenario blocks and keep every "
            "existing line unchanged.\n"
        )

        print("Retrying with stricter instructions...")
        continue

    # --------------------------------------------------------
    # VALIDATE: MARKERS PRESENT FOR MISSING CASES
    # --------------------------------------------------------

    new_markers = set(
        m.group(1).upper()
        for m in re.finditer(
            r"COVERAGE_CASE\s*[:=]\s*([A-Z0-9_]+)",
            corrected_tb,
            re.IGNORECASE,
        )
    )

    addable_missing = [
        case_id for case_id in missing_cases
        if case_id in new_markers
    ]

    missing_markers = [
        case_id for case_id in missing_cases
        if case_id not in new_markers
    ]

    print()
    print("MARKER VALIDATION")
    print("-" * 60)

    if addable_missing:
        print(
            "Coverage markers added: "
            + ", ".join(addable_missing)
        )

    if missing_markers:
        print(
            "Markers still missing: "
            + ", ".join(missing_markers)
        )

    if missing_markers:

        if fix_attempt >= coverage_improve_attempts:

            # Accept even if some markers are missing is NOT
            # allowed - coverage would still fail.  Fail out.
            print()
            print(
                "ERROR: AI did not add scenarios for "
                "all missing cases."
            )
            sys.exit(1)

        rejection_notice = (
            "PREVIOUS FIX WAS REJECTED.\n"
            "You did not add scenarios for these required "
            f"coverage cases: {', '.join(missing_markers)}\n"
            "Add a // COVERAGE_CASE: <ID> scenario for each "
            "missing case id listed above.\n"
        )

        print("Retrying to complete missing cases...")
        continue

    # --------------------------------------------------------
    # APPLY THE IMPROVED TESTBENCH, THEN VALIDATE BY SIMULATION
    # --------------------------------------------------------

    backup_file = tb_file.with_suffix(".v.bak")

    shutil.copy2(str(tb_file), str(backup_file))

    tb_file.write_text(
        corrected_tb + "\n",
        encoding="utf-8",
    )

    sim_ok, sim_reason = validate_by_simulation()

    if not sim_ok:

        tb_file.write_text(
            tb_code + "\n",
            encoding="utf-8",
        )

        if backup_file.exists():
            backup_file.unlink()

        print()
        print("SIMULATION VALIDATION")
        print("-" * 60)
        print("Improved testbench FAILED simulation.")
        print(f"Reason: {sim_reason}")
        print("Original testbench restored.")

        if fix_attempt >= coverage_improve_attempts:

            print()
            print("=" * 60)
            print("TESTBENCH IMPROVEMENT FAILED")
            print(
                "AI testbench failed simulation "
                "after maximum attempts"
            )
            print("=" * 60)
            sys.exit(1)

        rejection_notice = (
            "PREVIOUS FIX WAS REJECTED BY SIMULATION.\n"
            "YOUR ADDED SCENARIO CAUSED SIMULATION FAILURE. "
            f"REASON: {sim_reason}\n"
            "The design is CORRECT - it passed before your "
            "changes. Compute the exact expected value at the "
            "check time from the clock period and positive-edge "
            "behavior, or check an invariant you are certain "
            "holds (e.g. while reset is STILL asserted the "
            "output equals the reset value). Do not predict the "
            "counter value AFTER releasing reset with enable "
            "still high - it will have incremented. Test your "
            "scenario logic before printing COVERAGE_HIT.\n"
        )

        print("Retrying with stricter instructions...")
        continue

    print()
    print("SIMULATION VALIDATION")
    print("-" * 60)
    print("Improved testbench PASSED simulation.")
    print(f"Reason: {sim_reason}")

    print()
    print("=" * 60)
    print("TESTBENCH IMPROVED")
    print("=" * 60)
    print(f"Scenarios added      : {missing_cases}")
    print(f"RTL Modified         : NO")
    print(f"Backup saved to      : {backup_file}")

    sys.exit(0)


print("\nPIPELINE FINISHED (improvement agent).")
sys.exit(0)