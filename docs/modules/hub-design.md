# Action Hub design

Low-level design of `kitlib/hubvalidate`, `kitlib/hubclient`, `kitlib/hubsite`, `kitlib/hubjobs`, `kitlib/hubbacklog`, `kitlib/hubmcp` and `kitlib/autotasks`. The high-level design is ARCHITECTURE.md ("Action Hub") and wins on a conflict.

## Contract

- **One hub server per machine** (`tools/hub.py serve`) holds what waits on the owner: questions, to-dos, approvals, story intake, the PM's inbox, published pages, the landing feed, and the lock section.
- **Two listeners.** The local one tells callers apart by token and role. The tunnel listener is the only remote door, and its only gate is the access token check: a token it refuses is not the owner.
- **Owner writes are the owner's.** Answering, marking done, approving, sending and deleting are not agent tools; the guard refuses an agent the hub's secrets and its owner verbs.
- **The split.** `hubvalidate` is the data's shapes: field tables, constants, the page check, to-do steps; pure, no I/O. `hubclient` is everything another package needs to talk to the server: settings, the token, the client, its errors, the push result words, the stale-code notice. `hubsite` is the server. Nothing below the apps layer imports `hubsite`.
- **Nothing waits on an answer.** An agent posts, goes on with other work, and is told by push when the owner acts: the server tells the session that asked, and the PM, through the host's push delivery. With no delivery the hub serves as before.
- **The hub is the owner's proxy for the backlog.** A bound ask parks its record; when the ask is answered the hub lifts the block itself (`hubjobs.unblock_conditions`).
- **Timed jobs** are modules of `hubjobs`, one thread each, run by `hubsite.jobs`. The server is handed its jobs at start; it does not import them.
- **The store** is one folder of JSON documents in the PM-data worktree, checked against `hubvalidate`'s field tables before every write, and synced by git on its own schedule.
- **Automatic tasks** (`autotasks`): the hub's schedules, run on this machine by a tick the scheduler fires.
- **A running server restarts itself** through its scheduled task once newer hub code has landed.

## Packages and files

| Path | Holds |
|---|---|
| `hubvalidate/` | Field tables and checks, `pagecheck`, `todosteps`, the API's constants |
| `hubclient/` | `settings`, `token`, `client`, `localask`, `errors`, `push` (result words), `codestale` |
| `hubsite/store.py`, `schema.py`, `storecheck.py`, `gitsync.py` | The store and its sync |
| `hubsite/api*.py` | The verbs as functions over the store, one file per family (ask, to-dos, approvals, pages, posts, log, lock, schedules, skills, read) |
| `hubsite/httpd.py`, `auth.py`, `access.py`, `tunnel.py` | The listeners and who a request is |
| `hubsite/serve*.py`, `control.py`, `selfrestart.py`, `autostart.py` | The server wired from its parts, split by responsibility; stop, restart, the sign-in task |
| `hubsite/hublog.py` | The hub log (`hub.log` in the token folder: error, note and slow lines) and the size-capped append that it and push's undelivered file share |
| `hubsite/push.py`, `jobs.py`, `derived.py`, `backlog.py`, `recordwrite.py`, `lockdoor.py`, `repoweb.py`, `skilledits.py` | Push, the job runner, views read from other tools' files, the backlog views and writes, the lock door |
| `hubsite/cli/` | The work of each `tools/hub.py` verb, which takes the tool's `out` as its one writer: `common` (what the verbs share), `usage` (the verbs' names, their usage examples and the checks of option values), `server` (serve, status, open, unpair, stop, restart), `task` (the sign-in task or Linux job, in words), `setup` (setup, validate), `asks` (questions to wait), `pages` (pages, skill-edit), `approvals`; the tool's parser is `tools/_hub_args.py` |
| `hubjobs/` | One job per file: fill watch, coordinator restart, usage crossover, session watch, store commits, store upkeep, reminders, unblock conditions, plugin refresh; `_crossover_decide.py` holds the usage crossover's decisions, a `_` part that makers() skips |
| `hubbacklog/` | The Current backlog and Completed tabs |
| `hubmcp/` | The hub's API as MCP tools: the agent's reads and writes only |
| `autotasks/` | `due`, `tick` |

## State

The hub store, in the PM-data worktree: one writer (the server) under the store's tree lock. Each job keeps its own small state file in the store folder. The server's token and the tunnel's keys are secrets the guard closes to agents.

## Errors

`hubclient.errors`: `NotRunning`, `ServeError`, `Refused`. A caller that cannot reach the hub says so and carries on; no kit tool blocks on the hub except the ones whose job is to wait. `HubError` inside the server becomes the API's error body.

## Adding to it

A new collection: its fields in `hubvalidate`, its routes in one `api<family>.py`, its MCP tools in `hubmcp` if agents use it. A new timed job: one module in `hubjobs`. A new thing another package must ask the hub: a function in `hubclient`, never an import of `hubsite`.

## Tests

`tests/hubsite`, `tests/hubjobs`, `tests/hubmcp`, `tests/hubclient`, `tests/hubvalidate`, `tests/autotasks`. A job is tested with a fake clock and fake delivery.

## Reference

docs/action-hub.md (the index of the hub's topic docs), docs/hub-mcp.md, docs/hub-push.md, docs/hub-serving.md, docs/hub-remote-access.md, docs/hub-fill.md, docs/launcher.md ("Hub restart", "Usage crossover").
