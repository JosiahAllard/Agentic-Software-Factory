---
name: branch-review
description: Use as a coordinator when a builder reports its design (the design review before the build) and before giving any branch its landing verdict (the code review after it). Covers who reviews, what to look for, sending findings back, the three verdicts, follow-up records, preventing a defect class seen twice, and CI changes that only add strictness.
---

# Coordinator: design and branch review

No build without approved design, no land queue without your verdict. Checks: skill specialty-review; here: who, after.

## A. Design review (before the build)

1. Builder reports design (skill builder, step 3), or brief carried yours.
2. Low risk, no new module, class, public name, cross-package import: yourself, per specialty-review "Design review". Else fresh analyst, skill `specialty-review`, design review; tier by "Reviewer model" (roster.md); brief: record, builder's brief, design, profile **Review** design docs.
3. APPROVE: builder builds. REVISE: numbered findings; re-review revision only. Unsettled: owner, both positions fairly.

No architecture review after build: code review checks approved design.

## B. Code review (after the build)

1. Brief, approved design, exact diff range, touched-test results, log paths; `python <kit>/tools/review_pack.py -C <worktree> <branch>`.
2. Scope, yourself: `git diff --stat <merge-base>..<branch>` vs brief. File outside: finding, unless report names fold-in (skill builder, design rule 7).
3. Who reviews. Small (profile **Review**; kit's `.kit.json` `landing.self_review_max_lines`), low risk: every hunk yourself (docs, additive content: skim). Else fresh analyst, skill `specialty-review`, code review, never author, "Reviewer model" tier, after `python <kit>/tools/land.py -C <main checkout> <branch> --check-strict` (read-only; `STRICT`: strong-tier reviewer, lone landing). `verdict.py add` refuses self-reviewed Medium/High risk, and self-read diff over that size.
4. Look for: specialty-review "Code review", steps 4-7: Done-when MET/UNMET, defects, thin clean tests, copies, over-building.
5. Known-flake-list flake (`--find flakes`): one rerun; other failures real.
6. Findings to author, same agent: numbered, file, symbol, BLOCKING/FIX-HERE/NOTE. Author fixes, re-runs touched tests; re-review fix only. After verdict: author's one gate on reviewed tip; later changes, gate fixes included, reviewed before landing.
7. Folding finding (skill builder, design rule 7): fixed on branch, never filed. Follow-up: non-folding work only (record bar: skill backlog-ops step 2); `backlog.py add` warns on branch scope overlap: note in `## Found`.
8. Verdict: LAND; LAND WITH FOLLOW-UP (recorded, `--follow-up <ID>`; required on reviewer's UNMET); SEND BACK. Queue with branch, full tip, reviewed range (skill land-branch); new non-test source file or class: `--design-reviewer-id`.

## Prevent, once

- Defect class twice in honest use (not probe, reviewer's evasion form): prevent once at cause, in order: code guard (fail-fast check, test, step script); area reference doc; last, one instruction line (rule, not story; sharpen before adding). Ratchet names its rule; deleted at zero.
- CI change only adding strictness (pass-string grep, test step, assertion), approved plan item: ordinary work, High-risk review, land.

## Never

- Decide from green; review own implementation.
- Write repo instruction files, tracked host config (owner-only: propose); restate instructions or reference docs in a profile.
- Land a check removed, loosened, skipped, optional without owner's typed go.

Details: `<kit>/docs/review-pack.md`.
