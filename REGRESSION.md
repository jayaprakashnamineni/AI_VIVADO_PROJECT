# Regression Validation

## Result

`app/regression.py` — **11/11 checks PASS** (2026-10-11), exit code 0.

Run it with:

```powershell
.\.venv\Scripts\python.exe app\regression.py
```

## What the harness validates

The harness runs **in-process with no Ollama call and no Vivado run** and
exercises the deterministic core of the pipeline:

1. **Category classification** (`coverage_engine.classify_from_files`)
   — a combinational AND-gate text → `COMBINATIONAL`, a sequential counter
   → `SEQUENTIAL`, a FIFO → `MEMORY`.
2. **Exhaustive coverage verdicts** (`evaluate_coverage`)
   — hits on all input combinations → `100%`, a missing combination → `0%`
   for a COMBINATIONAL design.
3. **Sequential reset-asserted evidence** (`_eval_seq_reset_asserted`)
   — a `clk=1, rst=1` sample is recognised.
4. **Deterministic targeted fix** (`targeted_fix.attempt_targeted_fix`)
   — a single wrong expected literal is repaired (testbench literal
   rewritten, `EXPECTED_VALUE_CORRECTION`), and the repair is **declined**
   for (a) multiple assertion failures and (b) non-functional failures.
5. **Deterministic coverage completion**
   (`complete_coverage._complete_combinational`) — the one missing
   input combination is appended and the block is marker-agreed.

These checks guard the pure-logic behaviours that both the AI and Vivado
stages rely on, so a structural refactor cannot silently break them.

## What it does NOT validate

- It does **not** prove hardware correctness. A passing regression run is
  a *logic self-test of the harnessed Python functions*, not an exhaustive
  simulation, not a synthesis, and not an FPGA/ASIC qualification.
- LLM output quality, Vivado behaviour, timing, and real-design coverage
  are out of scope for this harness and are covered separately by the
  fixture runs described in `VALIDATION_STATUS.md` and the audit.

## Harness structure (11 checks)

| # | Area | Check |
|---|------|-------|
| 1 | classifier | AND → COMBINATIONAL |
| 2 | classifier | counter → SEQUENTIAL |
| 3 | classifier | FIFO → MEMORY |
| 4 | coverage | exhaustive hit → 100% |
| 5 | coverage | exhaustive miss → <100% |
| 6 | coverage | reset-asserted evidence detected |
| 7 | targeted fix | single wrong expected → applied |
| 8 | targeted fix | literal corrected in TB |
| 9 | targeted fix | multiple failures → declined |
| 10 | targeted fix | non-functional failure → declined |
| 11 | completion | missing combo added (rc=0) |