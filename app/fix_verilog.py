from pathlib import Path
from llm_client import chat
import difflib
import re
import sys
import json

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent

generated_dir = project_dir / "generated"
output_dir = project_dir / "output"
prompts_path = project_dir / "prompts"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    generated_dir = Path(_ws)
    output_dir = Path(_ws)

design_file = generated_dir / "design.v"
tb_file = generated_dir / "tb.v"
top_file = generated_dir / "top_module.txt"

log_file = output_dir / "simulation.log"
review_file = output_dir / "review_report.txt"

config_file = project_dir / "config.json"
prompt_file = prompts_path / "fix.txt"


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
strict_rtl_pct = config.get("fix_change_strict_rtl_pct", 20)
strict_tb_pct = config.get("fix_change_strict_tb_pct", 30)
relaxed_rtl_pct = config.get("fix_change_relaxed_rtl_pct", 50)
relaxed_tb_pct = config.get("fix_change_relaxed_tb_pct", 60)


# ============================================================
# PARSE FAILURE TYPE
# ============================================================

def parse_failure_type(review_text):
    """Extract FAILURE_TYPE from review report."""
    for line in review_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILURE_TYPE:"):
            return stripped.split(":", 1)[1].strip()
    return "UNKNOWN"


# ============================================================
# VALIDATE FIX (CHANGE-SIZE CHECK)
# ============================================================

