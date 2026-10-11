"""Deterministic coverage completion (no LLM involved).

Two independent mechanisms:

1. COMBINATIONAL - when exhaustive input coverage is below 100%, drive the
   missing input combinations by appending stimulus blocks.  The $monitor
   trace then samples every combination and coverage reports 100%.

2. SEQUENTIAL - append small, deterministic scenario blocks for missing
   sequential cases (ENABLE_INACTIVE, RESET_WHILE_OPERATING).  Each block
   genuinely drives the design's control signals (which is what the
   semantic evaluators verify) AND prints a COVERAGE_HIT line that is
   captured in simulation.log.  A case is only generated here when the
   block's behaviour actually exercises that case.

Exit codes:
    0  COMPLETED   - testbench updated with missing coverage
    1  NOT_NEEDED  - nothing to add (already complete)
    2  SKIPPED     - category not deterministically completable here
"""

import itertools
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from coverage_engine import (
    parse_samples,
    collect_signals,
    detect_clock,
    detect_reset,
    detect_enable,
    pick_output_signal,
    _eval_seq_enable_inactive,
    _eval_seq_reset_while_operating,
)

project_dir = Path(__file__).resolve().parent.parent

config = {}
config_file = project_dir / "config.json"
if config_file.exists():
    try:
        config = json.loads(
            config_file.read_text(encoding="utf-8")
        )
    except Exception:
        config = {}

exhaustive_limit = int(
    config.get("coverage", {})
    .get("exhaustive_input_limit", 8)
)

workspace = Path(os.environ.get(
    "AI_WORKSPACE", str(project_dir / "output")
))

tb_file = workspace / "tb.v"
design_file = workspace / "design.v"
log_file = workspace / "simulation.log"


# ============================================================
# LOCATE THE $finish / SIMULATION SUCCESSFUL ANCHOR LINE
# ============================================================

def _find_marker_index(lines):
    for index, line in enumerate(lines):
        if "SIMULATION SUCCESSFUL" in line:
            return index
    for index, line in reversed(list(enumerate(lines))):
        if re.search(r"\$finish\s*;", line):
            return index
    return None


# ============================================================
# COMBINATIONAL COMPLETION
# ============================================================

def _complete_combinational(tb_text, log_text):
    driven_values = {}
    assign_re = re.compile(
        r"\b([a-zA-Z_][a-zA-Z0-9_$]*)\s*=\s*"
        r"(?:(\d+)\s*'b([01]+)|(\d+))\s*;"
    )
    for match in assign_re.finditer(tb_text):
        bits = match.group(3)
        if bits is None:
            bits = match.group(4)
            if bits is None:
                continue
        driven_values.setdefault(
            match.group(1), set()
        ).update(bits)

    input_signals = sorted(
        signal
        for signal, values in driven_values.items()
        if values == {"0", "1"}
    )

    if not input_signals:
        print("RESULT: SKIPPED - no 1-bit driven input signals found")
        return 2

    n_inputs = len(input_signals)
    if n_inputs > exhaustive_limit:
        print(
            "RESULT: SKIPPED - "
            f"{n_inputs} inputs exceed exhaustive limit "
            f"({exhaustive_limit})"
        )
        return 2

    required = set(
        itertools.product("01", repeat=n_inputs)
    )

    tested = set()
    for sample in parse_samples(log_text):
        values = sample["values"]
        if all(signal in values for signal in input_signals):
            combination = tuple(
                values[signal] for signal in input_signals
            )
            if all(value in ("0", "1") for value in combination):
                tested.add(combination)

    missing = sorted(required - tested)

    if not missing:
        print("RESULT: NOT_NEEDED - every combination already traced")
        return 1

    lines = tb_text.splitlines(keepends=True)
    marker_index = _find_marker_index(lines)
    if marker_index is None:
        print("RESULT: ERROR - no success/finish anchor found in tb.v")
        return 2

    block_lines = [
        "\n    // Auto-added deterministic coverage completion\n"
    ]
    for combination in missing:
        stim = " ".join(
            f"{signal} = 1'b{value};"
            for signal, value in zip(input_signals, combination)
        )
        block_lines.append(f"    {stim} #10; \n")
    block_lines.append(
        '    $display("ALL COVERAGE COMBINATIONS APPLIED"); \n'
    )

    lines[marker_index:marker_index] = block_lines
    tb_file.write_text("".join(lines), encoding="utf-8")

    print("RESULT: COMPLETED")
    print(f"  Input signals      : {', '.join(input_signals)}")
    print(f"  Required combos    : {len(required)}")
    print(f"  Already traced     : {len(tested)}")
    print(f"  Combos added       : {len(missing)}")
    for combination in missing:
        print(
            "    - "
            + ", ".join(
                f"{s}={v}" for s, v in zip(input_signals, combination)
            )
        )
    return 0


# ============================================================
# SEQUENTIAL COMPLETION
# ============================================================

