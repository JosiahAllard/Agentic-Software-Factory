# PM kit architecture

The kit's one high-level design, and the top authority on its shape: every module, what it owns, what it may depend on, and the contracts between them. It describes the end goal, not today's tree. Where the code differs, the code changes. Each module's low-level design is in `docs/modules/` and loses to this file on a conflict; the reference docs (`docs/INDEX.md`) say how to run a tool and lose to both. An item that waits on the owner is marked **OPEN** where it applies and listed under "Open items". This file changes by an ordinary reviewed commit; the owner's approval is needed only for the owner-only paths (`owner.paths`; docs/kit-json.md).

## System

Several AI agent sessions work on several repositories on one machine. The kit keeps them from stepping on each other:

- **One branch, one worktree.** No agent edits a main checkout; work reaches main only by a landing.
- **One machine lock.** Every heavy run books a slot; nothing heavy runs beside it unbooked.
- **One gate, one landing at a time.** A branch is gated, reviewed and landed in turn; main never holds an ungated merge.
- **One guard.** Every tool call of every agent passes the same read-only rules, which refuse with the command to use instead.
- **One backlog, one ledger.** Work is a record with a scope; an owner ruling is a ledger entry; the standing rules are a rulebook.
- **One hub.** The owner answers questions, approves owner-only changes and reads pages in one place.

The kit is Python on the standard library, for Windows and Linux. It runs from its own folder, an export of pushed main; a repository holds a `.kit.json` and nothing of the kit. Each AI host plugs in through a thin adapter.

## Layers

`kitlib/` holds all logic, one package per module, in six layers; the entry points are a seventh on top and hold no logic. A package imports only packages in the layers below it, and in its own layer only the ones its row names. The module map (`docs/module-map.json`) is these tables as data, each module's layer and Same layer names included (`tests/modulemap/test_kit_map.py` holds the two in step); the "Design" column names the low-level design, `docs/modules/<name>-design.md`.

**1. Base** knows the operating system and nothing of repositories, hosts or kit features.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `platform` | Every operating-system call, behind one contract (`api`) with a complete Windows backend and a complete Linux backend. Nothing above it tests the OS. | - | base |
| `proc` | Processes: start with no window, job containment (pin, measure, kill as a tree), the process list, named pipes, the console guard. | `platform` | base |
| `fs` | File primitives no feature owns: atomic write, retried replace, forced rmtree, tree lock, write lock. | `platform`, `proc` | base |
| `schedtask` | The kit's scheduled tasks on either OS: the interpreter a task runs, its command line, register, read, remove. | `platform`, `proc` | base |
| `mcpwire`, `hostrpc` | The stdio JSON-RPC wires the MCP servers and the host adapters share. | - | base |
| `verdict` | One judgement rule for a tool that runs a tool and prints its verdict. | - | base |
| `kitlib-root` | The loose files at the top of `kitlib/`: `clock` (time fences), `ident` (bounded ids), `kitroot` (where this kit is); `__init__` installs the console guard and nothing else. | `platform`, `proc` | base |

**2. Facts** say which repository, which host and what is configured. They read; they change nothing but their own files.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `git` | Safe git invocation, repository, worktree and main-checkout detection, refs and ancestry, batched reads, where a lock file goes; `git/fake`, the in-process git the tests run on. | - | git |
| `host` | `hosts.json` read as data, the host contract and content contract types, agent names, the session mark. Names no host. | - | host |
| `attribution` | Who made a commit: the agent trailer, the model name, the co-author line. | `host` | host |
| `content` | Roles and skills written once: the model, the render into each host's files, the roster, render on commit. | `git`, `host` | content |
| `config` | What is configured: `.kit.json` (schema, load, policy, owner paths), the machine config, the PM-repo pointer, the PM-data record, the PM's settings and known repositories. | `git`, `host`, `content` | config |

