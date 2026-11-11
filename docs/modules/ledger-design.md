# Ledger design

Low-level design of `kitlib/ledger` and `kitlib/pending`. The high-level design is ARCHITECTURE.md ("Data ownership") and wins on a conflict.

## Contract

- **The rulings ledger is the record of the owner's decisions**, append-only, written only by the PM through `ledger.py`. An entry may supersede an earlier one; nothing is edited in place.
- **The rulebooks are the standing rules**: one file per repository and one shared, in the `rules/` folder beside the log. `ledger.py rules --repo <name>` prints the rules that bind a repository; a design that would change one is the owner's call.
- **Where the ledger lives comes only from the machine config.** Nothing overrides it, not `.kit.json` and not env. The guard denies an agent's edit of it; the ledger's own path checks run before every read and write as a second line.
- **One reader** (`ledger.read`): every consumer of entries reads through it.
- **One writer at a time**: a write holds the ledger's write lock; a busy lock is a refusal, not a wait without end.
- **No ruling id leaves the ledger.** Tracked files, commit messages and hub text paraphrase a ruling; the scrub fails an id in a tracked file.
- **The pending-steps file** (`pending`) is the owner's flat numbered list of hand steps, edited by the PM under the same kind of write lock.
- `ledger` commits its own file through git with the trailers `attribution` gives; it does not import `commit`.

## Packages and files

| Path | Holds |
|---|---|
| `ledger/files.py`, `safepath.py` | Which file holds what, from the machine config; the path checks |
| `ledger/fmt.py`, `read.py`, `write.py` | The format; the one reader; add, retire, show, grep |
| `ledger/rulebook.py`, `standing.py` | The rulebooks; the standing digest |
| `ledger/audit.py`, `precommit.py`, `vcs.py` | The whole-ledger check; the opt-in pre-commit check; commit and push of the ledger file |
| `pending/` | The pending-steps file |

## State

The ledger files and rulebooks, in the folder the machine config names (the PM-data worktree on this machine). Append-only, checked by `ledger.py check`.

## Errors

`Refused` and `Usage`, mapped to exit codes by the CLI. A path check that fails refuses before any read.

## Adding to it

A new standing rule: an entry with its tag, through `ledger.py`; never a hand edit. A new consumer reads through `ledger.read` or `ledger.rulebook`.

## Tests

`tests/ledger`, `tests/pending`.

## Reference

docs/ledger.md, docs/pending.md, docs/engineering-rulings.md.
