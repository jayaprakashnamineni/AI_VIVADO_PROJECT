from pathlib import Path
import re

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent

output_dir = project_dir / "output"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    output_dir = Path(_ws)

log_file = output_dir / "simulation.log"
review_file = output_dir / "review_report.txt"

output_dir.mkdir(exist_ok=True)


# ============================================================
# STORE REPORT LINES
# ============================================================

report_lines = []


def add_line(text=""):
    """Add a line to the report."""
    report_lines.append(str(text))


def finish_report(exit_code):
    """Print and save the complete report."""

    report = "\n".join(report_lines)

    print(report)

    review_file.write_text(
        report + "\n",
        encoding="utf-8"
    )

    print()
    print(f"Review report saved to: {review_file}")

    raise SystemExit(exit_code)


# ============================================================
# CHECK SIMULATION LOG
# ============================================================

if not log_file.exists():

    add_line("STATUS: FAIL")
    add_line("FAILURE_TYPE: LOG_FILE_MISSING")
    add_line("ERROR_DETAILS: simulation.log not found")
    add_line(
        "FEEDBACK_TO_DESIGNER: "
        "Run the Vivado simulation first."
    )

    finish_report(1)


# ============================================================
# READ SIMULATION LOG
# ============================================================

log = log_file.read_text(
    encoding="utf-8",
    errors="ignore"
)

log_lines = log.splitlines()
log_upper = log.upper()


# ============================================================
# EXTRACT VIVADO ERRORS
# ============================================================

error_lines = []

error_patterns = [
    r"\bERROR:",
    r"\bFATAL:",
    r"\bSYNTAX ERROR\b",
    r"\bUNRESOLVED REFERENCE\b"
]

for line in log_lines:

    line_upper = line.upper().strip()

    if not line_upper:
        continue

    for pattern in error_patterns:

        if re.search(pattern, line_upper):

            cleaned_line = line.strip()

            if cleaned_line not in error_lines:
                error_lines.append(cleaned_line)

            break


# ============================================================
# CHECK TESTBENCH RESULT
# ============================================================

simulation_success = bool(
    re.search(
        r"\bSIMULATION\s+SUCCESSFUL\b",
        log,
        re.IGNORECASE
    )
)

simulation_failed = bool(
    re.search(
        r"\bSIMULATION\s+FAILED\b",
        log,
        re.IGNORECASE
    )
)

assertion_failed = bool(
    re.search(
        r"\bASSERTION\s+FAILED\b",
        log,
        re.IGNORECASE
    )
)


# ============================================================
# PASS CONDITION
# ============================================================

if (
    simulation_success
    and not simulation_failed
    and not assertion_failed
    and not error_lines
):

    add_line("STATUS: PASS")
    add_line("FAILURE_TYPE: NONE")
    add_line("ERROR_DETAILS: None")
    add_line(
        "FEEDBACK_TO_DESIGNER: "
        "Design verified successfully."
    )

    finish_report(0)


# ============================================================
# FAILURE REPORT
# ============================================================

add_line("STATUS: FAIL")


# ============================================================
# CASE 1: VIVADO COMPILATION ERROR
# ============================================================

if error_lines:

    add_line("FAILURE_TYPE: VIVADO_COMPILATION_ERROR")
    add_line("ERROR_DETAILS:")

    for index, error in enumerate(error_lines[:20], start=1):
        add_line(f"{index}. {error}")

    add_line()

    add_line(
        "FEEDBACK_TO_DESIGNER: "
        "Fix the exact Vivado errors listed above. "
        "Check Verilog declarations, procedural assignments, "
        "continuous assignments, module ports, and syntax. "
        "Preserve the intended functionality."
    )

    finish_report(1)


# ============================================================
# CASE 2: FUNCTIONAL SIMULATION FAILURE
# ============================================================

elif (
    assertion_failed
    or simulation_failed
    or not simulation_success
):

    add_line("FAILURE_TYPE: FUNCTIONAL_SIMULATION_ERROR")

    if assertion_failed:
        add_line(
            "ERROR_DETAILS: "
            "Testbench reported ASSERTION FAILED"
        )
    elif simulation_failed:
        add_line(
            "ERROR_DETAILS: "
            "Testbench reported SIMULATION FAILED"
        )
    else:
        add_line(
            "ERROR_DETAILS: "
            "SIMULATION SUCCESSFUL message not found"
        )

    add_line()
    add_line("LAST_SIMULATION_ACTIVITY:")

    failed_index = -1

    for index, line in enumerate(log_lines):

        line_upper = line.upper().strip()

        if (
            "SIMULATION FAILED" in line_upper
            or "ASSERTION FAILED" in line_upper
        ):
            failed_index = index
            break

    if failed_index != -1:

        start_index = max(0, failed_index - 12)

        for line in log_lines[start_index:failed_index]:

            cleaned = line.strip()

            if cleaned:
                add_line(cleaned)

    add_line()

    add_line(
        "FEEDBACK_TO_DESIGNER: "
        "The RTL compiled successfully, but functional "
        "verification failed. Analyze the last simulation "
        "values shown above. Compare the current RTL behavior "
        "against the expected testbench behavior. Fix the root "
        "cause, not just the testbench message."
    )

    finish_report(1)


# ============================================================
# CASE 3: UNKNOWN FAILURE
# ============================================================

else:

    add_line("FAILURE_TYPE: UNKNOWN_SIMULATION_FAILURE")

    add_line(
        "ERROR_DETAILS: "
        "No recognized simulation outcome detected"
    )

    add_line(
        "FEEDBACK_TO_DESIGNER: "
        "The simulation did not complete with a recognized "
        "result. Check the testbench, $finish behavior, "
        "simulation timing, and RTL logic."
    )

    finish_report(1)