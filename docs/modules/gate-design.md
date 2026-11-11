# Gate design

Low-level design of `kitlib/gate`, `kitlib/loadnode` and `kitlib/dailyfull`. The high-level design is ARCHITECTURE.md ("Contracts": Gate-OK record, Result record, What a landing runs; "Testing") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-012, W-022, W-023, W-025).

## Contract

- **The gate runs a repository's steps on one commit** and records the result. Steps come from `.kit.json` "gate" (or `tools/gate.json`); with no section, from the stack the checkout shows. A configured section always wins whole.
- **A step is picked by what changed** (`globs`, `always`) and run in its selected form (`cmd`), which for the kit is the tests the change can break. Its full form (`full_cmd`) and daily form (`daily_cmd`) run only by hand, by the daily run, and at a landing that changes a gate config file.
- **A step's verdict is read from its log** by the step's pass and fail lines, never from an exit code alone. A `warn` step reports and never fails the gate.
- **Every run is booked.** The steps run in one machine-lock slot; a branch gets a budget of gate runs, and a session a cap on runs at once.
- **Docs-only changes** take the docs-only route: the steps the config names for it.
- **Reuse**: a step whose input digests (argv, tools, inputs at head and base, the change, the kit, its toolchain) all match an earlier pass is reused, on a rerun and at a landing. `--fresh` reuses nothing.
- **A red gate gets one targeted rerun** of its failing tests in a scratch tree: FLAKE lands the tip and records the flake; REAL, or a second failure of the same test, never lands.
- **The gate-OK record** is written through `records` for exactly the commit that passed. **The result record** says `running`, then the verdict, so another session can wait on it.
- **Pinned tools**: `gate.py` and `land.py` started from a kit checkout re-run themselves from an export of pushed main, so a branch cannot gate itself with its own gate.
- **The config a tree is judged by is the base's**: the merge base for a branch, main for a landing; the repo hook module is read from the same commit.
- **No gate and no landing while the stash stack holds an entry.**
- **The daily full run** is the only scheduled full-repo run: every step in its daily form, on main. A red is bisected to the first-parent commit that turned it red, and a backlog record is filed for the red it reproduced.
- **Load nodes** are other boxes the main machine may send heavy runs to: a registry, ssh, a mirror of main refreshed after each landing. A gate on a node writes the same records.
- `gate` knows nothing of verdicts, the landing queue or the backlog: `landing` is above it.

## Packages and files

| Path | Holds |
|---|---|
| `gate/steps.py`, `detect.py`, `hookrev.py` | Steps from config, selection, judging; the auto-detected gate; the hook module at one commit |
| `gate/runner.py` | The steps run in a lock slot; the branch gate and the merged-tree gate |
| `gate/reuse.py`, `rerun.py`, `result.py`, `outfile.py` | Reuse; the targeted rerun; the result record; whose run a log is |
| `gate/budget.py`, `gatecap.py`, `measure.py`, `stop.py`, `stash.py`, `caller.py`, `gitstatus.py`, `pin.py` | One concern each |
| `gate/strict_defaults.py` | The kit's default strict landing paths (owner-only) |
| `loadnode/` | `registry`, `remote`, `route`, `dispatch`, `job`, `box`, `frames`, `reply`, `mirror`, and the node's side of a gate and of a landing's gate |
| `dailyfull/` | The run, `bisect`, `file` (the record it files) |

## State

The result record, the reuse cache, the gate budget and each run's log, on the dev drive outside every checkout. The daily run's last result. The load-node registry in the PM repository.

## Errors

The verdict lines the CLI prints (the `RESULT` line, `GATE FAILED:`, `REFUSED:`, `BUSY:`, `NOT RUN:`, `RERUN VOID:`) and their exit codes are the contract with callers; a caller reports them as they are and does not retry.

## Adding to it

A new step: an entry in the repository's gate config with its globs, commands and pass line. A new auto-detected stack: a detector in `detect.py`. Anything about review, verdicts or merging belongs in `landing`.

## Tests

`tests/gate`, `tests/loadnode`, `tests/dailyfull`.

## Reference

docs/gate.md (the index of its pages), docs/gate-run.md, docs/gate-records.md, docs/gate-rerun-checkout.md, docs/daily-full.md, docs/load-nodes.md.
