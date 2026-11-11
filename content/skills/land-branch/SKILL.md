---
name: land-branch
description: Use as a coordinator or lander before queueing a branch for landing and after every landing. Covers verdict.py add and carry, <kit>/tools/land.py --submit, batches and folding, the RESULT verdicts and what to do on each, verifying the push, closing records, after-landing notices and cleanup. The one copy of the landing procedure.
---

# Land a branch

Each landing is one entry in the machine-lock queue, ahead of ordinary runs; it merges onto whatever main is at its turn, gates the merged tree, pushes and verifies. Submit each LAND verdict as soon as you give it; nothing aligns landings.

## 1. Queue the verdict

- `python <kit>/tools/verdict.py add -C <main checkout> --branch <branch> --tip <full sha> --verdict LAND --risk <Low|Medium|High> --reviewer <type> --reviewer-model <model> --reviewer-id <id> --author "<commit author> / <author agent type>" --range <base>..<tip> --session <session> [--follow-up <ID>] [--design-reviewer-id <id>]` (`<kit>/docs/gate-landing-policy.md` "Strict paths"). It refuses your own review of a Medium or High branch, a reviewer model below the strong tier on a High one (land.py refuses that on a strict branch too, at landing), a LAND over the reviewer's `Verdict: SEND BACK`, a review with no full `Checks:` line, a plain LAND over a Done-when item the reviewer reports UNMET, a LAND WITH FOLLOW-UP whose `--follow-up` names no open record, and a diff adding a new non-test source file or class with no `--design-reviewer-id` naming an approved design review.
- After a main sync of a reviewed branch: `python <kit>/tools/verdict.py carry -C <main checkout> --branch <branch>`. `CARRIED` moves the verdict to the new tip (conflict-free main merges only); `NOT CARRIED` means review and queue the new tip.
- The tip lands only with its one gate green on that exact tip: the gate the author ran when you asked (`gate.py -C <worktree>`). `land.py` refuses any other record and says what it needs (`<kit>/docs/gate-run.md` "The final gate"); never bounce a scoped gate it accepts.

## 2. Submit

From the main checkout, one bare background call, every flag before `--submit`:

`python <kit>/tools/land.py -C <main checkout> <branch> --expect-tip <reviewed tip> --reviewed-by <reviewer id> --wait-result --submit`

