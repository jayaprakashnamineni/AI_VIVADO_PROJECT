from pathlib import Path
import subprocess
import sys
import json
import os
import time
from vivado_config import resolve_vivado_bin

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent
output_dir = project_dir / "output"

_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    output_dir = Path(_ws)

logs_dir = output_dir / "logs"
simulation_log = output_dir / "simulation.log"
waveform_file = output_dir / "waveform.wdb"
tcl_file = output_dir / "generate_waveform.tcl"
open_wave_tcl = output_dir / "open_wave.tcl"
project_tcl = output_dir / "create_project.tcl"
top_module_file = output_dir / "top_module.txt"

config_file = project_dir / "config.json"


# ============================================================
# LOAD CONFIG
# ============================================================

if config_file.exists():
    config = json.loads(
        config_file.read_text(encoding="utf-8")
    )
else:
    config = {}

vivado_bin = resolve_vivado_bin(config)

xsim = vivado_bin / "xsim.bat"
vivado = vivado_bin / "vivado.bat"


# ============================================================
# ROBUST COMMAND RUNNER (file redirection, timeout, tree-kill)
# ============================================================

def run_command(command, step_name, step_filename, timeout):
    """Run one Vivado step redirecting output to a file.

    Vivado shell scripts detach grandchildren (xsimk.exe) which hold
    pipes open and make subprocess.run(timeout=...) hang forever on
    Windows, so output goes to a file and a poll loop enforces the
    deadline with a tree-kill fallback.
    """
    step_log = logs_dir / step_filename
    step_log.parent.mkdir(parents=True, exist_ok=True)

    with open(
        step_log, "w", encoding="utf-8", errors="ignore"
    ) as out_file:

        try:
            proc = subprocess.Popen(
                command,
                cwd=str(output_dir),
                stdout=out_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(
                    subprocess, "CREATE_NO_WINDOW", 0
                ),
            )
        except Exception as exc:
            print(f"ERROR: {step_name} failed to start: {exc}")
            return 1

        deadline = time.monotonic() + timeout
        timed_out = False

        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        else:
            timed_out = True
            print(
                f"ERROR: {step_name} timed out "
                f"after {int(timeout)} seconds."
            )
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=30,
                    creationflags=getattr(
                        subprocess, "CREATE_NO_WINDOW", 0
                    ),
                )
            except Exception:
                pass

    if not timed_out and proc.poll() == 0:
        return 0

    return_code = 1 if timed_out else proc.returncode

    if step_log.exists():
        print(step_log.read_text(encoding="utf-8", errors="ignore"))

    return return_code


# ============================================================
# CHECK SIMULATION LOG EXISTS
# ============================================================

if not simulation_log.exists():
    print("ERROR: simulation.log not found.")
    sys.exit(1)


# ============================================================
# CREATE TCL COMMANDS (log all signals, run, exit)
# ============================================================

tcl_commands = """log_wave -recursive /*
run all
exit
"""

tcl_file.write_text(tcl_commands, encoding="utf-8")


# ============================================================
# REMOVE OLD WAVEFORM
# ============================================================

if waveform_file.exists():
    waveform_file.unlink()


# ============================================================
# RUN XSIM WITH FULL WAVE CAPTURE
# ============================================================

print("Generating waveform...")

command = [
    str(xsim),
    "tb_sim",
    "-wdb",
    waveform_file.as_posix(),
    "-tclbatch",
    tcl_file.as_posix(),
]

print(f"Running: {' '.join(command)}")

result = run_command(command, "Waveform Capture", "waveform_capture.log", 120)

if result != 0:
    print("ERROR: XSIM waveform generation failed.")
    sys.exit(1)

if not waveform_file.exists():
    print("ERROR: waveform.wdb was not created.")
    sys.exit(1)

print("WAVEFORM GENERATED SUCCESSFULLY")
print(f"Saved to: {waveform_file}")


