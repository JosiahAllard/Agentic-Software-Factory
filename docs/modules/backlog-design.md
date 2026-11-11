# Backlog design

Low-level design of `kitlib/backlog`. The high-level design is ARCHITECTURE.md ("Contracts": Backlog claims) and wins on a conflict.

## Contract

- **Work is a record**: an id, a summary, a scope, a priority, a state. `backlog.py` is the only writer; every write is one commit to the store.
- **A scope is a claim.** A scope member is `none`, a literal path, or a path with a `/**` suffix. Two live pulls never overlap; a comparison that cannot be decided counts as an overlap.
- **A write verb is a plan**: `plan_<verb>(snapshot, context, ...)` returns a `Change` or raises one of the four stops. Planning reads; applying writes. A stop means nothing was written.
- **`add` never refuses an overlap.** It files the record, warns, and writes the overlap into the record's Found section. The pull-time guard holds such a record until the branch it overlaps lands.
- **`pull` narrows.** It narrows the pulled record and, when the in-flight record lists planned files, that one too, in one write, protecting the in-flight side's diff, dirty files and planned files, so claims stay disjoint. `commit.py` refuses a commit touching the other side's narrowed part (a move is judged whole); a landing refuses a scope breach of a narrowed record. Narrowing never ships without both refusals. `next` prints RESTORE once the other side lands.
- **A blocked worker** reports the file it needs and the record that holds it, and blocks on that record.
- **Work waiting on the owner is parked**: its claim is released and its record blocked on a hub item, so it never blocks other work from starting. Every owner block cites a hub item, and may carry an end condition (`until`) that the hub checks and lifts itself.
- **A pull is released only by its holder**, or as quiet: no process in the branch's worktree, no lock on it, no landing or queued verdict for it, and no activity for the quiet bar.
- **Who may run which verb** is `backlog/roles.py`, the owner's map; `backlog.policy` sorts a command into its rows and refuses a role that may not take one. The guard applies the same map to a command line; a script or unreadable line that names `backlog.py` is denied outright.
- **Writes that carry risk are audited**: one line per write in the audit log, checked against the store's history.
- **Facts from above come in through `plan.Context`.** Branch liveness and the quiet hold are probes from `worktree`; the opener requires them and has no default. The backlog imports nothing above itself.
- A record closes at its branch's landing (`at_landing`); a builder names it in a `Closes:` trailer and never closes it by hand.

## Packages and files

| Path | Holds |
|---|---|
| `record.py`, `scope.py`, `conditions.py` | One record: parse, schema, render; the scope language; a hold's end condition |
| `store*.py`, `removed_cache.py` | Where records live and how a write reaches them, split by backend (ref, branch, folder) and by read and write |
| `plan*.py` | The write verbs as plans, split by family: add and set, pull and release, block, close, the narrowing rules |
| `offer.py`, `landable.py`, `stale.py`, `find.py`, `check.py` | The offer and its rank; may a branch land; stale records; find; the named checks |
| `policy.py`, `roles.py` | Who may run what; the owner's map (owner-only) |
| `opener.py` | Open the configured store and map a verb to its plan |
| `at_landing.py`, `narrowed.py`, `pull_branch.py` | The store at a landing; the narrowed-record check `commit.py` runs; the main-branch rule for `pull` |
| `audit.py`, `audit_check.py`, `ledger.py`, `errors.py`, `retarget.py` | The audit log and its check; rulings for readers; the four stops; retarget a moved path |

## State

The configured store: a ref, a branch, or a folder in the PM-data worktree. Writes are compare-and-swap on the store's tip: a writer that lost the race replans on the new tip. The audit log is append-only.

## Errors

The four stops in `errors.py`, each with its CLI label and exit code: `Refusal` (a rule forbids the write), `Usage` (the command is wrong), `NotFound` (no such record; `Ended` when a commit ended it) and `Failure` (the store could not be read, git failed, or the write lost every race it was allowed; its kinds name a busy, dirty, overtaken or uncommitted store).

## Adding to it

A new verb: a `plan_<verb>` returning a `Change`, a row in the roles map (the owner's), its policy sort, its test. A new fact the backlog needs from a higher package: a probe on `plan.Context`, supplied by the caller.

## Tests

`tests/backlog`: every plan against a snapshot, the scope language, the narrowing rules on both sides, the policy against the roles map.

## Reference

docs/backlog.md, docs/backlog-store.md, docs/guard-jail-roles.md ("Backlog roles"), docs/maint-lint.md ("Backlog checks").