**3. Services** hold the machine-wide state and rules that many features share.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `lock` | The machine lock: store, queue, holds, runs, kinds, layout, the one API, the names of its owner-only tools. | - | lock |
| `lockui` | The schedule window's model and actions, over `lock.api` only. | `lock` | lock |
| `records` | A repository's records outside its checkouts: landing lock, gate-OK record, landing in flight, a verdict-queue entry, and where each lives. | - | records |
| `landed` | Has a commit landed: one read-only look. | `records` | records |
| `hubvalidate` | The Action Hub's data shapes: field tables, constants, the page check, to-do steps. Pure. | - | hub |
| `hubclient` | Talking to the hub server on this machine: settings, token, client, errors, push result words, the stale-code notice. | `hubvalidate` | hub |
| `ownerfiles` | Owner-only paths: which commits and merges touch them, the approval record and how long it holds, asking for and verifying an approval. | `hubvalidate`, `hubclient` | owner-files |
| `ledger`, `pending` | The rulings ledger and the rulebooks (the standing rules); the owner's pending-steps file. | `pending` uses `ledger` | ledger |
| `docs` | Doc index, doc budget, code map. | - | checks |
| `forbidden`, `scrub`, `machinepaths`, `pieces`, `trailerscheck` | The repo checks, one each: forbidden tokens, owner-specific text, machine paths, the path-to-piece table, commits with no agent trailer. | `scrub` uses `forbidden` | checks |
| `modulemap` | The module map: load, detect crossings, check, isolation runs, the generated page. | `lock`, `records` | checks |
| `pytests`, `tooldeps`, `testlist`, `ci` | The test tools: the parallel runner, which tests a change can break and the kit's own runner, test-name lists, the CI ratchet. | `tooldeps` uses `pytests`, `lock`; `ci` uses `tooldeps` | tests |
| `launcher` | Long-running sessions: start, stop, hand-off, accounts, state files, finding a live session, pipes, usage. | `hubclient`, `ledger` | sessions |
| `pluginsource` | The kit's export of pushed main: where it is, how one is written and pruned, the refresh. | `launcher` | install |
| `allowrules` | The permission rules that let an agent submit a landing unprompted. | `pluginsource` | install |
| `githooks` | The pre-commit dispatch: render, then the ledger check. | `ledger` | content |

**4. Work** is a branch's life, from record to main.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `backlog` | Work records: the store, the record schema, the scope language, the write verbs as plans, the offer, who may run which verb. | - | backlog |
| `worktree` | Linked worktrees: new, first-use prep, classify, remove, sweep, scratch trees, branch overlap, and the liveness and quiet probes the backlog takes. | `backlog` | worktree |
| `commit` | Stage explicit paths and commit with the right trailers, after the commit checks. | `backlog` | commit |
| `syncbase` | Merge the named main commit into a branch and check the result. | `commit` | commit |
| `preflight` | The start-of-task picture of one checkout. | `backlog`, `worktree` | worktree |
| `strayrestore` | Discard a main-checkout stray that a branch tip already holds. | - | worktree |
| `gate` | The gate: steps, selection by change, the run in a lock slot, reuse, targeted rerun, the result record, budget and cap, pinned tools. | `worktree` | gate |
| `loadnode` | Load nodes: registry, remote calls, jobs, a gate on a node. | `gate` | gate |
| `landing` | One branch landed on main: the verdict queue, submit, the landing itself, carry, cleanup, the landing feed. | `backlog`, `worktree`, `gate`, `loadnode` | landing |
| `dailyfull` | The daily full run and its bisect. | `backlog`, `gate` | gate |
| `coordstate` | A coordinator's event log and the state note rendered from it. | `gate`, `landing` | sessions |
| `reviewpack` | Everything a reviewer needs about a branch, and the quality signals. | `preflight`, `gate`, `landing` | commit |
| `delegations` | The owner's delegation list: read, check, place, run one operation. | - | owner-files |

**5. Guard** judges; nothing below it knows it exists.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `guard` | The tool-call guard and the pre-push check: read a command line as commands, read real state, judge by the rules, the role policy, the write jail. | - | guard |
| `hookrun` | The one hook runner behind `boot/hook.py`, and the kit header every session and subagent is given. | `guard` | guard |
| `agentview` | Everything one role is given and held to on one host. | `guard`, `hookrun` | guard |

