import os
import re
import shutil
from datetime import datetime
from pathlib import Path


# ============================================================
# DESIGN ID GENERATION
# ============================================================

_PREFIXES = [
    "design a ", "design an ", "design the ",
    "design ", "implement a ", "implement an ",
    "implement the ", "implement ", "create a ",
    "create an ", "create the ", "create ",
    "build a ", "build an ", "build ",
    "make a ", "make an ", "make ",
]


def generate_design_id(request):
    """Generate a safe, deterministic design ID from a user request.

    Examples:
        "Design a half adder"              -> "half_adder"
        "Design an 8-bit FIFO"             -> "8_bit_fifo"
        "Design a 4-bit counter with enable" -> "4_bit_counter"
    """
    text = request.strip().lower()

    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("_")

    if len(text) > 40:
        text = text[:40].rstrip("_")

    if not text:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        text = f"design_{ts}"

    return text


# ============================================================
# WORKSPACE CREATION
# ============================================================

def create_workspace(project_dir, design_id):
    """Create and return the workspace directory.

    If the workspace already exists, appends a timestamp suffix.
    """
    output_root = project_dir / "output"
    workspace = output_root / design_id

    if workspace.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = output_root / f"{design_id}_{ts}"

    workspace.mkdir(parents=True, exist_ok=True)

    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"Design workspace: {workspace}")

    return workspace


# ============================================================
# WORKSPACE PATH ACCESSORS
# ============================================================

def get_workspace(project_dir):
    """Get the active workspace for output files.

    Reads AI_WORKSPACE env var. Falls back to project output/.
    """
    ws = os.environ.get("AI_WORKSPACE")
    if ws:
        return Path(ws)
    return project_dir / "output"


def get_generated_dir(project_dir):
    """Get the active directory for generated Verilog files.

    Reads AI_WORKSPACE env var. Falls back to project generated/.
    """
    ws = os.environ.get("AI_WORKSPACE")
    if ws:
        return Path(ws)
    return project_dir / "generated"
