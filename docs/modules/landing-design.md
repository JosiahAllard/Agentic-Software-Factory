# Landing design

Low-level design of `kitlib/landing`. The high-level design is ARCHITECTURE.md ("Contracts": Landing, What a landing runs, Owner files) and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-023, W-024, W-025).

## Contract

- **Work reaches main only by a landing**: `land.py <branch>... --expect-tip <sha>... --reviewed-by <id>... --submit`. No agent merges or pushes main.
- **A branch lands with a queued verdict.** `verdict.py add` records LAND for one tip by one reviewer who did not write the branch; a strict path needs a strong-tier reviewer.
- **The one self-review exception**: a coordinator may review its own small, low-risk diff, up to `landing.self_review_max_lines`.
- **A verdict carries** to a later tip only when a main sync is all that changed, and the carry is checked again at the landing.
- **A submitted landing is a detached process** that books its turn in the machine lock's queue. Landings into one repository merge in ticket order, each onto the main it finds at its turn; branch gates and other repositories run beside it.
- **The steps, in order**: take the landing lock; fetch; check each tip, its verdict, its gate record, its owner-only changes and its scope; merge onto `<remote>/<main>`; gate the merged tree in the landing's own tree and slot; push; verify the push; close the branch's records; clean up; post the landing to the hub.
- **What the merged gate runs**: only what the landing's work touches. Each step is picked by what the merge changes and run in its selected form, reusing a step whose inputs match the branch's record. No branch needs a full run to land.
- **A strict-path landing** lands alone, after a strong-tier review, runs the same selection and reuses nothing. It never runs the full suite; the daily full run catches the rest.
- **A landing whose diff changes a gate config file** runs every step in its full form once, on the merged tree: the one landing that runs the full suite. Its branch needs no full run beforehand.
- **A red merge is undone.** A conflict goes back to the branch's author (`CONFLICT <branch> with main <sha>`); the author merges main and the verdict is queued again.
- **A batch** lands up to `landing.batch_max` branches as one merge train, each with its own reviewer, range and verdict; never a strict branch.
- **Scope at landing**: every path a branch changes falls in a record it holds. A breach of a narrowed record refuses; otherwise it warns.
- **Owner-only changes** are judged by `ownerfiles`, on the commits, not the net blob.
- **A commit with no agent trailer warns.** It never refuses a landing.
- **After the push**: the records named by `Closes:` close, the branch's remote branch, worktree and local branch go, the load nodes' mirror of main refreshes, the hub's landing feed gets the event. A failure after the push is reported; it never undoes the landing.
- **The result is one `RESULT` line** in the result record: `pass land` with `LANDED`, or the reason (`NOT LANDED:`, `STUCK:`, `REFUSED:`, `BUSY:`, `NOT RUN:`, `GATE FAILED:`, `RERUN VOID:`). A caller waits on the record and reports the line as it is.

## Packages and files

| Path | Holds |
|---|---|
| `landing/verdicts.py`, `carry.py` | The verdict queue: add, read, mark, tiers; carrying a verdict over a main sync |
| `landing/submit.py` | The detached start, its ticket, the wait command it prints |
| `landing/lander.py` | The landing's order of steps and its state |
| `landing/checks.py` | Tip, verdict, record, owner-only and scope checks before the merge |
| `landing/merge.py`, `mergedgate.py`, `push.py` | The merge and its undo; the merged-tree gate and what it runs; push and verify |
| `landing/after.py`, `cleanup.py`, `feed.py` | Record closes and notices; branch cleanup; the hub's landing event |

## State

The verdict queue (one file per branch in the git common dir), the landing lock (through `records`), the landing's own tree, the result record (through `gate`). The landing lock is held from the merge to the push or the end.

## Errors

Every stop is a `RESULT` line with its reason and, where there is one, the recovery step. A landing that dies leaves a `running` record that the waiter finishes as `error`, and a landing lock whose holder is provably gone.

## Adding to it

A new pre-merge check: a function in `checks.py` returning a refusal or a warning, placed in the order in `lander.py`. A new after-landing action goes in `after.py` and must not be able to fail the landing.

## Tests

`tests/landing`: each check, the merge and its undo, what the merged gate runs for a plain, a gate-config and a strict landing, a batch, the queue order, a dead landing.

## Reference

docs/gate-landing.md, docs/gate-landing-policy.md, docs/landed.md, docs/allow-rules.md, docs/hub-approvals.md ("landings/L-NNN").
