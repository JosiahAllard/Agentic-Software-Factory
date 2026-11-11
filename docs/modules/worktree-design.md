# Worktree design

Low-level design of `kitlib/worktree`, `kitlib/preflight` and `kitlib/strayrestore`. The high-level design is ARCHITECTURE.md ("System"; "Layers", work) and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-010, W-021, W-022, W-037).

## Contract

- **One branch, one worktree**, under the host's worktree folder in the repository, made by `new_worktree.py` in one call from `<remote>/<main>` as fetched. It waits out a landing in flight before it fetches.
- **First-use prep** writes local settings only: the worktree folder lines in `info/exclude`, the local `core.hooksPath` when none is set. Nothing tracked, nothing committed.
- **A worktree is removed only when its work has landed and nothing holds it**, or its unlanded work is abandoned under an archive tag. What holds a worktree: a process with its directory inside, a machine-lock record naming it, the landing lock, a `git worktree lock`, an exemption, backlog records in its tree that exist nowhere else.
- **Classify** sorts every registered worktree but the main checkout into one class, read-only. **The nightly sweep** removes the landed, idle, unheld ones; a stale mark lets it abandon an unlanded one under its tag.
- **An agent removes only its own worktree.**
- **Scratch trees**: a detached throwaway tree for evidence against another commit (a red-first run, a targeted rerun). `worktree.scratch` makes one, names a leftover, and removes it.
- **The backlog's probes live here**: branch liveness and the quiet hold (`worktree.quiet`). The backlog takes them through its context.
- **Branch overlap**: which unmerged branches change files in a given set. Read-only.
- **`preflight`** is the start-of-task picture of one checkout: where it is, its base, the lock in one line, leftovers, the config in use, and the next commands with the session's own values filled in. Print-only; it starts nothing.
- **`strayrestore`** discards a main-checkout stray only when a branch tip already holds it byte for byte.
- The main checkout is read-only to agents: every write tool refuses to run in it (`git.checkout`).

## Packages and files

| Path | Holds |
|---|---|
| `worktree/new.py`, `prep.py`, `hostdirs.py` | Create; first-use prep; each host's worktree folder |
| `worktree/classify.py`, `holders.py`, `marks.py` | Classes; what holds a tree; stale marks |
| `worktree/remove*.py`, `sweep.py`, `task.py` | Remove (split: checks, the removal, abandon under a tag), the sweep, its scheduled task |
| `worktree/scratch.py`, `quiet.py`, `overlap.py` | Scratch trees; the backlog's probes; branch overlap |
| `preflight/` | The report and its lines, one file per line family; the board |
| `strayrestore/` | The one check and restore |

## State

The repository's worktrees and branches; stale marks and exemptions in the git common dir; archive tags. No other store.

## Errors

A refusal names what holds the tree and the command that shows it. A read that cannot tell (a process that cannot be read, a lock record that cannot be parsed) counts as held.

## Adding to it

A new thing that can hold a worktree: one probe in `holders.py`, read by classify, remove, sweep and the quiet hold alike. A new preflight line: one function returning lines.

## Tests

`tests/worktree`, `tests/preflight`, `tests/strayrestore`.

## Reference

docs/new-worktree.md, docs/worktree-remove.md, docs/worktree-paths.md, docs/branch-overlap.md, docs/preflight.md, docs/stray-restore.md.
