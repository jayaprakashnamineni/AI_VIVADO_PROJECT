from pathlib import Path
import re
import sys
import matplotlib.pyplot as plt

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
png_file = output_dir / "waveform.png"


# ============================================================
# CHECK LOG FILE
# ============================================================

if not log_file.exists():
    print("ERROR: simulation.log not found.")
    sys.exit(1)

log_text = log_file.read_text(
    encoding="utf-8",
    errors="ignore"
)


# ============================================================
# FIND WAVEFORM LINES
# ============================================================

pattern = re.compile(
    r"At time\s+(\d+),\s*(.*)"
)

samples = []

for line in log_text.splitlines():

    match = pattern.search(line)

    if not match:
        continue

    time_value = int(match.group(1))
    signal_text = match.group(2)

    signals = {}

    for item in signal_text.split(","):

        item = item.strip()

        if "=" not in item:
            continue

        name, value = item.split("=", 1)

        name = name.strip()
        value = value.strip()

        signals[name] = value

    if signals:
        samples.append((time_value, signals))


# ============================================================
# CHECK DATA
# ============================================================

if not samples:
    print("ERROR: No waveform data found in simulation.log.")
    sys.exit(1)

print(f"Found {len(samples)} waveform samples.")


# ============================================================
# GET SIGNAL NAMES
# ============================================================

signal_names = list(samples[0][1].keys())

print("Signals found:")
for signal in signal_names:
    print(f"  {signal}")


# ============================================================
# CONVERT TIME TO NS
# ============================================================

times = []

for time_value, _ in samples:
    times.append(time_value / 1000.0)


# ============================================================
# CREATE DIGITAL WAVEFORM DATA
# ============================================================

signal_data = {}

for signal in signal_names:

    values = []

    for _, signals in samples:

        value = signals.get(signal, "x")

        if value == "0":
            values.append(0)

        elif value == "1":
            values.append(1)

        else:
            values.append(0)

    signal_data[signal] = values


# ============================================================
# CREATE WAVEFORM FIGURE
# ============================================================

number_of_signals = len(signal_names)

fig_height = max(3, number_of_signals * 1.3)

fig, ax = plt.subplots(
    figsize=(12, fig_height)
)


# ============================================================
# DRAW EACH SIGNAL
# ============================================================

signal_spacing = 2

for index, signal in enumerate(signal_names):

    base_y = (number_of_signals - index - 1) * signal_spacing

    values = signal_data[signal]

    y_values = []

    for value in values:

        if value == 1:
            y_values.append(base_y + 1)

        else:
            y_values.append(base_y)

    # Draw digital waveform
    ax.step(
        times,
        y_values,
        where="post",
        linewidth=2
    )

    # Add signal name
    ax.text(
        -5,
        base_y + 0.5,
        signal,
        ha="right",
        va="center",
        fontsize=11
    )


# ============================================================
# GRAPH SETTINGS
# ============================================================

ax.set_xlabel("Time (ns)")
ax.set_title("Digital Simulation Waveform")

ax.set_xlim(min(times), max(times) + 10)

ax.set_ylim(
    -0.5,
    number_of_signals * signal_spacing
)

ax.set_yticks([])

ax.grid(
    True,
    axis="x",
    linestyle=":"
)

plt.tight_layout()


# ============================================================
# SAVE PNG
# ============================================================

plt.savefig(
    png_file,
    dpi=150,
    bbox_inches="tight"
)

plt.close()

print()
print("WAVEFORM PNG GENERATED SUCCESSFULLY")
print(f"Saved to: {png_file}")