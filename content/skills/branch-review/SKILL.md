---
name: branch-review
description: Use as a coordinator when a builder reports its design (the design review before the build) and before giving any branch its landing verdict (the code review after it). Covers who reviews, what to look for, sending findings back, the three verdicts, follow-up records, preventing a defect class seen twice, and CI changes that only add strictness.
---

# Coordinator: design and branch review

No branch is built without an approved design, and none reaches the land queue without your verdict. Green proves the project still works, not that the change is right, in scope or exercised. The checks themselves are skill specialty-review's; this skill says who runs them and what follows.

## A. Design review (before the build)

1. The builder reports its design (skill builder, step 3), or your brief carried a design you wrote.
2. Low risk, and no new module, class, public name or cross-package import: review it yourself against specialty-review "Design review". Otherwise dispatch a fresh analyst with skill `specialty-review`, design review, its tier by the "Reviewer model" rule (roster.md), its brief the record, the builder's brief, the design and the design docs the profile's **Review** section names.
3. APPROVE: continue the builder to build it. REVISE: send the findings, numbered; re-review the revision only. A design disagreement you cannot settle goes to the owner, both positions stated fairly.

There is no separate architecture review after the build: the code review checks the code against the approved design.

## B. Code review (after the build)

1. Gather the brief, the approved design, the exact diff range, the author's touched-test results and log paths; `python <kit>/tools/review_pack.py -C <worktree> <branch>`.
2. Scope, yourself: `git diff --stat <merge-base>..<branch>` against the brief. A file outside it is a finding before you read a line, unless the report names it as a fold-in (skill builder, design rule 7).
3. Who reviews: small (the profile's **Review** section; the kit's `.kit.json` `landing.self_review_max_lines`) and low risk: read every hunk yourself (docs and additive content: skim). Otherwise a fresh analyst with skill `specialty-review`, code review, never the author, its tier by the "Reviewer model" rule, after `python <kit>/tools/land.py -C <main checkout> <branch> --check-strict` (read-only: `STRICT` means a strong-tier reviewer and a lone landing). `verdict.py add` refuses a Medium or High-risk verdict you reviewed yourself, and your own read of a diff over that size.
4. What to look for: specialty-review "Code review", steps 4-7: each Done-when item MET or UNMET, defects, thin and clean tests, copies and over-building.
5. Only a flake on the repo's known-flake list (`--find flakes`) gets one rerun; any other failing run is real.
6. Findings go to the author by continuing that agent: numbered, file and symbol, each BLOCKING, FIX-HERE or NOTE. It fixes them and re-runs the touched tests; you re-review the fix only. After your verdict the author runs the one gate on the reviewed tip; any change after review, gate fixes included, is reviewed before landing.
7. A finding that folds (skill builder, design rule 7) is fixed on this branch, never filed. File a follow-up only for work that does not fold and is worth doing (the record bar: skill backlog-ops step 2); `backlog.py add` files one whose scope overlaps the branch, with a warning and the overlap in `## Found`.
8. Verdict: LAND; LAND WITH FOLLOW-UP (the follow-up recorded and named with `--follow-up <ID>`; required when the reviewer reports a Done-when item UNMET); SEND BACK. Queue it with the branch, its full tip and the reviewed range (skill land-branch); a branch adding a new non-test source file or class also names its design review with `--design-reviewer-id`.

## Prevent, once

- A defect class seen twice in honest use (never a probe or a reviewer's evasion form) is prevented once, at its cause, in this order: a guard in code (a fail-fast check, a test, a script that does the step); else the reference doc owning the area; only then one instruction line, a rule not a story, sharpening a line already there before adding one. A ratchet names the rule it enforces and is deleted when its list reaches zero.
- A CI change that only adds strictness (a pass-string grep, a test step, an assertion) for an approved plan item is ordinary work: review it as High risk and land it.

## Never

- Decide from green alone, or review your own implementation.
- Write the repo's instruction files or tracked host config: owner-only; propose. Restate in a profile what the instructions or a reference doc already say.
- Land a check removed, loosened, skipped or made optional without the owner's typed go.

Details: `<kit>/docs/review-pack.md`; the coordinator role file, "The owner".
