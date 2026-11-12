---
name: land-branch
description: Use as a coordinator or lander before queueing a branch for landing and after every landing. Covers verdict.py add and carry, <kit>/tools/land.py --submit, batches and folding, the RESULT verdicts and what to do on each, verifying the push, closing records, after-landing notices and cleanup. The one copy of the landing procedure.
---

# Land a branch

Landing: one machine-lock queue entry, before ordinary runs; merge onto main, gate, push, verify. Submit each LAND at once.

## 1. Queue the verdict

- `python <kit>/tools/verdict.py add -C <main checkout> --branch <branch> --tip <full sha> --verdict LAND --risk <Low|Medium|High> --reviewer <type> --reviewer-model <model> --reviewer-id <id> --author "<commit author> / <author agent type>" --range <base>..<tip> --session <session> [--follow-up <ID>] [--design-reviewer-id <id>]` (`<kit>/docs/gate-landing-policy.md` "Strict paths"). Refuses: own review at Medium/High; sub-strong reviewer at High; LAND over `Verdict: SEND BACK`; no full `Checks:` line; plain LAND over UNMET Done-when; `--follow-up` with no open record; new non-test source file/class, no `--design-reviewer-id`.
- Main merged into reviewed branch: `python <kit>/tools/verdict.py carry -C <main checkout> --branch <branch>`. `CARRIED`: clean merge, verdict moves. `NOT CARRIED`: re-review, re-queue.
- Needs its gate green on that tip (author's `gate.py -C <worktree>`); `land.py` says what it lacks (`<kit>/docs/gate-run.md` "The final gate"). Accepted scoped gate: never bounce.

## 2. Submit

Main checkout; one bare background call; flags before `--submit`:

`python <kit>/tools/land.py -C <main checkout> <branch> --expect-tip <reviewed tip> --reviewed-by <reviewer id> --wait-result --submit`

- `--reviewed-by`: verdict's `reviewer_id` (Medium+: analyst's). Batch: `--expect-tip` + `--reviewed-by` per branch, same order; miscount: USAGE + `Instead:`; no verdict: REFUSED.
- Prints `REVIEW` lines, `SUBMITTED ... wait: ...` (missing: report), `RESULT`, JSON per branch; exit = landing's. Wait for host exit notice; no poll, sleep, Monitor (`<kit>/docs/gate-landing.md` "Submitting a landing", "Waiting in the same call").
- Killed before `RESULT`: background `gate.py wait-result <branch> --tip <sha> --kind land -C <main checkout>` from `SUBMITTED`. Queue: `python <kit>/tools/land.py -C <main checkout> --queue`.
- **Batches.** Queued submissions fold up to `"landing".batch_max` (default 5; 1: off); `FOLDED: ...`; may reorder. Profile **Landing** allows: several verdicts, same-tests or nested-tests branches only (`<kit>/docs/gate-landing.md` "Batch"); all or nothing. Strict, `.kit.json` branches: alone (`land.py --check-strict`).

## 3. Read the RESULT

Last line: `RESULT <verdict> land <branch> at <tip>: <line> (<log>)`.

| Verdict | Means | Do |
|---|---|---|
| `pass`, `LANDED ...` | landed, on hub feed | records, notices; feed `WARNING`: unreported |
| `fail`, `CONFLICT ...: the author merges main` | no change | author merges main (skill branch-sync), re-review, gate, resubmit |
| `fail`, `NOT LANDED: ...` | red gate/refused push; main untouched | send back with failing lines; main stray: `python <kit>/tools/stray_restore.py -C <main checkout>` |
| `refused`, `busy`, `not-run`, `usage` | line says why | fix; PM only if owner must act |
| `stuck`, `STUCK: ...` | lock held for owner | PM gets lines verbatim, `left:` too |
| `error` | exception/dead process | `land.py --queue` + log; then as `refused` |

Verify separately, read-only: `git ls-remote origin <main>`; `git merge-base --is-ancestor <tip> origin/<main>`.

## 4. After a landing

- Records: last commit `Closes: <ID>` (slice: `Part-of: <ID>`); `land.py` closes/releases. First: owner decisions (quoted, dated) to decisions log; constraints beside code. Still open on main: coordinator closes (skill backlog-ops); lander reports.
- Notices: `<merge>` from `LANDED <branch> on <main> at <merge>`, `<base>` from JSON `"base"`. Tell workstream `<W>` only if `git diff --name-only --no-renames <merge>...<W>` and `git diff --name-only --no-renames <base> <merge>` share a file; name the merge (skill branch-sync). PM: coordinator role's rule.
- Cleanup: `land.py` removes branch, worktree, remote branch. `kept` lines, push verified: repo command (`--find cleanup`), else `python <kit>/tools/worktree_remove.py -C <main checkout> <worktree>`. "Filename too long"/"Permission denied": leave, report.
- Test pinning files outside its module: map in gate selector (`--find gate`), else next landing reds.
- New session: `land.py --queue`, record land results (skill session-handoff); never resubmit queued.

## Lander

Optional: sections 2-3 + verify for its branch/batch; section 4 is coordinator's (lander reports open records, `kept` lines). Not landed: report; coordinator re-queues.
- Inputs (missing: say so, stop): branch; reviewed full sha; verdict (LAND; LAND WITH FOLLOW-UP + where recorded; GO); extra gate + resources.
- Main checkout, main branch, `git status` clean; never inside the worktree being removed.
- Docs touched both sides: merged lines = main's + branch net (`git diff --numstat <merge-base> <branch> -- <path>`) ±1, else stop, report.
- Extra gate: profile **Shared resources** lock, run (test-runner), release before submit (else deadlock).
- Pre-submit: `python <kit>/tools/backlog.py -C <main checkout> landable <branch>`. 0 LANDABLE, `closes <ID>`/`releases <ID>` per pull (`a slice branch needs a Part-of: <ID> line`: report, don't submit); 2 NOT LANDABLE, 1 CANNOT TELL: report. Never widen scope.
- One rerun, known flake only (`--find flakes`). Temp: `<scratchpad>\lander\`.
- Report: branch; old/new main; merge commit; test counts; extra gate; pushed + hashes; worktree removed; open records; follow-up location; judged lines.

## Never

- Force-push; push other than main via landing; `git add -A`, `git commit -a`, others' changes; hand-close/amend records; `reset --hard`; `branch -D`/`--force`; `git worktree remove`/`git branch -d` from a worktree; land without verdict or on moved tip (unless `verdict.py carry`); jump queue, break a lock; `head` on native output (hides failure).
- Lander also never: edits code, scenes, tests, CI; resolves conflicts; judges readiness; removes unnamed worktrees/files; adds unbriefed flags, modules, cleanup.

Details: `<kit>/docs/gate-run.md` ("Commands", "Exit codes"), `<kit>/docs/gate-landing.md` ("Landing"), `<kit>/docs/gate-landing-policy.md` ("Branch cleanup"); `<kit>/docs/worktree-remove.md`.
