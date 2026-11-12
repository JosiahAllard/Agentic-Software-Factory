# Agent bridge (optional install)

Messages between chats that are already open on one Windows machine: Claude Code terminal and
desktop chats and Codex desktop chats. The owner picks which chats may talk, in which direction.
A chat sends through MCP tools. The bridge stores each message durably, delivers it once through
the recipient host's native channel, and never resends a message whose outcome is unknown. What
works today, per host and direction, is under "Status" below. Read it before relying on
any of this: delivery has been shown live only for Codex chats answering a Claude chat and for a
Claude chat delivering to a Codex desktop chat through a writer lease.

**Claude and Codex are both served.** The bridge serves a peer only when its own list,
`peers.json`, names it and the peer has a half (`registry.py`). That list is apart from the kit's
`hosts.json`: a bridge peer need not be a kit host. It names Claude and Codex. A profile for a peer
that is not listed is refused with `native-provider-unavailable`. The refusal happens while the
configuration is checked, before that half's `bridge-profile.json` is read. Every pair of served
peers may be connected, except a pair `peers.json` refuses (Contracts, "Host pairs"); today the
one refused pair is Codex to Codex. Being served does not make a direction work: "Status" has
the per-direction limits. Claude to Codex needs an owner-granted writer lease (Contracts, "Writer
leases"), and the Codex terminal adapter is dead.

This is the bridge from `codex/robust-bridge-plan`, ported into the kit.
Its design history, the Phase 0 evidence and the
24 bridge docs it replaces are in git history (that branch, up to its tip when this port was made).

## Layout

| Path | What |
|---|---|
| `core/` | Host-neutral and Win32-free: `messages` (records, `DeliveryError`), `outbox` (durable SQLite store), `dispatch` (`CoreBroker`), `pairing` (owner-selected routes), `pmroutes` (the PM's routes, which need the PM launch mark), `hostpairs` (`HostPairs`, the host-pair rule), `owner` (owner decisions), `callers` (one-use caller proofs), `mcp` (the MCP tool server), `prompt` (delivered text header), `jsonwire` (bounded JSON lines), `contracts` (`AdapterDescription`, `Capability`, identities) |
| `port.py` | The port a host's half implements (below) |
| `peers.json` | The bridge's peers (`peers`) and the host pairs it refuses (`refused`, as `[sender, recipient]`) |
| `registry.py` | Which peers are served: `peers.json` names that have a half; `host_pairs()` reads the rule |
| `paths.py` | The entry scripts' kit paths, which the service verifies by command line |
| `win/` | The Windows service: `runtime` (config, assembly, `run_service`), `lifecycle` (supervisor, private state root, service marker), `leader` (singleton mutex and pipe), `controller`, `service` (private RPC handler), `rpc`/`rpcworker`/`worker`/`transport` (owned helper processes over named pipes), `frontend` (the MCP stdio frontend and its kernel verification), `channel` (an inherited native app channel), `hosts` (native image sets), `evidence` (bounded stable file reads), `ancestry` (process ancestry read one process at a time), `install` (installation preparation), `startup` (startup task), `owner_view`, `launch` (`-I -B`) |
| `ui/window.py` | The owner's connection window (tkinter) |
| `mcp_frontend.py` | The MCP server entry a host runs: `python -I -B <kit>/optional/bridge/mcp_frontend.py <marker>` |
| `tools/` | `bridge_service.py` (the service), `bridge_prepare.py` (installation), `bridge_connections.py` (open the owner window), and the two private helper entries `bridge_rpc_worker.py` and `bridge_pipe_worker.py` |
| `hosts/<name>/` | A host's half: its `HOST`, `bridge-profile.json` and native code |

Layer rules:
- The core kit never imports `optional/` (tests/host/test_layering.py).
- The bridge imports from the kit only `kitlib.ident`, `kitlib.proc` (`pipe`, `errors`,
  `process`, `job`), `kitlib.host.sessionmark` (the frontend's launch mark) and, in the Claude half,
  `hosts.claude.adapter.peer` (the peer wire format and pipe write). It does not read
  `hosts.json`. Its Win32 code is in `win/`, the halves and `kitlib/proc`.
- `core/` names no host and touches no Win32. No bridge code outside `hosts/` names a host
  (tests/bridge/test_port.py); host names outside the halves are data, in `peers.json`. The
  service loops over the served halves.

## The port (`port.py`)

The seam that the scaffold's `ports.py` only sketched, declared from what the service actually
calls. A half's package exports `HOST`, a `BridgeHost`:

| Field | Called | Must |
|---|---|---|
| `provider(controller, native_images, *, host_id, **profile)` | once per service start | return a Provider (below), from the half's validated `bridge-profile.json` settings |
| `delivery(provider, runtime)` | after every provider is built, before the service marker is published | return a `DeliveryAdapter`, or be `None` for a half that cannot deliver |
| `install(profile, *, python, marker, kit_root)` | by `bridge_prepare.py` | return an `InstallFragment`: the MCP server entry's name and metadata, files to add (such as a hook registration), and the paths they depend on |

Beside it, the half's `bridge-profile.json` is data, read without importing the half. It has the
keys `required`, `optional`, `directories` and `names` (the provider's settings) and an optional
`channel_env`: the environment variables in which the host's app may nominate its own tool pipe to
the MCP frontend it launches.

A Provider is called with each verified MCP frontend (`win.frontend.NativeFrontend`) and returns
a `SenderAttestor` for it. The service calls `attest()` on whatever it returns, and also
`prepare()` when that exists. `port.NativeSource(observe, prepare)` is the usual one, and a plain
callable is taken as `attest` itself. The Provider's other methods are optional, and the service
uses only those it finds:
- `sender_mode(binding)`: `'prompting'` or `'bypass'`
- `hook_sink`: a `HookSink`
- `refresh()`
- `close()`

| Protocol | Method | Contract |
|---|---|---|
| `SenderAttestor` | `attest(caller_proof) -> SenderAuthority` | The sender behind one MCP call, from private metadata the frontend received from its native parent. It never comes from tool arguments. Anything else, or a raise, refuses the call. |
| `DeliveryAdapter` | `describe() -> AdapterDescription` | Gives the adapter id, the contract version and the capabilities. The core sends only through an adapter that names the recipient's adapter id and `SEND`. |
| | `send(message) -> NativeResult` | Writes once. `BeforeWriteError` means no byte was written. Any other raise is an unknown outcome, which is never resent. |
| | `reconcile(message, native_id)` | Reads an earlier send's native receipt without resending. `None` means no news. |
| `ReturnChannel` | `close()` | An owned endpoint for replies or receipts. `port.OwnedDelivery(adapter, channel, ...)` closes it with the adapter. |
| `HookSink` | `(peer, payload)` | Takes a native hook event from a verified helper process. The service routes the frame to the one provider that has a sink. Two sinks refuse the frame. |

`registry.py` serves a peer when `peers.json` lists it and `optional/bridge/hosts/<module>/`
exists. The module is the peer name with `-` replaced by `_`. A folder for an unlisted peer is
never imported or read. A `peers.json` that cannot be read or is malformed refuses with
`invalid-bridge-peers`. The MCP frontend reads only the served halves' `channel_env`, from their
`bridge-profile.json`, and imports no host code. A served half whose profile cannot be read
nominates no channel, and the other halves keep working.

## Contracts

- **Owner-selected routes** (`core/pairing.py`, `core/owner.py`). A chat is registered only through
  a provider's verified observation, and registration alone connects nothing. The owner selects
  each route, one-way or both ways, in the connection window. Every choice is one-use, short-lived
  and bound to the exact chat reference. A route is remembered for the same logical chat (session,
  native id, adapter, account, workspace) and restored when it reopens. A queued message keeps its
  original full binding and is refused rather than moved onto a reopened process. Display titles
  grant nothing. No MCP tool writes owner policy. Beside the owner's routes the only other way a
  route opens is a PM route (below), which writes no owner choice.
- **Host pairs** (`core/hostpairs.py`, `peers.json`). Which host may send to which, checked in the
  core and named by no code: a chat belongs to the peer its adapter id names (the name, or the
  name and `-`; the longest listed name wins). Every ordered pair of listed peers is allowed,
  self pairs included, unless `refused` lists it, so a newly listed peer reaches every other one
  with no pair written down. An adapter id no peer names is outside the rule. The service's broker
  refuses a refused pair at admission (`host-pair-refused`, nothing queued) and again before any
  send, so a message queued before the rule held is refused (`host-pair-refused`) without reaching
  an adapter. `refused` holds `["codex", "codex"]`, the owner's decision; the Codex half's own
  checks below stay as a backstop at the write. The owner window applies the same rule
  (`OwnerControls`, given `host_pairs` by the controller): it lists no owner route in a refused
  direction, and Connect refuses one (`host-pair-refused`, nothing written; the window says
  the bridge does not connect these two kinds of chat). Stop still works on such a pair.
- **Writer leases** (`core/owner.py` `WriterLeases`, `hosts/codex/runtime.py`). A Codex chat is
  written only by a Codex desktop chat in an active turn, which is the native executor. A sender
  of another host has none, so the owner may lend one: in the window, select a Codex desktop chat
  (the writer) and then the Codex recipient, and click "Grant lease". The lease:
  - names one writer for one recipient, and a new grant for that recipient replaces the old one
  - has no expiry: it ends when the owner clicks Stop, the PM revokes it (`OwnerControls.revoke`,
    a private port), the service restarts, or either chat's session is observed with a different
    binding (for example a permission change). A failed live check, a turn change or a chat missed
    by one discovery pass ends nothing: the write-time checks below refuse while the chat is away.
    A closed chat keeps its lease (it cannot deliver, and it still holds one of the 32 slots)
    until Stop, a revoke or a restart. The window lists every lease with its grant time, marks one
    that cannot deliver now (a chat not listed, or failing its live check at the last full
    refresh: a failed check keeps a chat listed, and the 5 s re-read keeps the mark), and its
    "Stop selected connection" stops it by reference
  - is logged: every grant, revoke and replacement appends one JSON line (`at`, `event`, `by`,
    `writer`, `recipient`; chat references only) to `writer-leases.log` beside `pairings.sqlite`.
    A grant that cannot be logged is refused, and a writer it would have replaced keeps its lease
  - is granted with a one-use owner proof; a route choice's proof never grants a lease, and a
    lease's proof never selects a route
  - lives in the service's memory only, so a restart drops it; only its log is written to disk
  - is never an MCP tool, and no agent can grant, renew or read it
  - selects no route: the sender's own route to the recipient must still be selected

  At every write the writer must hold the lease, be live, be in an active turn, be on the
  recipient's account and not be the recipient, or the send refuses before any byte. A reply
  return from another host to a Codex chat uses the same lease. A Codex sender never uses a lease: the bridge does not carry
  Codex-to-Codex. The host-pair rule refuses it first (`host-pair-refused`); the half still
  refuses it at the write (`codex-direct-route-unavailable`). The window's lease buttons have no test.
- **Caller proofs** (`core/callers.py`). For each call, the frontend's attestor turns private
  metadata into a sender and task root, bound to that exact intent. A proof is used once and lasts
  5 seconds. The sender and task root are never tool arguments.
- **Durable outbox** (`core/outbox.py`, `core/dispatch.py`). Admission persists the whole body
  before any send. The operation id is deduplicated against its full original context. Dispatch
  re-attests both live bindings and the route, then writes inside a fenced transaction. A failure
  after the write, or a malformed receipt, becomes `unknown`. A refusal before the write keeps
  the adapter's code as its reason when the code is an identifier, and `native-before-write`
  otherwise. `unknown`, `held` and dispatched operations are never resent. After a restart, work
  that was in flight becomes `unknown`. A correlated native refusal is kept for its task root and
  blocks its queued siblings and later ids.
- **Replies** (`bridge_reply`, `CoreBroker.reply_to`). Only the operation's exact recipient may
  reply, and only over the reverse route the owner selected. A reply creates its own linked return
  delivery, so the original receipt still means only "reply recorded". Without a reverse route the
  reply is recorded but its return is permanently suppressed.
- **Native semantics**. The `held`, `expired` and `refused` states each keep their meaning, and
  none is inferred from silence. Delivered text carries the operation id and a reply instruction
  (`core/prompt.py`). Native permissions and approval prompts stay with each chat.
- **Codex delivery timing** (`NativeToolsPipe` in `hosts/codex/delivery_transport.py`). Each send
  runs one helper in two bounded phases. The start phase (helper spawn, its handshake and the
  host's re-attestation) has 3 s; a miss there fails before the write, since no request byte has
  left the helper. The response phase has 5 s counted from the commit, so a slow helper start never
  spends it; a miss there is uncertain and becomes `unknown`. A send reaches its outcome within
  8 s and ends, kill cleanup included, within 11.5 s, inside the service's 12 s worker join
  (`run_service` in `win/runtime.py`).
- **Service** (`win/`). There is one service per user and state root. It holds a thread-owned mutex
  and an exclusive current-user pipe, and its state root is private by ACL readback. It publishes
  `service.json` (pipe, pid, creation time) only after assembly succeeds. The frontend verifies the
  pipe server's kernel identity. The service verifies:
  - the helper's exact isolated command line
  - the frontend's exact command line
  - that the frontend's parent is one of the configured native images

  JSON identity claims grant nothing. Helpers run in job objects and are killed as a tree. A
  restarted service is rejoined by replaying only the frontend's initialize handshake; a tool call
  is never replayed.
- **Launch mark** (`mcp_frontend.py`, `win/frontend.py`, `Controller.launched`). A session the kit
  launched carries its kind in `PM_KIT_ROLE` (`kitlib/host/sessionmark.py`). The frontend reads it once,
  at start, so a later change to the session's environment reaches no frame. It sends the mark
  beside `channel` on every frame, the replayed handshake included. A value that is not a plain
  name (letters, digits, `-` and `_`, at most 64) is not sent. The service binds the mark into that
  frontend's `NativeFrontend` and refuses a frame that changes or drops it. Every authority the
  frontend attests carries it as `SenderAuthority.launched`. `Controller.launched(binding)` gives it
  only for the exact live chat that frontend attested last, and only while that frontend is
  attached; an unmarked frontend attesting the same chat clears it. A frame without `mark` is still
  served, so a frontend started before the mark existed keeps working until its host restarts. The
  reverse is refused: a service started before the mark refuses a new, marked frontend's frames as
  `invalid-private-request`, so restart the service after updating the kit. Unmarked sessions are
  unaffected. The mark's one use is PM routes (below): the `pm` mark lets a chat call the PM tools,
  and any mark makes a chat one the PM may connect.
  - Claude passes its MCP servers the session's whole environment, so a kit-launched Claude
    session's frontend is marked. Codex passes only `env_vars`, and the installer forwards no
    `PM_KIT_ROLE`. No kit launch starts a Codex chat yet, so every Codex chat is unmarked and needs
    the owner's click.
  - Claude Desktop chats are never marked: the kit launches none.
  - A mark belongs to one frontend process, so to one launched window.
  - The mark is only as strong as this bridge-only boundary (`core/owner.py`). Any process running
    as the same user can start a frontend with any `PM_KIT_ROLE`, or set another process's
    environment before it starts. The mark tells kit launches apart from the owner's own chats; it
    is no defence against code running as the owner.
- **PM routes** (`core/pmroutes.py`, `Controller.pm`, `OwnerControls.pm_routes` and `stop_pm`). The
  PM may connect two kit-launched chats without the owner's click; every other chat still needs it.
  - Only a chat whose authority carries the `pm` launch mark (`PM_MARK`, the launcher's
    `kitlib/launcher/window.py` `PM`) may call `bridge_pm_chats`, `bridge_pm_connect` or
    `bridge_pm_disconnect`; any other caller gets `pm-launch-mark-required`. No tool argument
    names a mark or a caller.
  - Both chats must be listed, live and kit-launched now (`Controller.launched` gives a mark), or
    the connect is refused (`sender-not-kit-launched`, `recipient-not-kit-launched`). The host-pair
    rule still applies, so Codex to Codex stays refused (`host-pair-refused`).
  - The owner's own row for a direction always wins. A direction the owner disconnected, by
    Stop or by "Stop selected connection" on a PM route, is refused to the PM (`owner-disconnected`) until
    the owner connects it. A direction the owner connected is reported as the owner's and the PM
    makes nothing there.
  - A PM route is held for the exact chat bindings, in the service's memory only: it writes no row
    to `pairings.sqlite`, a restart drops it, and a different binding for either chat's session
    ends it. At most 64 at a time.
  - At every admission and dispatch the route opens only while both chats are still
    kit-launched: a chat attested by an unmarked frontend, or whose frontend detached, refuses.
  - Reading a reply follows the ordinary route's rule: the PM route back still exists and the
    reader is kit-launched now. The replier is not checked, so a reply from a Codex chat whose
    turn has moved on still reads.
  - The PM removes only routes it made (`route-not-made-by-pm` for any other).
  - Logged: every connect, disconnect, owner stop, replacement and refused connect or disconnect
    appends one JSON line (`at`, `event`, `by`, `caller`, `sender`, `recipient`, and `sender_mark`
    and `recipient_mark` on a connect, `action`, `code` and `caller_mark` on a refusal, `unblocked`
    on an owner stop whose block was not written; chat references only) to `pm-routes.log` beside
    `pairings.sqlite`. A connect that cannot be logged is refused.
  - The owner window lists every PM route the owner's row does not decide ("A → B, connected by
    the PM since …", with "(cannot deliver now)" when a chat is not listed or no longer
    kit-launched) and re-reads the list every 5 s without discovery or live checks, keeping each
    row's mark from the last full refresh. "Stop selected connection" stops one
    direction and writes the owner's disconnect for it, even when a chat is not listed. The
    disconnect is written first; if it cannot be (for example `route-capacity`), the route is still
    stopped and the window says the PM could make it again, with the reason. The window's PM rows
    are tested only for the kept mark (tests/bridge/test_window_fill.py); Stop has no test.
  - The PM agent and its subagents share one frontend, so a subagent the PM starts carries the
    `pm` mark and may call the PM tools too.
  - The service detaches a frontend whose process has ended only when the next frame from any
    frontend arrives (`win/service.py`). Until then an exited marked frontend's chat keeps its mark,
    so a PM route to it still opens; the chat's live checks are then the only guard.
  - The new tools change the Claude half's tool list, so prepare again (`bridge_prepare.py`) and
    re-register `claude-hooks.json`. The Codex half does not offer the PM tools.
- **MCP tools**:
  - `bridge_connections`
  - `bridge_send` (a queued receipt is not delivery)
  - `bridge_receipt`
  - `bridge_read_reply`
  - `bridge_reply`
  - `bridge_events`
  - `bridge_poll_native` (refreshes a receipt and never resends)
  - `bridge_pm_chats`, `bridge_pm_connect`, `bridge_pm_disconnect` (PM only, above)

## Connections window (`ui/window.py`)

The window displays; only `OwnerControls` acts, with the same checks as before (who may connect,
grant a lease or deliver is unchanged). Tests: tests/bridge/test_window_ux.py.
- **Chat names.** Each chat is listed by the name its host already shows: a Claude terminal chat by
  its session record's `name` field (`~/.claude/sessions/<pid>.json`), a Claude desktop chat by
  its task title, a Codex chat by the last `thread_name` for its thread in the
  Codex profile's `session_index.jsonl` (`hosts/codex/context.py` `thread_name`; read at each
  bridge call, at most 4 MiB, display only). A chat with no name is "unnamed chat" and a short
  label: the host half's `short` detail (an untitled Claude desktop task gives 8 characters of its
  id after `local_`), else the first 8 characters of its session id. A name grants nothing and is
  never part of a binding, so a rename changes no route.
- **Buttons.** Select two chats, then Connect (both directions, or first to second, as the list
  beside it says) or Stop. "Grant lease" and "Stop lease" act on a Codex writer and its recipient. Refresh runs
  discovery and live-checks each chat once (`OwnerControls._live`, shared by the chat list and
  the rows); the list re-reads every 5 s without either.
- **State.** A full refresh marks a listed chat "not answering" when its live check fails (between
  turns or closed); it stays listed, cannot deliver now, and the timed re-read keeps the mark.
- **Current connections and leases.** One row per direction: "A → B, connected by you" (with
  "delivered through W's lease" when a lease carries it; such a row can deliver only while W does
  too, so it is marked when W does not answer), "Lease: W delivers to B for other apps,
  granted by you since T", and "A → B, connected by the PM since T". A row whose chat cannot deliver
  now says so; a chat no longer listed keeps its last name and says "(not listed: it needs a bridge
  call, then Refresh)". "Stop selected connection" stops one row.
- **What to do.** The line under the chat list says most chats are listed only after a bridge call
  since the bridge started (Codex chats and Claude terminal chats; Claude desktop chats are listed
  on Refresh), and that a chat running without calling the bridge cannot be seen: have it make a
  bridge call (any bridge tool), then Refresh. The window's own text names no host (tests/bridge/
  test_port.py `Layout`). After a restart the window counts the remembered connections
  waiting for their chats (each direction once) and says to have each chat make a bridge call,
  then Refresh; the connections come back by themselves, and leases must be granted again. A
  Connect or Stop on a chat that left the list says the chat is no longer listed and how to bring
  it back; a Connect on a chat that does not answer says it is between turns or closed. Only a refusal
  raised before anything is written says "Nothing was changed" (ui/window.py `BEFORE_WRITE`).
  A Stop whose PM-route cleanup fails after the owner's row is written says the connection is
  stopped but a PM connection may not be (`owner-change-partly-done`); any other failure says what
  changed is not known and to click Refresh.
