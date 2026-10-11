"""Deterministic verification coverage engine.

Measures which of a design's required verification cases are actually
exercised by the testbench, using evidence drawn from:

1. Simulation-log value traces ("At time ..." $monitor/$display samples).
2. Testbench scenario markers ("// COVERAGE_CASE: <ID>" + a printed
   "COVERAGE_HIT: <ID>" line when the scenario runs).

A case is VERIFIED only when real evidence is found.  Anything unresolved
is reported as NOT_VERIFIED (never assumed passed).
"""

from pathlib import Path
import re

# ============================================================
# SIGNAL NAME GROUPS
# ============================================================

_RESET_NAMES = [
    "rst_n", "reset_n", "nrst", "n_reset", "aresetn", "arst_n",
    "rst", "reset", "arst", "areset", "rstn", "rstn_r",
]

_ENABLE_NAMES = ["ena", "enable", "en", "ce", "cnt_en"]

_CLOCK_NAMES = ["clk", "clock", "clkin", "sysclk", "s_axi_aclk"]

_WRITE_NAMES = ["wr_en", "we", "wren", "wr", "write", "w_en"]

_READ_NAMES = ["rd_en", "re", "rden", "rd", "read", "r_en"]

_FULL_NAMES = ["full", "fifo_full"]

_EMPTY_NAMES = ["empty", "fifo_empty"]

_ERROR_NAMES = ["error", "err", "rx_error", "tx_error", "overflow_err"]

_STATE_NAMES = ["state", "state_reg", "cs", "current_state", "fsm_state"]

_SERIAL_NAMES = ["tx", "rx", "sda", "scl", "sclk", "sck",
                 "mosi", "miso", "dat", "data", "din", "dout", "busy"]

_OUTPUT_PREFERENCE = [
    "count", "cnt", "q", "dout", "out", "sum", "result",
    "product", "data", "state", "value", "y",
]

# ============================================================
# SAMPLE PARSING
# ============================================================

AT_TIME_RE = re.compile(
    r"At time\s+(\d+),\s*(.*)",
    re.IGNORECASE
)

TOKEN_RE = re.compile(
    r"(\w+)\s*=\s*([01xXzZ]+(?!\d)|\d+'[bdh][0-9a-fA-F]+|\d+)"
)


def parse_samples(log_text):
    """Parse "At time ..." sample lines into a list of dicts.

    Each sample: {"time": int, "values": {signal: value_string}}
    """
    samples = []

    for match in AT_TIME_RE.finditer(log_text):

        time_value = int(match.group(1))
        signal_text = match.group(2)

        values = {}

        for name, value in TOKEN_RE.findall(signal_text):
            values[name] = value.strip()

        samples.append(
            {"time": time_value, "values": values}
        )

    return samples


def to_int(value):
    """Convert a sampled value string to an integer, else None."""
    v = value.strip().lower()

    m = re.fullmatch(r"(\d+)'([bdh])([0-9a-f]+)", v)
    if m:
        base = {"b": 2, "o": 8, "d": 10, "h": 16}[m.group(2)]
        return int(m.group(3), base)

    if re.fullmatch(r"[01]+", v):
        return int(v, 2)

    if re.fullmatch(r"\d+", v):
        return int(v, 10)

    if v in ("x", "z", "xx", "zz"):
        return None

    if re.fullmatch(r"[0-9a-f]+", v):
        return int(v, 16)

    return None


# ============================================================
# SIGNAL DETECTION
# ============================================================

def _find_signal(signals, names):
    for name in names:
        for signal in signals:
            if signal.lower() == name:
                return signal
    for name in names:
        for signal in signals:
            if name in signal.lower():
                return signal
    return None


def collect_signals(samples, tb_text, design_text):
    """Gather candidate signal names from samples + source files."""
    signals = set()

    for sample in samples:
        signals.update(sample["values"].keys())

    for text in (tb_text or "", design_text or ""):
        for match in re.finditer(
            r"\b(?:reg|wire)\s+(\[[^\]]+\]\s+)?\s*([a-zA-Z_][a-zA-Z0-9_$]*)"
            r"(?:\s*,?\s*[a-zA-Z_][a-zA-Z0-9_$]*)*",
            text,
        ):
            decl = match.group(0)
            for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_$]*)\b", decl):
                if name in ("reg", "wire"):
                    continue
                signals.add(name)

    return sorted(signals)


