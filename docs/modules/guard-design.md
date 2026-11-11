# Guard design

Low-level design of `kitlib/guard`, `kitlib/hookrun` and `kitlib/agentview`. The high-level design is ARCHITECTURE.md ("Contracts": Guard rules, Roles, Hook entry; "Security") and wins on a conflict.

## Contract

- **Every guarded tool call of every agent passes the guard**, on every host, as one neutral `Action`. The guard reads real state and never writes it.
- **It never answers allow.** A call is denied, asked about (deny on a host with no ask), warned about, or passed in silence. A crash, a payload it cannot read or a state it cannot place fails closed.
- **A refusal names the command to use instead**, with kit paths filled in, and is written to the refusal log.
- **Command lines are read as commands.** Bash, PowerShell and cmd are tokenized into what runs; the rules judge that. A line the parser cannot read goes to the fallback's patterns, and the kept rules still judge its text.
- **A rule's mode** is deny, warn or off: the kit's defaults, overlaid by the machine config's `guard_rules`, and by nothing a branch carries.
- **Always deny**: an agent's edit in a main checkout; a write outside the session's jail or off the dev drive; destructive git in a main checkout, `branch -D`, forcing or deleting main, a push to main without a record; an edit of an owner-only path, `.kit.json`, the ledger, the machine config, the lock's state, PM data, a gate result or a generated file; the hub's secrets and owner verbs; the owner's lock tools and delegation list; the owner's identity; killing by name; a raw backlog write.
- **Role bypass by script is closed in the guard**: a script, interpreter or unreadable line that names `backlog.py` is denied. No hook rewrites a subagent's command to carry its role; that is reopened only if a live write gets past the deny.
- **A raw heavy program outside the lock is a warning** by default: lock etiquette, not a wall.
- **Role policy** is the same on every host: what each role's mode may edit, read-only owner paths, the shell-cwd check, the lock-verb allowlist. One switch per repository turns it on.
- **The pre-push check** refuses a push to main without a gate-OK record where the repository blocks pushes, a delete or non-fast-forward of main always, and an owner-only change the owner neither authored nor approved (`ownerfiles`).
- **`hookrun`** is the one hook runner behind `boot/hook.py`: it builds the `Action` through the host's adapter, calls the guard, renders the `Decision`, and for a start event gives the kit header and the role's start text. A session-start self-test alarms when a host stops sending the subagent identity.
- **`agentview`** prints everything one role is given and held to on one host: its file, its skills, its header, its policy.
- The guard imports nothing from the apps layer, and nothing below it imports the guard.

## Packages and files

| Path | Holds |
|---|---|
| `guard/shell.py`, `commands*.py`, `fallback.py` | Reading a command line; what a parsed command runs, split by tool family; the fallback |
| `guard/context.py`, `place.py`, `paths.py` | The real state the rules read, behind one fake-able object; where a path is, without starting git |
| `guard/rules.py`, `rules_*.py`, `modes.py` | The rules by family (base, edit, write, run, net, tools, owner, push, and the git families); rule modes |
| `guard/jail.py`, `rolepolicy.py`, `backlogrole.py`, `gaterole.py`, `delegfile.py`, `generated.py`, `hub.py` | The write jail; role policy; the backlog roles; gate allow; the delegation list; generated files; what the hub rules know |
| `guard/prepush.py`, `refusal_log.py` | The pre-push check; the refusal log |
| `hookrun/main.py`, `header.py`, `headerfollow.py` | The runner and hook command lines; the kit header; the header after a plugin refresh |
| `agentview/` | The role view |

## State

The refusal log, one JSON line per deny, in the PM-data worktree. Nothing else: every other fact is read fresh for each call.

## Errors

There is one outcome for anything unexpected: deny, with the reason. The hook entry itself fails closed when the kit cannot boot.

## Adding to it

A new rule: one function in the family file it belongs to, a row in the guard's test table for the call it refuses and the nearest call it lets through, and its mode when it is not deny. A rule reads state through `Ctx`, so a test hands it a fake machine. A new tool to guard: its name in the host's `hosts.json` `guarded_tools`.

## Tests

`tests/guard` (the decision table over recorded command lines, each rule family, the parser, the fallback's known limits), `tests/hookrun`, `tests/agentview`. A move in the guard keeps every decision in the table identical.

## Reference

docs/guard.md (the index of its pages), docs/guard-core.md, docs/guard-rules.md, docs/guard-jail-roles.md, docs/guard-fallback.md, docs/guard-owner.md, docs/guard-hooks.md.