**6. Apps** are programs composed from the layers below. No package below imports one.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `machinesetup`, `stackskill`, `kitcmd`, `pmdata` | Install: set up a machine, a repository's committed setup, a stack's skills, the `kit` command, making the PM-data branch (`pmdata` holds only its `init`; the record is in `config`). | `machinesetup` uses `stackskill`, `autotasks` | install |
| `pmwatch`, `pmstart`, `coordinatorstart`, `hoststart`, `agentstart` | The PM watcher and the session starts: the checks and context each kind of session is given. | `pmwatch` uses `machinesetup`, `autotasks`; `pmstart` uses `pmwatch`, `machinesetup`, `autotasks`; `coordinatorstart` uses `pmwatch`, `pmstart`, `machinesetup`, `autotasks`; `hoststart` uses `pmwatch`, `pmstart`, `coordinatorstart`, `machinesetup`, `autotasks`; `agentstart` uses `pmwatch`, `pmstart`, `coordinatorstart`, `hoststart`, `machinesetup`, `autotasks` | sessions |
| `autotasks` | The automatic tasks the hub schedules. | - | hub |
| `maintlint` | The maintenance lint. | - | checks |
| `redfirst` | Red-first evidence runs. | - | tests |
| `hubbacklog`, `hubsite`, `hubjobs` | The Action Hub server: store, API, listeners, push, backlog views, timed jobs. | `hubsite` uses `hubbacklog`, `autotasks`, `maintlint`, `pmwatch`; `hubjobs` uses `hubsite`, `pmwatch` | hub |
| `hubmcp`, `lockmcp`, `blendermcp` | The MCP servers: the hub's API, the lock's read tools, a booked headless Blender. | - | hub, lock, extras |

**7. Entry points** parse arguments, call the library, print, exit. Paths and exit codes stay stable. Nothing in `kitlib` imports one.

| Package | Owns | Same layer | Design |
|---|---|---|---|
| `tools` | The `tools/` command lines; `tools/_host.py` is the one place a tool imports a host adapter. | `hosts` | - |
| `boot` | `boot/hook.py`, the one hook entry of every host, and `boot/kitboot.py`, the check every host entry runs first. | - | host |
| `mcp` | The MCP server entry scripts: hub, lock, Blender. | - | hub, lock, extras |
| `githooks-dir` | The top-level `githooks/` folder: the pre-push and pre-commit hooks. | - | content |
| `hosts` | `hosts/<name>/`: one adapter and one shell per AI host. | - | host |
| `opt-agenttemp`, `opt-bridge`, `opt-comfy`, `opt-loadnode`, `opt-procwatch`, `opt-usage` | The `optional/<name>` programs, installed on demand; the core never imports one. | - | extras |

`reserve_ui.pyw` is an entry point too; the map holds it in `(root)`. The complete lists: `python <kit>/tools/codemap.py` and `python <kit>/tools/doc_index.py --tools`.

## Dependency rules

1. **Down only.** A package imports packages in lower layers, and in its own layer the ones its row names. An import the module map does not list fails `module_map.py --check`; the map carries no ratchet and no "same layer, any direction" rule.
2. **`kitlib` never imports an entry point, a host folder or `optional/`** (`tests/host/test_layering.py`).
3. **A fact that lives above comes in as an argument.** A lower package that needs a higher one's answer takes a probe from its caller and has no default that reaches up. The standing probes: the backlog's `plan.Context` (branch liveness and the quiet hold, from `worktree`); the hub server's job list (from `hubjobs`, passed by `tools/hub.py`); the test child's audit (from `tooldeps`, passed by `tools/pytests.py`); a delegated run's checks that read an app (passed by its entry point); the console guard's incident folder (registered by `config`).
4. **No import inside a function to reach up.** A late import defers a cost; it never hides an edge.
5. **No command lines, no printing to the user, no `sys.exit` in `kitlib`**, except the standalone worker entry scripts (`gate/runner.py`, `lock/bg_client.py`, `tooldeps/pool_worker.py`). Standard library only.
6. **One home per fact.** A constant, a path rule or a record format is defined in the package that owns it and read from there; a package that needs it from a higher one is a sign it lives too high.
7. **One responsibility per file.** A file past about 1,500 non-test lines is split by responsibility, never by size alone.
8. **OS code lives in `platform`.** A package that needs the OS asks `platform`; `sys.platform` and `os.name` appear nowhere else.

