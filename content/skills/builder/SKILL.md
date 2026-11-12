---
name: builder
description: Use when you build or fix on a branch - as worker, senior-worker, strong-worker, deep-worker or topic. Covers the design-first flow, where code goes, root-cause fixes, tests, commits, test runs and the gate, fold-ins, the report, bulk work to the helper, and what you never do.
---

# Builder

Caller: coordinator (deep-worker: requester; topic: PM). Kit rules: kit header.

## Flow

1. **Start**: `python <kit>/tools/preflight.py -C <worktree>`; whole brief; profile: brief's sections + **Branches and worktrees**, **Build and test**, **Shared resources that must be serialised** (none: `<kit>/content/default-profile.md` + README). Gap: ask. Engineering question: `consultant-<repo>`.
2. **Read the design**: profile **Review** design doc (else `ARCHITECTURE.md`, README), area section; code to change (`codemap.py --symbol NAME [path]`). None: say so.
3. **Design**, ≤15 lines: home + design section; contract (in, out, errors); deletes; tests; files; split/new class: parts, jobs, deps, size. `python <kit>/tools/backlog.py -C <worktree> set <ID> --scope <path> [--scope <path>]`. Stop, report; build on approval. Brief has approved design: build.
4. **Build**: approved design only. Misfit, or cause unclear after one try: stop, report.
5. **Test**: touched tests (Tests).
6. **Commit**.
7. **Report**.
8. **Review and gate**: fix findings, rerun touched tests, gate when asked.

## Design rules

1. Fit: design's home; reshape it in design. No new layer, wrapper, flag, parallel path. No home: ask.
2. Reuse first: search (`codemap.py`, `doc_index.py --tools`, test-support, data files); extend. Copy = defect.
3. Root cause: mechanism + disproving observation. One fix, one function, all cases. "Observed" vs "expect".
4. Observed only: no unrequested option, config, hook, seam, abstraction, test-only member, speculative hardening. Least machinery, not fewest lines.
5. No god classes: past profile size line (~1,500 lines): split first. Split = move, no growth, no cycles, no new global state. Constructor ≤8 params, else config object.
6. Facts in data files; logic in kitlib (fork only if needed, say why).
7. **Clean up as you go**: widen scope (`set <ID> --scope`). Fold-in: same job/module, no other pulled record, no owner decision, <600 lines, <10 files. Bigger: ask. Rest: report (skill backlog-ops step 2).
8. Scope: else untouched. Docs with code, same branch; cite sections by title; contradictions: report (`<kit>/content/skills/specialty-docs/doc-writing.md`).
9. Shared resources: as assigned; unassigned collision: ask. Heavy: machine lock (skill machine-lock-run).

## Tests

- One test per behaviour; extend, don't add. Cheapest layer. No re-checking other layers.
- Thin: state/return value. Log text only for named-constant tokens. Shared doubles in test-support. Own test data. Port 0.
- **Delete stale tests**; commit names what covers it now.
- Red first: `python <kit>/tools/redfirst.py -C <worktree> --tests FILE[::FILTER]` (`<kit>/docs/redfirst.md`); never revert tracked files.
- Building: touched tests only, `python <kit>/tools/kit_tests.py -C <worktree> --select`; not a gate. Filtered (`gradlew test --tests`, `dotnet test --filter`) sent to lock: `--kind quick-test --expect <estimate>` (skill machine-lock-run step 4); `full suite: ...`: machine lock.
- Gate: once, after review, when asked: `python <kit>/tools/gate.py -C <worktree>` to a log; read it (`<kit>/docs/gate-run.md` "The final gate"; waiting: `<kit>/docs/gate-records.md` "Waiting on a gate: who arms what"; own gate: no Monitor). ≤5 runs, then ask. Red: one `python <kit>/tools/gate.py -C <worktree> --rerun`; FLAKE: lands, report its `backlog.py add` line; REAL/twice: fix, never lands. Base moved: skill branch-sync.

## Commit

`python <kit>/tools/commit.py -C <worktree> -m "<what and why>" --model "<model name>" <path> ...`. Explicit literal paths; small; per milestone, before revert/stash/checkout. Model unknown: say so; never amend trailers. Slices: `Part-of: <ID>`; final slice: `Closes: <ID>`. State note: scratch. Owner-only path: approval (skill hub-ops, step 5).

## Report

Answer first. Confirmed vs inferred; branch, commits; test lines per claim (gate only if asked); fold-ins; out-of-scope. Done-when: MET/UNMET + evidence. No narrative.

## Dispatching a subagent

Brief: role, scope, worktree, branch, resources, validation. Writer in your worktree, never host checkout. Planner may lack writes. Before resuming: check what it left running. No cross-host assumptions.

## Bulk work to the helper

Only if `<kit>/docs/ROSTER.md` light tier is cheaper; costs ~15-25k tokens.
- Read: >1,000-line file, 20+ file search, tree listing.
- Write: one mechanical change, 10+ files or 100+ lines; scripts stay yours.
- Brief: worktree, files, exact change; hands off meanwhile. Re-read cited lines, read diff, test, commit yourself.

## Never

- Land, merge to main, push, force-push, rewrite history; `reset --hard`, `git clean`.
- Redo a refused sync, `REFUSED:`, guard/hook block another way (`git merge --no-commit` + commit.py): report exact command + message.
- Weaken tests/checks; rerun failures beyond the one targeted rerun.
- Self-review as independent; follow a contradicted theory.
- Write the owner's clear-session marker.

Details: skill shell-and-worktree-traps; `<kit>/docs/gate.md`.
