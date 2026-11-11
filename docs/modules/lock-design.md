# Machine lock design

Low-level design of `kitlib/lock`, `kitlib/lockui` and `kitlib/lockmcp`. The high-level design is ARCHITECTURE.md ("Contracts": Booking, Kinds; "Scheduler UI") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-017, W-037, W-040).

## Contract

- **One lock per machine.** The machine is divided into cpu slots; a run books slots by its kind and holds them until it ends. One first-come queue serves every run that waits; a landing books its turn in the same queue.
- **`lock.run.book(kind)` returns a slot; `slot()` hands back a launcher.** Every heavy child starts through it inside the lock's job: pinned cores, memory measured, ended as a tree. Acquisition fails closed. There is no unlocked fallback.
- **Held means provable.** A live run in the store whose process is an ancestor of the caller, shown by the `MACHINE_LOCK_RUN=<run>:<pid>:<ctime>` token that `nest_check` verifies. An env flag alone proves nothing.
- **Kinds** come from one table (`lock/kinds.json`, read through `config.policy`), plus a repository's own `.kit.json` `kinds`; a kit kind wins a name clash. Sizes change only by a reviewed commit of a measured proposal.
- **`lock.api` is the one API**: read snapshots, a revision number, validated owner writes, versioned and transport-neutral. Every UI uses only it. The status text is rendered from `api.snapshot()` alone.
- **Agents read; the owner writes.** Holds an owner reserves, the division of slots and game mode change only from the schedule window or an outside terminal. The lock MCP server ships read tools only. `lock` owns the names of its owner-only tools, which the guard reads.
- **The owner is never pre-empted, and neither is a holder.** A stale run (its sessions gone, far past its guide) is asked to stop, not killed silently.
- Runs may give back slots they no longer need; a background tier uses what nothing else wants and pauses for anything that does.
- `lock` knows nothing of gates, landings or the hub: they book through it.

## Packages and files

| Path | Holds |
|---|---|
| `lock/store.py` | The store: a folder of JSON records under named mutexes (flocks on Linux), each run's liveness mutex |
| `lock/layout.py`, `kinds.py`, `site.py` | The machine and its slots; the kinds table; the machine's lock settings |
| `lock/queue.py`, `holds.py` | The queue; holds (owner, agent, game) |
| `lock/run.py`, `run_wait.py`, `run_held.py`, `run_end.py`, `run_cmdline.py` | A run under the lock, split by responsibility: booking (`run`), the wait in the queue (`run_wait`), the held run and its launcher (`run_held`), ending and cleanup (`run_end`), the command line a run starts (`run_cmdline`) |
| `lock/giveback.py`, `background.py`, `bg_client.py`, `game.py`, `stale.py`, `notice.py`, `jobset.py`, `detach.py`, `release.py` | One concern each |
| `lock/api.py`, `status.py` | The API; the status text |
| `lockui/` | The schedule window's model, actions and tkinter view |
| `lockmcp/` | The MCP server: read tools over `lock.api` |

## State

The lock store, in the machine's lock folder: runs, holds, the queue, history. Every read and write happens under the store's named mutex; a run's liveness mutex is held by the run's process and dropped by the kernel when it dies. `slot()` takes its mutex on the thread that releases it.

## Errors

`LockError` with the exit codes the CLI prints (`EXIT_BUSY`, `EXIT_RESERVED`, `EXIT_TIMEOUT`, `EXIT_HOLD`, `EXIT_REQUIRE`, `EXIT_USAGE`, `EXIT_FAIL`). A caller reports a busy or reserved refusal and does not retry round it.

## Adding to it

A new kind: an entry in the kinds table, sized by `gate.py --measure`. A new UI: an adapter over `lock.api`. A new owner write: validated in `lock.api`, reachable only from the window and the outside-terminal CLI.

## Tests

`tests/lock`, `tests/lockui`, `tests/lockmcp`. Concurrency tests run real processes against a temp store.

## Reference

docs/lock-api.md, docs/lock-mcp.md, docs/lock-release.md, docs/load-nodes.md ("Lock runs on a node").