# ============================================================
# WRITE ONE-COMMAND VIEWER
# ============================================================

ws_posix = str(output_dir).replace("\\", "/")
open_wave_tcl.write_text(
    f"open_wave_database {{{ws_posix}/waveform.wdb}}\n",
    encoding="utf-8",
)
print(f"Viewer written to: {open_wave_tcl}")


# ============================================================
# CREATE VIVADO PROJECT (.xpr) WITH DESIGN + TB + WAVEFORM
# ============================================================

proj_name = output_dir.name

tb_top = ""
if top_module_file.exists():
    tb_top = top_module_file.read_text(encoding="utf-8").strip()

proj_lines = []
proj_lines.append(f"create_project -force {{{proj_name}}} {{{ws_posix}}}")
proj_lines.append(
    f"add_files -norecurse {{{ws_posix}/design.v}}"
)
proj_lines.append(
    f"add_files -fileset sim_1 -norecurse {{{ws_posix}/tb.v}}"
)
if tb_top:
    proj_lines.append(
        f"set_property top {{{tb_top}}} [get_filesets sim_1]"
    )
proj_lines.append("update_compile_order -fileset sources_1")
proj_lines.append("close_project")

project_tcl.write_text("\n".join(proj_lines) + "\n", encoding="utf-8")

print("\nCreating Vivado project...")

pv_command = [
    str(vivado),
    "-mode", "batch",
    "-nolog",
    "-nojournal",
    "-source",
    str(project_tcl),
]

print(f"Running: {' '.join(pv_command)}")

pv_result = run_command(pv_command, "Vivado Project", "vivado_project.log", 300)

xpr_file = output_dir / f"{proj_name}.xpr"

if pv_result != 0 or not xpr_file.exists():
    print("ERROR: Vivado project was not created.")
    sys.exit(1)

print("VIVADO PROJECT CREATED SUCCESSFULLY")
print(f"Saved to: {xpr_file}")

print("\nArtifacts in workspace:")
print(f"  design.v       : {output_dir / 'design.v'}")
print(f"  tb.v           : {output_dir / 'tb.v'}")
print(f"  waveform.wdb   : {waveform_file}")
print(f"  waveform.png   : {output_dir / 'waveform.png'}")
print(f"  open_wave.tcl  : {open_wave_tcl}")
print(f"  project        : {xpr_file}")


# ============================================================
# CLEAN UP TRANSIENT FILES
# (keep deliverables + run record; drop snapshot/log temp data)
# ============================================================

import shutil

cleanup_dirs = [
    output_dir / "xsim.dir",
    output_dir / f"{proj_name}.cache",
    output_dir / f"{proj_name}.hw",
    output_dir / f"{proj_name}.gen",
    output_dir / f"{proj_name}.runs",
    output_dir / ".Xil",
    output_dir / "logs",
]

cleanup_files = [
    output_dir / "tb_sim.wdb",
    output_dir / "generate_waveform.tcl",
    output_dir / "create_project.tcl",
    output_dir / "xsim.jou",
    output_dir / "xsim.log",
    output_dir / "xelab.log",
    output_dir / "xelab.pb",
    output_dir / "xvlog.log",
    output_dir / "xvlog.pb",
    output_dir / "webtalk.jou",
    output_dir / "webtalk.log",
    output_dir / "vivado.jou",
    output_dir / "vivado.log",
]

for d in cleanup_dirs:
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

for f in cleanup_files:
    if f.is_file():
        f.unlink(missing_ok=True)

for pattern in ("*.backup.jou", "*.backup.log", "vivado_pid*.str"):
    for f in output_dir.glob(pattern):
        if f.is_file():
            f.unlink(missing_ok=True)

print("CLEANUP DONE: transient files removed.")
print("Kept: design.v, tb.v, waveform.wdb/png, simulation.log,")
print("      review report, open_wave.tcl, and the .xpr project.")