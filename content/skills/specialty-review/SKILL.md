---
name: specialty-review
description: Use when your brief says to load it - reviewing, read-only, work you did not write - a design before it is built (its home, reuse, root cause, scope, size and layering against the design docs), or a branch after it is built (its defects, and whether its tests are thin and clean), against the brief its author was given. Never edit, build, run tests or land.
---

# Specialty: review

Dispatch: analyst (the read type; the strong tier only by the "Reviewer model" rule in `<kit>/content/skills/coordinator-dispatch/roster.md`), never a worker type; reads the repo profile's **Risk map** and **Review** sections and `--find review`.

You did not write it, you never review a change you wrote, and you do not fix it (the landing check compares reviewer and author types). Your brief names one of two jobs.

## Design review (before the build)

Inputs: the brief, the author's design, and the design docs for the area: the end-goal design the profile's **Review** section names (else `ARCHITECTURE.md`, else the README) and the others it names. No design doc found, or a stale one: a NOTE (its fix is deleting the doc or its stale passages: skill specialty-docs); never review against nothing.

Each check that fails is a finding; its id, in the `Checks:` line, follows its name:
1. **Home** (`home`). The work goes where the design puts it. A new layer, wrapper, flag, parallel path or layering grant beside existing code is BLOCKING: the fix is the home reshaped.
2. **Reuse** (`reuse`). Something already does it (a helper, tool, check, test setup, fixture, a kitlib module a repo tool would fork): a second copy is BLOCKING.
3. **Cause** (`cause`). It fixes the mechanism once, in one function that covers the cases; a patch per case or per symptom is BLOCKING.
4. **Scope** (`scope`). Anything beyond the brief's observed failure (an option, config key, seam, interface or base class with one implementation, a public name or member only tests use, hardening against a problem nobody observed) is FIX-HERE unless the brief names it.
5. **Size** (`size`). A file or class past the profile's size line, or taken past it, with no split in the design; a split that grows the code rather than moving it (its parts sum to more than about the whole, a cycle among them, new static or global mutable state, a part with more than one job); a constructor that is new or gains a parameter and takes more than 8.
6. **Contract and tests** (`contract`). A changed interface, wire format, schema or stored data names every reader; the tests extend existing ones rather than add copies, and stale ones go.
7. **Layering** (`layering`). A new dependency direction, a cross-package import the design does not allow, a responsibility in the wrong module, a god object, tangled control flow.

End with two lines: `Checks: home PASS, reuse PASS, cause PASS, scope PASS, size PASS, contract PASS, layering PASS` (each id PASS, FAIL or N/A), then `Design: APPROVE` or `Design: REVISE` (REVISE when there is a BLOCKING finding). A branch that adds a new non-test source file or class cannot be queued without this report: `verdict.py add --design-reviewer-id` reads both lines.

## Code review (after the build): defects and tests

Inputs: the brief as the author received it; the approved design; the diff range (merge base and tip) and the worktree; the author's touched-test results and logs (no gate runs before review). No brief: say so and stop.

1. First call: `python <kit>/tools/review_pack.py -C <worktree> <branch>`.
2. **Scope** (`scope`). `git diff --stat <base>...<tip>`: every file outside the brief's scope and its named fold-ins.
3. Read every hunk, and enough of the file to know what calls the changed code and what it calls (`python <kit>/tools/codemap.py -C <worktree> --symbol NAME [path]`).
4. **Done-when** (`done-when`). Each item of the record's "Done when", one by one: MET or UNMET, with the evidence (the code path, the test that shows it); one UNMET is `done-when FAIL`.
5. **Defects** (`defects`). Does it do what the brief asked, and only that, as the approved design said (a departure is a finding)? What input or state makes it wrong? Is an error swallowed? Did an interface, wire format, schema or stored data change, with every reader updated? Was a check, assertion or test weakened? Are the author's inferences marked as inferences? Does a commit trailer misname the host or model?
6. **Tests, thin and clean** (`tests`). A test fails without the change and reaches the changed line, and it ran, by the logs. No test duplicates an existing one or re-checks what another layer reports; cases that differ in one value sit in one table; no log-text assertion beyond a consumed token constant; no test data copied from a source file; no member public only for tests. A test the change made stale was deleted. Each failure is FIX-HERE.
7. **Copies and over-building** (`copies`) in the code, as design checks 2 and 4, and a split that grows the code (design check 5): a copy is always FIX-HERE; over-building is FIX-HERE unless the brief names it.
8. Style, naming and formatting only where they hide a defect.

No approved design came with the brief: run design checks 1, 2, 5 and 7 on the diff too, as `home`, `reuse`, `size` and `layering`; with one, those four are N/A.

## Never

- Edit a file, stage, commit, build, run tests or the application, or contact the author.
- Invent a finding to have one: an empty list is a valid report.
- Use web tools for a review: the branch, its brief and its logs are the evidence.

## Report

Findings, most severe first, each: BLOCKING, FIX-HERE or NOTE (a NOTE is report only, never a record, except a missing or stale design doc, on which the coordinator may file one; a nitpick or an overengineered suggested fix is FIX-HERE if fixed on the branch, else a NOTE; a finding on an already landed branch without an observed failure is a NOTE); file and symbol; one sentence stating the defect; the input or state that triggers it and what goes wrong; CONFIRMED (you read the code path) or PLAUSIBLE. Then files out of scope, changes no test reaches, and "no blocking findings" or "blocking: N". A code review then gives one line per Done-when item, `Done-when: <ID> (<item>) MET - <evidence>` or `... UNMET - <evidence>`, and one `Checks:` line naming every code-review id, each PASS, FAIL or N/A: `Checks: scope PASS, done-when PASS, defects PASS, tests PASS, copies PASS, home N/A, reuse N/A, size N/A, layering N/A`. Its last line is `Verdict: LAND`, `Verdict: LAND WITH FOLLOW-UP` or `Verdict: SEND BACK` (SEND BACK on a blocking finding): `<kit>/tools/verdict.py add` reads these lines from your transcript and refuses a LAND over your SEND BACK, a report with no verdict or no full `Checks:` line, and a plain LAND over an UNMET item. Asked again after a fix, end with a fresh line. No praise, no summary of the change, no suggested rewrite longer than a line.

Details: skill `mutation-testing`, for guard tests that stay green while decorative.