## Contracts

- **Booking.** `book(kind)` returns a slot. `slot()` hands back a launcher, and every heavy child runs through it inside the lock's job object (pinned cores, memory measured, killed as a tree). Acquisition fails closed: there is no unlocked fallback. Lock held means a live run in the store whose process is an ancestor, shown by the `MACHINE_LOCK_RUN=<run>:<pid>:<ctime>` nest token that `nest_check` verifies. No env flag counts as proof. `slot()` takes the lock's mutex on the thread that releases it and prints nothing.
- **Kinds.** One JSON kinds table in kit config, read by the lock and by the gate. A repository may add kinds of its own in its main checkout's `.kit.json` `kinds` section; a kit kind or alias of the same name wins (docs/lock-api.md "Repository kinds"). `gate.py --measure` writes a proposed table; the PM reviews and commits it. Sizes never change automatically. A size is a whole number of cpu slots 1..agent_cap; a run may ask for fewer, never more. A program is heavy because it is on the heavy list, and its size comes from measurement.
- **Gate-OK record.** The gate writes `<dev_drive>\pm-kit\kit-gate-ok\<repo key>\<sha>.json`, outside every checkout, for exactly the commit it passed; with no `dev_drive` it writes `<git common dir>/kit-gate-ok/<sha>.json`, which every reader then takes. `records` owns the format, the place and the verification; `gate` writes it. A branch gate's record holds per-step input digests, from which a landing reuses a step whose digests all match (docs/gate-landing-policy.md "Gate reuse"). The pre-push hook refuses a push to main without one when the repo sets `repo.push_block` (read from the base's committed `.kit.json`, never the working tree); a delete or non-fast-forward push of main is refused whatever it says. With no "gate" section the gate detects the repo's stack and runs its usual steps (docs/gate-run.md "Auto-detected gate").
- **Result record.** A branch gate or a landing writes `<dev_drive>\pm-kit\kit-gate-result\<repo key>\<branch stem>.<gate|land>.json`, `running` at the start and the verdict at the end (with no `dev_drive` there is no record, and a landing refuses); `gate.py wait-result` waits on it, finishing `error` a `running` record whose process died (docs/gate-records.md "Result record").
- **Landing.** `land.py <branch>... --expect-tip <sha>... [--reviewed-by <id>... --submit]`. It books one machine-lock entry, takes the repo's landing lock, merges onto `<remote>/<main>` as it is at its turn, gates the merged tree in that slot, pushes, and verifies. A red merge is undone; a conflict goes back to the branch's author. Landings into one repo take turns; branch gates and different repos run in parallel. A branch lands only with a queued verdict from a reviewer who is not its author. The one exception: a coordinator may review its own small, low-risk diff, up to the line limit `landing.self_review_max_lines` sets (docs/gate-landing.md).
- **What a landing runs.** The merged-tree gate runs only what the landing's work touches: each step picked by what the merge changes and run in its selected form, reusing a step whose inputs match the branch's record. No landing runs the full suite, and no branch needs a full run to land. A strict-path landing runs the same selection, always fresh (nothing reused), after a strong-tier review. The one exception: a landing whose diff changes a gate config file runs every step in its full form once, on the merged tree, at that landing. The scheduled daily full run (`daily_full.py`) is the only other full-repo run; it bisects a red and files the record.
- **Backlog claims.** A record's scope is its claim; two live pulls never overlap. `pull` narrows the pulled record, and the in-flight one when it lists planned files, in one write; `commit.py` refuses a commit that touches the other side's narrowed part, and a landing refuses a scope breach of a narrowed record. `add` never refuses an overlap: it files the record, warns, and writes the overlap into its Found section; the pull-time guard holds such a record until the branch lands. Work that waits on the owner is parked: its claim is released and its record blocked on a hub item, with an end condition the hub checks and lifts itself (docs/backlog.md).
- **Owner files.** A path in `owner.paths` changes only by a commit the owner authored or approved. When the owner approves, the hub server makes the approved commit itself, carrying the owner's approval trailer, so no agent writes, stages or commits an owner-only path. An approval holds until that change is on main, with a 30-day backstop: a rebuild, rebase, merge or re-land carrying the same change needs no second approval; only a change beyond what was approved does. A sync merge may carry an owner-only path when the path equals the merge-tree result and neither side's own commits breach the rule; a rename, a mode change, a non-default merge attribute or an octopus merge fails closed. `ownerfiles` holds these rules once; `commit`, `landing`, the pre-push check and the hub read them there.
- **Roles.** `PM_KIT_ROLE` is the one role marker on every host, and `PM_KIT_HOST` names the host. The host adapter tells subagents apart and passes the identity as `Action.agent`; a host whose hooks carry no subagent identity says so in `hosts.json` and gets the role-only policy. A role's mode is the `mode:` of its role file, which `.kit.json` `roles.<role>.mode` overrides; `guard.role_policy` turns the role policy on per repository. The policy itself is `kitlib/guard/rolepolicy.py`, the same on every host. Who may run which backlog verb is `kitlib/backlog/roles.py`, the owner's map.
- **Guard rules.** Read-only checks of real state, judged once over a neutral `Action`. A refusal names the command to use instead. The guard never answers allow: it denies, asks, warns or stays silent. A rule's mode (deny, warn, off) comes from the kit's defaults and the machine config's `guard_rules`, never from a file a branch carries. Denied by default: an agent's edit in a main checkout, a write outside the session's jail, damaging git in a main checkout (`checkout`, `switch`, `reset --hard`, `clean`, `stash`, a push to main without a record), an edit of an owner-only path, the ledger, the lock's state or a generated file, a search from a drive root, and a script or unreadable line that names `backlog.py` (no hook rewrites a subagent's command to carry its role). A raw heavy program outside the lock is a warning by default. Bash, PowerShell and cmd lines are read as commands; a line the parser cannot read goes through `kitlib/guard/fallback.py`'s patterns (docs/guard.md).
- **Trailers.** `commit.py` writes the agent's trailers. A commit without a Co-Authored-By line is never refused: the gate's trailers step and the landing only warn.
- **Hook entry.** Every host's hooks run `boot/hook.py <host> <event> [<part>]`. It reads the host's `hosts.json` hook data with json alone, lets a call to an unguarded tool through on a host whose `fast_path` allows it, then boots the kit and hands the event to `kitlib.hookrun`: the guard, failing closed, or a start event's context.

## Data ownership

One package writes each kind of data; others read it through that package.

| Data | Owner | Where |
|---|---|---|
| `.kit.json`, the machine config, the PM-repo pointer, the PM-data record | `config` | the repository; the PM repository's `.pm-kit/`; the machine registry |
| The lock store (runs, holds, queue, history) | `lock` | the machine's lock folder, under named mutexes |
| Landing lock, gate-OK record, in-flight mark, verdict-queue entry (format and place) | `records` | the git common dir, or the dev drive outside every checkout |
| Result record, gate reuse cache, gate budget | `gate` | the dev drive, outside every checkout |
| The verdict queue's entries | `landing` | the git common dir |
| Backlog records, the audit log | `backlog` | the configured store: a ref, a branch, or the PM-data worktree |
| The rulings ledger, rulebooks, pending steps | `ledger`, `pending` | where the machine config says; never `.kit.json`, never env |
| The hub store (questions, to-dos, approvals, pages, landings) | `hubsite` | the PM-data worktree |
| Approval rules (what an approval covers, when it is spent) | `ownerfiles` | read from the hub store through `hubclient` |
| Sessions and launcher state | `launcher` | the launcher's state folder |
| The kit export and its marker | `pluginsource` | the live root |
| The header records (which export each session's kit header named) | `hookrun` | `<export root>/headers/` |
| Rendered agents and manifests | `content` | `hosts/<name>/shell/`; never edited by hand |
| The refusal log | `guard` | the PM-data worktree |
| A coordinator's event log and state note | `coordstate` | the agent temp folder, outside every repository |
| Scheduled tasks | `schedtask` registers; the feature that needs one names it | the OS scheduler |
| The delegation list, the module map, the backlog role map, the strict path defaults | the owner | owner-only files |

The kit holds no machine paths: every machine fact comes from the machine config.

## Hosts

- `kitlib` stays host-neutral and never imports a host. The host is chosen at the edge: a host's entry points build its adapter and pass it in; shared CLIs read `PM_KIT_HOST` (default `claude`) and resolve the adapter through `hosts.json`.
- A host name appears only in `hosts.json`, its own folder, `optional/bridge/hosts/<name>/`, the bridge's peer list and tests (`tests/host/test_leaks.py`). A host adds no hook script. Adding a host changes no other folder (docs/new-host.md).
- Capabilities a host lacks (no `ask`, no subagent identity in hooks) are declared in `hosts.json`; the core degrades them one way for every host (`ask` becomes deny with the reason).
- One shared conformance suite (`tests/host/conformance.py`) defines a supported host; each host's tests subclass it with recorded payloads.

### Host contract

`kitlib/host/`: the neutral `Action` and `Decision` types, `HostAdapter`, `HostProfile`, `SessionHost`, `CommandHost`, `ContentRenderer`. Protocols and data only. A `HostAdapter` parses its hook payloads into `Action`, renders `Decision` back, and supplies the rest; optionally it gives the text each of its start hooks adds. `HostProfile` (the `profile` section of `hosts.json`) holds only consumed fields.

### Content

`content/roles/` and `content/skills/`: every role and skill written once. `python tools/render_content.py --write` renders the roles into each host's agents and manifests; every host loads `content/skills/` itself, so skill text is identical everywhere. Rendered files are committed and open with a generated-file line; `tools/commit.py` renders when a source is committed, the guard refuses a hand edit, and a test fails on drift. Every role exists on every host, with a tier per role and a tier-to-model map per host; a role's own model, where it has one, is `hosts.json` `role_models` (deep-worker runs the host's strongest long-context model). The role list lives only in `content/roles/`: docs/ROSTER.md, the launcher and the backlog policy read it from there. `content/default-profile.md` is the profile a role reads in a repository with none.

## Packaging

- **One version.** `kit.json` holds the one name and version; the renderer writes it into every host manifest.
- **The live kit.** The machine runs the kit from an export of pushed main (`pluginsource`): each host installs its thin plugin from that export, and a refresh re-points every account when main moves (docs/plugin-source.md). There is no other way to ship it: no pinned release.
- **Boot.** Every host entry runs the stdlib-only `boot/kitboot.py` before importing `kitlib`.
- **Hooks.** The one hook entry (Contracts, "Hook entry"). Each host's shell registers its hooks in a rendered manifest; Codex's hooks are a second plugin whose fixed-bytes stub runs the installed export's entry, so a new kit version asks for no new hook trust.
- **Bundled per host.** The rendered agents, the skills, the lock, hub and Blender MCP servers, and the host's hooks.
- **Instructions.** `AGENTS.md` is canonical; `CLAUDE.md` imports it. The repo profile is `docs/agent-profile.md`; each host's own-folder stub points to it.
- **Git hooks.** `githooks/` holds `pre-push` and the opt-in `pre-commit`.
- **Machine setup** runs once per machine and writes nothing into any repository. A repository that wants a committed setup asks for one (`machine_setup.py --repo`).
- **Repo layer.** A repository holds `.kit.json` (optional: with none, the defaults of docs/kit-json.md "With no file") and at most one repo hook module. It holds no copy of the kit and no stub of its tools: skills and roles run `<kit>/tools/<x>.py -C <checkout>`.

## Scheduler UI

`kitlib/lock` exposes one versioned, transport-neutral API: read snapshots, a revision number, validated owner writes. Every UI uses only this API (docs/lock-api.md): the schedule window (`reserve_ui.pyw`, logic in `kitlib/lockui`), the Action Hub's lock section (in-process, through `kitlib/hubsite/lockdoor.py`), and the lock MCP server, which ships read tools only so the lock never trusts a session mark. Owner lock writes stay on the window and the outside-terminal CLI.

## Action Hub

A local website (`tools/hub.py serve`) holds the owner's questions, to-dos, approvals, pages and story intake, and the lock section. It is reachable remotely only through the tunnel listener's access check. Agents reach it through `hubclient` (the CLI and the hub MCP server); nothing below the apps layer imports the server. When the owner acts, the server tells the session that asked, and the PM, through the host's push delivery. Its timed jobs (`hubjobs`) are passed in at start (docs/action-hub.md).

## Security

- The owner's personal details never leave the machine, and never enter a tracked file: the scrub and the machine-path check gate every commit.
- Owner-only paths, the ledger, the lock's state, the hub's secrets and the machine config are closed to agents by the guard, whatever the role.
- Everything that decides fails closed: the guard on an error, the lock on acquisition, a landing on a record it cannot read, an approval it cannot verify.
- A branch can never loosen the rules it is judged by: gate config, guard modes, push rules and owner paths are read from the base's committed tree or the machine config, never from the branch.
- An agent ends only a process it started, by pid and start time.

## Testing

- Tests live in `tests/<pkg>/` as unittest tests and target the library; entry-point tests cover only arguments and exit codes.
- Tests run on the in-process fake git (`kitlib/git/fake`), except the classes marked `real_git` (docs/test-tools.md "Fake git").
- A change runs the tests it can break: `kit_tests.py --select` picks them from the import graph. A branch gate and a landing run that selection (Contracts, "What a landing runs"); the daily full run is the only full-repo run, except that a change to a gate config file runs every step in full once, at its landing. A full run by hand goes through `gate.py --full`.
- A fix comes with a test that fails without it (`redfirst.py`).
- Each host passes the shared conformance suite. The module map check runs in the gate.

## Never in the kit

autoMode, git identity, preferences, memory, the knowledge contents and synced skills. A skill tied to one repository's engine, models or worktree tools stays in that repository.

## Open items

An item that waits on the owner is listed here and marked **OPEN** where it applies; until it is answered, the code stays as it is on that point.

None open.

## Designs

| Design | Covers |
|---|---|
| `docs/modules/base-design.md` | `platform`, `proc`, `fs`, `schedtask`, the wires, the root files |
| `docs/modules/git-design.md` | `git`, the fake git |
| `docs/modules/host-design.md` | `host`, `attribution`, `hosts/<name>/`, `boot/` |
| `docs/modules/content-design.md` | `content`, `githooks`, `content/` |
| `docs/modules/config-design.md` | `config` |
| `docs/modules/lock-design.md` | `lock`, `lockui`, `lockmcp` |
| `docs/modules/records-design.md` | `records`, `landed` |
| `docs/modules/hub-design.md` | `hubvalidate`, `hubclient`, `hubsite`, `hubjobs`, `hubbacklog`, `hubmcp`, `autotasks` |
| `docs/modules/owner-files-design.md` | `ownerfiles`, `delegations` |
| `docs/modules/ledger-design.md` | `ledger`, `pending` |
| `docs/modules/checks-design.md` | `docs`, the repo checks, `modulemap`, `maintlint` |
| `docs/modules/tests-design.md` | `pytests`, `tooldeps`, `testlist`, `ci`, `redfirst` |
| `docs/modules/sessions-design.md` | `launcher`, `pmwatch`, the session starts, `coordstate` |
| `docs/modules/backlog-design.md` | `backlog` |
| `docs/modules/worktree-design.md` | `worktree`, `preflight`, `strayrestore` |
| `docs/modules/commit-design.md` | `commit`, `syncbase`, `reviewpack` |
| `docs/modules/gate-design.md` | `gate`, `loadnode`, `dailyfull` |
| `docs/modules/landing-design.md` | `landing` |
| `docs/modules/guard-design.md` | `guard`, `hookrun`, `agentview` |
| `docs/modules/install-design.md` | `pluginsource`, `allowrules`, `machinesetup`, `stackskill`, `kitcmd`, `pmdata/init.py`, `installer/`, `templates/` |
| `docs/modules/extras-design.md` | `blendermcp`, `optional/` |
