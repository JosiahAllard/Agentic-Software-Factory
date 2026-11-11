# Records design

Low-level design of `kitlib/records` and `kitlib/landed`. The high-level design is ARCHITECTURE.md ("Contracts": Gate-OK record; "Data ownership") and wins on a conflict.

## Contract

- **`records` owns the format, the place and the checking** of a repository's records that live outside its checkouts. The packages that produce them (`gate`, `landing`) write through it; everyone else reads through it.
- **Where records live** is decided once here: under the dev drive's kit folder, keyed by repository, or in the git common dir when the machine has no dev drive. Every writer and reader takes the same answer.
- **Gate-OK record**: the gate's word that it passed exactly one commit. `records.gate_ok` writes it, reads it and verifies it against a commit (`verify`): the pre-push check, a landing and a worktree's classification all ask here.
- **Landing lock**: one per repository, held from a landing's merge to its push or its end. It names the branch being landed.
- **In flight**: is a landing in flight for this repository, and waiting for it to end. `new_worktree` and `sync_base` ask before they fetch.
- **Verdict-queue entry**: where a branch's entry is, how it reads, and whether it has expired against main. The queue's writes are `landing`'s.
- **`landed.look(root, sha)`**: has this commit landed. One read-only look, never a wait.
- `records` imports the facts layer only. It knows nothing of how a gate runs or a landing proceeds.

## Packages and files

| Path | Holds |
|---|---|
| `records/place.py` | The kit folder, the repo key, the log base: where each record and log goes |
| `records/gate_ok.py` | The gate-OK record: write, read, verify |
| `records/landing.py` | The landing lock |
| `records/inflight.py` | Landing in flight |
| `records/verdict_entry.py` | A verdict-queue entry: path, parse, expired |
| `landed/` | `look` |

## State

Files only, each written whole (temp file, then replace). The landing lock is a file whose holder is proved by a live process.

## Errors

A record that cannot be read is "cannot tell", and every caller treats that as no: a push is refused, a landing stops, a pull stays held.

## Adding to it

A new record that more than one package reads: its format and place go here, with its writer above. A record one package alone reads stays in that package.

## Tests

`tests/records`, `tests/landed`.

## Reference

docs/gate-records.md, docs/landed.md, docs/new-worktree.md ("Waiting for a landing in flight").
