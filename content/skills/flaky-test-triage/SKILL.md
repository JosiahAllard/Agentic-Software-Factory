---
name: flaky-test-triage
description: Use when a load-sensitive or timing-dependent test fails intermittently, or when you must decide whether a change made one better or worse by comparing runs. Covers paired runs and telling a dropped event from a slow one.
---

# Flaky test triage

1. Actual failing lines, repo's known-flakes list: `python <kit>/tools/doc_index.py -C <worktree> --find flaky`, profile's "Known flakes".
2. Failing case's own duration (JUnit XML `time=`, runner timing) first.
3. Expiry AT budget: event never came; satisfied late: slow work. Duration vs nominal cost: ~34x = dropped event; ~1.3x (Windows' 15.6 ms timer granularity) = genuine slowness.
4. Before a timeout change: deadline, nominal cost, polling cadence, machine load.
5. Reproduce on unchanged base too (old failure vs regression), isolated scratch worktree.
6. Branch vs base: interleaved paired runs, same window.
7. Report exact failure, timings, sample counts; shared-machine runs labelled "shared machine". Heavy repeats: machine lock (skill machine-lock-run).

## Never

- Rerun red to green: red gate gets one targeted rerun (`python <kit>/tools/gate.py -C <worktree> --rerun`; skill builder, "Tests"), no more. FLAKE: report its printed `backlog.py add` line; coordinator decides filing. REAL, or failed twice: real, never lands.
- Loosen assertion or timeout without the evidence above.
