from llm_client import chat
from pathlib import Path
import re
import json
import sys
import shutil
import subprocess

# ============================================================
# PROJECT PATHS
# ============================================================

project_path = Path(__file__).parent.parent

generated_path = project_path / "generated"
output_path = project_path / "output"
prompts_path = project_path / "prompts"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    generated_path = Path(_ws)
    output_path = Path(_ws)

analysis_file = output_path / "requirement_analysis.json"
config_file = project_path / "config.json"
prompt_file = prompts_path / "generate.txt"

generated_path.mkdir(exist_ok=True)
output_path.mkdir(exist_ok=True)


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
    config.get("ollama_generate_model")
    or config.get("ollama_model", "qwen2.5-coder:7b")
)


# ============================================================
# GET USER REQUEST
# ============================================================

if len(sys.argv) > 1:
    user_request = " ".join(sys.argv[1:]).strip()
else:
    user_request = input(
        "Enter your VLSI design request: "
    ).strip()

if not user_request:
    print("ERROR: No design request entered.")
    sys.exit(1)


# ============================================================
# LOAD REQUIREMENT ANALYSIS
# ============================================================

requirement_analysis = None

if analysis_file.exists():

    try:

        requirement_analysis = json.loads(
            analysis_file.read_text(
                encoding="utf-8"
            )
        )

        print()
        print("Requirement analysis found.")

    except Exception as error:

        print()
        print(
            "WARNING: Could not read "
            "requirement_analysis.json"
        )

        print(f"Reason: {error}")

else:

    print()
    print(
        "Requirement analysis not found."
    )

    print(
        "Using default AI verification strategy."
    )


# ============================================================
# VERIFY ANALYSIS MATCHES CURRENT REQUEST
# ============================================================

analysis_text = ""

if requirement_analysis:

    analyzed_request = requirement_analysis.get(
        "user_request",
        ""
    ).strip()

    if analyzed_request.lower() == user_request.lower():

        design_type = requirement_analysis.get(
            "design_type",
            "CUSTOM / COMPLEX RTL"
        )

        category = requirement_analysis.get(
            "category",
            "Complex"
        )

        verification_strategy = requirement_analysis.get(
            "verification_strategy",
            {}
        )

        strategy_name = verification_strategy.get(
            "strategy",
            "FEATURE-BASED TESTING"
        )

        required_tests = verification_strategy.get(
            "tests",
            []
        )

        coverage_method = verification_strategy.get(
            "coverage_method",
            "Requirement coverage"
        )

        coverage_cases = (
            requirement_analysis
            .get("coverage", {})
            .get("cases", [])
        )

        coverage_cases_text = "\n".join(
            f"- {case.get('id', '')}: "
            f"{case.get('name', '')}"
            for case in coverage_cases
        ) or "- No explicit coverage cases"

        tests_text = "\n".join(
            f"- {test}"
            for test in required_tests
        )

        analysis_text = f"""
REQUIREMENT ANALYSIS:

Design Type:
{design_type}

Design Category:
{category}

Recommended Verification Strategy:
{strategy_name}

Required Verification Tests:
{tests_text}

Required Coverage Cases (tag each tested scenario with a
// COVERAGE_CASE: <ID> comment and print a COVERAGE_HIT: <ID>
line when it runs):
{coverage_cases_text}

Recommended Coverage Method:
{coverage_method}
"""

        print(
            "Requirement analysis matches "
            "the current request."
        )

    else:

        print()
        print(
            "WARNING: Existing requirement analysis "
            "belongs to a different request."
        )

        print(
            "Ignoring old analysis and using "
            "default verification strategy."
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
# CREATE AI PROMPT
# ============================================================

prompt = prompt_template.replace(
    "{user_request}", user_request
)
prompt = prompt.replace(
    "{analysis_text}", analysis_text
)

prompt = (
    prompt
    + "\n\nBegin with the first code block now. "
    + "Output ONLY these three items in order, separated "
    + "by exactly one blank line, with no prose anywhere: "
    + "1) design code block, 2) testbench code block, "
    + "3) the single line TOP_MODULE=<testbench module name>. "
    + "Stop output immediately after the TOP_MODULE line."
)


# ============================================================
# CALL AI MODEL
# ============================================================

print()
print("Generating RTL and verification-aware testbench...")
print(f"Using model: {model_name}")

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
            "num_predict": 2600,
            "temperature": 0.2
        }
    )
except Exception as error:
    print()
    print("ERROR: LLM generation call failed or timed out.")
    print(f"Reason: {error}")
    sys.exit(1)


# ============================================================
# GET AI OUTPUT
# ============================================================