def detect_reset(signals):
    signal = _find_signal(signals, _RESET_NAMES)
    if signal is None:
        return None, None
    name = signal.lower()
    active_when = "1"
    if (
        name.startswith("n")
        or name.endswith("_n")
        or "_n" in name
        or "nrst" in name
    ):
        active_when = "0"
    return signal, active_when


def detect_enable(signals):
    return _find_signal(signals, _ENABLE_NAMES)


def detect_clock(signals):
    return _find_signal(signals, _CLOCK_NAMES)


def detect_write(signals):
    return _find_signal(signals, _WRITE_NAMES)


def detect_read(signals):
    return _find_signal(signals, _READ_NAMES)


def detect_full(signals):
    return _find_signal(signals, _FULL_NAMES)


def detect_empty(signals):
    return _find_signal(signals, _EMPTY_NAMES)


def detect_error(signals):
    return _find_signal(signals, _ERROR_NAMES)


def detect_serial_data(signals):
    return _find_signal(signals, _SERIAL_NAMES)


def pick_output_signal(signals, samples):
    """Choose the signal that best represents DUT output progress."""
    sample_signals = []
    if samples:
        sample_signals = sorted(samples[0]["values"].keys())

    for name in _OUTPUT_PREFERENCE:
        for signal in sample_signals:
            if signal.lower() == name:
                return signal

    counts = {}

    for signal in sample_signals:
        distinct = set()
        for sample in samples:
            if signal in sample["values"]:
                distinct.add(sample["values"][signal])
        counts[signal] = len(distinct)

    if counts:
        return max(counts, key=counts.get)

    return None


# ============================================================
# REQUIRED CASE CATALOG
# ============================================================

def build_coverage_requirements(category):
    """Return category-appropriate required coverage cases.

    Each case: {"id": str, "name": str, "description": str}
    """
    catalog = {

        "COMBINATIONAL": [
            {
                "id": "EXHAUSTIVE",
                "name": "All input combinations",
                "description": "Every practical input combination exercised",
            },
            {
                "id": "ZERO_INPUTS",
                "name": "Zero input case",
                "description": "All inputs driven low",
            },
            {
                "id": "MAX_INPUTS",
                "name": "Maximum input case",
                "description": "All inputs driven high",
            },
            {
                "id": "SINGLE_HOT",
                "name": "Single-hot inputs",
                "description": "Each input driven alone while others are low",
            },
            {
                "id": "OUTPUT_CHECKS",
                "name": "Expected output checks",
                "description": "Testbench asserts against expected outputs",
            },
        ],

        "SEQUENTIAL": [
            {
                "id": "RESET_ASSERTED",
                "name": "Reset asserted",
                "description": "Reset input asserted to initialize the design",
            },
            {
                "id": "RESET_RELEASED",
                "name": "Reset released",
                "description": "Reset de-asserted after being asserted",
            },
            {
                "id": "CLOCK_ACTIVITY",
                "name": "Clock activity",
                "description": "Design advances across multiple clock cycles",
            },
            {
                "id": "ENABLE_ACTIVE",
                "name": "Enable active",
                "description": "Counting/operation while enabled",
            },
            {
                "id": "ENABLE_INACTIVE",
                "name": "Enable inactive / hold",
                "description": "Output holds while disabled",
            },
            {
                "id": "STATE_TRANSITION",
                "name": "State/output transition",
                "description": "Output changes between samples",
            },
            {
                "id": "BOUNDARY_WRAP",
                "name": "Boundary / wraparound",
                "description": "Output reaches an extreme and wraps or saturates",
            },
            {
                "id": "REPEATED_OPERATION",
                "name": "Repeated operation",
                "description": "Multiple output transitions observed",
            },
            {
                "id": "RESET_WHILE_OPERATING",
                "name": "Reset while operating",
                "description": "Reset asserted while enable is active",
            },
        ],

        "MEMORY": [
            {
                "id": "RESET",
                "name": "Reset / initialization",
                "description": "Design initialized to a known state",
            },
            {
                "id": "WRITE",
                "name": "Write operation",
                "description": "Data stored into the memory",
            },
            {
                "id": "READ",
                "name": "Read operation",
                "description": "Data retrieved from the memory",
            },
            {
                "id": "EMPTY",
                "name": "Empty condition",
                "description": "Empty flag asserted",
            },
            {
                "id": "FULL",
                "name": "Full condition",
                "description": "Full flag asserted",
            },
            {
                "id": "OVERFLOW_GUARD",
                "name": "Overflow prevention",
                "description": "Write attempted while full",
            },
            {
                "id": "UNDERFLOW_GUARD",
                "name": "Underflow prevention",
                "description": "Read attempted while empty",
            },
            {
                "id": "SIMULT_RW",
                "name": "Simultaneous read/write",
                "description": "Read and write active in the same cycle",
            },
            {
                "id": "BOUNDARY_FILL_DRAIN",
                "name": "Fill then drain",
                "description": "Fill to full then drain back to empty",
            },
        ],

        "PROTOCOL": [
            {
                "id": "RESET",
                "name": "Reset / initialization",
                "description": "Design initialized to a known state",
            },
            {
                "id": "IDLE",
                "name": "Idle condition",
                "description": "Line/data idle before a transaction",
            },
            {
                "id": "START",
                "name": "Start condition",
                "description": "First transition away from idle",
            },
            {
                "id": "VALID_TRANSACTION",
                "name": "Valid transaction",
                "description": "Data activity across multiple samples",
            },
            {
                "id": "MULTI_TRANSACTION",
                "name": "Multiple transactions",
                "description": "Repeated bursts of activity",
            },
            {
                "id": "END_STOP",
                "name": "End / stop condition",
                "description": "Return to idle after activity",
            },
            {
                "id": "ERROR_HANDLING",
                "name": "Error handling",
                "description": "Error condition exercised",
            },
        ],

        "COMPLEX": [
            {
                "id": "RESET",
                "name": "Reset / initialization",
                "description": "Design initialized to a known state",
            },
            {
                "id": "FUNCTIONAL_PROGRESS",
                "name": "Functional progress",
                "description": "Outputs change during normal operation",
            },
            {
                "id": "BOUNDARY",
                "name": "Boundary conditions",
                "description": "Extreme input/output conditions",
            },
            {
                "id": "REPEATED_OPERATION",
                "name": "Repeated operation",
                "description": "Multiple operations observed",
            },
        ],
    }

    return catalog.get(category, catalog["COMPLEX"])


