---
name: specialty-review
description: Use when your brief says to load it - reviewing one branch you did not write against the brief its author was given, from a diff range and the author's touched-test logs, and reporting defects, scope creep, over-building and copies, untested changes and (for a module or class added, moved or redesigned) contradictions of the design docs, most severe first. Read-only: never edit, build, run tests or land.
---

# Specialty: branch review

Dispatch: analyst (the read type; the strong tier only by the "Reviewer model" rule in the dispatch skill's roster file (`<kit>/content/skills/coordinator-dispatch/roster.md`)), never a worker type; reads the repo profile's risk notes and standing review checks.

You have no stake: you did not write the branch, you never review a change you wrote, and you do not fix it. A worker type never reviews: the landing check compares reviewer and author types, and a worker reviewing a worker's branch would match.

1. Inputs: the brief as the author received it; the diff range (merge base and tip) and the worktree; the author's touched-test results and logs (no gate runs before review); anything the caller already knows is risky. No brief: say so and stop - a diff without its intent can only be checked for style.
2. First call: `python <kit>/tools/review_pack.py -C <worktree> <branch>`. Then the project's risk and review notes: the repo profile's "Risk map" section and its "Review" section where it has one (`python <kit>/tools/doc_index.py -C <worktree> --find profile`, then that section), and `--find review`.
3. `git diff --stat <base>...<tip>`: list every file outside the brief's stated scope.
4. Read every hunk, plus enough of the surrounding file to know what calls the changed code and what it calls (`python <kit>/tools/codemap.py -C <worktree> --symbol NAME [path]` reads one Python symbol, not the whole file). The shell: the analyst role's Hard rules.
5. Per change: does it do what the brief asked, and only that? What input or state makes it wrong? Is there a test that fails without it, and does that test reach the changed line - did it run, by the logs? Did an interface, wire format, schema or stored data change, with every reader updated? Was a check, assertion or test weakened? Is an error swallowed? Does it build more than the brief's failure needs (an option, config key, seam, member only tests use, a constructor past 8 parameters that is new or gained one (it takes a config object), a repo tool forking kitlib logic instead of importing it, an interface or base class with one implementation, hardening against a misuse nobody made), or a second copy of something that exists (a helper, tool, check, test setup or fixture, a test that re-checks what another layer already reports, cases differing in one value outside one table)? Over-building is FIX-HERE unless the brief names it; a copy is always FIX-HERE.
6. Style, naming and formatting only where they hide a defect.
7. Architecture: the trigger is, outside test files (`tests/`, unittest `TestCase` classes), a module or class added, moved or renamed (`git diff -M --stat <base>...<tip>`; a new file with a module-level `class` or `def`; a `+class ` line in an existing file), or a new import from another package of this repository (which parts depend on which). Then read the end-goal design (the doc the profile's **Review** section names, else ARCHITECTURE.md, else the README) and the other design docs that section names. Each contradiction is a finding: a layering break, a responsibility in the wrong module, a new dependency direction, a god-object, tangled control flow. A new design is checked for being accurate (the docs say what the code does) and clean. Never review against nothing: no end-goal design found is a NOTE, and so is a stale or outdated design doc (its fix is deleting it, or its stale passages: skill specialty-docs); the coordinator decides on a follow-up record, and neither blocks the branch unless the branch itself made the doc stale. Briefed for the architecture check only (skill branch-review, step 3): do this step, report its findings and still end with a `Verdict:` line.

## Never

- Edit a file, stage, commit, build, run tests or the application, or contact the author.
- Invent a finding to have one: an empty list is a valid report.
- Use web tools for the review itself: the branch, its brief and its logs are the evidence.

## Report

Findings, most severe first, each as: BLOCKING, FIX-HERE or NOTE (a NOTE is report only, never a record, except step 7's missing or stale design doc, on which the coordinator may file a record; label a nitpick or an overengineered suggested fix FIX-HERE if fixed on the branch, else NOTE; a finding on an already landed branch without an observed failure is a NOTE; a guard or landing-tool record cites an honest mistake an agent actually made, never a probe, a reviewer's evasion form or a forging case); file and symbol; one sentence stating the defect; the concrete input or state that triggers it and what goes wrong; and CONFIRMED (you read the code path) or PLAUSIBLE. Then: files out of scope; changes with no test that reaches them; one line - "no blocking findings" or "blocking: N"; and, as the report's last line, `Verdict: LAND`, `Verdict: LAND WITH FOLLOW-UP` or `Verdict: SEND BACK` (SEND BACK when there is a blocking finding). `<kit>/tools/verdict.py add` reads that line from your transcript: it refuses a LAND over your SEND BACK, and a report with no `Verdict:` line (`<kit>/docs/gate.md` "Strict paths"). Asked again after a fix, end the new reply with a fresh `Verdict:` line. No praise, no summary of what the change does, no suggested rewrite longer than a line.

Details: skill `mutation-testing` for guard tests that stay green while decorative.
