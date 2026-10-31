# Agent bridge: status

What the bridge does today, per host and direction. Design and install are in
`optional/bridge/README.md`. The evidence comes from the source branch's records, as of
`codex/robust-bridge-plan` at its tip when the port was made. The port moved code and changed little behaviour; what it did
change is listed in "Changes from the source".

**Claude and Codex are both served.** The bridge's own `peers.json` lists both, so the service
and `bridge_prepare.py` accept a Codex profile, and the MCP frontend reads Codex's channel variable.
Every pair of the two may be connected except Codex to Codex, which `peers.json` refuses (README
"Host pairs"). Being served is not being deliverable. The limits below still hold:
- Claude to Codex is delivered only through an owner-granted writer lease (README "Writer
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

## Senders: who can call the bridge

| Host and form | Attested how | State |
|---|---|---|
| Claude desktop chat | Its signed-in account, its active local task and its live registry record, matched to the frontend's kernel parent | Works on fixtures. A read-only probe matched one real desktop task and its permission class. |
| Claude terminal chat | Only through the bridge's PreToolUse hook (`hosts/claude/hook.py`). The account file alone gives no permission mode. | Works on fixtures. One live canary, run with the source's paths, measured the hook's plugin-scoped provenance shape. Claude's unattended permission flow denied that call, so no live bridge call has run. |
| Codex desktop chat | The native tool metadata, the active turn in its rollout, the creator account matched to the current file login, and the effective sandbox and approval mode | Works on fixtures. A read-only probe verified a real desktop host, chat, turn and mode. |
| Codex terminal chat | The same observer covers terminal rollouts. | Fixture shapes only. No live terminal was open to probe. |

## Delivery: sender → recipient

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
  deliver. The window still lists it, marked as not listed, and can Stop it.
- A correlated refusal still blocks the recipient's whole chat, which is its task root.
- The owner window's lease buttons have no test.

PM routes (README "PM routes"): the PM-marked chat may connect two kit-launched chats; built and
tested on fixtures only, with no live run. Limits:
- Only Claude terminal sessions the kit launched carry a mark today, so only they can be PM-routed.
- A subagent of the PM shares the PM's frontend and its `pm` mark.
- An exited marked frontend keeps its chat's mark until the next frame from any frontend reaches
  the service.
- A restart drops every PM route. The owner window's PM rows and its 5 s re-read have no test.
- The owner window lists and connects no Codex-to-Codex route (README "Host pairs").

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

## Install and operation

| Item | State |
|---|---|
| `bridge_prepare.py` | Writes inactive review files: the runtime config, MCP entries, Claude's hook fragment, the startup task and a hashed manifest. The scheduler accepted the task XML with validate-only. |
| Registering the MCP servers and hook in a host | **Manual** (README "Install"). It was never activated by the source. |
| Startup task | Rendered and validated, never registered |
| Service (`bridge_service.py`) | Idle start tested from a relocated copy, with the source's paths: marker, leadership, no chats, no SEND, clean shutdown. Needs re-running on this layout. |
| Codex updates | An update moves both pinned Codex executables: the app package folder holding `ChatGPT.exe` and the `bin\<16 hex>\codex.exe` copy. Preparation and every service start move them to the current install (`hosts/codex/locate.py`; README "Configuration"); tests on fake package trees, and the Windows package lookup checked against the installed app (26.1023.6123). An update while the service runs is not followed: frontends of the new `codex.exe` are refused (`frontend-host-unverified`) until the service restarts. |
| Migrating from the older bridge the owner runs | Not done. Its in-memory sends must be reconciled before any cutover. |
| Linux and macOS | None. Named pipes, ACLs, tokens and job objects (plan section 6). |

## Changes from the source

The port changes these behaviours, all visible to a caller or an operator:
- **Error classes and messages:**
  - The probe's `ProbeError` is now `kitlib.proc.errors.ProcError`, with the message
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

## Known problems seen in the 2026-10-31 sitting

Observations only; none is fixed here.
- `bridge_read_reply` returned `reply-chat-not-observed` for `ping-20261031-005` and for
  `notes-append-20261031-002`, although each reply was pushed into the Claude chat.
- One early send returned the frontend's catch-all "bridge connection unavailable", which hides
  the real code.
- The recipient chat (the kit folder) dropped out of the window's list after its turns ended. Its
  routes vanished from Current connections, and the lease row showed the chat "(not listed) ...
  cannot deliver". It came back after a `bridge_connections` call.
- The appended line used CRLF while `notes.md` is LF.
- Codex's own Windows sandbox failed some commands with `CreateProcessSecurityEnvironment`
  0x8007000F, probably because it cannot see drive Z:. This is in the owner's Codex
  configuration, not the bridge.

## Open items

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

## Retest on main

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
1. Prepare and register the bridge for both hosts (README "Install", steps 1 and 2), start the
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
private folder; `<out>-input.json` follows the half profiles, see README "Configuration"):
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

## Verification of this port

This port was checked on Linux only, with a ctypes shim and no Win32, so nothing was run on
Windows:
- `tests/bridge`: 425 tests, of which 159 pass on Linux. The 266 that fail all open real pipes,
  tokens or processes, and they are exactly the tests that failed the same way on the source
  branch. Checked after merging step 9: there are no other new failing ids.
- `tests/proc/test_pipe.py`: 10 tests; the 9 Windows-only ones skip on Linux.
- `tests/ident`: 7 tests, all passing on Linux.
- The bridge part of the conformance suite runs for Claude and for Codex: 4 tests each. 3 pass
  on Linux, and the fake-pipe delivery test skips there. Codex records no fake recipient pipe yet,
  so that test also skips for Codex on Windows.

Before relying on the port, run the bridge's own packages on Windows:
`python tools/gate.py --full`, or at least `tests/bridge`, `tests/proc` and `tests/host`.