# ============================================================
# EVIDENCE RULES
# ============================================================

def _values_for(samples, signal):
    return [
        (sample["time"], sample["values"][signal])
        for sample in samples
        if signal in sample["values"]
    ]


def _has_value(samples, signal, value):
    for sample in samples:
        if signal in sample["values"]:
            if sample["values"][signal] == value:
                return True
    return False


def _consecutive_pairs(list_values):
    for i in range(len(list_values) - 1):
        yield list_values[i], list_values[i + 1]


def _stable_count(samples, signal, value, minimum=2):
    seq = [v for (_t, v) in _values_for(samples, signal)]
    run = 0
    for v in seq:
        run = run + 1 if v == value else 0
        if run >= minimum:
            return True
    return False


# ============================================================
# PER-CASE EVALUATION
# ============================================================

def _eval_seq_reset_asserted(signals, samples, tb_text, design_text):
    reset_sig, active = detect_reset(signals)
    if reset_sig is None:
        return False
    return _has_value(samples, reset_sig, active)


def _eval_seq_reset_released(signals, samples, tb_text, design_text):
    reset_sig, active = detect_reset(signals)
    if reset_sig is None:
        return False
    released = "1" if active == "0" else "0"
    seq = [v for (_t, v) in _values_for(samples, reset_sig)]
    asserted = False
    for v in seq:
        if v == active:
            asserted = True
        elif v == released and asserted:
            return True
    return False


def _eval_seq_clock_activity(signals, samples, tb_text, design_text):
    clk_sig = detect_clock(signals)

    if clk_sig is not None and any(
        clk_sig in sample["values"] for sample in samples
    ):
        seen = set(sample["values"][clk_sig] for sample in samples
                   if clk_sig in sample["values"])
        return "0" in seen and "1" in seen

    return len(samples) >= 4


def _pairs_by_time(samples, sig_a, sig_b):
    """Return (time, val_a, val_b) triples where both signals were sampled."""
    by_time = {}

    for sample in samples:
        t = sample["time"]
        v = sample["values"]
        if sig_a in v and sig_b in v:
            by_time[t] = (v[sig_a], v[sig_b])

    return [
        (t, vals[0], vals[1])
        for t, vals in sorted(by_time.items())
    ]


