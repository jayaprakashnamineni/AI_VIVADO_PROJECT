from pathlib import Path
import json
import sys

from coverage_engine import build_coverage_requirements

# ============================================================
# PROJECT PATHS
# ============================================================

project_dir = Path(__file__).parent.parent
output_dir = project_dir / "output"

import os
_ws = os.environ.get("AI_WORKSPACE")
if _ws:
    output_dir = Path(_ws)

requirement_file = output_dir / "requirement_analysis.json"

output_dir.mkdir(exist_ok=True)


# ============================================================
# GET USER REQUIREMENT
# ============================================================

print("=" * 60)
print("AI REQUIREMENT AND RTL TYPE ANALYZER")
print("=" * 60)
print()

if len(sys.argv) > 1:
    request = " ".join(sys.argv[1:]).strip()
else:
    request = input("Enter your VLSI design request: ").strip()

if not request:
    print("ERROR: No design request entered.")
    raise SystemExit(1)


# ============================================================
# NORMALIZE REQUEST
# ============================================================

request_lower = request.lower()


# ============================================================
# DESIGN TYPE KEYWORDS
# ============================================================

combinational_keywords = [
    "adder",
    "subtract",
    "subtractor",
    "multiplexer",
    "mux",
    "demultiplexer",
    "demux",
    "encoder",
    "decoder",
    "comparator",
    "logic gate",
    "and gate",
    "or gate",
    "xor gate",
    "alu",
    "boolean"
]

sequential_keywords = [
    "counter",
    "flip flop",
    "flip-flop",
    "register",
    "shift register",
    "state machine",
    "fsm",
    "sequence detector",
    "clock divider",
    "timer",
    "pulse"
]

memory_keywords = [
    "fifo",
    "memory",
    "ram",
    "rom",
    "buffer",
    "queue",
    "stack",
    "cache"
]

protocol_keywords = [
    "uart",
    "spi",
    "i2c",
    "communication",
    "serial",
    "protocol",
    "transmitter",
    "receiver"
]


# ============================================================
# CLASSIFY DESIGN
# ============================================================

design_type = "CUSTOM / COMPLEX RTL"
category = "Complex"

if any(
    keyword in request_lower
    for keyword in memory_keywords
):
    design_type = "MEMORY / STORAGE RTL"
    category = "Memory"

elif any(
    keyword in request_lower
    for keyword in protocol_keywords
):
    design_type = "COMMUNICATION / PROTOCOL RTL"
    category = "Protocol"

elif any(
    keyword in request_lower
    for keyword in sequential_keywords
):
    design_type = "SEQUENTIAL RTL"
    category = "Sequential"

elif any(
    keyword in request_lower
    for keyword in combinational_keywords
):
    design_type = "COMBINATIONAL RTL"
    category = "Combinational"


# ============================================================
# SELECT VERIFICATION STRATEGY
# ============================================================

if category == "Combinational":

    verification_strategy = {
        "strategy": "EXHAUSTIVE OR DIRECTED INPUT TESTING",
        "tests": [
            "Normal input combinations",
            "All combinations for small input widths",
            "Zero input case",
            "Maximum input case",
            "Boundary cases",
            "Expected output checks"
        ],
        "coverage_method": (
            "Input combination coverage when total "
            "combinations are practical"
        )
    }


elif category == "Sequential":

    verification_strategy = {
        "strategy": "SCENARIO-BASED SEQUENTIAL TESTING",
        "tests": [
            "Reset behavior",
            "Clock behavior",
            "Initial state",
            "State transitions",
            "Boundary conditions",
            "Repeated operation",
            "Invalid or unexpected conditions"
        ],
        "coverage_method": (
            "State and transition-oriented coverage"
        )
    }


elif category == "Memory":

    verification_strategy = {
        "strategy": "FEATURE AND CORNER-CASE TESTING",
        "tests": [
            "Reset behavior",
            "Write operation",
            "Read operation",
            "Empty condition",
            "Full condition",
            "Overflow condition",
            "Underflow condition",
            "Simultaneous read/write",
            "Pointer or address boundary cases"
        ],
        "coverage_method": (
            "Feature coverage and boundary coverage"
        )
    }


elif category == "Protocol":

    verification_strategy = {
        "strategy": "PROTOCOL SCENARIO TESTING",
        "tests": [
            "Reset behavior",
            "Clock behavior",
            "Valid transaction",
            "Multiple transactions",
            "Timing behavior",
            "Start condition",
            "Stop or completion condition",
            "Invalid transaction",
            "Error handling"
        ],
        "coverage_method": (
            "Protocol feature and transaction coverage"
        )
    }


else:

    verification_strategy = {
        "strategy": "AI-SELECTED FEATURE-BASED TESTING",
        "tests": [
            "Functional requirements",
            "Normal operation",
            "Boundary conditions",
            "Corner cases",
            "Reset behavior if applicable",
            "Clock behavior if applicable",
            "Error conditions"
        ],
        "coverage_method": (
            "Requirement and feature coverage"
        )
    }


# ============================================================
# LOAD COVERAGE CONFIG
# ============================================================

config_file = project_dir / "config.json"

coverage_config = {}

if config_file.exists():

    try:
        coverage_config = json.loads(
            config_file.read_text(encoding="utf-8")
        ).get("coverage", {})
    except Exception:
        coverage_config = {}

exhaustive_limit = coverage_config.get(
    "exhaustive_input_limit", 8
)

threshold_config = coverage_config.get("thresholds", {})

coverage_threshold = threshold_config.get(
    category.upper(),
    threshold_config.get(
        "SEQUENTIAL",
        100
    )
)


# ============================================================
# CREATE ANALYSIS RESULT
# ============================================================

coverage_requirements = build_coverage_requirements(
    category.upper()
)

analysis_result = {
    "user_request": request,
    "design_type": design_type,
    "category": category,
    "verification_strategy": verification_strategy,
    "coverage": {
        "threshold": coverage_threshold,
        "exhaustive_input_limit": exhaustive_limit,
        "cases": coverage_requirements,
    },
}


# ============================================================
# SAVE JSON RESULT
# ============================================================

requirement_file.write_text(
    json.dumps(
        analysis_result,
        indent=4
    ),
    encoding="utf-8"
)


# ============================================================
# PRINT RESULT
# ============================================================

print()
print("=" * 60)
print("REQUIREMENT ANALYSIS RESULT")
print("=" * 60)

print(f"User Request : {request}")
print(f"Design Type  : {design_type}")
print(f"Category     : {category}")

print()
print("VERIFICATION STRATEGY")
print("-" * 60)

print(
    f"Strategy: "
    f"{verification_strategy['strategy']}"
)

print()
print("Required Tests:")

for test in verification_strategy["tests"]:
    print(f"  - {test}")

print()
print(
    "Coverage Method: "
    f"{verification_strategy['coverage_method']}"
)

print()
print("REQUIRED COVERAGE CASES")
print("-" * 60)
print(f"Required Coverage : {coverage_threshold}%")

for case in coverage_requirements:
    print(f"  - {case['id']}: {case['name']}")

print()

print("=" * 60)
print("ANALYSIS COMPLETED")
print("=" * 60)

print()
print("Analysis saved to:")
print(requirement_file)