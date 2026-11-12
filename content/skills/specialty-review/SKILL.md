---
name: specialty-review
description: Use when your brief says to load it - reviewing, read-only, work you did not write - a design before it is built (its home, reuse, root cause, scope, size and layering against the design docs), or a branch after it is built (its defects, and whether its tests are thin and clean), against the brief its author was given. Never edit, build, run tests or land.
---

# Specialty: review

Dispatch: analyst (the read type; the strong tier only by the "Reviewer model" rule in `<kit>/content/skills/coordinator-dispatch/roster.md`), never a worker type; reads the repo profile's **Risk map** and **Review** sections and `--find review`.

Never own change; never fix.

## Design review (before the build)

Inputs: brief; author's design; design docs: profile **Review** end-goal design (else `ARCHITECTURE.md`, else README) + named others. None/stale: NOTE (fix: delete doc or stale passages, skill specialty-docs); never review against nothing.

Failed check = finding; `Checks:` id after name:
1. **Home** (`home`). Where design puts it. New layer, wrapper, flag, parallel path, layering grant beside existing code: BLOCKING; fix: reshape home.
2. **Reuse** (`reuse`). Already done somewhere (helper, tool, check, test setup, fixture, kitlib module a repo tool would fork): second copy BLOCKING.
3. **Cause** (`cause`). Mechanism fixed once, one function covering cases; per-case/per-symptom patch BLOCKING.
4. **Scope** (`scope`). Beyond brief's observed failure (option, config key, seam, single-implementation interface or base class, test-only public name/member, unobserved-problem hardening): FIX-HERE unless brief names it.
5. **Size** (`size`). File/class past profile's size line, or taken past, unsplit; split growing code, not moving (parts sum above about whole, cycle, new static/global mutable state, two-job part); new/grown constructor over 8 parameters.
6. **Contract and tests** (`contract`). Changed interface, wire format, schema, stored data names every reader; tests extend existing, no copies; stale go.
7. **Layering** (`layering`). New dependency direction, disallowed cross-package import, misplaced responsibility, god object, tangled control flow.

End with `Checks: home PASS, reuse PASS, cause PASS, scope PASS, size PASS, contract PASS, layering PASS` (each PASS, FAIL or N/A), then `Design: APPROVE` or `Design: REVISE` (any BLOCKING: REVISE). Branch adding non-test source file/class needs it to queue: `verdict.py add --design-reviewer-id` reads both.

## Code review (after the build): defects and tests

Inputs: brief as author got it; approved design; diff range (merge base, tip), worktree; author's touched-test results, logs (no gate before review). No brief: say so, stop.

1. First call: `python <kit>/tools/review_pack.py -C <worktree> <branch>`.
2. **Scope** (`scope`). `git diff --stat <base>...<tip>`: each file outside brief's scope, named fold-ins.
3. Every hunk, callers, callees (`python <kit>/tools/codemap.py -C <worktree> --symbol NAME [path]`).
4. **Done-when** (`done-when`). Each record "Done when" item: MET or UNMET, evidence (code path, test); one UNMET = `done-when FAIL`.
5. **Defects** (`defects`). Brief's ask, only that, per approved design (departure = finding)? Breaking input/state? Swallowed error? Changed interface, wire format, schema, stored data: every reader updated? Weakened check, assertion, test? Inferences marked? Trailer misnames host/model?
6. **Tests, thin and clean** (`tests`). Fails without change, reaches changed line, ran (logs). No duplicate test or other layer's report; one-value variants: one table; log-text assertions only on consumed token constants; no data copied from source; no test-only public member. Stale tests deleted. Each failure FIX-HERE.
7. **Copies and over-building** (`copies`) in code, as design checks 2 and 4, plus growing split (design check 5): copy always FIX-HERE; over-building FIX-HERE unless brief names it.
8. Style, naming, formatting: only where hiding a defect.

No approved design came with the brief: run design checks 1, 2, 5 and 7 on the diff too, as `home`, `reuse`, `size` and `layering`; with one, those four are N/A.

## Never

- Edit, stage, commit, build, run tests or app, contact author.
- Invent a finding: empty list valid.
- Web tools (evidence: branch, brief, logs).

## Report

- Findings, most severe first: BLOCKING, FIX-HERE, NOTE. NOTE: report only, no record (missing/stale design doc: coordinator may file). Nitpick/overbuilt fix: FIX-HERE if fixed on branch, else NOTE; landed branch, no observed failure: NOTE.
- Each: file, symbol; one-sentence defect; trigger (input/state), effect; CONFIRMED (code path read) or PLAUSIBLE.
- Then: out-of-scope files; changes no test reaches; "no blocking findings" or "blocking: N".
- Code review: per Done-when item `Done-when: <ID> (<item>) MET - <evidence>` or `... UNMET - <evidence>`; one `Checks:` line, every code-review id PASS, FAIL or N/A: `Checks: scope PASS, done-when PASS, defects PASS, tests PASS, copies PASS, home N/A, reuse N/A, size N/A, layering N/A`.
- Last line: `Verdict: LAND`, `Verdict: LAND WITH FOLLOW-UP` or `Verdict: SEND BACK` (blocking finding). `<kit>/tools/verdict.py add` reads them from transcript; refuses LAND over your SEND BACK, no verdict, no full `Checks:` line, plain LAND over UNMET. Re-asked after fix: fresh line.
- No praise, change summary, rewrite over a line.

Details: skill `mutation-testing` (decorative green guards).