def _eval_seq_enable_active(signals, samples, tb_text, design_text):
    en_sig = detect_enable(signals)
    output_sig = pick_output_signal(signals, samples)

    if en_sig is None or output_sig is None or output_sig == en_sig:
        return False

    pairs = _pairs_by_time(samples, en_sig, output_sig)

    if len(pairs) < 2:
        return False

    for i in range(len(pairs) - 1):
        for j in range(i + 1, len(pairs)):
            if (
                pairs[i][1] == "1" == pairs[j][1]
                and pairs[i][2] != pairs[j][2]
            ):
                return True
    return False


def _eval_seq_enable_inactive(signals, samples, tb_text, design_text):
    en_sig = detect_enable(signals)
    output_sig = pick_output_signal(signals, samples)

    if en_sig is None or output_sig is None or output_sig == en_sig:
        return False

    en_and_out = _pairs_by_time(samples, en_sig, output_sig)

    if len(en_and_out) < 3:
        return False

    # Require an earlier sample where enable was active (operation context),
    # so an initialization/reset sequence does not count as a "hold".
    first_active_time = None

    for sample in samples:
        if en_sig in sample["values"]:
            if sample["values"][en_sig] == "1":
                first_active_time = sample["time"]
                break

    if first_active_time is None:
        return False

    for i in range(len(en_and_out) - 1):
        left = en_and_out[i]
        right = en_and_out[i + 1]
        if (
            left[1] == "0" == right[1]
            and left[2] == right[2]
            and left[0] >= first_active_time
        ):
            return True
    return False


def _eval_seq_state_transition(signals, samples, tb_text, design_text):
    output_sig = pick_output_signal(signals, samples)
    if output_sig is None:
        return False
    seq = [v for (_t, v) in _values_for(samples, output_sig)]
    for a, b in _consecutive_pairs(seq):
        if a != b:
            return True
    return False


def _eval_seq_boundary_wrap(signals, samples, tb_text, design_text):
    output_sig = pick_output_signal(signals, samples)
    if output_sig is None:
        return False

    values = [v for (_t, v) in _values_for(samples, output_sig)]
    ints = [to_int(v) for v in values if to_int(v) is not None]

    if len(ints) < 2:
        return False

    max_v = max(ints)
    min_v = min(ints)

    if max_v - min_v <= 1:
        return False

    for a, b in _consecutive_pairs(ints):
        if a > b and (
            (a == max_v and b <= min_v + 1)
            or (b == min_v and a >= max_v - 1)
        ):
            return True
    return False


def _eval_seq_repeated_operation(signals, samples, tb_text, design_text):
    output_sig = pick_output_signal(signals, samples)
    if output_sig is None:
        return False
    seq = [v for (_t, v) in _values_for(samples, output_sig)]
    changes = sum(1 for a, b in _consecutive_pairs(seq) if a != b)
    return changes >= 3


def _eval_seq_reset_while_operating(signals, samples, tb_text, design_text):
    reset_sig, active = detect_reset(signals)
    en_sig = detect_enable(signals)

    if reset_sig is None or en_sig is None:
        return False

    for sample in samples:
        v = sample["values"]
        if v.get(reset_sig) == active and v.get(en_sig) == "1":
            return True
    return False


def _eval_mem_reset(signals, samples, tb_text, design_text):
    reset_sig, active = detect_reset(signals)
    if reset_sig is not None and _has_value(samples, reset_sig, active):
        return True
    empty_sig = detect_empty(signals)
    if empty_sig is not None and samples:
        first = samples[0]
        if empty_sig in first["values"] and first["values"][empty_sig] == "1":
            return True
    return False


def _eval_mem_write(signals, samples, tb_text, design_text):
    wr_sig = detect_write(signals)
    if wr_sig is None:
        return False
    return _has_value(samples, wr_sig, "1")


def _eval_mem_read(signals, samples, tb_text, design_text):
    rd_sig = detect_read(signals)
    if rd_sig is None:
        return False
    return _has_value(samples, rd_sig, "1")


def _eval_mem_empty(signals, samples, tb_text, design_text):
    empty_sig = detect_empty(signals)
    if empty_sig is None:
        return False
    return _has_value(samples, empty_sig, "1")


def _eval_mem_full(signals, samples, tb_text, design_text):
    full_sig = detect_full(signals)
    if full_sig is None:
        return False
    return _has_value(samples, full_sig, "1")


