"""Portable resolution of the Vivado bin directory.

The commit-time config.json deliberately carries an EMPTY ``vivado_path``
so the repository does not depend on a machine-specific install location.
The bin directory is resolved at runtime in this priority order:

  1. ``VIVADO_BIN`` environment variable (recommended portable override).
  2. ``vivado_path`` from ``config.json`` (local / CI override).
  3. ``xvlog.bat``/``xelab.bat`` already on ``PATH``.
  4. Known default install roots (last resort, e.g. ``C:\\Xilinx\\...``).

The directory must exist and contain the Vivado batch tools for the
pipeline to run.
"""

import os
import shutil
from pathlib import Path

KNOWN_INSTALL_ROOTS = [
    r"C:\Xilinx\Vivado\2018.2\bin",
    r"C:\Xilinx\Vivado\2019.2\bin",
    r"C:\Xilinx\Vivado\2020.2\bin",
    r"C:\Xilinx\Vivado\2021.2\bin",
    r"C:\Xilinx\Vivado\2022.2\bin",
    r"C:\Xilinx\Vivado\2023.2\bin",
]


def _is_bin_directory(candidate):
    """True when the directory looks like a Vivado bin folder."""
    if not candidate:
        return None
    path = Path(candidate)
    if not path.is_dir():
        return None
    has_xvlog = (path / "xvlog.bat").exists()
    has_xelab = (path / "xelab.bat").exists()
    has_xsim = (path / "xsim.bat").exists()
    if has_xvlog and has_xelab and has_xsim:
        return path
    return None


def resolve_vivado_bin(config):
    """Return a Path to a usable Vivado bin directory (or a last-resort
    default). Tries environment, config, PATH, then known install roots."""
    candidates = [
        _is_bin_directory(os.environ.get("VIVADO_BIN")),
        _is_bin_directory(config.get("vivado_path", "")),
    ]
    if isinstance(candidates[0], Path):
        return candidates[0]
    if isinstance(candidates[1], Path):
        return candidates[1]

    on_path = shutil.which("xvlog.bat") or shutil.which("xelab.bat")
    if on_path:
        parent = Path(on_path).resolve().parent
        if _is_bin_directory(parent):
            return parent

    for root in KNOWN_INSTALL_ROOTS:
        found = _is_bin_directory(root)
        if found:
            return found

    return Path(os.environ.get("VIVADO_BIN")
                or config.get("vivado_path")
                or KNOWN_INSTALL_ROOTS[0])