- **Limits.** Stop on your own connection needs both chats listed, whether or not answering; a
  PM row's Stop does not need them listed. The waiting count cannot tell the
  host of a chat that is not listed, so it may count a remembered Codex-to-Codex row written
  before the host-pair rule. A Codex index past 4 MiB gives no name, and two chats may share a
  name (the short label is not shown then). No Codex-to-Codex feature is built (owner ruling).

## Configuration

`runtime.json` (protocol 1) holds these keys:
- `kit_root`: must be the kit the service runs from
- `python`: must be the running interpreter
- `state_root`, `host_id`
- `native_hosts`: kind → one or up to 8 absolute executable images. An image may belong to only
  one kind.
- `profiles`: kind → settings

Every kind must have a half. Its settings must match the half's `bridge-profile.json`, which lists
the required and optional settings, which are directories and which are names. A path must be
absolute and regular. A name is limited to `[A-Za-z0-9_-]` with no `__`. The configuration cannot
name a module, a caller, an owner approval or an adapter.

A path that fails its check is refused naming the setting, never the path:
`runtime-path-missing-<setting>` when it does not exist, `runtime-path-invalid-<setting>` when it is a
link or the wrong kind. The setting is written with `-` for `.` and `_`, as
`runtime-path-missing-codex-native-image-0` or `runtime-path-missing-codex-desktop-executable`.