def _eval_mem_overflow(signals, samples, tb_text, design_text):
    wr_sig = detect_write(signals)
    full_sig = detect_full(signals)
    if wr_sig is None or full_sig is None:
        return False
    for sample in samples:
        v = sample["values"]
        if (
            wr_sig in v and v[wr_sig] == "1"
            and full_sig in v and v[full_sig] == "1"
        ):
            return True
    return False


def _eval_mem_underflow(signals, samples, tb_text, design_text):
    rd_sig = detect_read(signals)
    empty_sig = detect_empty(signals)
    if rd_sig is None or empty_sig is None:
        return False
    for sample in samples:
        v = sample["values"]
        if (
            rd_sig in v and v[rd_sig] == "1"
            and empty_sig in v and v[empty_sig] == "1"
        ):
            return True
    return False


def _eval_mem_simult_rw(signals, samples, tb_text, design_text):
    wr_sig = detect_write(signals)
    rd_sig = detect_read(signals)
    if wr_sig is None or rd_sig is None:
        return False
    for sample in samples:
        v = sample["values"]
        if (
            wr_sig in v and v[wr_sig] == "1"
            and rd_sig in v and v[rd_sig] == "1"
        ):
            return True
    return False


def _eval_mem_fill_drain(signals, samples, tb_text, design_text):
    full_sig = detect_full(signals)
    empty_sig = detect_empty(signals)
    if full_sig is None or empty_sig is None:
        return False

    full_seen_t = None
    empty_seen_t = None

    for sample in samples:
        v = sample["values"]
        if full_sig in v and v[full_sig] == "1":
            full_seen_t = sample["time"]
        if empty_sig in v and v[empty_sig] == "1":
            empty_seen_t = sample["time"]

    return (
        full_seen_t is not None
        and empty_seen_t is not None
        and empty_seen_t > full_seen_t
    )


def _serial_seq(signals, samples):
    sig = detect_serial_data(signals)
    if sig is None:
        return []
    return [v for (_t, v) in _values_for(samples, sig)]


def _eval_proto_reset(signals, samples, tb_text, design_text):
    reset_sig, active = detect_reset(signals)
    if reset_sig is not None and _has_value(samples, reset_sig, active):
        return True
    return False


def _eval_proto_idle(signals, samples, tb_text, design_text):
    seq = _serial_seq(signals, samples)
    if len(seq) < 2:
        return False
    for a, b in _consecutive_pairs(seq):
        if a == b:
            return True
    return False


def _changes(seq):
    return [
        (i, a, b)
        for i, (a, b) in enumerate(_consecutive_pairs(seq))
        if a != b
    ]


def _eval_proto_start(signals, samples, tb_text, design_text):
    seq = _serial_seq(signals, samples)
    if len(seq) < 3:
        return False
    return len(_changes(seq)) >= 1


def _eval_proto_valid_transaction(signals, samples, tb_text, design_text):
    seq = _serial_seq(signals, samples)
    if len(seq) < 4:
        return False
    distinct = set(seq)
    return (
        len(_changes(seq)) >= 2
        or len(distinct) >= 3
    )


def _eval_proto_multi_transaction(signals, samples, tb_text, design_text):
    seq = _serial_seq(signals, samples)
    changes = _changes(seq)
    if len(changes) < 3:
        return False

    idle = False
    for i, a, b in changes:
        if i + 2 < len(seq) - 1 and seq[i + 1] != a:
            idle = idle or (a == seq[i + 1])
    return True


def _eval_proto_end_stop(signals, samples, tb_text, design_text):
    seq = _serial_seq(signals, samples)
    if len(seq) < 4:
        return False
    changes = _changes(seq)
    if not changes:
        return False
    return seq[0] == seq[-1]


def _eval_proto_error(signals, samples, tb_text, design_text):
    err_sig = detect_error(signals)
    if err_sig is None:
        return False
    return _has_value(samples, err_sig, "1")


def _eval_comb_zero(signals, samples, tb_text, design_text, input_signals):
    if not input_signals:
        return False
    for sample in samples:
        v = sample["values"]
        if all(
            sig in v and v[sig] == "0"
            for sig in input_signals
        ):
            return True
    return False


def _eval_comb_max(signals, samples, tb_text, design_text, input_signals):
    if not input_signals:
        return False
    for sample in samples:
        v = sample["values"]
        if all(
            sig in v and v[sig] == "1"
            for sig in input_signals
        ):
            return True
    return False


