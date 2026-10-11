from pathlib import Path
import subprocess
import sys
import json
import shutil
import os
import re
import time
from datetime import datetime
from workspace import (
    generate_design_id, create_workspace
)

project_dir = Path(__file__).parent.parent
app_dir = project_dir / "app"
runs_dir = project_dir / "runs"

analyze_script = app_dir / "analyze_requirement.py"
generate_script = app_dir / "generate_verilog.py"
simulate_script = app_dir / "run_simulation.py"
review_script = app_dir / "review_simulation.py"
analyze_tb_script = app_dir / "analyze_testbench.py"
enhance_tb_script = app_dir / "enhance_testbench.py"
fix_script = app_dir / "fix_verilog.py"
waveform_script = app_dir / "export_waveform_png.py"
waveform_db_script = app_dir / "generate_waveform.py"


# ============================================================
# LOAD CONFIG
# ============================================================

config_file = project_dir / "config.json"

if config_file.exists():
    config = json.loads(
        config_file.read_text(encoding="utf-8")
    )
else:
    config = {}

max_attempts = config.get("max_attempts", 3)

max_pipeline_seconds = (
    int(config.get("max_pipeline_minutes", 10)) * 60
)

pipeline_start = time.monotonic()


def fail_fast_budget():
    """True when the hard wall-clock budget has been exceeded."""
    elapsed = time.monotonic() - pipeline_start
    if elapsed < max_pipeline_seconds:
        return False

    print()
    print("=" * 60)
    print("FINAL STATUS: FAIL")
    print(
        f"TIME BUDGET EXCEEDED "
        f"({max_pipeline_seconds // 60} MINUTES)"
    )
    print(
        "DETERMINISTIC PATHS EXHAUSTED "
        "WITHIN BUDGET"
    )
    print("=" * 60)

    generate_waveform_artifacts()

    save_run(
        user_request,
        "FAIL",
        globals().get("attempt", 0)
    )
    sys.exit(1)


# ============================================================
# CLEAN CURRENT-RUN ARTIFACTS
# ============================================================

def clean_output():
    """Remove only current-run artifacts from output/ and generated/."""

    output_files = [
        "simulation.log",
        "review_report.txt",
        "verification_report.txt",
        "requirement_analysis.json",
        "waveform.wdb",
        "waveform.png",
        "generate_waveform.tcl",
    ]

    out = project_dir / "output"

    for filename in output_files:
        target = out / filename
        if target.exists():
            target.unlink()

    logs = out / "logs"
    if logs.exists():
        for item in logs.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

    gen = project_dir / "generated"

    gen_files = [
        "design.v",
        "tb.v",
        "top_module.txt",
    ]

    for filename in gen_files:
        target = gen / filename
        if target.exists():
            target.unlink()


# ============================================================
# HELPER: RUN SCRIPT
# ============================================================

def run_script(script_path, args=None):
    print("\n" + "=" * 60)
    print(f"RUNNING: {script_path.name}")
    print("=" * 60)

    command = [sys.executable, str(script_path)]
    if args:
        command.extend(args)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    result = subprocess.run(
        command,
        cwd=str(project_dir),
        env=env
    )

    return result.returncode


# ============================================================
# HELPER: GENERATE WAVEFORM ARTIFACTS + VIVADO PROJECT
# ============================================================

def generate_waveform_artifacts():
    return run_script(waveform_db_script)


# ============================================================
# HELPER: SAVE RUN
# ============================================================

def save_run(user_request, status, attempts):
    runs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime(
        "%Y-%m-%d_%H-%M-%S_%f"
    )
    run_dir = runs_dir / timestamp
    suffix = 0
    while run_dir.exists():
        suffix += 1
        run_dir = runs_dir / f"{timestamp}_{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)

    ws = Path(os.environ.get(
        "AI_WORKSPACE",
        str(project_dir / "output")
    ))

    if ws.exists():
        dest = run_dir / "workspace"
        shutil.copytree(
            ws, dest, dirs_exist_ok=True
        )

    summary = {
        "request": user_request,
        "design_id": design_id,
        "status": status,
        "attempts": attempts,
        "timestamp": timestamp,
        "workspace": str(ws)
    }

    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=4),
        encoding="utf-8"
    )

    print(f"\nRun saved to: {run_dir}")


# ============================================================
# PARSE CLI ARGUMENTS
# ============================================================

args = sys.argv[1:]

skip_generate = "--skip-generate" in args

if skip_generate:
    args.remove("--skip-generate")

workspace_arg = None