A half may move paths its host's own updates move (port.py `locate`). Before the check, at
preparation and at every start, the Codex half (`hosts/codex/locate.py`) does this:
- A path inside a Codex app package folder (`...\WindowsApps\OpenAI.Codex_<version>_<arch>__<publisher id>\`)
  moves to the same place in the current install of that package family, as Windows reports it.
- A `...\OpenAI\Codex\bin\<16 hex>\codex.exe` path becomes the one copy there whose bytes are the
  current package's `app\resources\codex.exe`. Without a package path in the configuration it is
  left as written.

Its refusals are `codex-package-unavailable`, `codex-package-ambiguous` (two package families),
`codex-package-location-refused` (the current install is not directly in a WindowsApps folder, or a
configured path leaves the package) and `codex-cli-image-unmatched-<setting>` or
`codex-cli-image-ambiguous-<setting>`. Profile setting names are checked against the schema first.

## Install

Installing the bridge adds nothing to the kit's host shells. With the bridge not installed, no
host shell, plugin manifest or settings file mentions it (tests/bridge/test_port.py checks the
shells).

1. **Prepare.** Run
   `python -I -B <kit>/optional/bridge/tools/bridge_prepare.py --config <input.json> --output <new folder>`.
   It writes inactive review files into a new private folder and touches no native profile, state
   or task:
   - `runtime.json`
   - `mcp.json`: each host's MCP server entry. The command is the isolated frontend command line
     above, with the marker's absolute path. The name and metadata come from the half's
     `install()`. Each entry includes `env_vars` only for that host's declared `channel_env`
     names; current environment values are never copied. This forwards a host-nominated channel
     to its frontend. It grants no writer lease (only the owner's click in the window does) and
     does not prove live delivery readiness.
   - each half's own files, such as `claude-hooks.json`
   - `start-bridge.ps1` and `startup-task.xml`: a per-user, least-privilege task, not registered
   - `manifest.json`: every file's hash, every dependency and every bound path, marked
     `prepared,notactivated`
2. **Register in each host.** The owner adds that host's `mcp.json` entry, and any hook file, to
   the host's own configuration. The halves say what goes where:
   - Claude: the server is a plugin server. Its `plugin_name` and `scoped_name` must be the plugin
     that carries it, because the provider checks the plugin-scoped tool names. The PreToolUse hook
     in `claude-hooks.json` goes in that plugin's (or the user's) hooks.
   - Codex: the `agent-bridge` server goes in its MCP configuration. Codex has no bridge hook.
3. **Start.** Register `startup-task.xml`, or run
   `python -I -B <kit>/optional/bridge/tools/bridge_service.py --config <prepared>/runtime.json`.
   Add `--connections` to open the owner window. `bridge_connections.py --marker <marker>` reopens
   it later.

Step 2 is deliberately manual. The source kept registration disabled until live acceptance, and
nothing has passed it ("Status"). Writing the entries into settings automatically, the way
machine setup adds the guard, is a small step once the owner accepts a live canary: the halves
already render the exact entries. A relocated kit needs a new preparation, because every path is
absolute.

## Adding a host half

Add `optional/bridge/hosts/<module>/` with:
- `__init__.py` exporting `HOST = port.BridgeHost(...)`, with the native modules imported lazily
- `bridge-profile.json`, with `channel_env` if the host's app nominates a tool pipe
- the provider, and optionally the delivery adapter

The half is served only once `peers.json` lists the peer (docs/new-host.md). A peer need not be
a kit host in `hosts.json`. Its pairs with every other peer are allowed unless `refused` lists
them, and `tests/bridge/test_host_pairs.py` walks them all with no change to the test.

Its tests go in `tests/bridge/`. The host's conformance class then runs the bridge part of the
suite by itself (`tests/host/bridge_conformance.py`, through `bridge_conformance.mixin(HOST)`).
The suite checks:
- the half's `HOST`, its profile schema and its install fragment
- on Windows, its delivery adapter's `describe` and `send` on a fake recipient pipe, when the
  conformance class supplies `bridge_delivery()`

## What was deleted, and what moved

The Phase 0 experiments and the never-wired scaffold were deleted once nothing imported them:
- `kitlib/bridgeprobe/`: AppContainer, enrollment, handle hand-off, leadership, worker broker,
  workspace and the probe runtime
- `claude/bridge_phase0.py`
- `tools/bridge_live_probe.py`, `tools/bridge_schema_probe.py` and `tools/bridge.py`
- `kitlib/bridge/{bootstrap,journal,pipeline,restart,delegation,schema_probe,ports,disabled,status,errors}.py`
- the tests of all of the above

tests/bridge/test_port.py keeps them deleted.

Four helpers were load-bearing and moved instead:

| Was | Now |
|---|---|
| `kitlib/bridgeprobe/winpipe.py` | `kitlib/platform/windows/pipe.py`. Its probe-only JSON `send` and `frames_until_close` went to the test helper `tests/bridge/pipes.py`. |
| `bridgeprobe.runtime.ProbeError` | `kitlib.platform.ProcError` |
| `bridge/bootstrap.identifier`, `digest` and `integer` | `kitlib/ident.py` (`IdentError` replaces `BootstrapError` for these) |
| `claude/bridge_phase0.read_record` | `optional/bridge/hosts/claude/records.py` |

`codex/pmkit_codex/bridge_client.py` is now `hosts/codex/client.py`, and the bridge owns it. It is
the Codex plugin's client of this frontend. The Codex host (`hosts/codex/`) keeps no copy of
it. If its diagnostics ever probe the bridge, they import `optional.bridge.hosts.codex.client`
lazily and optionally. The legacy `session_delivery.py` helper was not ported, since nothing in
the bridge uses it.

## Status

What the bridge does today, per host and direction. The evidence comes from the source branch's records, as of
`codex/robust-bridge-plan` at its tip when the port was made. The port moved code and changed little behaviour; what it did
change is listed in "Changes from the source".

**Claude and Codex are both served.** The bridge's own `peers.json` lists both, so the service
and `bridge_prepare.py` accept a Codex profile, and the MCP frontend reads Codex's channel variable.
Every pair of the two may be connected except Codex to Codex, which `peers.json` refuses ("Host pairs"). Being served is not being deliverable. The limits below still hold:
- Claude to Codex is delivered only through an owner-granted writer lease ("Writer
  leases"). Live runs showed it on 2026-10-26 and 2026-10-31 (see "Retest on main").
- Only the Codex desktop app can deliver to a Codex chat.
- The Codex terminal adapter is dead.

**Bottom line:**
- Live delivery has been shown. On 2026-10-26 a Codex chat answered a Claude chat's `ping` with a
  correlated "pong" (`reply-received`), and the Codex reply reached the Claude chat as a pushed
  message. On 2026-10-31 a Claude chat delivered a ping and a one-line file-append task to a Codex
  desktop chat through an owner-granted writer lease, and both replies came back to the Claude chat.
  The appended line was checked by diff. The conditions are under "Running a sitting".
- No live run reached `scheduled` for Claude to Codex (10-26 receipts stayed `unknown`; 10-31
  ping-005 reached `recipient-accepted`), and `bridge_read_reply` often
  fails with a not-observed error although the reply was pushed (see "Known problems seen in the
  2026-10-31 sitting").
- No idle chat has been shown to wake on a message: every recipient had made a bridge call first.
- The service, storage, routes, frontend and helpers are built and tested against owned pipes,
  processes and fixture records.
- Claude to Claude, and the PM routes, have had no live run.

### Senders: who can call the bridge

| Host and form | Attested how | State |
|---|---|---|
| Claude desktop chat | Its signed-in account, its active local task and its live registry record, matched to the frontend's kernel parent | Works on fixtures. A read-only probe matched one real desktop task and its permission class. |
| Claude terminal chat | Only through the bridge's PreToolUse hook (`hosts/claude/hook.py`). The account file alone gives no permission mode. | Works on fixtures. One live canary, run with the source's paths, measured the hook's plugin-scoped provenance shape. Claude's unattended permission flow denied that call, so no live bridge call has run. |
| Codex desktop chat | The native tool metadata, the active turn in its rollout, the creator account matched to the current file login, and the effective sandbox and approval mode | Works on fixtures. A read-only probe verified a real desktop host, chat, turn and mode. |
| Codex terminal chat | The same observer covers terminal rollouts. | Fixture shapes only. No live terminal was open to probe. |

### Delivery: sender → recipient

| From → to | Adapter | State |
|---|---|---|
| any → Claude terminal or desktop | `claude-peer`. It writes Claude's protocol-1 peer packet into the recipient's own pipe and receives status packets on an owned return pipe. | Built. It writes once, and the outcome is **unknown** (`native-outcome-unconfirmed`) until a correlated `held`, `refused` or `expired` packet arrives. Neither acceptance nor a reply is confirmed natively. Return expectations live only for the endpoint's lifetime (512 at most). Waking an idle chat is unproved. |
| Codex → Codex | none | **Refused by design.** The bridge is for Claude and Codex and outside tools; Codex chats message each other through Codex's own messaging. `peers.json` refuses the pair, so the broker refuses it at admission (`host-pair-refused`, nothing queued) and again before any send. Behind that, the Codex half still refuses a Codex sender before writing (`codex-direct-route-unavailable`), and a lease never serves one. |
| Claude → Codex | `codex-desktop`, through an owner-granted **writer lease**: a Codex desktop chat in an active turn carries the message as the native executor | Delivered and replied on live runs (2026-10-26 and 2026-10-31; see "Retest on main"). Receipts on the 10-26 runs stayed `unknown`, never `scheduled`; on 10-31 ping-005 reached `recipient-accepted`. The owner grants it in the window for one recipient until Stop, a PM revoke or either chat's binding being replaced (a turn change or a failed observation ends nothing); it lives in memory, a service restart drops it, and every grant is logged. A writer whose turn has ended is refused (`codex-direct-context-unverified`) until it makes a bridge call in a new active turn. The sender's own route must still be selected, and the writer must be live, in an active turn, on the recipient's account and not the recipient, at every write. Without a lease the adapter refuses before writing (`codex-direct-route-unavailable`). A reply return from another host to a Codex chat needs the lease too. A correlated success means "scheduled" only, never "ran" or "replied"; no live run has shown `scheduled`, and the replies came back through `bridge_reply`. The check after the write accepts the recipient's turn having moved on (the delivery starts a new turn) but still requires the same app process, image and channel, the lease, and the recipient's same rollout file holding the captured turn; it drops no chat from the observed list. A failure there gives **unknown**, its reason `native-outcome-uncertain:<adapter code>`. The tools pipe is a measured private protocol, not a public contract. Both chats must be in the same app, distinct and on the same account. |
| Codex terminal as recipient | `codex-terminal` (`CodexTerminalAdapter`) | **Dead.** It advertises no capability and every send refuses before writing. No verified control channel to the original terminal exists (the default control socket was absent; the experimental queue methods are unproved). The service never installs it. |
| Codex chat's reply → a Codex sender | none | Does not arise: the Codex-to-Codex message it would answer is refused at admission (`host-pair-refused`). A return delivery queued earlier is refused before any send, and the Codex half would refuse it at the write too. |

Routes, durable queueing, receipts, events, refusals, replies and the owner window work the same
for every host. They are core and service mechanics, and they are tested.

Writer lease limits:
- The writer must be in an active turn at every write. A writer whose turn has ended writes
  nothing, so in practice it waits inside a long-running turn.
- The Codex app attributes the message to the writer's chat, and the writer's approval mode
  governs the call, so a prompt may appear in the writer.
- Only Codex desktop chats in the same app and on the same account qualify, never a terminal.
- A restart drops every lease. Leases are capped at 32 at a time.
- A closed chat keeps its lease, and its slot, until Stop, a revoke or a restart; it cannot
  deliver. The window still lists it, marked as unable to deliver (a full refresh live-checks
  both chats, so a closed chat that stays listed is marked too, and the 5 s re-read keeps that
  mark until the next full refresh), and can Stop it.
- A correlated refusal still blocks the recipient's whole chat, which is its task root.
- The owner window's lease buttons have no test.

PM routes ("PM routes"): the PM-marked chat may connect two kit-launched chats; built and
tested on fixtures only, with no live run. Limits:
- Only Claude terminal sessions the kit launched carry a mark today, so only they can be PM-routed.
- A subagent of the PM shares the PM's frontend and its `pm` mark.
- An exited marked frontend keeps its chat's mark until the next frame from any frontend reaches
  the service.
- A restart drops every PM route. The owner window's PM rows and its 5 s re-read have no test.
- The owner window lists and connects no Codex-to-Codex route ("Host pairs").

Connections window ("Connections window"): built and tested on fixtures and on a withdrawn
Tk window (tests/bridge/test_window_ux.py); no live run yet. It lists chats by their host's name
(Claude session name or desktop task title, Codex `thread_name` from `session_index.jsonl`),
else "unnamed chat" and 8 characters of the id; marks a chat that fails its live check "not
answering"; lists each connection with who made it and the lease it goes through, and each lease
with who granted it; and says how to bring a chat back (a bridge call, then Refresh), including
after a restart, when it counts the remembered connections waiting for their chats. Limits:
- Stop on the owner's own connection needs both chats listed, whether or not answering.
- A chat that runs without calling the bridge is still not seen; the window says so.
- The Codex name is read from a file the Codex app writes; its format is observed, not documented.

Idle roster at frontend start: **one live probe so far (2026-10-31).** A kit-launched Claude
session (`launch.py start --no-agent`) had its frontend running and was not listed until it
called `bridge_connections`; the Codex frontend question below is not answered by it. A chat is
listed today only after its first bridge tool call, except Claude desktop chats, which are listed
when their frontend starts. When a frontend starts, neither half receives a chat identity it can
verify: a Codex binding needs the thread, turn and auto-review flags from a tool call's
`x-codex-turn-metadata`, and a Claude terminal binding needs `permission_mode` from a PreToolUse
hook. Listing Codex chats from rollout files is rejected: those files are tied to no frontend and
the auto-review flags would have to be invented. The probe must capture:
- the Codex desktop frontend's `initialize` params and their `_meta`
- the environment variable names that frontend receives
- whether Codex starts one frontend per thread or one per app

The idle chats the PM could route are kit-launched Claude terminals (Codex chats carry no launch
mark). Listing them before their first bridge call needs a SessionStart or UserPromptSubmit hook
that carries `permission_mode`.

### Install and operation

| Item | State |
|---|---|
| `bridge_prepare.py` | Writes inactive review files: the runtime config, MCP entries, Claude's hook fragment, the startup task and a hashed manifest. The scheduler accepted the task XML with validate-only. |
| Registering the MCP servers and hook in a host | **Manual** ("Install"). It was never activated by the source. |
| Startup task | Rendered and validated, never registered |
| Service (`bridge_service.py`) | Idle start tested from a relocated copy, with the source's paths: marker, leadership, no chats, no SEND, clean shutdown. Needs re-running on this layout. |
| Codex updates | An update moves both pinned Codex executables: the app package folder holding `ChatGPT.exe` and the `bin\<16 hex>\codex.exe` copy. Preparation and every service start move them to the current install (`hosts/codex/locate.py`; "Configuration"); tests on fake package trees, and the Windows package lookup checked against the installed app (26.1023.6123). An update while the service runs is not followed: frontends of the new `codex.exe` are refused (`frontend-host-unverified`) until the service restarts. |
| Migrating from the older bridge the owner runs | Not done. Its in-memory sends must be reconciled before any cutover. |
| Linux and macOS | None. Named pipes, ACLs, tokens and job objects |

### Changes from the source

The port changes these behaviours, all visible to a caller or an operator:
- **Error classes and messages:**
  - The probe's `ProbeError` is now `kitlib.platform.ProcError`, with the message
    `process call refused: <code>`. It was `live probe stopped: <code>`. The codes are unchanged.
    `read_record` raises it too.
  - `identifier`, `digest` and `integer` raise `kitlib.ident.IdentError`, a `ValueError`, with
    the message `value refused: <code>`. They raised `BootstrapError` with
    `bootstrap fixture rejected: <code>`.
- **Prepared manifest:** `manifest.json`'s `source.module` is `optional.bridge.win.install`. It was
  `kitlib.bridgewin.install`.
- **Hook routing:** a native hook frame goes to the one served provider that has a hook sink.
  When none or two have one, the frame is refused (`hook-producer-unavailable`). The source sent
  it to the provider named `claude`.
- **Serving:** a peer's half is used only when the bridge's `peers.json` lists the peer (not
  `hosts.json`: a bridge peer need not be a kit host). Claude and Codex are both listed. A profile
  for an unlisted peer fails the configuration check with `native-provider-unavailable`, and its
  half's files are not read. The source wired Codex whenever a profile named it.
- **Host pairs:** the core refuses a pair `peers.json` lists as refused, at admission and before
  any send (`host-pair-refused`). Codex to Codex is the one refused pair; the source queued such a
  message and the Codex half failed it at the write (`codex-direct-route-unavailable`). Every
  other pair, Claude to Claude included, is unchanged.
- **Channel variables:** these are data in each half's `bridge-profile.json` (`channel_env`), read
  only for served hosts. The source's frontend read `CODEX_APP_TOOLS_PIPE_PATH` unconditionally.
- **Attesting:** the service calls `attest()` on any object a provider returns that has one.
  `NativeSource.attest` is its `observe`. A source whose `prepare` is present but not callable is
  refused (`invalid-native-source`), so client `_meta` never reaches `attest()` unreplaced.
- **Channel variable names** are compared without case, as Windows does: two names that differ
  only by case are refused in one profile and counted once across halves.
- **Paths:** the entry scripts moved:
  - the frontend is `optional/bridge/mcp_frontend.py`
  - the service, prepare, connection and helper entries are in `optional/bridge/tools/`
  - the Claude hook is `optional/bridge/hosts/claude/hook.py`
  - the half profiles are `optional/bridge/hosts/<name>/bridge-profile.json`

  The service verifies these exact paths on every command line. So every installation file
  prepared before the port no longer matches: the MCP entries, the hook fragment, `runtime.json`,
  `start-bridge.ps1` and `startup-task.xml`. Prepare again, and register again.

The source's live evidence was gathered with the old paths. This covers the Claude hook canary
(its plugin-scoped PreToolUse provenance and the hook's kernel ancestry) and the idle start from
a relocated copy. Both need re-running against this layout before they count for it.

### Known problems seen in the 2026-10-31 sitting

Observations; only the one marked fixed has been fixed since.
- `bridge_read_reply` returned `reply-chat-not-observed` for `ping-20261031-005` and for
  `notes-append-20261031-002`, although each reply was pushed into the Claude chat.
- One early send returned the frontend's catch-all "bridge connection unavailable", which hides
  the real code.
- The recipient chat (the kit folder) dropped out of the window's list after its turns ended. Its
  routes vanished from Current connections, and the lease row showed the chat "(not listed) ...
  cannot deliver". It came back after a `bridge_connections` call.
  Fixed since: the pairing store no longer drops a chat on a failed live
  check (a Codex chat whose turn moved on). The chat stays listed and every use checks it again,
  so its routes stay in Current connections, and the lease row's note comes from a live check.
- The appended line used CRLF while `notes.md` is LF.
- Codex's own Windows sandbox failed some commands with `CreateProcessSecurityEnvironment`
  0x8007000F, probably because it cannot see drive Z:. This is in the owner's Codex
  configuration, not the bridge.

### Open items

1. Re-run the Claude hook canary and the relocated idle start on this layout.
2. A live canary from an owner-selected Claude chat to a Claude chat: delivery, wake and a
   correlated reply.
3. The writer lease was shown live on 2026-10-26 and 2026-10-31 (ping and append task, both
   replied). Not yet shown: a run with no owner permission line in the recipient chat (a standing
   line was in place on 10-26, and the explicit line was needed on 10-31).
4. A verified control channel to the original Codex terminal before `codex-terminal` advertises
   SEND.
5. Precise task ancestry for incoming operations. Codex currently uses the chat as the task root,
   so a refusal blocks that whole chat.
6. Durable return-address recovery and retention for the Claude peer.
7. Owner-present registration in each host, with a rollback, after reconciling the older bridge.
8. A full gate of the combined tree on Windows. The source's last full gate was 6,366 tests in
   335 modules, nine skipped, before its final assembly commits.

### Retest on main

Live round trip: **step 2 passed on 2026-10-26 (probe U, second and third sends).** `bridge_read_reply`
returned "pong". This needed a standing approval from the owner in the Codex chat, and that chat
had to be listed, which happens only after it makes a bridge call. On the 10-26 runs the
receipt stayed `unknown`. Details below.

**Live sitting, 2026-10-31: the pass bar is met**, under conditions (a), (b) and (d) of "Running
a sitting" below. Details under "Live sitting, 2026-10-31".

**Pass bar, re-scoped by the owner on 2026-10-26:** the bridge is for Claude and Codex chats
(plus outside tools); Codex-to-Codex is out of scope. The retest passes when:
(a) Claude to Codex: a Claude chat delivers to a Codex chat. That covers step 2 and step 3, the
small task checked by diff. The writer Codex chat under the lease is the delivery path for a
Claude sender (`hosts/codex/runtime.py`; without a lease a Claude sender is refused), so step 3
is Claude to Codex, not Codex to Codex. Probe U-2 and U-3 meet step 2. **Step 3 passed on
2026-10-31:** the append task went through the writer lease and was checked by diff (see "Live
sitting, 2026-10-31").
(b) Codex to Claude: a Codex chat delivers to a Claude chat. **Passed on 2026-10-26.** The Codex
recipient's `bridge_reply` for step 3 arrived in the sending Claude session as a pushed message,
headed "Reply to bridge operation: ..." and carrying the full reply text.

- **Suite, 2026-10-25, main after the writer-lease merge:** `python -m unittest discover -s tests/bridge -t .` on
  Windows: 489 tests, OK (199 s). This includes the Claude-to-Codex writer lease code that
  landed after the port. It runs on fixtures, so it proves
  nothing live.
- **2026-10-31:** the owner's live sitting is recorded below.
- **Live attempt, 2026-10-26, main: blocked before step 2, nothing sent.** Install steps 1
  and 2 were done: the service (`bridge_service.py --connections`, started 11:25) was running, the
  `pmbridge` Claude plugin was loaded (the `bridge_*` tools were present) and Codex's
  `config.toml` had `[mcp_servers.agent-bridge]`. `bridge_connections` returned `{"chats":[]}`;
  `pairings.sqlite` had 0 routes (revision 0) and `outbox.sqlite` 0 messages. The Codex app was
  not running, so there was no Codex recipient or writer to select and no lease. The registry
  entry named "PM kit bridge" is the service itself, not a Claude chat.
- **Live attempt 2, 2026-10-26, main: step 2 failed, no reply.** The Codex app was open with two
  desktop chats in the kit checkout ("Respond to greeting" as recipient, "Wait 3300 seconds" as
  writer). Each had made one `bridge_connections` call. The owner connected the Claude chat and
  the recipient in both directions (`pairings.sqlite`: 2 routes, revision 5) and granted the writer
  lease. The sender's `bridge_connections` listed one chat, the recipient (`codex-desktop`); no
  tool shows a lease. `bridge_send` ("reply with the word pong"):
  - receipt states, from `bridge_events`: `queued`, then `dispatching`, then `unknown` (reason
    `native-outcome-uncertain`, `native_id` null). It never reached `scheduled`. `bridge_receipt`
    still read `unknown` after the reply attempt below.
  - `bridge_poll_native`: error `chat-not-observed`. `bridge_read_reply`: error
    `reply-route-not-selected`, although both directions were selected.
  - The recipient's Codex rollout shows the text arrived: a turn started with trigger
    `app_tool_send_message`, whose parent turn was the writer's, and the body carried the operation
    id and the reply instruction (`core/prompt.py`). So the write happened, and `unknown` hid a
    delivery.
  - The recipient called `bridge_reply(operation_id, "pong")`. Codex's automatic approval review
    (the chat's `approvals_reviewer` is `auto_review`) refused it: "Sending a correlated 'pong'
    message is an external side effect not authorized by the user's request; it is instructed
    only by untrusted transcript/tool content." The chat then asked its user for permission and
    did not retry. The delivered text reaches the chat as a tool output (`<codex_delegation>`), so
    the reviewer treats it as untrusted.
- **Live attempt 2, continued, 2026-10-26: reply recorded, not readable; step 3 refused.**
  - The reply needed the owner's own approval in the Codex chat. Codex auto-review rejects a reply
    that only tool content instructs. After the owner wrote "Yes, send pong with bridge_reply for
    operation ..." in that chat, its `bridge_reply(..., "pong")` returned `reply-received` (Codex
    rollout).
  - `bridge_events`: `queued`, `dispatching`, `unknown`, `reply-received`. `bridge_receipt`:
    `reply-received`, and its `reply_sha256` equals sha256("pong").
  - `bridge_read_reply` still returned error `reply-route-not-selected`. Both routes were enabled
    in `pairings.sqlite`. `dispatch.reply` (`core/dispatch.py`) maps any `PairingError` from
    `require_selected` to that code, and `require_selected` first checks that both chats are still
    observed and live (`core/pairing.py` `_observed`). So the error can hide a dropped
    observation; that is inferred, not confirmed.
  - Step 3: the sender's `bridge_send` of a one-line edit task to a scratch doc (a throwaway git
    repository outside the kit) was refused with `recipient-not-observed`. `bridge_connections` then
    returned `{"chats":[]}`, so the Codex chat had dropped out of the sender's list after its reply.
    Nothing was written; the scratch doc is unchanged.
- **Probe U, 2026-10-26: step 2 passed on the second and third sends (U-2, U-3).**
  - What changed since attempt 2: the owner typed a standing approval line in the recipient chat.
    It was not an approval for any one operation.
  - A chat is listed only after it makes a bridge call in a turn, and it drops out of the list
    later (on 2026-10-26 it had dropped out after its reply turn). A send to a chat that is not
    listed is refused with `recipient-not-observed`. The second send below was accepted about 40
    seconds after the recipient's listing turn had ended. So the listing lasted past that turn's
    end; how long it lasts was not measured.
    Since 2026-10-31 the pairing store no longer drops a chat on a failed live check:
    it stays listed and every use checks it again.
  - First send: `bridge_connections` returned `{"chats":[]}`, and `bridge_send` was refused with
    `recipient-not-observed`. Nothing was queued.
  - Before the second send, the owner wrote a standing approval in the Codex recipient chat
    ("I approve replying with bridge_reply to bridge operations from connected chats in this
    chat."). That chat then made one `bridge_connections` call in its own turn, which ended.
  - Second send, made right after that: `bridge_events` `queued`, `dispatching`, `unknown`,
    `reply-received`. The recipient's own `bridge_reply("pong")` went through with no auto-review
    rejection and no approval for this operation (Codex rollout). `bridge_receipt` returned
    `reply-received`, and `bridge_read_reply` returned `{"reply":"pong"}`.
  - While the receipt was still `unknown`, `bridge_receipt` once returned error
    `caller-context-unverified`. It returned the receipt on a later call.
  - U-2 timeline (recipient rollout): the delegated turn started at 16:37:15Z with auto-review on.
    `bridge_reply` was called at 16:37:19Z, and `reply-received` came back at 16:37:25Z. The turn
    ended at 16:37:29Z, with no user message and no rejection in it.
  - U-3, the same text about ten minutes later: events `queued`, `dispatching`, `unknown`,
    `reply-received`. The recipient's `bridge_reply("pong")` went through at 16:48:13Z with no
    user message and no rejection, and `bridge_read_reply` returned `{"reply":"pong"}`. The
    recipient made no bridge call between U-2 and U-3, yet the send was accepted. So these runs
    do not support "a chat stays listed only for the turn in which it made a bridge call". What
    keeps a chat listed, and what drops it, is still open.
    Since 2026-10-31 the pairing store no longer drops a chat on a failed live check:
    it stays listed and every use checks it again.
  - Step 3 was not rerun.
- **Step 3, 2026-10-26, new Codex recipient and writer chats: delivered and replied, task not
  done.**
  - `bridge_connections` (Claude sender) listed one chat, the Codex recipient (`codex-desktop`).
    It showed no route or lease details.
  - `bridge_send` with the one-line append task: `queued`, `dispatching`, `unknown`. A later
    `bridge_events` call once returned error `caller-context-unverified`.
  - The recipient's rollout shows the task delivered through the new writer chat
    (`app_tool_send_message`). There was no auto-review rejection and no hand approval.
  - The recipient tried to read the kit's shell-traps skill from its Codex plugin cache first.
    That read failed ("Access to the path ... is denied", then "Cannot find path ... because it
    does not exist"). Following the repo's refusal rule, it stopped and made no edit.
  - Its `bridge_reply` returned `reply-received`. `bridge_read_reply` returned that blocker text
    ("Blocked; scratch file unchanged. Required shell skill read was denied. ...").
  - `git diff` on the scratch repo was empty.
  - The bridge path worked end to end, task out and reply back. The reply was also pushed into
    the sending Claude session, which passes Codex to Claude (pass bar (b)).
  - Cause of the failed task: the skill file is not missing; the cache content was refreshed.
    The Codex sandbox is denied the read through the `%USERPROFILE%\.codex\plugins\cache`
    junction to the cache folder on another drive, and "Cannot find path" follows from that
    denial. The owner's fix, scheduled: make the junction a real folder. After it: a read test
    from a new chat, fresh lease setup, then step 3 once more under a new operation id.

- **Live sitting, 2026-10-31, main: passed.** The owner sat at the machine with the Codex app
  open.
  - Claude to Codex, writer lease, ping: operations -003 and -004 failed
    (`codex-direct-context-unverified`) while the writer ran `ping` in the background. With the
    writer told "run ping in the foreground and wait", `ping-20261031-005` reached
    `recipient-accepted`. Codex replied "pong", and the reply was pushed into the Claude chat as a
    cross-session message. `bridge_read_reply` had first returned `reply-chat-not-observed`.
  - Claude to Codex, task through the writer lease: `notes-append-20261031-002`. The recipient
    appended exactly one line ("bridge test") to the scratch `notes.md`, with a CRLF ending in an
    LF file. Its first `bridge_reply` was rejected by Codex's automatic approval reviewer ("would
    disclose an internal local filesystem path ... without trusted user authorization"). After the
    owner typed an explicit permission line in that chat, the reply went through as
    `reply-received` and was pushed into the Claude chat. `bridge_read_reply` never returned it
    (`reply-chat-not-observed`). In the same chat a `ping` failed with the sandbox error
    `0x8007000F`.
  - Check after the sitting: `git diff` in the scratch repo showed exactly one added line,
    "bridge test".
  - Frontend count (python `mcp_frontend` processes): baseline 3 (claude, codex, codex). After
    hello chat 1: 7 (a new codex frontend plus 3 python-parented ones). After hello chat 2: 4 (the
    python-parented ones gone; no new frontend for chat 2). On refresh there were no hello-chat
    rows. Pass. Checked again after the sitting, with Codex closed and the probe stopped: 0.
  - Probe session (`launch.py start --no-agent`): its frontend (parent claude) appeared, and the
    session was not listed until it called `bridge_connections`; after that it was listed. The
    probe and the recipient connected both ways. `pong-20261031-1` went through, "pong" was
    pushed back, and the recipient showed no approval prompt. Pass.
  - Shutdown: the probe was stopped and its frontend was gone. After Codex closed there were 0
    frontends. Reopening Codex gave no hook-trust prompt, and its header showed the kit plugin
    0.4.0-main.5566. Pass.
  - Pass bar: Claude to Codex pong, the task through the writer lease, and Codex to Claude were
    all met, given conditions (a), (b) and (d) below.

Live round-trip plan (the owner grants the lease; no agent can):
1. Prepare and register the bridge for both hosts ("Install", steps 1 and 2), start the
   service with `--connections`.
2. Round trip (read "Running a sitting" below first: conditions (a), (b) and (d)): in the
   window select the Claude chat as sender and a Codex desktop chat as recipient, select a second
   Codex desktop chat in an active turn as writer, click the lease button. The Claude chat calls
   `bridge_send` with a one-line task ("reply with the word pong"). Observed: on the 2026-10-26
   runs `queued`, `dispatching`, `unknown` (`native-outcome-uncertain`); on 2026-10-31 ping-005
   reached `recipient-accepted`. No run reached `scheduled`. The Codex chat replies with
   `bridge_reply` (`reply-received`) and the Claude chat reads it with `bridge_read_reply`. The
   pass is the reply, not the receipt: a receipt short of `scheduled` followed by
   `reply-received` with the right text passes. Record each receipt state.
3. If that passes, one small real delegated task (for example, add one sentence to a scratch
   doc in a throwaway worktree), same path, checked by diff. The lease lasts until it is stopped; the writer must make a bridge call in each new turn before it can deliver.
3b. Codex to Claude: the Codex chat's reply (or its own `bridge_send`) reaches the Claude chat as
   a pushed message, and the Claude chat records it.
4. Record the result here. A pass lets coordinators delegate suitable work to Codex workers.

Running a sitting. The 2026-10-31 sitting showed these conditions:
- (a) The Codex writer must be in a foreground active turn. Codex runs `ping` in the background
  and ends its turn after about 20 s, which gives `codex-direct-context-unverified`. Tell it to
  "run ping in the foreground and wait".
- (b) A Codex recipient needs the owner's explicit line in its chat ("You may answer bridge
  messages from <sender> with bridge_reply in this chat"). Without it, Codex's own automatic
  approval reviewer rejects the `bridge_reply` as disclosing a local path.
- (c) A chat that has dropped out of the connections list needs a fresh bridge call
  (`bridge_connections`) to be listed again.
- (d) Address the recipient by its reference, not its chat id: a chat id gives
  `recipient-not-observed`.

Owner steps (PowerShell; `<repo>` is the kit checkout, `<kit>` the kit folder, `<out>` a new
private folder; `<out>-input.json` follows the half profiles, see "Configuration"):
1. `Set-Location <repo>`
2. `python -I -B <kit>\optional\bridge\tools\bridge_prepare.py --config <out>-input.json --output <out>`
3. Review `<out>\manifest.json`, then add `<out>\mcp.json` entries: the Claude entry to the
   plugin's MCP config plus `<out>\claude-hooks.json` to its hooks; the `agent-bridge` entry to
   Codex's MCP configuration. Restart both hosts.
4. `python -I -B <kit>\optional\bridge\tools\bridge_service.py --config <out>\runtime.json --connections`
5. A chat appears in the window only after it makes one bridge tool call (for example
   `bridge_connections`) during an active turn. The call's metadata is what enrolls it
   (`hosts/codex/runtime.py` `observe`, `controller.enroll`; `hosts/codex/context.py` checks the
   active turn). For the writer, make that call in the same turn as its long-running command.
   Then, in the window, select the sender and recipient routes, select the writer Codex chat (in
   an active turn) and the recipient, and click the lease button. Tell the worker to run the
   round trip. See "Running a sitting" above for conditions (a) to (d): a foreground writer turn,
   the owner's explicit reply line in the recipient chat, and addressing the recipient by its
   reference.
6. Rollback: stop the service, remove the entries added in step 3.

### Verification of this port

This port was checked on Linux only, with a ctypes shim and no Win32, so nothing was run on
Windows:
- `tests/bridge`: 425 tests, of which 159 pass on Linux. The 266 that fail all open real pipes,
  tokens or processes, and they are exactly the tests that failed the same way on the source
  branch. Checked after merging step 9: there are no other new failing ids.
- `tests/platform/test_pipe.py`: 10 tests; the 9 Windows-only ones skip on Linux.
- `tests/ident`: 7 tests, all passing on Linux.
- The bridge part of the conformance suite runs for Claude and for Codex: 4 tests each. 3 pass
  on Linux, and the fake-pipe delivery test skips there. Codex records no fake recipient pipe yet,
  so that test also skips for Codex on Windows.

Before relying on the port, run the bridge's own packages on Windows:
`python tools/gate.py --full`, or at least `tests/bridge`, `tests/proc` and `tests/host`.

## Tests

`tests/bridge/` covers the core, the service, both halves and the port.
`tests/platform/test_pipe.py` covers the promoted pipe helpers, and `tests/ident/` covers
`kitlib.ident`. Most of these tests use real named pipes, job objects and process tokens, so they
run on Windows only ("Status" "Verification").
