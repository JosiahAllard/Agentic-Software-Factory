---
name: session-handoff
description: Use at a coordinator's session start (fresh or as a successor), when the PM tells you to "checkpoint and stand down" for an account hand-off (coordinator or long-running session), and when a coordinator needs a successor for its context or has nothing left to start. Covers the start checks, the state note, the exact ready reply, and the PM's launcher steps.
---

# Session start and hand-off

A successor comes only for context or account reasons, never to get more work: work in `next` is pulled, not restarted for. A coordinator judges only its own context; an account reason comes as the PM's checkpoint. Never mid-landing, during a live session or an exclusive window.

## Coordinator start

1. List the agents and sessions (your host's agent-list tool). Another coordinator of this repo live (not your predecessor handing off): stop and tell the owner.
2. Read the coordinator profile and the agent profile (both optional) by section (`doc_index.py --find <topic>`), and `docs/ops/coordinator-runbook.md` where it exists (again after every compaction). No agent profile: every agent uses `<kit>/content/default-profile.md` with the README and instructions file, and each landing waits for the owner's go through the PM. No coordinator profile: read the repo instructions, README, CI config and scripts, and mark every inference. No work waits for a profile.
3. Run `python <kit>/tools/coord_state.py -C <repo> start`. Your start context carries the state note's path and last lines, `hub pending`, the `next` offer, the `--classify` list and the `rulebooks` line; a "could not read X" line: run that call yourself. A note labelled guessed: check it is `state-<your session name>.md`. A successor (started with "read <state file>") reads that note whole; one beginning `# Coordinator state:` renders again with `python <kit>/tools/coord_state.py -C <main checkout> show --session <old session>`.
4. Check in with the PM at once (the PM session your start context names, else Project Manager or pm-session; it may be on another account): repo, account, main tip, and "ask: holds, owner decisions since <your last PM check-in>, anything to act on". Its reply is your brief. No PM listed: ask the owner in your chat. A successor names every landing its note lists as not yet reported.
5. Check real state before acting on the note: `python <kit>/tools/land.py -C <main checkout> --queue` and the result record of each landing the note lists (`python <kit>/tools/gate.py wait-result <branch> --tip <sha> --kind land --timeout 0 -C <main checkout>`: a final record prints `RESULT`; exit 7: still queued or running, so start its `wait-result` again in the background, never resubmit); `git ls-remote <remote>` for main and branches; processes, jobs and ports for agents and runs; `python <kit>/tools/machine_lock.py status`; every lock and port the profile names.
6. A predecessor's agents report to it, not to you: before re-dispatching on a worktree, confirm they stopped (no process with it as cwd, no file change for 5 minutes); never wait for their reports.
7. `python <kit>/tools/worktree_remove.py -C <main checkout> --classify --apply`: it removes A and E trees and drops O registrations. Each B tree: land it or record why not. Each C tree: dispatch it, or stop its record and `--abandon`. Never land, reuse or remove an L tree. An open pull of the old session whose tree has gone quiet (no process, lock or queued verdict, 4 h idle): release it under your own key, `python <kit>/tools/backlog.py -C <checkout> --session <your key> release <ID> --reason "<why>"`. Send the PM the summary and the B/C list.
8. Pricing pass: `python <kit>/tools/backlog.py -C <repo> list --needs-priority`; leave follow-ups unpriced; check and price every other unpriced record 2-5 by skill backlog-pricing; one that needs the owner goes on the hub bound to it (skill hub-ops), with the other look-ahead questions.
9. Queue every LAND verdict the note lists that is not already submitted; fill your slots.
10. List your scheduled jobs (your host's cron tools); no fill check yet: create one, recurring at `17 * * * *`: "Fill check: count agents in flight against your cap. A free slot and no PM hold on new work: start an item of yours that can start, else pull the next record from `next` and dispatch it. Items waiting on a landing, a live slot or an owner answer hold no slot. Nothing startable, no agent in flight or waiting on a gate, landing queue clear: ready to stop. Else do nothing and say nothing."
11. First start of a day: the daily prune; weekly: the tool-set report (skill coordinator-dispatch, Upkeep).

## Checkpoint (the old session)

1. Dispatch nothing new and submit no landing; one already submitted finishes on its own.
2. The state note (coordinators: `<agent temp>/<repo slug>-coordinator/state-<session>.md`, outside the repo; another session: the path the profile names). Open items only, facts apart from assumptions: brief and scope; what is running; branches and worktrees; hashes; jobs and log paths; locks, ports and resources held; every LAND verdict (branch, tip, reviewer, range); landings not yet reported; open pulls (id, branch, `pulled_session`); pending owner items; blockers; the next step; the id of each detached run (lock runs, submitted landings, containers), which keep going. With the event log on, write only `judgments-<session>.md` (what the rendered note lacks), then `python <kit>/tools/coord_state.py -C <repo> show`.
3. Name each running agent's branch and worktree: those agents report to you, not the successor.
4. Reply to the PM exactly: `ready to hand off: <state file path>` (the PM's checkpoint); `ready to restart: <state file path>` (your context cannot carry the session on; the same account); `ready to stop: <state file path>` (nothing startable in `next`, every LAND verdict landed, the landing queue clear, no agent in flight or waiting on a gate). Record it on the hub with the same word and path: `hub_handoff_ready` (kind `handoff`, `restart` or `stop`; state_path; from your session key) or `python <kit>/tools/hub.py handoffs add --kind <kind> --state <state file path> --from <key>`; refused or unreachable: say so in the reply, do not retry.
5. Go idle; land nothing more. The PM stops your window after the successor checks in (after `ready to stop:`, with none).

## PM

`python <kit>/tools/launch.py handoff --session <key> --state <state file> --what-if`, then without `--what-if` (`--recycle` for a restart, which keeps the account). Once the successor has checked in, stop exactly that old session (`python <kit>/tools/launch.py stop --name <name>`) and tell the owner. On `ready to stop:` start no successor: stop it the same way, and start it again when its repo gets startable work, an owner answer or an approval: `python <kit>/tools/launch.py start --session <key> --account <n> --prompt "Start up now: run your start-up routine, read <state file path>, check in with the PM, then act on the note."`.

## Never

- Assume a session record that is gone means its process ended.
- Take a saved note as permission to send or act on anything it does not authorise.

Details: `<kit>/docs/launcher.md`; `<kit>/docs/coord-state.md`.