def _case_missing(case_id, evaluator, signals, samples, tb_text, design_text):
    """True when a sequential case is applicable AND still unverified."""
    check = {
        "ENABLE_INACTIVE": (
            lambda s: detect_enable(s) is not None
            and pick_output_signal(s, samples) not in (None, detect_enable(s))
        ),
        "RESET_WHILE_OPERATING": (
            lambda s: detect_reset(s)[0] is not None
            and detect_enable(s) is not None
        ),
    }.get(case_id)
    if check is None:
        return False
    if not check(signals):
        return False
    return not bool(evaluator(signals, samples, tb_text, design_text))


def _complete_sequential(tb_text, log_text, design_text):
    samples = parse_samples(log_text)
    signals = collect_signals(samples, tb_text, design_text)

    clk = detect_clock(signals)
    reset_sig, reset_active = detect_reset(signals)
    en_sig = detect_enable(signals)
    out_sig = pick_output_signal(signals, samples)

    scenarios = []

    # --- ENABLE_INACTIVE: hold output while enable is de-asserted.
    # Requires an earlier active (operation) context, which the main
    # stimulus already establishes; we only add if still missing.
    if (
        en_sig is not None
        and out_sig not in (None, en_sig)
        and _case_missing(
            "ENABLE_INACTIVE",
            _eval_seq_enable_inactive,
            signals, samples, tb_text, design_text,
        )
    ):
        block = [
            "\n    // Auto-added deterministic coverage: ENABLE_INACTIVE\n",
            f"    {en_sig} = 1'b0;  // disable -> output must hold\n",
        ]
        if clk is not None:
            # Toggle the clock several times while disabled: each edge
            # produces a $monitor sample showing en=0 with an UNCHANGED
            # output, which proves the hold behaviour.
            block.append(
                f"    repeat (8) begin #5 {clk} = ~{clk}; end\n"
            )
        else:
            block.append("    #40;\n")
        block.append(
            '    $display("COVERAGE_HIT: ENABLE_INACTIVE"); \n'
        )
        scenarios.append(("ENABLE_INACTIVE", block))

    # --- RESET_WHILE_OPERATING: assert reset while enable is active.
    if (
        reset_sig is not None
        and en_sig is not None
        and _case_missing(
            "RESET_WHILE_OPERATING",
            _eval_seq_reset_while_operating,
            signals, samples, tb_text, design_text,
        )
    ):
        block = [
            "\n    // Auto-added deterministic coverage: RESET_WHILE_OPERATING\n",
            f"    {en_sig} = 1'b1;  // keep counting in progress\n",
            f"    {reset_sig} = 1'b{reset_active};  // reset while operating\n",
            f"    #20;\n",
        ]
        if clk is not None:
            block.insert(
                1, f"    repeat (2) begin #5 {clk} = ~{clk}; end\n"
            )
        block.append(
            f"    {reset_sig} = 1'b{1 - int(reset_active)};  // release reset\n"
        )
        block.append(
            '    $display("COVERAGE_HIT: RESET_WHILE_OPERATING"); \n'
        )
        scenarios.append(("RESET_WHILE_OPERATING", block))

    if not scenarios:
        print(
            "RESULT: SKIPPED - no deterministic sequential "
            "scenarios applicable"
        )
        return 2

    lines = tb_text.splitlines(keepends=True)
    marker_index = _find_marker_index(lines)
    if marker_index is None:
        print("RESULT: ERROR - no success/finish anchor found in tb.v")
        return 2

    block_lines = [
        "\n    // Auto-added deterministic sequential coverage\n"
    ]
    for _case_id, block in scenarios:
        block_lines.extend(block)
    block_lines.append('    $display("ALL SEQUENTIAL COVERAGE APPLIED"); \n')

    lines[marker_index:marker_index] = block_lines
    tb_file.write_text("".join(lines), encoding="utf-8")

    print("RESULT: COMPLETED (sequential)")
    for case_id, _block in scenarios:
        print(f"  Added scenario      : {case_id}")
    print("  Control signals     : "
          + ", ".join(
              filter(None, [clk and f"clk={clk}",
                           reset_sig and f"rst={reset_sig}",
                           en_sig and f"en={en_sig}"])
          ))
    return 0


# ============================================================
# MAIN
# ============================================================

def main():
    if not tb_file.exists():
        print("RESULT: ERROR - tb.v not found")
        return 2

    if not log_file.exists():
        print("RESULT: ERROR - simulation.log not found")
        return 2

    tb_text = tb_file.read_text(encoding="utf-8")
    log_text = log_file.read_text(encoding="utf-8", errors="ignore")

    design_text = ""
    if design_file.exists():
        design_text = design_file.read_text(
            encoding="utf-8", errors="ignore"
        )

    analysis_text = ""
    analysis_file = workspace / "requirement_analysis.json"
    if analysis_file.exists():
        try:
            analysis_text = (
                json.loads(
                    analysis_file.read_text(encoding="utf-8")
                )
                .get("category", "")
                .upper()
            )
        except Exception:
            analysis_text = ""

    if "COMBINATIONAL" in analysis_text:
        return _complete_combinational(tb_text, log_text)

    if "SEQUENTIAL" in analysis_text:
        return _complete_sequential(tb_text, log_text, design_text)

    print(
        "RESULT: SKIPPED - "
        f"category '{analysis_text or 'UNKNOWN'}' "
        "not deterministically completable"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
