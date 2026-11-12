---
name: session-handoff
description: Use at a coordinator's session start (fresh or as a successor), when the PM tells you to "checkpoint and stand down" for an account hand-off (coordinator or long-running session), and when a coordinator needs a successor for its context or has nothing left to start. Covers the start checks, the state note, the exact ready reply, and the PM's launcher steps.
---

# Session start and hand-off

Successor: context (own call) or account (PM's checkpoint) only; never for work (`next` is pulled). Never mid-landing, live session, exclusive window.

## Coordinator start

1. List agents, sessions (host tool). Other live coordinator here, not predecessor: stop, tell owner.
2. Coordinator, agent (optional) profiles by section (`doc_index.py --find <topic>`); `docs/ops/coordinator-runbook.md` if any, reread after compaction. No agent profile: `<kit>/content/default-profile.md` + README + instructions; landings need owner's go via PM. No coordinator profile: instructions, README, CI, scripts, inferences marked; never wait.
3. `python <kit>/tools/coord_state.py -C <repo> start`; start context: note path + tail, `hub pending`, `next`, `--classify`, `rulebooks`; "could not read X": run it. Guessed note: `state-<your session name>.md`. Successor ("read <state file>"): whole note; `# Coordinator state:` note: `python <kit>/tools/coord_state.py -C <main checkout> show --session <old session>`.
4. PM check-in now (start context's PM, else Project Manager or pm-session; any account): repo, account, main tip, "ask: holds, owner decisions since <your last PM check-in>, anything to act on". Reply = brief; no PM: owner. Successor: name unreported landings.
5. Real state before note: `python <kit>/tools/land.py -C <main checkout> --queue`; per listed landing `python <kit>/tools/gate.py wait-result <branch> --tip <sha> --kind land --timeout 0 -C <main checkout>` (`RESULT`: final; exit 7: running, rerun `wait-result` in background, never resubmit); `git ls-remote <remote>` main + branches; processes, jobs, ports; `python <kit>/tools/machine_lock.py status`; profile's locks, ports.
6. Predecessor's agents report to it; never await them. Re-dispatch a worktree only once stopped (no cwd process, 5 min unchanged); else wait, tell PM.
7. `python <kit>/tools/worktree_remove.py -C <main checkout> --classify --apply` (removes A, E; drops O). B: land or record why not. C: dispatch, or stop record + `--abandon`. L: never land, reuse, remove. Old session's pull quiet 4 h (no process, lock, queued verdict): `python <kit>/tools/backlog.py -C <checkout> --session <your key> release <ID> --reason "<why>"`. PM: summary, B/C list.
8. Pricing: `python <kit>/tools/backlog.py -C <repo> list --needs-priority`; price 2-5 (skill backlog-pricing), follow-ups unpriced; owner-needed: bound hub question (skill hub-ops), batched with look-ahead ones.
9. Queue note's unsubmitted LAND verdicts; fill slots.
10. No fill-check job (host cron tools): create, recurring `17 * * * *`: "Fill check: count agents in flight against your cap. A free slot and no PM hold on new work: start an item of yours that can start, else pull the next record from `next` and dispatch it. Items waiting on a landing, a live slot or an owner answer hold no slot. Nothing startable, no agent in flight or waiting on a gate, landing queue clear: ready to stop. Else do nothing and say nothing."
11. Day's first start: daily prune; weekly: tool-set report (skill coordinator-dispatch, Upkeep).

## Checkpoint (the old session)

1. No dispatch, submit; submitted landings finish alone.
2. State note (coordinator: `<agent temp>/<repo slug>-coordinator/state-<session>.md`, outside repo; else profile's path). Open items, facts apart from assumptions: brief, scope, running work, branches, worktrees, hashes, jobs, logs, held locks/ports/resources, LAND verdicts (branch, tip, reviewer, range), unreported landings, open pulls (id, branch, `pulled_session`), owner items, blockers, next step, detached run ids (lock runs, landings, containers). Event log on: only `judgments-<session>.md` (what render lacks), then `python <kit>/tools/coord_state.py -C <repo> show`.
3. Name running agents' branches, worktrees.
4. Reply to PM exactly: `ready to hand off: <state file path>` (PM checkpoint); `ready to restart: <state file path>` (context spent, same account); `ready to stop: <state file path>` (nothing startable, all LAND landed, queue clear, no agent running or gating). Hub, same word + path: `hub_handoff_ready` (kind `handoff`, `restart` or `stop`; state_path; from your key) or `python <kit>/tools/hub.py handoffs add --kind <kind> --state <state file path> --from <key>`; refused/unreachable: say so, no retry.
5. Idle, land nothing. PM stops you after successor check-in (`ready to stop:`: none).

## PM

`python <kit>/tools/launch.py handoff --session <key> --state <state file> --what-if`, then without `--what-if` (`--recycle`: restart, same account). Successor in: stop exactly the old one, `python <kit>/tools/launch.py stop --name <name>`; tell owner. `ready to stop:`: no successor; stop it; on startable work, owner answer, approval: `python <kit>/tools/launch.py start --session <key> --account <n> --prompt "Start up now: run your start-up routine, read <state file path>, check in with the PM, then act on the note."`.

## Never

- Gone session record = ended process.
- Saved note = permission beyond what it authorises.

Details: `<kit>/docs/launcher.md`; `<kit>/docs/coord-state.md`.