if "--workspace" in args:
    ws_index = args.index("--workspace")
    if ws_index + 1 < len(args):
        workspace_arg = args[ws_index + 1]
        del args[ws_index:ws_index + 2]
    else:
        print("ERROR: --workspace requires a path value.")
        sys.exit(1)

if args:
    user_request = " ".join(args).strip()
else:
    user_request = input(
        "Enter your VLSI design request: "
    ).strip()

if not user_request:
    print("ERROR: No design request entered.")
    sys.exit(1)


# ============================================================
# CREATE DESIGN WORKSPACE
# ============================================================

if workspace_arg:

    workspace = Path(workspace_arg)
    workspace.mkdir(parents=True, exist_ok=True)
    design_id = workspace.name

    print(f"Design workspace: {workspace}")

else:

    design_id = generate_design_id(user_request)
    workspace = create_workspace(project_dir, design_id)

os.environ["AI_WORKSPACE"] = str(workspace)


# ============================================================
# SKIP-GENERATE MODE (developer/testing)
# ============================================================

if skip_generate:

    if not workspace_arg:

        print(
            "\nPIPELINE STOPPED: "
            "--skip-generate requires an explicit "
            "--workspace <dir>."
        )
        print(
            "The global generated/ fallback is disabled "
            "to preserve workspace isolation."
        )
        sys.exit(1)

    print(
        "\nSKIP-GENERATE MODE: "
        "Using existing generated files"
    )

    gen = workspace

    required_gen_files = [
        "design.v",
        "tb.v",
        "top_module.txt",
    ]

    missing = [
        f for f in required_gen_files
        if not (gen / f).exists()
    ]

    if missing:

        print(
            "\nPIPELINE STOPPED: "
            "skip-generate requires all generated "
            f"files in workspace: {gen}"
        )
        for f in missing:
            print(f"  MISSING: {gen / f}")
        sys.exit(1)

    print(f"  design.v      : OK")
    print(f"  tb.v          : OK")
    print(f"  top_module.txt: OK")


# ============================================================
# NORMAL MODE: CLEAN + ANALYZE + GENERATE
# ============================================================

else:

    clean_output()

    print("\nSTEP 0: REQUIREMENT ANALYSIS")

    result = run_script(analyze_script, [user_request])

    if result != 0:
        print(
            "\nPIPELINE STOPPED: "
            "Requirement analysis failed."
        )
        sys.exit(1)

    print("\nSTEP 1: AI VERILOG GENERATION")

    result = run_script(generate_script, [user_request])

    if result != 0:

        generation_retries = int(
            config.get("generation_retries", 1)
        )

        retried = 0

        while result != 0 and retried < generation_retries:

            if fail_fast_budget():
                print(
                    "\nPIPELINE STOPPED: "
                    "Generation budget exhausted."
                )
                sys.exit(1)

            print(
                f"\nGeneration attempt failed. "
                f"Retrying ({retried + 1}/{generation_retries})..."
            )

            result = run_script(
                generate_script, [user_request]
            )

            retried += 1

        if result != 0:

            print(
                "\nPIPELINE STOPPED: "
                "Verilog generation failed."
            )
            sys.exit(1)


# ============================================================
# STEP 2-4: SIMULATION, REVIEW, COVERAGE, FIX LOOP
# ============================================================

def read_coverage_status(workspace_dir):
    """Read 'Coverage Status : PASS/FAIL' from verification_report.txt."""
    verif_file = workspace_dir / "verification_report.txt"

    if not verif_file.exists():
        return False

    text = verif_file.read_text(
        encoding="utf-8",
        errors="ignore"
    )

    match = re.search(
        r"Coverage Status\s*:\s*(\w+)",
        text
    )

    if match:
        return match.group(1).upper() == "PASS"

    return False


# ============================================================
# PIPELINE TIME BUDGET
# ============================================================