- `--reviewed-by` is the `reviewer_id` of the verdict queued at that tip (an analyst's at Medium and above, never your own session); a batch names one `--expect-tip` and one `--reviewed-by` per branch, in the same order. A missing or miscounted one is a USAGE error whose `Instead:` line is the command; no usable verdict is REFUSED.
- It prints a `REVIEW` line per verdict and a `SUBMITTED ... wait: ...` line (none: report the output), waits, then prints the landing's outcome lines, a `RESULT` line and a JSON line per branch, and exits with the landing's code. Act on the host's exit notice; never poll, loop, sleep or arm a Monitor (`<kit>/docs/gate-landing.md` "Submitting a landing", "Waiting in the same call").
- Killed before its `RESULT` (the landing goes on): start the `gate.py wait-result <branch> --tip <sha> --kind land -C <main checkout>` its `SUBMITTED` line names, in the background. `python <kit>/tools/land.py -C <main checkout> --queue` shows who holds and who waits.
- **Batches.** A landing folds the submissions queued behind it into its batch, up to `"landing".batch_max` (default 5; 1: none); one folded prints `FOLDED: ...` and that batch's outcome, and folding may land them out of ticket order. Where the profile's **Landing** section allows batches you may name ready verdicts in one submission, only branches whose landings run the same tests or tests inside another's (an owner rule: a mixed batch saves nothing; `<kit>/docs/gate-landing.md` "Batch"); a named batch is all or nothing. A strict branch and a gate-config (`.kit.json`) branch land alone (`land.py --check-strict` says which).

## 3. Read the RESULT

`RESULT <verdict> land <branch> at <tip>: <line> (<log>)`, the last branch's last:

| Verdict | Means | Do |
|---|---|---|
| `pass`, `LANDED ...` | landed and posted to the hub's Landings feed | close records, send notices (below); a feed `WARNING` changes nothing, but the landing counts as not yet reported |
| `fail`, `CONFLICT ...: the author merges main` | nothing changed | the author merges main (skill branch-sync); re-review; the author's gate on the new tip; resubmit |
| `fail`, `NOT LANDED: ...` | a red merged gate or a refused push; main untouched | send it back with the log's failing lines; a stray in the main checkout: `python <kit>/tools/stray_restore.py -C <main checkout>` |
| `refused`, `busy`, `not-run`, `usage` | the line says why | fix the cause; tell the PM only when the owner must act |
| `stuck`, `STUCK: ...` | the lock stays held for the owner | send the PM the lines, `left:` lines included, verbatim; do nothing else |
| `error` | an exception, or its process died | read `land.py --queue` and the log first, then as `refused` |

Verify whatever the verdict, read-only and on its own: `git ls-remote origin <main>` and `git merge-base --is-ancestor <tip> origin/<main>`.

## 4. After a landing

- Records: the branch's last commit names its record (`Closes: <ID>`, or `Part-of: <ID>` for a slice) and `land.py` closes or releases it. Before a record closes, what must survive moves (owner decisions, quoted and dated, to the decisions log; constraints beside the code or its doc). One still open at the pushed main: a coordinator closes it (skill backlog-ops); a lander reports it.
- Notices: take `<merge>` from `LANDED <branch> on <main> at <merge>` and `<base>` from the JSON line's `"base"`. Tell an open workstream `<W>` only when `git diff --name-only --no-renames <merge>...<W>` shares a file with `git diff --name-only --no-renames <base> <merge>`; name the merge commit (skill branch-sync). The PM hears only by the coordinator role's rule.
- Cleanup: `land.py` removes the branch, worktree and remote branch itself; what its `kept` lines name, after the push is verified: the repo's command (`--find cleanup`), else `python <kit>/tools/worktree_remove.py -C <main checkout> <worktree>`. "Filename too long" or "Permission denied": leave it, report. Absolute paths only.
- A test pinning files outside its module is mapped to them in the gate's selector (`--find gate`), or main goes red at the next landing.
- A fresh or successor session reads `land.py --queue` and the land result records first (skill session-handoff), and never resubmits a branch already queued.

## Lander

A lander (optional) submits, waits and reports for one branch or the batch its brief names: sections 2 and 3, the verify included. Section 4 (records, notices, cleanup) stays the coordinator's: the lander reports a record still open and each `kept` line. Not landed: report; the coordinator re-queues. With these additions:
- Inputs, all required (missing one: say what you need, do nothing): the branch; the reviewed tip, full sha; a verdict clearly saying to land (LAND, LAND WITH FOLLOW-UP: note where the follow-up is recorded; GO); any extra gate and its resources.
- Work from the main checkout on the main branch, its `git status` clean, never inside the worktree being removed.
- Size-check each doc both sides touched: the merged file's line count equals main's plus the branch's net lines for that file (`git diff --numstat <merge-base> <branch> -- <path>`), plus or minus 1; else stop and report.
- An extra gate: take the lock the profile's **Shared resources** names, run it (pure pass/fail: dispatch the test-runner), and release that lock before submitting: the landing takes its own locks, and one you still hold deadlocks it.
- Before submitting: `python <kit>/tools/backlog.py -C <main checkout> landable <branch>`: exit 0 LANDABLE, a `closes <ID>` or `releases <ID>` line per pull (one ending `a slice branch needs a Part-of: <ID> line` on a slice branch: report, do not submit); 2 NOT LANDABLE, 1 CANNOT TELL: report; never widen scope at landing.
- Only a flake on the repo's known-flake list (`--find flakes`) gets one rerun. Temporary files under `<scratchpad>\lander\`.
- Report: branch; old and new main hash; merge commit; test counts; extra gate result; pushed yes/no with both hashes; worktree removed yes/no; any record still open; where a follow-up is recorded; any line you had to judge.

## Never

- Force-push or push anything but main through the landing command; `git add -A`, `git commit -a` or staging what you did not change; close or amend a record by hand; `reset --hard`; `branch -D` or `--force`; `git worktree remove` or `git branch -d` from a worktree; land without a verdict or a tip that moved after review (unless `verdict.py carry` recorded it); jump the queue, override or break a lock; truncate native output through `head` (in bash without pipefail it hides a failure).
- A lander also never: edits code, scenes, tests or CI; resolves a conflict; decides a branch is ready; removes a worktree or untracked file the brief did not name; adds a flag, module or cleanup step the brief did not name.

Details: `<kit>/docs/gate-run.md` ("Commands", "Exit codes"), `<kit>/docs/gate-landing.md` ("Landing"), `<kit>/docs/gate-landing-policy.md` ("Branch cleanup"); `<kit>/docs/worktree-remove.md`.
