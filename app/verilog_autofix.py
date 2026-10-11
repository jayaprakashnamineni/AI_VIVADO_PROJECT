"""Deterministic Verilog auto-fix helpers (no LLM)."""

import re


# ============================================================
# FIND UNDECLARED SIGNALS FROM VIVADO ERROR TEXT
# ============================================================

def find_undeclared_signals(error_text):
    """Return unique signal names from '<name> is not declared' errors."""
    signals = []
    seen = set()

    for match in re.finditer(
        r"([A-Za-z_][A-Za-z0-9_$]*)\s+is not declared",
        error_text
    ):
        signal = match.group(1)
        if signal not in seen:
            seen.add(signal)
            signals.append(signal)

    return signals


# ============================================================
# FIND INSERTION INDEX (BEFORE FIRST PROCEDURAL BLOCK)
# ============================================================

def _insertion_index(lines):
    """Index just before the first module-level initial/always block."""
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("always") or stripped.startswith("initial"):
            return i
    return len(lines)


# ============================================================
# PATCH UNDECLARED SIGNALS IN A TESTBENCH
# ============================================================

def strip_coverage_displays(tb_text):
    """Remove $display call lines that reference $coverage.

    xsim 2018.2 has no $coverage; such probe lines stop simulation
    at elaboration. They carry no functional effect, so the lines
    are simply dropped. Returns (cleaned_text, lines_removed).
    """
    if "$coverage" not in tb_text:
        return tb_text, 0

    kept = []
    removed = 0

    for line in tb_text.splitlines(keepends=True):
        if "$coverage" in line:
            removed += 1
            continue
        kept.append(line)

    return "".join(kept), removed

def patch_undeclared_signals(tb_text, error_text):
    """Deterministically declare undeclared signals referenced in a TB.

    For each signal reported as '<name> is not declared':
      - declares it as a reg before the first initial/always block
      - if it is also used inside @(posedge/negedge <name>),
        adds a self-contained free-running generator that
        initializes the signal low and toggles every 5ns:
        initial begin <name> = 0; forever #5 <name> = ~<name>; end

    Returns (patched_text, patched_signals, lines_changed) or
    (tb_text, [], 0) when no undeclared-signal errors are found.
    """
    if "is not declared" not in (error_text or "").lower():
        return tb_text, [], 0

    signals = find_undeclared_signals(error_text)

    if not signals:
        return tb_text, [], 0

    lines = tb_text.splitlines(keepends=True)
    insert_idx = _insertion_index(lines)

    patched = []
    lines_changed = 0

    for signal in signals:
        if signal in patched:
            continue

        patched.append(signal)

        declaration = f"reg {signal};\n"
        lines.insert(insert_idx, declaration)
        insert_idx += 1
        lines_changed += 1

        uses_edge = re.search(
            r"@\s*\((?:posedge|negedge)\s+"
            + re.escape(signal)
            + r"\b",
            tb_text
        )

        if uses_edge:
            clock_gen = (
                f"initial begin {signal} = 0; "
                f"forever #5 {signal} = ~{signal}; end\n"
            )
            lines.insert(insert_idx, clock_gen)
            insert_idx += 1
            lines_changed += 1

    return "".join(lines), patched, lines_changed