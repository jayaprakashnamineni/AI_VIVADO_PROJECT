# Validation Status

Source of truth: `PHASE1_VALIDATION_AUDIT.md` (2026-10-11). Phase 1 is a
*software/logic* validation of the pipeline mechanisms — it does **not**
constitute hardware validation (no logic synthesis, no FPGA/ASIC sign-off).

## IMPLEMENTED

- End-to-end pipeline (`app/main.py`): analyze → generate → simulate →
  review → coverage → (fix/enhance loop) → archive.
- LLM generation with deterministic gates (relevance, width fidelity,
  testbench self-consistency, compile gate).
- Deterministic targeted fix (expected-value repair on the testbench).
- AI fix agent with change-size validation.
- Deterministic combinational coverage completion.
- Timeout-bounded LLM client (`llm_client.py`).
- Portable Vivado bin resolution (`vivado_config.py`, `VIVADO_BIN`).
- Conditional-`--skip-generate` workspace guard.
- Collision-proof run archives (microsecond timestamps).
- Deterministic regression harness (`app/regression.py`).

## VALIDATED

- Deterministic components, controlled by `app/regression.py`: **11/11
  checks pass** (classifier, coverage engine, targeted-fix apply/decline,
  coverage completion). See `REGRESSION.md`.
- Vivado XSim round-trip on fixtures: compile → elaborate → simulate;
  e.g. half-adder smoke = SUCCESS; AND-gate fixtures = PASS.
- **F1 fix**: AI generation of a 4-bit counter = correct RTL, compiles
  clean, `SIMULATION SUCCESSFUL` (reviewer PASS, 0 Vivado errors).
- **F2 fix**: run archives no longer collide (verified live:
  `runs/2026-10-11_17-49-25_871543`).

## PARTIALLY VALIDATED

- **4-bit counter — coverage loop:** functional sim PASS, but the
  sequential coverage-completion path hits F8 (3B enhance agent
  unreliable). Fixture-based coverage = 100 %.
- **FSM (traffic light) and 8-entry FIFO:** validated via deterministic
  fixtures only (PASS + 100 % coverage). Their full AI-generation path was
  NOT re-validated after the F1 prompt fix.
- **Waveform export:** image generation works but signal extraction is
  partial (F6) — only `clk` found for the counter, none for the FIFO.

## KNOWN LIMITATIONS

| ID | Description |
|----|-------------|
| F3 | Coverage classification substring bug: `"ram"` matches `localparam`/`parameter` → false MEMORY category. |
| F4 | `to_int` parses decimal `0/1` values as binary (`count=10` → 2). |
| F5 | No run-away guard: a TB that never calls `$finish` can grow `simulation.log` to hundreds of MB before the per-step timeout. |
| F6 | Waveform signal export is partial. |
| F7 | Deterministic targeted fix assumes the RTL is correct; it aligns the testbench to the DUT on a single parseable mismatch. |
| F8 | Sequential coverage enhancement with the 3B model is unreliable (injects failing assertions); the enhanced TB is reverted and the loop reports `TESTBENCH IMPROVEMENT FAILED`. |

## NOT STARTED

- Phase 2 logic synthesis (`synth_design`, timing), implementation, or
  bitstream generation.
- FPGA/ASIC hardware validation of any design.
- Fixes for F3–F8 (explicitly out of scope for Phase 1).
- Multi-design full-AI-path validation of FSM/FIFO after F1.