ai_output = response["message"]["content"]


# ============================================================
# EXTRACT CODE BLOCKS
# ============================================================

code_blocks = re.findall(
    r"```{2,3}(?:[a-z]*)?\s*(.*?)```{2,3}",
    ai_output,
    re.DOTALL | re.IGNORECASE
)

module_blocks = [
    block.strip()
    for block in code_blocks
    if re.search(r"\bmodule\b", block)
]

if len(module_blocks) < 2:

    print(
        "WARNING: Code fences not matched; "
        "falling back to module-boundary extraction."
    )

    module_headers = list(
        re.finditer(
            r"\bmodule\s+[a-zA-Z_][a-zA-Z0-9_$]*",
            ai_output
        )
    )

    module_blocks = []

    for i, header in enumerate(module_headers):

        start = header.start()

        if i + 1 < len(module_headers):
            end = module_headers[i + 1].start()
        else:
            end = len(ai_output)

        chunk = ai_output[start:end].strip()

        em_match = re.search(r"\bendmodule\b", chunk)

        if em_match:

            chunk = chunk[: em_match.end()]

            clean_lines = [
                line for line in chunk.splitlines()
                if not re.match(r"^\s*`{2,3}.*$", line)
            ]

            module_blocks.append(
                "\n".join(clean_lines).strip()
            )

if len(module_blocks) < 2:

    print(
        "ERROR: AI did not generate both "
        "design and testbench."
    )

    print()
    print(ai_output)

    sys.exit(1)


trimmed_blocks = []

for block in module_blocks:

    em_match = re.search(r"\bendmodule\b", block)

    if em_match:
        block = block[: em_match.end()]

    trimmed_blocks.append(block.strip())

module_blocks = trimmed_blocks


design_code = module_blocks[0]
tb_code = module_blocks[1]


# ============================================================
# REQUEST-RELEVANCE CHECK
# ============================================================

request_words = set(
    re.findall(r"[a-z0-9]{2,}", user_request.lower())
)

design_words = set(
    re.findall(r"[a-z0-9]{2,}", design_code.lower())
)

non_keywords = {
    "design", "the", "and", "for", "with",
    "using", "verilog", "rtl", "module",
    "input", "output", "wire", "reg", "1-bit", "a", "an"
}

design_name_match = re.search(
    r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_$]*)",
    design_code
)

if design_name_match:

    design_words |= set(
        re.findall(
            r"[a-z0-9]{2,}",
            design_name_match.group(1).lower()
        )
    )

signal_words = {
    "clk", "rst", "en", "reset", "clock", "count",
    "sum", "cout", "cin", "sel", "dout", "din",
    "wr", "rd", "full", "empty"
}

relevant = (
    request_words - non_keywords - signal_words
) & design_words

if not relevant:

    print(
        "ERROR: Generated design does not match the "
        "request; this looks like a copied or wrong "
        "example."
    )
    print(
        f"REQUEST KEYWORDS: "
        f"{sorted(request_words - non_keywords - signal_words)}"
    )
    print(f"DESIGN KEYWORDS  : {sorted(design_words)}")

    sys.exit(1)

print(
    f"Request relevance check: PASS "
    f"(matched: {sorted(relevant)})"
)


# ============================================================
# WIDTH-FIDELITY CHECK (DETERMINISTIC, NO LLM)
#
# When the request states an explicit bit width (e.g. "2 bit",
# "2-bit", "4 bit"), verify the generated design's counter/output
# width matches.  A long-standing failure mode is the model copying
# a 4-bit counter example for a "2 bit" request.  Reject and
# regenerate when widths are incompatible.
# ============================================================

def _requested_width(user_request):
    """Return int width if the request declares one, else None."""
    m = re.search(
        r"\b(\d{1,3})\s*(?:[- ])?bit\b",
        user_request.lower()
    )
    if m:
        return int(m.group(1))
    return None


def _detect_counter_width(design_code):
    """Return the width (bits) of the design's counter-like output.

    Scans reg/output vector declarations for a counter-style name
    (count/cnt/q/out/value).  Returns the declared bit width, or
    None if no vector counter output is found or no width is stated.
    """
    # Prefer a counter-style signal that is declared as an output reg
    output_regs = []

    for m in re.finditer(
        r"(?:output\s+)?reg\s*"
        r"(?:\[([0-9]+):(?:[0-9]+)\])?\s*"
        r"([a-zA-Z_][a-zA-Z0-9_$]*)",
        design_code
    ):
        width = m.group(1)
        name = m.group(2).lower()
        if width is not None:
            bits = int(width) + 1
            output_regs.append((name, bits))

    counter_names = {
        "count", "cnt", "counter", "q", "cout",
        "value", "out", "data_out", "dout",
    }

    counter_vectors = [
        (name, bits) for (name, bits) in output_regs
        if any(cn in name for cn in counter_names)
    ]

    if not counter_vectors:
        return None

    # Return the widest counter-style output as the primary width
    return max(bits for (_n, bits) in counter_vectors)


