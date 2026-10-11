from pathlib import Path
import subprocess
import sys
import json
import time
from vivado_config import resolve_vivado_bin

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent
generated_dir = project_dir / "generated"
output_dir = project_dir / "output"
logs_dir = output_dir / "logs"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    generated_dir = Path(_ws)
    output_dir = Path(_ws)
    logs_dir = output_dir / "logs"

design_file = generated_dir / "design.v"
tb_file = generated_dir / "tb.v"
top_file = generated_dir / "top_module.txt"

log_file = output_dir / "simulation.log"


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

vivado_bin = resolve_vivado_bin(config)

xvlog = vivado_bin / "xvlog.bat"
xelab = vivado_bin / "xelab.bat"
xsim = vivado_bin / "xsim.bat"


# ============================================================
# CREATE OUTPUT DIRECTORY
# ============================================================

output_dir.mkdir(exist_ok=True)
logs_dir.mkdir(exist_ok=True)


# ============================================================
# CHECK REQUIRED FILES
# ============================================================

required_files = [
    design_file,
    tb_file,
    top_file
]

for file in required_files:
    if not file.exists():
        print(f"ERROR: Required file not found: {file}")
        sys.exit(1)


# ============================================================
# READ TESTBENCH TOP MODULE
# ============================================================

top_module = top_file.read_text(
    encoding="utf-8"
).strip()

print(f"Testbench top module: {top_module}")


# ============================================================
# VIVADO COMMANDS
# ============================================================

commands = [

    (
        "Compile Design",
        [
            str(xvlog),
            str(design_file)
        ]
    ),

    (
        "Compile Testbench",
        [
            str(xvlog),
            str(tb_file)
        ]
    ),

    (
        "Elaborate Simulation",
        [
            str(xelab),
            top_module,
            "-debug",
            "all",
            "-s",
            "tb_sim"
        ]
    ),

    (
        "Run Simulation",
        [
            str(xsim),
            "tb_sim",
            "-runall"
        ]
    )

]


# ============================================================
# RUN VIVADO COMMANDS
# ============================================================

full_log = ""
simulation_failed = False
COMMAND_TIMEOUT = 60


def run_command(command, step_name, step_filename):
    """Run one Vivado step, redirecting output to a file.

    Redirects stdout/stderr to a file instead of pipes: the Vivado
    shell scripts detach grandchildren (xsimk.exe) which otherwise
    hold the pipe open and make subprocess.run(timeout=...) hang
    forever on Windows.  Output is read back after the process ends.
    """
    step_log = logs_dir / step_filename
    step_log.parent.mkdir(parents=True, exist_ok=True)

    with open(
        step_log, "w", encoding="utf-8", errors="ignore"
    ) as out_file:

        proc = subprocess.Popen(
            command,
            cwd=str(output_dir),
            stdout=out_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ),
        )

        deadline = time.monotonic() + COMMAND_TIMEOUT
        timed_out = False

        while time.monotonic() < deadline:

            if proc.poll() is not None:
                break

            time.sleep(0.5)

        else:

            timed_out = True

            print(
                f"ERROR: {step_name} timed out "
                f"after {COMMAND_TIMEOUT} seconds."
            )

            try:

                subprocess.run(
                    [
                        "taskkill", "/F", "/T", "/PID",
                        str(proc.pid)
                    ],
                    capture_output=True,
                    timeout=30
                )

            except Exception:

                pass

        return_code = proc.returncode
        if timed_out:
            with open(
                step_log, "a",
                encoding="utf-8", errors="ignore"
            ) as out_file:
                out_file.write(
                    "\nERROR: COMMAND TIMED OUT "
                    f"AFTER {COMMAND_TIMEOUT} SECONDS\n"
                )
            return_code = 1

    step_content = step_log.read_text(
        encoding="utf-8", errors="ignore"
    )

    return return_code, step_content, timed_out


for step_name, command in commands:

    print("\n" + "=" * 60)
    print(f"STEP: {step_name}")
    print(f"Running: {' '.join(command)}")
    print("=" * 60)

    step_filename = (
        step_name.lower().replace(" ", "_") + ".log"
    )

    return_code, step_content, timed_out = run_command(
        command, step_name, step_filename
    )

    # ========================================================
    # DISPLAY VIVADO OUTPUT
    # ========================================================

    if step_content:
        print(step_content)

    # ========================================================
    # SAVE OUTPUT TO COMBINED LOG
    # ========================================================

    full_log += "\n" + "=" * 60 + "\n"
    full_log += f"STEP: {step_name}\n"
    full_log += f"COMMAND: {' '.join(command)}\n"
    full_log += "=" * 60 + "\n"

    if step_content:
        full_log += step_content

    # ========================================================
    # FLUSH COMBINED LOG AFTER EACH STEP
    # ========================================================

    log_file.write_text(
        full_log,
        encoding="utf-8",
        errors="ignore"
    )

    # ========================================================
    # CHECK COMMAND RESULT
    # ========================================================

    if timed_out or return_code != 0:

        print(
            f"ERROR: {step_name} "
            f"{'timed out' if timed_out else 'failed'}."
        )

        simulation_failed = True
        break


# ============================================================
# SAVE SIMULATION LOG
# ============================================================

log_file.write_text(
    full_log,
    encoding="utf-8",
    errors="ignore"
)


# ============================================================
# FINAL STATUS
# ============================================================

print("\n" + "=" * 60)
print("SIMULATION PROCESS FINISHED")
print("=" * 60)

print(f"Log saved to: {log_file}")
print(f"Step logs saved to: {logs_dir}")


if simulation_failed:

    print("Simulation result: FAIL")
    sys.exit(1)


print("Simulation result: SUCCESS")