def validate_fix(original, corrected, label, failure_type,
                 strict_pct, relaxed_pct):
    """Compare original vs corrected code.

    Returns (accepted, report_string).
    """

    orig_lines = original.splitlines()
    corr_lines = corrected.splitlines()

    diff = list(difflib.unified_diff(
        orig_lines, corr_lines,
        lineterm="",
        n=0
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
    corr_count = len(corr_lines)
    max_count = max(orig_count, corr_count, 1)

    change_pct = (changed / max_count) * 100

    orig_mod = re.search(r"\bmodule\s+(\w+)", original)
    corr_mod = re.search(r"\bmodule\s+(\w+)", corrected)
    module_changed = (
        orig_mod is not None
        and corr_mod is not None
        and orig_mod.group(1) != corr_mod.group(1)
    )

    is_functional = "FUNCTIONAL" in failure_type.upper()
    threshold_pct = strict_pct if is_functional else relaxed_pct

    absolute_limit = 8 if is_functional else 15
    small_file = orig_count <= 20 and corr_count <= 20

    report_lines = [
        f"  {label} change percentage: {change_pct:.1f}%",
        f"  {label} original lines: {orig_count}",
        f"  {label} corrected lines: {corr_count}",
        f"  {label} lines added: {added}",
        f"  {label} lines removed: {removed}",
        (
            f"  Module name changed: "
            f"{'YES' if module_changed else 'NO'}"
        ),
    ]

    reasons = []

    if module_changed:
        reasons.append("module renamed")

    if small_file:
        if changed > absolute_limit:
            reasons.append(
                f"small file: {changed} lines changed "
                f"(limit {absolute_limit})"
            )
    else:
        if change_pct > threshold_pct:
            reasons.append(
                f"change {change_pct:.1f}% exceeds "
                f"threshold {threshold_pct}%"
            )

    accepted = len(reasons) == 0

    if accepted:
        report_lines.append("  Validation result: ACCEPTED")
    else:
        report_lines.append("  Validation result: REJECTED")
        report_lines.append(
            "  Reason: " + "; ".join(reasons)
        )

    return accepted, "\n".join(report_lines)


# ============================================================
# CHECK REQUIRED FILES
# ============================================================

required_files = [
    design_file,
    tb_file,
    log_file
]

for file in required_files:

    if not file.exists():

        print(f"ERROR: File not found: {file}")

        sys.exit(1)


# ============================================================
# READ CURRENT DESIGN
# ============================================================

design_code = design_file.read_text(
    encoding="utf-8",
    errors="ignore"
)


# ============================================================
# READ CURRENT TESTBENCH
# ============================================================

tb_code = tb_file.read_text(
    encoding="utf-8",
    errors="ignore"
)


# ============================================================
# READ VIVADO SIMULATION LOG
# ============================================================

vivado_log = log_file.read_text(
    encoding="utf-8",
    errors="ignore"
)


# ============================================================
# READ DETAILED REVIEWER REPORT
# ============================================================

review_report = ""

if review_file.exists():

    review_report = review_file.read_text(
        encoding="utf-8",
        errors="ignore"
    )

else:

    review_report = (
        "No detailed reviewer report available. "
        "Analyze the Vivado simulation log directly."
    )


# ============================================================
# LOAD PROMPT TEMPLATE
# ============================================================

if not prompt_file.exists():
    print(f"ERROR: Prompt file not found: {prompt_file}")
    sys.exit(1)

prompt_template = prompt_file.read_text(
    encoding="utf-8"
)


# ============================================================
# CREATE AI DEBUGGING PROMPT
# ============================================================

failure_type = parse_failure_type(review_report)

print()
print("=" * 60)
print("AI FIX AGENT STARTED")
print("=" * 60)

print(f"Failure type: {failure_type}")
print(f"Using model: {model_name}")
print(f"Max fix retries: {max_fix_retries}")
print()

rejection_notice = ""


# ============================================================
# FIX LOOP WITH VALIDATION
# ============================================================

for fix_attempt in range(1, max_fix_retries + 2):

    print("-" * 60)
    print(
        f"FIX ATTEMPT "
        f"{fix_attempt}/{max_fix_retries + 1}"
    )
    print("-" * 60)

    # Build prompt
    prompt = prompt_template.replace(
        "{design_code}", design_code
    )
    prompt = prompt.replace("{tb_code}", tb_code)
    prompt = prompt.replace(
        "{review_report}", review_report
    )
    prompt = prompt.replace("{vivado_log}", vivado_log)

    if rejection_notice:
        prompt = rejection_notice + "\n\n" + prompt

    # Call AI
    try:

        response = chat(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            options={
                "num_predict": 2048,
                "temperature": 0.2
            }
        )

    except Exception as error:

        print("ERROR: AI model call failed.")
        print(f"Reason: {error}")

        if fix_attempt >= max_fix_retries + 1:
            sys.exit(1)

        print("Retrying...")
        continue

    # Get AI output
    ai_output = response["message"]["content"]

    # Extract code blocks
    code_blocks = re.findall(
        r"```(?:verilog|text)?\s*\n?(.*?)```",
        ai_output,
        re.DOTALL | re.IGNORECASE
    )

    # Extract top module
    top_match = re.search(
        r"TOP_MODULE\s*=\s*"
        r"([a-zA-Z_][a-zA-Z0-9_$]*)",
        ai_output
    )

    # Validate AI output structure
    if len(code_blocks) < 2:

        print(
            "ERROR: AI did not return both design "
            "and testbench code."
        )

        if fix_attempt >= max_fix_retries + 1:
            print()
            print("AI OUTPUT:")
            print("=" * 60)
            print(ai_output)
            sys.exit(1)

        print("Retrying...")
        continue

    if not top_match:

        print("ERROR: AI did not return TOP_MODULE.")

        if fix_attempt >= max_fix_retries + 1:
            print()
            print("AI OUTPUT:")
            print("=" * 60)
            print(ai_output)
            sys.exit(1)

        print("Retrying...")
        continue

    # Extract corrected content
    corrected_design = code_blocks[0].strip()
    corrected_tb = code_blocks[1].strip()
    top_module = top_match.group(1)

    # Basic Verilog validation
    if not re.search(
        r"\bmodule\b",
        corrected_design,
        re.IGNORECASE
    ):

        print(
            "ERROR: Corrected design does not "
            "contain a Verilog module."
        )

        if fix_attempt >= max_fix_retries + 1:
            sys.exit(1)

        print("Retrying...")
        continue

    if not re.search(
        r"\bmodule\b",
        corrected_tb,
        re.IGNORECASE
    ):

        print(
            "ERROR: Corrected testbench does not "
            "contain a Verilog module."
        )

        if fix_attempt >= max_fix_retries + 1:
            sys.exit(1)

        print("Retrying...")
        continue

    # ========================================================
    # CHANGE-SIZE VALIDATION
    # ========================================================

    rtl_ok, rtl_report = validate_fix(
        design_code, corrected_design,
        "RTL", failure_type,
        strict_rtl_pct, relaxed_rtl_pct
    )

    tb_ok, tb_report = validate_fix(
        tb_code, corrected_tb,
        "Testbench", failure_type,
        strict_tb_pct, relaxed_tb_pct
    )

    print()
    print("FIX VALIDATION REPORT")
    print("-" * 60)
    print(rtl_report)
    print(tb_report)

    # ========================================================
    # NO-OP CHECK: a "fix" that changes nothing is a no-op.
    # It must NOT be accepted, and it does not count as a valid
    # attempt unless it is the final attempt (then it fails).
    # ========================================================

    rtl_unchanged = corrected_design == design_code
    tb_unchanged = corrected_tb == tb_code

    if rtl_unchanged and tb_unchanged:

        print()
        print(
            "Validation result: REJECTED (NO-OP FIX)"
        )
        print(
            "Corrected files are identical to the "
            "originals (0% change)."
        )

        if fix_attempt >= max_fix_retries + 1:
            print()
            print(
                "ERROR: Max fix retries exhausted."
            )
            print(
                "AI returned a no-op fix (no changes)."
            )
            sys.exit(1)

        rejection_notice = (
            "PREVIOUS FIX WAS REJECTED: NO-OP.\n"
            "Your corrected files were byte-for-byte "
            "identical to the originals.\n\n"
            "RULES FOR THIS RETRY:\n"
            "- You MUST actually change the code to "
            "fix the reported failure.\n"
            "- Analyze the simulation log and alter "
            "the specific lines that are wrong.\n"
            "- Preserve all working code; change only "
            "what is broken.\n"
            "- Return the complete files with at "
            "least one real fix applied.\n"
        )

        print("Retrying with stricter instructions...")
        continue

    if rtl_ok and tb_ok:
        print()
        print("Validation result: ACCEPTED")
        break

    # Rejected
    print()
    print("Validation result: REJECTED")

    if fix_attempt >= max_fix_retries + 1:

        print()
        print(
            "ERROR: Max fix retries exhausted."
        )
        print(
            "AI cannot produce a correction with"
        )
        print("acceptable change size.")
        sys.exit(1)

    # Prepare rejection notice for retry
    rejection_notice = (
        "PREVIOUS FIX WAS REJECTED BY CHANGE-SIZE"
        " VALIDATOR.\n"
        "Your correction changed too much code.\n\n"
        f"{rtl_report}\n{tb_report}\n\n"
        "RULES FOR THIS RETRY:\n"
        "- If the RTL is correct, preserve it "
        "byte-for-byte.\n"
        "- In the testbench, change ONLY the "
        "specific lines\n"
        "  that fix the reported failure.\n"
        "- Do not rename modules.\n"
        "- Do not restructure working code.\n"
        "- Do not change clock generation or "
        "reset sequencing.\n"
        "- Return the complete files, but they "
        "must differ\n"
        "  from the originals by only a few "
        "lines.\n"
    )

    print("Retrying with stricter instructions...")


# ============================================================
# SAVE CORRECTED FILES
# ============================================================

design_file.write_text(
    corrected_design,
    encoding="utf-8"
)

tb_file.write_text(
    corrected_tb,
    encoding="utf-8"
)

top_file.write_text(
    top_module,
    encoding="utf-8"
)


# ============================================================
# PRINT SUCCESS
# ============================================================

print()
print("=" * 60)
print("AI CORRECTION COMPLETED")
print("=" * 60)

print(f"Corrected design saved   : {design_file}")
print(f"Corrected testbench saved: {tb_file}")
print(f"Corrected top module     : {top_module}")

print()
print("Fix Agent used:")
print("- Current RTL")
print("- Current testbench")
print("- Detailed reviewer report")
print("- Vivado simulation log")
print("- Root-cause debugging rules")
print("- Assertion-aware verification rules")
print("- Race-condition prevention rules")
print("- Change-size validation")