def _eval_comb_single_hot(signals, samples, tb_text, design_text, input_signals):
    if not input_signals:
        return False
    for sig in input_signals:
        found = False
        for sample in samples:
            v = sample["values"]
            if v.get(sig) == "1" and all(
                (other == sig) or (v.get(other) == "0")
                for other in input_signals
            ):
                found = True
                break
        if not found:
            return False
    return True


def _eval_comb_output_checks(signals, samples, tb_text, design_text, input_signals):
    return bool(re.search(r"\bEXPECTED\s*:", tb_text or ""))


_EVALUATORS = {
    "RESET": _eval_mem_reset,
    "RESET_ASSERTED": _eval_seq_reset_asserted,
    "RESET_RELEASED": _eval_seq_reset_released,
    "CLOCK_ACTIVITY": _eval_seq_clock_activity,
    "ENABLE_ACTIVE": _eval_seq_enable_active,
    "ENABLE_INACTIVE": _eval_seq_enable_inactive,
    "STATE_TRANSITION": _eval_seq_state_transition,
    "BOUNDARY_WRAP": _eval_seq_boundary_wrap,
    "BOUNDARY": _eval_seq_boundary_wrap,
    "REPEATED_OPERATION": _eval_seq_repeated_operation,
    "WRITE": _eval_mem_write,
    "READ": _eval_mem_read,
    "EMPTY": _eval_mem_empty,
    "FULL": _eval_mem_full,
    "OVERFLOW_GUARD": _eval_mem_overflow,
    "UNDERFLOW_GUARD": _eval_mem_underflow,
    "SIMULT_RW": _eval_mem_simult_rw,
    "BOUNDARY_FILL_DRAIN": _eval_mem_fill_drain,
    "RESET_WHILE_OPERATING": _eval_seq_reset_while_operating,
    "IDLE": _eval_proto_idle,
    "START": _eval_proto_start,
    "VALID_TRANSACTION": _eval_proto_valid_transaction,
    "MULTI_TRANSACTION": _eval_proto_multi_transaction,
    "END_STOP": _eval_proto_end_stop,
    "ERROR_HANDLING": _eval_proto_error,
    "FUNCTIONAL_PROGRESS": _eval_seq_state_transition,
}


# ============================================================
# APPLICABILITY RULES
# ============================================================

_IMPLIES_SIGNAL = {
    "ENABLE_ACTIVE": detect_enable,
    "ENABLE_INACTIVE": detect_enable,
    "WRITE": detect_write,
    "READ": detect_read,
    "EMPTY": detect_empty,
    "FULL": detect_full,
    "OVERFLOW_GUARD": lambda signals: (
        detect_write(signals) and detect_full(signals)
    ),
    "UNDERFLOW_GUARD": lambda signals: (
        detect_read(signals) and detect_empty(signals)
    ),
    "SIMULT_RW": lambda signals: (
        detect_write(signals) and detect_read(signals)
    ),
    "BOUNDARY_FILL_DRAIN": lambda signals: (
        detect_full(signals) and detect_empty(signals)
    ),
    "ERROR_HANDLING": detect_error,
    "RESET_WHILE_OPERATING": lambda signals: bool(
        detect_enable(signals) and detect_reset(signals)[0]
    ),
    "IDLE": detect_serial_data,
    "START": detect_serial_data,
    "VALID_TRANSACTION": detect_serial_data,
    "MULTI_TRANSACTION": detect_serial_data,
    "END_STOP": detect_serial_data,
    "BOUNDARY_WRAP": lambda signals: True,
}


def _case_applicable(case_id, signals):
    check = _IMPLIES_SIGNAL.get(case_id)
    if check is None:
        return True
    try:
        return bool(check(signals))
    except TypeError:
        return True


# ============================================================
# MARKER PARSING
# ============================================================

_COVERAGE_CASE_RE = re.compile(
    r"COVERAGE_CASE\s*[:=]\s*([A-Z0-9_]+)",
    re.IGNORECASE,
)

_COVERAGE_HIT_RE = re.compile(
    r"COVERAGE_HIT\s*[:=]\s*([A-Z0-9_]+)",
    re.IGNORECASE,
)


def parse_marker_ids(tb_text):
    return {
        m.group(1).upper()
        for m in _COVERAGE_CASE_RE.finditer(tb_text or "")
    }