requested_width = _requested_width(user_request)
design_width = _detect_counter_width(design_code)

# Only enforce when the request explicitly names a width AND the
# design has a detectable counter-style vector output.
if requested_width is not None and design_width is not None:

    if requested_width != design_width:

        print()
        print(
            "ERROR: Requested bit width does not match the "
            "generated design."
        )
        print(
            f"  Requested width : {requested_width} bit"
        )
        print(
            f"  Design width    : {design_width} bit "
            f"(counter-style output)"
        )
        print(
            "The model likely copied a counter example of the "
            "wrong width. Regenerating is required."
        )
        sys.exit(1)

    print(
        f"Width fidelity check: PASS "
        f"(requested {requested_width} bit = design "
        f"{design_width} bit)"
    )


# ============================================================
# TESTBENCH SELF-CONSISTENCY GATE (DETERMINISTIC, NO LLM)
#
# Catches the recurring sequential-TB bug where an assertion's
# comparison literal contradicts the value stated in that same
# block's CHECK/EXPECTED message (e.g. literal 2'b01 = 1 next to
# "EXPECTED: 2").  Such a block can never pass and would otherwise
# waste a full sim + fix cycle.  When found, regenerate instead.
# ============================================================

def _verilog_int(text):
    m = re.fullmatch(
        r"(\d+)'([bdh])([0-9a-fA-F_]+)", text
    )
    if m:
        base = {"b": 2, "d": 10, "h": 16}[m.group(2).lower()]
        digits = m.group(3).replace("_", "")
        return int(digits, base)
    if re.fullmatch(r"[01]+", text):
        return int(text, 2)
    if re.fullmatch(r"\d+", text):
        return int(text, 10)
    return None


def tb_self_consistency_check(tb_text):
    """Return list of human-readable self-contradiction reports."""
    reports = []
    lines = tb_text.splitlines()

    for idx, line in enumerate(lines):
        stripped = line.strip()
        m = re.search(r"if\s*\((.+?)\)", stripped)
        if not m:
            continue
        cond_text = m.group(1)

        if "ASSERTION FAILED" not in "\n".join(
            lines[idx + 1: idx + 6]
        ):
            continue

        # Only flag unambiguous single-comparison checks.  Compound
        # (|| / &&) conditions check multiple outputs at once, whose
        # literal/EXPECTED pairing we cannot reliably reason about.
        if "||" in cond_text or "&&" in cond_text:
            continue

        expected_val = None
        for j in range(idx + 1, min(idx + 8, len(lines))):
            em = re.search(
                r"EXPECTED\s*:\s*([^\"\n]*)",
                lines[j]
            )
            if em:
                expected_val = em.group(1).strip()
                break

        cm = re.search(
            r"(!==?|===?)\s*"
            r"([0-9]+'[bdh][0-9a-fA-F_]+|[0-9]+)",
            cond_text
        )
        if not cm:
            continue
        literal = cm.group(2)
        literal_int = _verilog_int(literal)

        if expected_val is None or literal_int is None:
            continue

        exp_m = re.search(r"=\s*([0-9]+)", expected_val)
        exp_num = int(exp_m.group(1)) if exp_m else None
        if exp_num is None:
            bare = re.search(r"(?:^|\s)([0-9]+)(?:\s|$)", expected_val)
            exp_num = int(bare.group(1)) if bare else None
        if exp_num is None:
            continue

        if literal_int != exp_num:
            reports.append(
                f"check '{cond_text.strip()}': literal "
                f"{literal} (= {literal_int}) but message says "
                f"EXPECTED {expected_val}\n"
            )

    return reports


consistency_reports = tb_self_consistency_check(tb_code)

if consistency_reports:

    print()
    print(
        "ERROR: Generated testbench has self-contradictory "
        "checks (comparison literal != its own EXPECTED "
        "message). Regenerating is required."
    )
    for report in consistency_reports:
        print("  - " + report.strip())

    sys.exit(1)

print(
    "Testbench self-consistency check: PASS "
    f"({len(consistency_reports)} issues)"
)


# ============================================================
# EXTRACT TOP MODULE (TESTBENCH MODULE NAME)
# ============================================================

