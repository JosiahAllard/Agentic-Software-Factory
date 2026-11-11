# Commit design

Low-level design of `kitlib/commit`, `kitlib/syncbase` and `kitlib/reviewpack`. The high-level design is ARCHITECTURE.md ("Contracts": Trailers, Owner files, Backlog claims) and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-011, W-015, W-027, W-029).

## Contract

- **`commit.py` is how an agent commits**: explicit paths, one call, the right trailers. A plain `git commit` by an agent is refused by the guard.
- **Before it commits, it checks**: not in a main checkout; the paths are named explicitly (no folder, glob or `-A`); nothing else is staged; no machine path in the message or a staged file; no path in another record's narrowed part; no owner-only path unless the owner authored the commit (`ownerfiles`). A refusal leaves nothing staged that the call staged.
- **The commit records only what the call named**, rendered or let a merge carry: it is made from a private index, so a file another writer stages meanwhile is not swept in. A HEAD that moved during the call refuses, and this call's own commit on a moved HEAD is undone.
- **It renders** when a render source is named in a kit checkout, and commits the rendered files with their source.
- **After the commit** it prints the hash, the files, and the doc budget and doc index verdicts.
- **Trailers**: the agent trailer, the model, the session, `Closes:` where given; `attribution` holds their form. A commit with no Co-Authored-By line is warned about later, never refused.
- **A merge commit** is accepted when it is a sync merge: staged paths equal the clean merge of its parents. An owner-only path rides along under the owner-files merge rule.
- **`sync_base.py` merges exactly the main commit the coordinator named** into a branch and checks the result. Never a rebase, never a local main, never a force-push. It waits out a landing in flight.
- **The review pack** is everything a reviewer or lander needs about a branch in one read-only call: the diff range, the commits and their trailers, owner-only changes, the gate record, doc checks, and quality signals (size, growth, import cycles among changed files, static state). It judges nothing; the reviewer does.

## Packages and files

| Path | Holds |
|---|---|
| `commit/run.py` | The call: order of checks, stage, commit, put back on a refusal |
| `commit/checks.py`, `message.py` | The pre-write checks, the sync-merge rule among them; the message and trailers |
| `syncbase/` | The named merge and its checks |
| `reviewpack/` | The pack, `quality`, `codegraph`, `codesignals`, `scorecard` |

## State

None. A commit is git's.

## Errors

`refused:` with the reason and what to do instead; exit 1 with nothing written, exit 2 for usage. A refusal after staging unstages what the call staged.

## Adding to it

A new commit check: a function in `commit/checks.py` returning a reason, placed in the order in `run.py`, with a test that it writes nothing. A new quality signal: a function in `reviewpack/quality.py`; it informs, it never blocks.

## Tests

`tests/commit`, `tests/syncbase`, `tests/reviewpack`.

## Reference

docs/commit.md, docs/sync-base.md, docs/review-pack.md, docs/model-ab.md ("Branch quality scorecard").
