---
name: topic-session
description: Use as the PM when the owner wants to drive one topic's work directly in a plain session - a batch of records built on one branch, the owner deciding as it goes. Covers the worktree, the coordinator's batch hold, the brief, `launch.py topic`, and how the work comes back for review and landing.
---

# A topic session

A topic session is a plain session (no agent) the owner drives directly, launched with the session mark `topic` and named `topic-<slug>`. It builds one batch of records on one branch in its own linked worktree. Its backlog rights are a builder's: it reads, pulls, and edits text on (its scope included), releases or blocks its own pulls. It never adds, prices, closes, lands or merges. Every write naming a record is refused unless that record's `batch:` is its branch (`<kit>/docs/backlog.md` "Roles").

A session the owner drives creates no new records for the work it is given: it pulls the existing records batched for it, and when the owner says it is done it lands through the coordinator. The owner files any new record for it, or asks a coordinator to; its `add` is refused.

## Starting one (the PM)

1. Make the worktree in the repository: `python <kit>/tools/new_worktree.py -C <repo> <area>/<slug>`. It prints the worktree's path and branch. The session never works in the main checkout.
2. Message the repository's coordinator, in one message: the record ids, the branch, and that the owner drives them in a topic session. The coordinator releases any of them a pull holds (skill backlog-ops) and, one call per record, sets the hold: `python <kit>/tools/backlog.py -C <its worktree> set <ID> --batch <branch>`. Setting the batch is the recorded decision. It is a lead's write and audited. Wait for its reply naming each record set.
3. Write the brief in your scratch folder from `brief-template.md`, beside this file. Fill in every placeholder: the records, the worktree, the scope, what is off limits, the session key when the records carry `session:`, and your session's name.
4. Start it: `python <kit>/tools/launch.py topic --worktree <worktree> --brief <brief file> --slug <slug> [--account <n>|auto] --plan` shows the window first; the same call without `--plan` starts it (`<kit>/docs/launcher.md` "Topic"). It refuses a folder that is not a linked worktree, a missing brief and a live session of that name.
5. Tell the owner the session's name. Stop it only by name, when the owner is done: `python <kit>/tools/launch.py stop --name topic-<slug>`.

## What the hold does

- A record batched for a branch is held from every other branch: `next` elsewhere passes it over (`held for the batch on <branch>`), and any role's pull onto another branch is refused, with or without `--reason`.
- The topic session's pull of a record batched for its own branch takes the whole batch first. The rank order and the cap are no refusal, and no `preempt` line is written. Every other refusal stands: a live pull on another branch, the fold-in guard, resources and `requires:`.
- Lifting a hold is the coordinator's, in its own step: `set <ID> --unset batch`. It does that when the owner drops a record from the topic, or the whole topic is abandoned (it also releases the session's pulls then).

## When the session reports

The session gates its tip and messages you the branch, the tip, the gate result and the records it covers. Send that to the repository's coordinator as work: it reviews the branch, gives its verdict, lands it and closes the records. Never land it yourself, and never let the session land.