top_match = re.search(
    r"TOP_MODULE\s*=\s*"
    r"([a-zA-Z_][a-zA-Z0-9_$]*)",
    ai_output
)

if top_match:

    top_module = top_match.group(1)

else:

    tb_module_match = re.search(
        r"\bmodule\s+([a-zA-Z_][a-zA-Z0-9_$]*)",
        tb_code
    )

    if tb_module_match:

        top_module = tb_module_match.group(1)

        print()
        print(
            "WARNING: TOP_MODULE not provided; "
            f"using testbench module "
            f"'{top_module}'."
        )

    else:

        print(
            "ERROR: Could not determine "
            "testbench module name."
        )

        print()
        print(ai_output)

        sys.exit(1)


# ============================================================
# VALIDATE BASIC OUTPUT
# ============================================================

if not re.search(
    r"\bmodule\b",
    design_code
):

    print(
        "ERROR: Generated design does not "
        "contain a Verilog module."
    )

    sys.exit(1)


if not re.search(
    r"\bmodule\b",
    tb_code
):

    print(
        "ERROR: Generated testbench does not "
        "contain a Verilog module."
    )

    sys.exit(1)


# ============================================================
# SAVE GENERATED FILES
# ============================================================

design_file = generated_path / "design.v"
tb_file = generated_path / "tb.v"
top_file = generated_path / "top_module.txt"

design_file.write_text(
    design_code,
    encoding="utf-8"
)

tb_file.write_text(
    tb_code,
    encoding="utf-8"
)

top_file.write_text(
    top_module,
    encoding="utf-8"
)


# ============================================================
# COMPILATION GATE (DETERMINISTIC, NO LLM)
# ============================================================

def compile_gate():
    """Quick xvlog check + deterministic undeclared-signal auto-patch.

    Runs in an isolated scratch dir so the workspace library stays
    clean for the real simulation step.
    """
    print()
    print("=" * 60)
    print("COMPILATION GATE")
    print("=" * 60)

    from verilog_autofix import (
        patch_undeclared_signals,
        strip_coverage_displays
    )

    from vivado_config import resolve_vivado_bin

    vivado_bin = resolve_vivado_bin(config)

    xvlog = vivado_bin / "xvlog.bat"
    scratch = generated_path / "_compile_gate"

    def run_xvlog(verilog_file):
        scratch.mkdir(exist_ok=True)
        try:
            result = subprocess.run(
                [
                    str(xvlog),
                    str(verilog_file)
                ],
                cwd=str(scratch),
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.stdout + result.stderr
        except Exception as error:
            return f"ERROR: xvlog execution failed: {error}"

    try:

        design_output = run_xvlog(design_file)

        if "error" in design_output.lower():

            print(
                "  Design compile failed - "
                "left for the targeted fix agent."
            )

            return

        tb_output = run_xvlog(tb_file)

        original_tb = tb_file.read_text(encoding="utf-8")

        cleaned_tb, removed = strip_coverage_displays(
            original_tb
        )

        if removed:
            tb_file.write_text(cleaned_tb, encoding="utf-8")
            print(
                f"  Removed {removed} $coverage display line(s)."
            )
            original_tb = cleaned_tb

        if "is not declared" not in tb_output.lower():

            if "error" in tb_output.lower():

                print(
                    "  Testbench compile failed - "
                    "residual errors left for fix agent."
                )

            else:

                print("  Testbench compiles clean.")

            return

        patched_tb, signals, changed = (
            patch_undeclared_signals(
                original_tb, tb_output
            )
        )

        if not signals:

            print(
                "  Testbench compile failed - "
                "left for the targeted fix agent."
            )

            return

        tb_file.write_text(patched_tb, encoding="utf-8")

        print(
            "  Declared missing signals : "
            f"{', '.join(signals)}"
        )

        print(f"  Lines changed            : {changed}")

        final_output = run_xvlog(tb_file)

        if "error" in final_output.lower():

            print(
                "  Residual compile errors - "
                "left for the targeted fix agent."
            )

        else:

            print(
                "  Testbench compiles clean "
                "after auto-fix."
            )

    finally:

        shutil.rmtree(scratch, ignore_errors=True)


compile_gate()


# ============================================================
# PRINT SUCCESS
# ============================================================

print()
print("=" * 60)
print("AI GENERATION COMPLETED")
print("=" * 60)

print(f"Top module saved : {top_module}")
print(f"Design file saved: {design_file}")
print(f"Testbench saved  : {tb_file}")

if analysis_text:

    print()
    print("Generation mode  : Requirement-aware")

else:

    print()
    print("Generation mode  : Default")