def parse_hit_ids(log_text):
    return {
        m.group(1).upper()
        for m in _COVERAGE_HIT_RE.finditer(log_text or "")
    }


# ============================================================
# FALLBACK CATEGORY CLASSIFIER (no requirement analysis available)
# ============================================================

def classify_from_files(request, tb_text, design_text):
    """Deterministically guess the design category from source text."""
    text = "\n".join(filter(None, [tb_text, design_text])).lower()
    request_text = (request or "").lower()

    all_text = text + " " + request_text

    if any(word in all_text for word in
           ["uart", "spi", "i2c", "serial", "tx", "rx", "protocol",
            "transmitter", "receiver", "sda", "scl"]):
        return "PROTOCOL"

    if any(word in all_text for word in
           ["fifo", "memory", "ram", "rom", "stack", "queue", "cache"]):
        return "MEMORY"

    if ("always" in text and any(word in all_text for word in
                                 ["count", "counter", "state", "fsm",
                                  "register", "clock", "clk"])):
        return "SEQUENTIAL"

    if re.search(r"\bassign\b", text):
        return "COMBINATIONAL"

    if "always" in text:
        return "SEQUENTIAL"

    return "COMPLEX"


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def evaluate_coverage(
    category,
    samples,
    log_text,
    tb_text,
    design_text,
    input_signals=None,
    exhaustive_limit=8,
    exhaustive_ok=None,
):
    """Return coverage verdict dict for a category.

    Result fields:
        results        : list of case dicts with status
        applicable     : number of applicable cases
        verified       : number verified
        missing        : list of missing case names/ids
        coverage_pct   : percentage (0 if none applicable)
        required_pct   : 100.0
        status         : True if coverage_pct >= required threshold
    """
    requirements = build_coverage_requirements(category)
    signals = collect_signals(samples, tb_text, design_text)

    if category == "COMBINATIONAL" and input_signals is not None:
        n_inputs = len(input_signals)
        categorize = (
            "exhaustive"
            if n_inputs and n_inputs <= exhaustive_limit
            else "directed"
        )
    else:
        categorize = "directed"

    markers = parse_marker_ids(tb_text)
    hits = parse_hit_ids(log_text)

    results = []

    for req in requirements:
        case_id = req["id"].upper()
        status = "NOT_VERIFIED"
        detail = ""

        if categorize == "exhaustive":
            if case_id == "EXHAUSTIVE":
                status = (
                    "VERIFIED" if exhaustive_ok else "NOT_VERIFIED"
                )
                if not exhaustive_ok:
                    detail = "Some input combinations not exercised"
                results.append({**req, "status": status, "detail": detail})
            else:
                results.append({
                    **req,
                    "status": "NOT_APPLICABLE",
                    "detail": "Subsumed by exhaustive combination coverage",
                })
            continue

        # Directed / non-exhaustive combinational or other category
        if case_id == "EXHAUSTIVE":
            results.append({
                **req,
                "status": "NOT_APPLICABLE",
                "detail": "Input width exceeds exhaustive limit",
            })
            continue

        if not _case_applicable(case_id, signals):
            status = "NOT_APPLICABLE"
            results.append({**req, "status": status,
                            "detail": "No relevant signal detected"})
            continue

        evaluator = _EVALUATORS.get(case_id)
        verified = False

        if evaluator is not None:
            verified = bool(
                evaluator(signals, samples, tb_text, design_text)
            )

        if not verified and case_id in hits:
            verified = True

        status = "VERIFIED" if verified else "NOT_VERIFIED"
        results.append({**req, "status": status})

    applicable = [r for r in results if r["status"] != "NOT_APPLICABLE"]
    verified_count = sum(1 for r in applicable if r["status"] == "VERIFIED")
    applicable_count = len(applicable)

    if applicable_count:
        coverage_pct = (verified_count / applicable_count) * 100
    else:
        coverage_pct = 0.0

    missing = [
        r for r in applicable if r["status"] == "NOT_VERIFIED"
    ]

    return {
        "results": results,
        "applicable": applicable_count,
        "verified": verified_count,
        "missing": missing,
        "coverage_pct": coverage_pct,
        "required_pct": 100.0,
        "categorize": categorize,
        "status": (
            coverage_pct >= 100.0
            if applicable_count else False
        ),
        "signals": signals,
        "markers_present": bool(markers),
    }