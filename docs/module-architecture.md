# Module map

Generated from `docs/module-map.json` by `module_map.py --page --write` (the PM kit's docs/module-map.md). Do not edit it by hand: change the map, then write the page again; `module_map.py --check` fails while the two differ.

A crossing the map does not allow (an edge of its kind, a messaging module or a ratchet) is refused. A path no module holds is in `(root)`.

## Modules

Layers, lowest first: base, facts, services, work, guard, apps, entry. An edge goes down a layer, or to a module of its own layer the Same layer column names.

| Module | Layer | Same layer | Folders | What it is |
|---|---|---|---|---|
| fs | base | `platform`, `proc` | `kitlib/fs` | File primitives no feature owns |
| hostrpc | base | - | `kitlib/hostrpc` | The stdio JSON-RPC wires the MCP servers and the host adapters share |
| kitlib-root | base | `platform`, `proc` | `kitlib` | The loose files at the top of `kitlib/` |
| mcpwire | base | - | `kitlib/mcpwire` | The stdio JSON-RPC wires the MCP servers and the host adapters share |
| platform | base | - | `kitlib/platform` | Every operating-system call, behind one contract (`api`) with a complete Windows backend and a complete Linux backend |
| proc | base | `platform` | `kitlib/proc` | Processes |
| schedtask | base | `platform`, `proc` | `kitlib/schedtask` | The kit's scheduled tasks on either OS |
| verdict | base | - | `kitlib/verdict` | One judgement rule for a tool that runs a tool and prints its verdict |
| attribution | facts | `host` | `kitlib/attribution` | Who made a commit |
| config | facts | `git`, `host`, `content` | `kitlib/config` | What is configured |
| content | facts | `git`, `host` | `kitlib/content` | Roles and skills written once |
| git | facts | - | `kitlib/git` | Safe git invocation, repository, worktree and main-checkout detection, refs and ancestry, batched reads, where a lock file goes; `git/fake`, the in-process git the tests run on |
| host | facts | - | `kitlib/host` | `hosts.json` read as data, the host contract and content contract types, agent names, the session mark |
| allowrules | services | `pluginsource` | `kitlib/allowrules` | The permission rules that let an agent submit a landing unprompted |
| ci | services | `tooldeps` | `kitlib/ci` | The test tools |
| docs | services | - | `kitlib/docs` | Doc index, doc budget, code map |
| forbidden | services | - | `kitlib/forbidden` | The repo checks, one each |
| githooks | services | `ledger` | `kitlib/githooks` | The pre-commit dispatch |
| hubclient | services | `hubvalidate` | `kitlib/hubclient` | Talking to the hub server on this machine |
| hubvalidate | services | - | `kitlib/hubvalidate` | The Action Hub's data shapes |
| landed | services | `records` | `kitlib/landed` | Has a commit landed |
| launcher | services | `hubclient`, `ledger` | `kitlib/launcher` | Long-running sessions |
| ledger | services | - | `kitlib/ledger` | The rulings ledger and the rulebooks (the standing rules); the owner's pending-steps file |
| lock | services | - | `kitlib/lock` | The machine lock |
| lockui | services | `lock` | `kitlib/lockui` | The schedule window's model and actions, over `lock.api` only |
| machinepaths | services | - | `kitlib/machinepaths` | The repo checks, one each |
| modulemap | services | `lock`, `records` | `kitlib/modulemap` | The module map |
| ownerfiles | services | `hubvalidate`, `hubclient` | `kitlib/ownerfiles` | Owner-only paths |
| pending | services | `ledger` | `kitlib/pending` | The rulings ledger and the rulebooks (the standing rules); the owner's pending-steps file |
| pieces | services | - | `kitlib/pieces` | The repo checks, one each |
| pluginsource | services | `launcher` | `kitlib/pluginsource` | The kit's export of pushed main |
| pytests | services | - | `kitlib/pytests` | The test tools |
| records | services | - | `kitlib/records` | A repository's records outside its checkouts |
| scrub | services | `forbidden` | `kitlib/scrub` | The repo checks, one each |
| tooldeps | services | `pytests`, `lock` | `kitlib/tooldeps` | The test tools |
| trailerscheck | services | - | `kitlib/trailerscheck` | The repo checks, one each |
| backlog | work | - | `kitlib/backlog` | Work records |
| commit | work | `backlog` | `kitlib/commit` | Stage explicit paths and commit with the right trailers, after the commit checks |
| coordstate | work | `gate`, `landing` | `kitlib/coordstate` | A coordinator's event log and the state note rendered from it |
| dailyfull | work | `backlog`, `gate` | `kitlib/dailyfull` | The daily full run and its bisect |
| delegations | work | - | `kitlib/delegations` | The owner's delegation list |
| gate | work | `worktree` | `kitlib/gate` | The gate |
| landing | work | `backlog`, `worktree`, `gate`, `loadnode` | `kitlib/landing` | One branch landed on main |
| loadnode | work | `gate` | `kitlib/loadnode` | Load nodes |
| preflight | work | `backlog`, `worktree` | `kitlib/preflight` | The start-of-task picture of one checkout |
| reviewpack | work | `preflight`, `gate`, `landing` | `kitlib/reviewpack` | Everything a reviewer needs about a branch, and the quality signals |
| staging | work | `dailyfull`, `gate`, `landing` | `kitlib/staging` | The staging branch promoted to main: the promotion run, its phase record and its move of main |
| strayrestore | work | - | `kitlib/strayrestore` | Discard a main-checkout stray that a branch tip already holds |
| syncbase | work | `commit` | `kitlib/syncbase` | Merge the named main commit into a branch and check the result |
| worktree | work | `backlog` | `kitlib/worktree` | Linked worktrees |
| agentview | guard | `guard`, `hookrun` | `kitlib/agentview` | Everything one role is given and held to on one host |
| guard | guard | - | `kitlib/guard` | The tool-call guard and the pre-push check |
| hookrun | guard | `guard` | `kitlib/hookrun` | The one hook runner behind `boot/hook.py`, and the kit header every session and subagent is given |
| agentstart | apps | `pmwatch`, `pmstart`, `coordinatorstart`, `hoststart`, `machinesetup`, `autotasks` | `kitlib/agentstart` | The PM watcher and the session starts |
| autotasks | apps | - | `kitlib/autotasks` | The automatic tasks the hub schedules |
| blendermcp | apps | - | `kitlib/blendermcp` | The MCP servers |
| coordinatorstart | apps | `pmwatch`, `pmstart`, `machinesetup`, `autotasks` | `kitlib/coordinatorstart` | The PM watcher and the session starts |
| hoststart | apps | `pmwatch`, `pmstart`, `coordinatorstart`, `machinesetup`, `autotasks` | `kitlib/hoststart` | The PM watcher and the session starts |
| hubbacklog | apps | - | `kitlib/hubbacklog` | The Action Hub server |
| hubjobs | apps | `hubsite`, `pmwatch` | `kitlib/hubjobs` | The Action Hub server |
| hubmcp | apps | - | `kitlib/hubmcp` | The MCP servers |
| hubsite | apps | `hubbacklog`, `autotasks`, `maintlint`, `pmwatch` | `kitlib/hubsite` | The Action Hub server |
| kitcmd | apps | - | `kitlib/kitcmd` | Install |
| lockmcp | apps | - | `kitlib/lockmcp` | The MCP servers |
| machinesetup | apps | `stackskill`, `autotasks` | `kitlib/machinesetup` | Install |
| maintlint | apps | - | `kitlib/maintlint` | The maintenance lint |
| pmdata | apps | - | `kitlib/pmdata` | Install |
| pmstart | apps | `pmwatch`, `machinesetup`, `autotasks` | `kitlib/pmstart` | The PM watcher and the session starts |
| pmwatch | apps | `machinesetup`, `autotasks` | `kitlib/pmwatch` | The PM watcher and the session starts |
| redfirst | apps | - | `kitlib/redfirst` | Red-first evidence runs |
| stackskill | apps | - | `kitlib/stackskill` | Install |
| boot | entry | - | `boot` | `boot/hook.py`, the one hook entry of every host, and `boot/kitboot.py`, the check every host entry runs first |
| githooks-dir | entry | - | `githooks` | The top-level `githooks/` folder |
| hosts | entry | - | `hosts` | `hosts/<name>/` |
| mcp | entry | - | `mcp` | The MCP server entry scripts |
| opt-agenttemp | entry | - | `optional/agenttemp` | The `optional/<name>` programs, installed on demand; the core never imports one |
| opt-bridge | entry | - | `optional/bridge` | The `optional/<name>` programs, installed on demand; the core never imports one |
| opt-comfy | entry | - | `optional/comfy` | The `optional/<name>` programs, installed on demand; the core never imports one |
| opt-loadnode | entry | - | `optional/loadnode` | The `optional/<name>` programs, installed on demand; the core never imports one |
| opt-procwatch | entry | - | `optional/procwatch` | The `optional/<name>` programs, installed on demand; the core never imports one |
| opt-usage | entry | - | `optional/usage` | The `optional/<name>` programs, installed on demand; the core never imports one |
| tools | entry | `hosts` | `tools` | The `tools/` command lines; `tools/_host.py` is the one place a tool imports a host adapter |

## Messaging

None: no module is open to every other.

## Allowed edges

| From | To | Kind | Why |
|---|---|---|---|
| agentstart | config | build | apps uses facts |
| agentstart | content | build | apps uses facts |
| agentstart | git | build | apps uses facts |
| agentstart | host | build | apps uses facts |
| agentstart | kitlib-root | build | apps uses base |
| agentstart | preflight | build | apps uses work |
| agentview | backlog | build | guard uses work |
| agentview | config | build | guard uses facts |
| agentview | content | build | guard uses facts |
| agentview | guard | build | same layer (guard): agentview's row names guard |
| agentview | hookrun | build | same layer (guard): agentview's row names hookrun |
| agentview | host | build | guard uses facts |
| allowrules | config | build | services uses facts |
| allowrules | git | build | services uses facts |
| allowrules | pluginsource | build | same layer (services): allowrules's row names pluginsource |
| attribution | host | build | same layer (facts): attribution's row names host |
| autotasks | config | build | apps uses facts |
| autotasks | fs | build | apps uses base |
| autotasks | git | build | apps uses facts |
| autotasks | hubclient | build | apps uses services |
| autotasks | kitlib-root | build | apps uses base |
| autotasks | launcher | build | apps uses services |
| autotasks | platform | build | apps uses base |
| autotasks | proc | build | apps uses base |
| backlog | config | build | work uses facts |
| backlog | content | build | work uses facts |
| backlog | docs | build | work uses services |
| backlog | fs | build | work uses base |
| backlog | git | build | work uses facts |
| backlog | host | build | work uses facts |
| backlog | hubclient | build | work uses services |
| backlog | kitlib-root | build | work uses base |
| backlog | launcher | build | work uses services |
| backlog | ledger | build | work uses services |
| backlog | machinepaths | build | work uses services |
| backlog | pluginsource | build | work uses services |
| backlog | proc | build | work uses base |
| blendermcp | git | build | apps uses facts |
| blendermcp | lock | build | apps uses services |
| blendermcp | mcpwire | build | apps uses base |
| blendermcp | platform | build | apps uses base |
| blendermcp | proc | build | apps uses base |
| boot | hookrun | build | entry uses guard |
| ci | platform | build | services uses base |
| ci | tooldeps | build | same layer (services): ci's row names tooldeps |
| commit | attribution | build | work uses facts |
| commit | backlog | build | same layer (work): commit's row names backlog |
| commit | config | build | work uses facts |
| commit | content | build | work uses facts |
| commit | docs | build | work uses services |
| commit | git | build | work uses facts |
| commit | kitlib-root | build | work uses base |
| commit | machinepaths | build | work uses services |
| commit | ownerfiles | build | work uses services |
| commit | proc | build | work uses base |
| commit | verdict | build | work uses base |
| config | content | build | same layer (facts): config's row names content |
| config | git | build | same layer (facts): config's row names git |
| config | host | build | same layer (facts): config's row names host |
| config | kitlib-root | build | facts uses base |
| config | platform | build | facts uses base |
| config | proc | build | facts uses base |
| content | git | build | same layer (facts): content's row names git |
| content | host | build | same layer (facts): content's row names host |
| content | proc | build | facts uses base |
| coordinatorstart | backlog | build | apps uses work |
| coordinatorstart | config | build | apps uses facts |
| coordinatorstart | git | build | apps uses facts |
| coordinatorstart | host | build | apps uses facts |
| coordinatorstart | kitlib-root | build | apps uses base |
| coordinatorstart | launcher | build | apps uses services |
| coordinatorstart | pmstart | build | same layer (apps): coordinatorstart's row names pmstart |
| coordinatorstart | proc | build | apps uses base |
| coordinatorstart | records | build | apps uses services |
| coordstate | config | build | work uses facts |
| coordstate | fs | build | work uses base |
| coordstate | gate | build | same layer (work): coordstate's row names gate |
| coordstate | git | build | work uses facts |
| coordstate | host | build | work uses facts |
| coordstate | landing | build | same layer (work): coordstate's row names landing |
| coordstate | records | build | work uses services |
| dailyfull | backlog | build | same layer (work): dailyfull's row names backlog |
| dailyfull | config | build | work uses facts |
| dailyfull | fs | build | work uses base |
| dailyfull | gate | build | same layer (work): dailyfull's row names gate |
| dailyfull | git | build | work uses facts |
| dailyfull | lock | build | work uses services |
| dailyfull | records | build | work uses services |
| delegations | config | build | work uses facts |
| delegations | fs | build | work uses base |
| delegations | git | build | work uses facts |
| delegations | host | build | work uses facts |
| delegations | hubclient | build | work uses services |
| delegations | kitlib-root | build | work uses base |
| delegations | landed | build | work uses services |
| delegations | ledger | build | work uses services |
| delegations | ownerfiles | build | work uses services |
| delegations | pluginsource | build | work uses services |
| delegations | proc | build | work uses base |
| docs | config | build | services uses facts |
| docs | git | build | services uses facts |
| docs | host | build | services uses facts |
| docs | verdict | build | services uses base |
| forbidden | config | build | services uses facts |
| forbidden | git | build | services uses facts |
| fs | platform | build | same layer (base): fs's row names platform |
| fs | proc | build | same layer (base): fs's row names proc |
| gate | config | build | work uses facts |
| gate | content | build | work uses facts |
| gate | fs | build | work uses base |
| gate | git | build | work uses facts |
| gate | kitlib-root | build | work uses base |
| gate | lock | build | work uses services |
| gate | platform | build | work uses base |
| gate | pluginsource | build | work uses services |
| gate | proc | build | work uses base |
| gate | records | build | work uses services |
| gate | worktree | build | same layer (work): gate's row names worktree |
| git | platform | build | facts uses base |
| git | proc | build | facts uses base |
| githooks | content | build | services uses facts |
| githooks | git | build | services uses facts |
| githooks | ledger | build | same layer (services): githooks's row names ledger |
| githooks-dir | githooks | build | entry uses services |
| guard | backlog | build | guard uses work |
| guard | config | build | guard uses facts |
| guard | content | build | guard uses facts |
| guard | delegations | build | guard uses work |
| guard | fs | build | guard uses base |
| guard | git | build | guard uses facts |
| guard | host | build | guard uses facts |
| guard | hubvalidate | build | guard uses services |
| guard | kitlib-root | build | guard uses base |
| guard | ledger | build | guard uses services |
| guard | lock | build | guard uses services |
| guard | ownerfiles | build | guard uses services |
| guard | platform | build | guard uses base |
| guard | pluginsource | build | guard uses services |
| guard | records | build | guard uses services |
| guard | worktree | build | guard uses work |
| hookrun | config | build | guard uses facts |
| hookrun | guard | build | same layer (guard): hookrun's row names guard |
| hookrun | host | build | guard uses facts |
| hookrun | platform | build | guard uses base |
| hookrun | pluginsource | build | guard uses services |
| host | platform | build | facts uses base |
| host | proc | build | facts uses base |
| hosts | agentstart | build | entry uses apps |
| hosts | attribution | build | entry uses facts |
| hosts | backlog | build | entry uses work |
| hosts | blendermcp | build | entry uses apps |
| hosts | config | build | entry uses facts |
| hosts | content | build | entry uses facts |
| hosts | coordinatorstart | build | entry uses apps |
| hosts | gate | build | entry uses work |
| hosts | git | build | entry uses facts |
| hosts | guard | build | entry uses guard |
| hosts | hookrun | build | entry uses guard |
| hosts | host | build | entry uses facts |
| hosts | hostrpc | build | entry uses base |
| hosts | hoststart | build | entry uses apps |
| hosts | hubclient | build | entry uses services |
| hosts | hubmcp | build | entry uses apps |
| hosts | kitlib-root | build | entry uses base |
| hosts | landing | build | entry uses work |
| hosts | launcher | build | entry uses services |
| hosts | lock | build | entry uses services |
| hosts | lockmcp | build | entry uses apps |
| hosts | machinesetup | build | entry uses apps |
| hosts | platform | build | entry uses base |
| hosts | pluginsource | build | entry uses services |
| hosts | pmstart | build | entry uses apps |
| hosts | proc | build | entry uses base |
| hosts | records | build | entry uses services |
| hosts | worktree | build | entry uses work |
| hoststart | config | build | apps uses facts |
| hoststart | coordinatorstart | build | same layer (apps): hoststart's row names coordinatorstart |
| hoststart | git | build | apps uses facts |
| hoststart | pmstart | build | same layer (apps): hoststart's row names pmstart |
| hubbacklog | backlog | build | apps uses work |
| hubbacklog | config | build | apps uses facts |
| hubbacklog | git | build | apps uses facts |
| hubclient | config | build | services uses facts |
| hubclient | hubvalidate | build | same layer (services): hubclient's row names hubvalidate |
| hubclient | kitlib-root | build | services uses base |
| hubjobs | backlog | build | apps uses work |
| hubjobs | config | build | apps uses facts |
| hubjobs | coordstate | build | apps uses work |
| hubjobs | fs | build | apps uses base |
| hubjobs | git | build | apps uses facts |
| hubjobs | host | build | apps uses facts |
| hubjobs | hubclient | build | apps uses services |
| hubjobs | hubsite | build | same layer (apps): hubjobs's row names hubsite |
| hubjobs | kitlib-root | build | apps uses base |
| hubjobs | landing | build | apps uses work |
| hubjobs | launcher | build | apps uses services |
| hubjobs | lock | build | apps uses services |
| hubjobs | machinepaths | build | apps uses services |
| hubjobs | ownerfiles | build | apps uses services |
| hubjobs | platform | build | apps uses base |
| hubjobs | pluginsource | build | apps uses services |
| hubjobs | pmwatch | build | same layer (apps): hubjobs's row names pmwatch |
| hubjobs | proc | build | apps uses base |
| hubjobs | records | build | apps uses services |
| hubjobs | staging | build | apps uses work |
| hubjobs | worktree | build | apps uses work |
| hubmcp | config | build | apps uses facts |
| hubmcp | hubclient | build | apps uses services |
| hubmcp | hubvalidate | build | apps uses services |
| hubmcp | mcpwire | build | apps uses base |
| hubmcp | ownerfiles | build | apps uses services |
| hubsite | autotasks | build | same layer (apps): hubsite's row names autotasks |
| hubsite | backlog | build | apps uses work |
| hubsite | config | build | apps uses facts |
| hubsite | content | build | apps uses facts |
| hubsite | fs | build | apps uses base |
| hubsite | git | build | apps uses facts |
| hubsite | host | build | apps uses facts |
| hubsite | hubbacklog | build | same layer (apps): hubsite's row names hubbacklog |
| hubsite | hubclient | build | apps uses services |
| hubsite | hubvalidate | build | apps uses services |
| hubsite | kitlib-root | build | apps uses base |
| hubsite | launcher | build | apps uses services |
| hubsite | lock | build | apps uses services |
| hubsite | lockui | build | apps uses services |
| hubsite | maintlint | build | same layer (apps): hubsite's row names maintlint |
| hubsite | ownerfiles | build | apps uses services |
| hubsite | platform | build | apps uses base |
| hubsite | pluginsource | build | apps uses services |
| hubsite | pmwatch | build | same layer (apps): hubsite's row names pmwatch |
| hubsite | proc | build | apps uses base |
| hubsite | schedtask | build | apps uses base |
| kitcmd | config | build | apps uses facts |
| kitcmd | pluginsource | build | apps uses services |
| kitcmd | proc | build | apps uses base |
| kitlib-root | platform | build | same layer (base): kitlib-root's row names platform |
| kitlib-root | proc | build | same layer (base): kitlib-root's row names proc |
| landed | config | build | services uses facts |
| landed | git | build | services uses facts |
| landed | records | build | same layer (services): landed's row names records |
| landing | backlog | build | same layer (work): landing's row names backlog |
| landing | config | build | work uses facts |
| landing | content | build | work uses facts |
| landing | fs | build | work uses base |
| landing | gate | build | same layer (work): landing's row names gate |
| landing | git | build | work uses facts |
| landing | host | build | work uses facts |
| landing | hubclient | build | work uses services |
| landing | loadnode | build | same layer (work): landing's row names loadnode |
| landing | lock | build | work uses services |
| landing | ownerfiles | build | work uses services |
| landing | pluginsource | build | work uses services |
| landing | proc | build | work uses base |
| landing | records | build | work uses services |
| landing | worktree | build | same layer (work): landing's row names worktree |
| launcher | config | build | services uses facts |
| launcher | content | build | services uses facts |
| launcher | fs | build | services uses base |
| launcher | git | build | services uses facts |
| launcher | host | build | services uses facts |
| launcher | hubclient | build | same layer (services): launcher's row names hubclient |
| launcher | kitlib-root | build | services uses base |
| launcher | ledger | build | same layer (services): launcher's row names ledger |
| launcher | platform | build | services uses base |
| launcher | proc | build | services uses base |
| ledger | attribution | build | services uses facts |
| ledger | config | build | services uses facts |
| ledger | fs | build | services uses base |
| ledger | git | build | services uses facts |
| ledger | kitlib-root | build | services uses base |
| ledger | platform | build | services uses base |
| ledger | proc | build | services uses base |
| loadnode | config | build | work uses facts |
| loadnode | content | build | work uses facts |
| loadnode | gate | build | same layer (work): loadnode's row names gate |
| loadnode | git | build | work uses facts |
| loadnode | kitlib-root | build | work uses base |
| loadnode | lock | build | work uses services |
| loadnode | platform | build | work uses base |
| loadnode | proc | build | work uses base |
| loadnode | records | build | work uses services |
| lock | config | build | services uses facts |
| lock | fs | build | services uses base |
| lock | git | build | services uses facts |
| lock | host | build | services uses facts |
| lock | platform | build | services uses base |
| lock | proc | build | services uses base |
| lockmcp | host | build | apps uses facts |
| lockmcp | lock | build | apps uses services |
| lockmcp | mcpwire | build | apps uses base |
| lockui | lock | build | same layer (services): lockui's row names lock |
| machinepaths | config | build | services uses facts |
| machinepaths | git | build | services uses facts |
| machinesetup | allowrules | build | apps uses services |
| machinesetup | autotasks | build | same layer (apps): machinesetup's row names autotasks |
| machinesetup | config | build | apps uses facts |
| machinesetup | fs | build | apps uses base |
| machinesetup | git | build | apps uses facts |
| machinesetup | hookrun | build | apps uses guard |
| machinesetup | host | build | apps uses facts |
| machinesetup | kitlib-root | build | apps uses base |
| machinesetup | launcher | build | apps uses services |
| machinesetup | platform | build | apps uses base |
| machinesetup | pluginsource | build | apps uses services |
| machinesetup | proc | build | apps uses base |
| machinesetup | schedtask | build | apps uses base |
| machinesetup | stackskill | build | same layer (apps): machinesetup's row names stackskill |
| machinesetup | worktree | build | apps uses work |
| maintlint | backlog | build | apps uses work |
| maintlint | config | build | apps uses facts |
| maintlint | dailyfull | build | apps uses work |
| maintlint | git | build | apps uses facts |
| maintlint | hubclient | build | apps uses services |
| maintlint | hubvalidate | build | apps uses services |
| maintlint | kitlib-root | build | apps uses base |
| maintlint | ledger | build | apps uses services |
| maintlint | lock | build | apps uses services |
| maintlint | modulemap | build | apps uses services |
| maintlint | records | build | apps uses services |
| maintlint | worktree | build | apps uses work |
| mcp | blendermcp | build | entry uses apps |
| mcp | host | build | entry uses facts |
| mcp | hubmcp | build | entry uses apps |
| mcp | lockmcp | build | entry uses apps |
| modulemap | config | build | services uses facts |
| modulemap | fs | build | services uses base |
| modulemap | git | build | services uses facts |
| modulemap | lock | build | same layer (services): modulemap's row names lock |
| modulemap | proc | build | services uses base |
| modulemap | records | build | same layer (services): modulemap's row names records |
| opt-agenttemp | config | build | entry uses facts |
| opt-agenttemp | machinesetup | build | entry uses apps |
| opt-agenttemp | proc | build | entry uses base |
| opt-bridge | host | build | entry uses facts |
| opt-bridge | kitlib-root | build | entry uses base |
| opt-bridge | platform | build | entry uses base |
| opt-bridge | proc | build | entry uses base |
| opt-comfy | config | build | entry uses facts |
| opt-comfy | fs | build | entry uses base |
| opt-comfy | kitlib-root | build | entry uses base |
| opt-comfy | platform | build | entry uses base |
| opt-comfy | proc | build | entry uses base |
| opt-loadnode | kitlib-root | build | entry uses base |
| opt-loadnode | loadnode | build | entry uses work |
| opt-loadnode | lock | build | entry uses services |
| opt-procwatch | pmstart | build | entry uses apps |
| opt-usage | config | build | entry uses facts |
| ownerfiles | config | build | services uses facts |
| ownerfiles | content | build | services uses facts |
| ownerfiles | git | build | services uses facts |
| ownerfiles | hubclient | build | same layer (services): ownerfiles's row names hubclient |
| ownerfiles | hubvalidate | build | same layer (services): ownerfiles's row names hubvalidate |
| pending | fs | build | services uses base |
| pending | git | build | services uses facts |
| pending | ledger | build | same layer (services): pending's row names ledger |
| pieces | config | build | services uses facts |
| pluginsource | config | build | services uses facts |
| pluginsource | fs | build | services uses base |
| pluginsource | git | build | services uses facts |
| pluginsource | host | build | services uses facts |
| pluginsource | kitlib-root | build | services uses base |
| pluginsource | launcher | build | same layer (services): pluginsource's row names launcher |
| pluginsource | platform | build | services uses base |
| pluginsource | proc | build | services uses base |
| pluginsource | schedtask | build | services uses base |
| pmdata | config | build | apps uses facts |
| pmdata | git | build | apps uses facts |
| pmdata | kitlib-root | build | apps uses base |
| pmstart | allowrules | build | apps uses services |
| pmstart | autotasks | build | same layer (apps): pmstart's row names autotasks |
| pmstart | config | build | apps uses facts |
| pmstart | git | build | apps uses facts |
| pmstart | host | build | apps uses facts |
| pmstart | hubclient | build | apps uses services |
| pmstart | kitlib-root | build | apps uses base |
| pmstart | launcher | build | apps uses services |
| pmstart | ledger | build | apps uses services |
| pmstart | machinesetup | build | same layer (apps): pmstart's row names machinesetup |
| pmstart | platform | build | apps uses base |
| pmstart | pluginsource | build | apps uses services |
| pmstart | pmwatch | build | same layer (apps): pmstart's row names pmwatch |
| pmstart | proc | build | apps uses base |
| pmstart | records | build | apps uses services |
| pmstart | schedtask | build | apps uses base |
| pmstart | worktree | build | apps uses work |
| pmwatch | config | build | apps uses facts |
| pmwatch | git | build | apps uses facts |
| pmwatch | hubclient | build | apps uses services |
| pmwatch | kitlib-root | build | apps uses base |
| pmwatch | launcher | build | apps uses services |
| pmwatch | machinesetup | build | same layer (apps): pmwatch's row names machinesetup |
| pmwatch | pluginsource | build | apps uses services |
| pmwatch | proc | build | apps uses base |
| pmwatch | schedtask | build | apps uses base |
| preflight | backlog | build | same layer (work): preflight's row names backlog |
| preflight | config | build | work uses facts |
| preflight | docs | build | work uses services |
| preflight | git | build | work uses facts |
| preflight | proc | build | work uses base |
| preflight | verdict | build | work uses base |
| preflight | worktree | build | same layer (work): preflight's row names worktree |
| proc | platform | build | same layer (base): proc's row names platform |
| pytests | config | build | services uses facts |
| pytests | git | build | services uses facts |
| pytests | host | build | services uses facts |
| pytests | kitlib-root | build | services uses base |
| pytests | platform | build | services uses base |
| pytests | proc | build | services uses base |
| records | config | build | services uses facts |
| records | fs | build | services uses base |
| records | git | build | services uses facts |
| records | proc | build | services uses base |
| redfirst | config | build | apps uses facts |
| redfirst | content | build | apps uses facts |
| redfirst | gate | build | apps uses work |
| redfirst | git | build | apps uses facts |
| redfirst | guard | build | apps uses guard |
| redfirst | kitlib-root | build | apps uses base |
| redfirst | lock | build | apps uses services |
| redfirst | platform | build | apps uses base |
| redfirst | proc | build | apps uses base |
| redfirst | records | build | apps uses services |
| redfirst | tooldeps | build | apps uses services |
| redfirst | worktree | build | apps uses work |
| reviewpack | config | build | work uses facts |
| reviewpack | docs | build | work uses services |
| reviewpack | gate | build | same layer (work): reviewpack's row names gate |
| reviewpack | git | build | work uses facts |
| reviewpack | kitlib-root | build | work uses base |
| reviewpack | landing | build | same layer (work): reviewpack's row names landing |
| reviewpack | modulemap | build | work uses services |
| reviewpack | ownerfiles | build | work uses services |
| reviewpack | preflight | build | same layer (work): reviewpack's row names preflight |
| reviewpack | proc | build | work uses base |
| reviewpack | records | build | work uses services |
| reviewpack | trailerscheck | build | work uses services |
| reviewpack | verdict | build | work uses base |
| schedtask | platform | build | same layer (base): schedtask's row names platform |
| schedtask | proc | build | same layer (base): schedtask's row names proc |
| scrub | forbidden | build | same layer (services): scrub's row names forbidden |
| stackskill | host | build | apps uses facts |
| stackskill | kitlib-root | build | apps uses base |
| staging | config | build | work uses facts |
| staging | dailyfull | build | same layer (work): staging's row names dailyfull |
| staging | fs | build | work uses base |
| staging | gate | build | same layer (work): staging's row names gate |
| staging | git | build | work uses facts |
| staging | landing | build | same layer (work): staging's row names landing |
| staging | records | build | work uses services |
| strayrestore | fs | build | work uses base |
| strayrestore | git | build | work uses facts |
| strayrestore | host | build | work uses facts |
| strayrestore | platform | build | work uses base |
| syncbase | attribution | build | work uses facts |
| syncbase | commit | build | same layer (work): syncbase's row names commit |
| syncbase | config | build | work uses facts |
| syncbase | git | build | work uses facts |
| syncbase | kitlib-root | build | work uses base |
| syncbase | records | build | work uses services |
| tooldeps | config | build | services uses facts |
| tooldeps | fs | build | services uses base |
| tooldeps | git | build | services uses facts |
| tooldeps | host | build | services uses facts |
| tooldeps | kitlib-root | build | services uses base |
| tooldeps | lock | build | same layer (services): tooldeps's row names lock |
| tooldeps | platform | build | services uses base |
| tooldeps | proc | build | services uses base |
| tooldeps | pytests | build | same layer (services): tooldeps's row names pytests |
| tools | agentview | build | entry uses guard |
| tools | allowrules | build | entry uses services |
| tools | attribution | build | entry uses facts |
| tools | autotasks | build | entry uses apps |
| tools | backlog | build | entry uses work |
| tools | ci | build | entry uses services |
| tools | commit | build | entry uses work |
| tools | config | build | entry uses facts |
| tools | content | build | entry uses facts |
| tools | coordstate | build | entry uses work |
| tools | dailyfull | build | entry uses work |
| tools | delegations | build | entry uses work |
| tools | docs | build | entry uses services |
| tools | forbidden | build | entry uses services |
| tools | fs | build | entry uses base |
| tools | gate | build | entry uses work |
| tools | git | build | entry uses facts |
| tools | host | build | entry uses facts |
| tools | hosts | build | same layer (entry): tools's row names hosts |
| tools | hubclient | build | entry uses services |
| tools | hubjobs | build | entry uses apps |
| tools | hubsite | build | entry uses apps |
| tools | hubvalidate | build | entry uses services |
| tools | kitcmd | build | entry uses apps |
| tools | kitlib-root | build | entry uses base |
| tools | landed | build | entry uses services |
| tools | landing | build | entry uses work |
| tools | launcher | build | entry uses services |
| tools | ledger | build | entry uses services |
| tools | loadnode | build | entry uses work |
| tools | lock | build | entry uses services |
| tools | machinepaths | build | entry uses services |
| tools | machinesetup | build | entry uses apps |
| tools | maintlint | build | entry uses apps |
| tools | modulemap | build | entry uses services |
| tools | ownerfiles | build | entry uses services |
| tools | pending | build | entry uses services |
| tools | pieces | build | entry uses services |
| tools | platform | build | entry uses base |
| tools | pluginsource | build | entry uses services |
| tools | pmdata | build | entry uses apps |
| tools | pmstart | build | entry uses apps |
| tools | pmwatch | build | entry uses apps |
| tools | preflight | build | entry uses work |
| tools | proc | build | entry uses base |
| tools | pytests | build | entry uses services |
| tools | records | build | entry uses services |
| tools | redfirst | build | entry uses apps |
| tools | reviewpack | build | entry uses work |
| tools | stackskill | build | entry uses apps |
| tools | staging | build | entry uses work |
| tools | strayrestore | build | entry uses work |
| tools | syncbase | build | entry uses work |
| tools | tooldeps | build | entry uses services |
| tools | trailerscheck | build | entry uses services |
| tools | worktree | build | entry uses work |
| trailerscheck | attribution | build | services uses facts |
| trailerscheck | config | build | services uses facts |
| trailerscheck | git | build | services uses facts |
| worktree | backlog | build | same layer (work): worktree's row names backlog |
| worktree | config | build | work uses facts |
| worktree | fs | build | work uses base |
| worktree | git | build | work uses facts |
| worktree | host | build | work uses facts |
| worktree | kitlib-root | build | work uses base |
| worktree | ownerfiles | build | work uses services |
| worktree | pieces | build | work uses services |
| worktree | platform | build | work uses base |
| worktree | proc | build | work uses base |
| worktree | records | build | work uses services |
| worktree | schedtask | build | work uses base |

## Ratchets

None.