for attempt in range(1, max_attempts + 1):

    print("\n" + "#" * 60)
    print(
        f"SIMULATION ATTEMPT {attempt}/{max_attempts}"
    )
    print("#" * 60)

    # Run Vivado simulation
    simulation_result = run_script(simulate_script)

    # Run reviewer (simulation-level verdict)
    review_result = run_script(review_script)

    # Run testbench analyzer + coverage gate
    analyze_result = run_script(analyze_tb_script)

    # Read review result
    review_file = (
        workspace / "review_report.txt"
    )

    if not review_file.exists():
        print("FINAL STATUS: FAIL")
        print("review_report.txt not found.")
        generate_waveform_artifacts()
        save_run(user_request, "FAIL", attempt)
        sys.exit(1)

    review_text = review_file.read_text(
        encoding="utf-8",
        errors="ignore"
    )

    # Check for Vivado compilation errors
    log_file = (
        workspace / "simulation.log"
    )
    vivado_errors = False

    if log_file.exists():
        log_text = log_file.read_text(
            encoding="utf-8",
            errors="ignore"
        )
        log_upper = log_text.upper()
        vivado_errors = any(
            word in log_upper
            for word in [
                "ERROR:",
                "FATAL",
                "SYNTAX ERROR",
                "UNRESOLVED REFERENCE"
            ]
        )

    review_pass = (
        "STATUS: PASS" in review_text
        and not vivado_errors
    )

    coverage_pass = read_coverage_status(workspace)

    # ----------------------------------------------------------
    # FULL PASS: simulation AND coverage both satisfied
    # ----------------------------------------------------------
    if review_pass and coverage_pass:

        print("\n" + "=" * 60)
        print("FINAL STATUS: PASS")
        print("DESIGN VERIFIED SUCCESSFULLY")
        print("SIMULATION     : PASS")
        print("COVERAGE       : PASS")
        print("=" * 60)

        # Generate waveform artifacts (wdb + project + png)
        generate_waveform_artifacts()
        run_script(waveform_script)

        save_run(user_request, "PASS", attempt)
        sys.exit(0)

    # ----------------------------------------------------------
    # COVERAGE INSUFFICIENT (simulation is fine)
    # ----------------------------------------------------------
    if review_pass and not coverage_pass:

        print("\n" + "=" * 60)
        print("SIMULATION        : PASS")
        print("COVERAGE          : FAIL")
        print("=" * 60)

        print(
            "\nCoverage insufficient. "
            "Improving testbench to add missing scenarios..."
        )

        if attempt >= max_attempts:

            print()
            print("=" * 60)
            print("FINAL STATUS: FAIL")
            print(
                "TESTBENCH COVERAGE NOT SATISFIED "
                "WITHIN MAXIMUM ATTEMPTS"
            )
            print("=" * 60)

            generate_waveform_artifacts()

            save_run(user_request, "FAIL", attempt)
            sys.exit(1)

        if fail_fast_budget():
            sys.exit(1)

        coverage_completion_script = (
            app_dir / "complete_coverage.py"
        )

        completion_result = run_script(
            coverage_completion_script
        )

        if completion_result == 0:

            print(
                "\nMissing combinations added "
                "deterministically. Re-running simulation..."
            )
            continue

        print(
            "\nDeterministic completion not applicable. "
            "Using AI improve path..."
        )

        enhance_result = run_script(enhance_tb_script)

        if enhance_result != 0:

            print()
            print("=" * 60)
            print("FINAL STATUS: FAIL")
            print(
                "TESTBENCH IMPROVEMENT FAILED"
            )
            print("=" * 60)

            generate_waveform_artifacts()

            save_run(user_request, "FAIL", attempt)
            sys.exit(1)

        print(
            "\nTestbench improved. Re-running simulation..."
        )
        continue

    # ----------------------------------------------------------
    # SIMULATION-LEVEL FAILURE
    # ----------------------------------------------------------
    print(f"\nATTEMPT {attempt}: FAIL")

    review_first_line = (
        review_text.splitlines()[0]
        if review_text
        else "No review"
    )
    print(f"Review: {review_first_line}")

    # Stop after maximum attempts
    if attempt >= max_attempts:

        print("\n" + "=" * 60)
        print("FINAL STATUS: FAIL")
        print(
            "MAXIMUM AI CORRECTION ATTEMPTS REACHED"
        )
        print("=" * 60)

        generate_waveform_artifacts()

        save_run(user_request, "FAIL", attempt)
        sys.exit(1)

    # DETERMINISTIC TARGETED FIX
    sys.path.insert(0, str(app_dir))
    from targeted_fix import attempt_targeted_fix
    fix_info = attempt_targeted_fix()

    if fix_info["applied"]:
        print(
            "\nTargeted fix applied. "
            "Re-running simulation..."
        )
        continue

    # AI correction
    print("\nSending error to AI Fix Agent...")

    if fail_fast_budget():
        sys.exit(1)

    fix_result = run_script(fix_script)

    if fix_result != 0:
        print(
            "\nPIPELINE STOPPED: "
            "AI correction failed."
        )
        generate_waveform_artifacts()

        save_run(user_request, "FAIL", attempt)
        sys.exit(1)


print("\nPIPELINE FINISHED.")
