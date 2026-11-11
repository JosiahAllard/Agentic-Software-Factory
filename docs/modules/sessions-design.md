# Sessions design

Low-level design of `kitlib/launcher`, `kitlib/pmwatch`, the session starts (`pmstart`, `coordinatorstart`, `hoststart`, `agentstart`) and `kitlib/coordstate`. The high-level design is ARCHITECTURE.md ("Layers", services and apps) and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-006, W-009, W-019, W-035).

## Contract

- **The launcher starts, hands off and stops the long-running sessions**, each in its own window with a remote-control name, from a home worktree it never edits in. `sessions.json` and the launcher's state files are its data; `launcher.lookup` is the one answer to "is this session live" and "which process is it", for the backlog's offer, the hub's jobs and the session starts alike.
- **Accounts**: which account folder a launch uses, whether it is signed in, how much of its usage windows is left, which to pick next. Account folders are found here, for the launcher and the install tools.
- **A hand-off moves a session to another account** with a state note and a ready file; the session checkpoints, the successor reads the note. The hub's usage crossover drives it when an account nears its limit.
- **A start is context, not control.** Each session start prints what that kind of session needs to know: standing rules, then live checks, each check a line. A check that cannot run says "could not check"; a start never fails the session.
- **`pmstart` is the PM's checklist** and nothing else. The PM settings it reads are `config`'s; the process and session facts are `proc`'s and `launcher`'s; the hub is reached through `hubclient`.
- **`pmwatch`** is the watcher's checks over sessions, usage and notices, run by the hub's session-watch job; it keeps a heartbeat the starts read.
- **`coordinatorstart`** gives a coordinator its standing rules, the state note's tail, hub pending, and the backlog's offer. **`hoststart`** holds the checks any host's start shares. **`agentstart`** is the few lines a dispatched agent gets about its checkout, inside a time fence.
- **`coordstate`** is a coordinator's event log, appended as it works, and the state note rendered from it joined with what the repository already records. It lives outside every repository.
- A role's model comes from the host's maps (docs/modules/content-design.md); the launcher reads them and names no model itself.

## Packages and files

| Path | Holds |
|---|---|
| `launcher/start.py`, `stop.py`, `relaunch.py`, `handoff.py`, `reboot.py`, `home.py` | The verbs |
| `launcher/accounts.py`, `usage.py`, `settings.py`, `state.py`, `lookup.py`, `pipes.py`, `unlisted.py`, `host.py` | Accounts, usage lines, settings, state files, finding live sessions, pipe addresses, sessions with no entry, the fake-able outside |
| `launcher/window.py`, `linuxwin.py`, `pushdelivery.py` | The window a session runs in; push delivery over the settings |
| `pmwatch/`, `pmstart/`, `coordinatorstart/`, `hoststart/`, `agentstart/` | The watcher and the starts |
| `coordstate/log.py`, `render.py` | The event log; the state note |

## State

`sessions.json`, the launcher's state folder, each session's pipe hint, the watcher's heartbeat and state file, a coordinator's event log in the agent temp folder. One writer each; each written whole.

## Errors

`LaunchError` for a start, stop or hand-off that cannot proceed, with the reason. A stop ends only the process the launcher started, by pid and start time.

## Adding to it

A new session kind: a role file, a launcher verb if it starts differently, a start part if it needs context. A new start check: one function returning lines, added to the start that needs it, with its own "could not check" path.

## Tests

`tests/launcher`, `tests/pmwatch`, `tests/pmstart`, `tests/coordinatorstart`, `tests/hoststart`, `tests/agentstart`, `tests/coordstate`.

## Reference

docs/launcher.md, docs/pm-start.md, docs/pm-watch.md, docs/pm-first-run.md, docs/coord-state.md, docs/preflight.md ("Agent start block").
