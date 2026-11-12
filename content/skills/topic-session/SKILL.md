---
name: topic-session
description: Use as the PM when the owner wants to drive one topic's work directly in a plain session - a batch of records built on one branch, the owner deciding as it goes. Covers the worktree, the coordinator's batch hold, the brief, `launch.py topic`, and how the work comes back for review and landing.
---

# A topic session

Plain owner-driven session (no agent), mark `topic`, named `topic-<slug>`: one record batch, one branch, own linked worktree. Builder's backlog rights: read, pull, edit text (scope too), release/block own pulls; never add, price, close, land, merge. Record writes refused unless `batch:` is its branch (`<kit>/docs/backlog.md` "Roles"). Files no records (owner, coordinator do).

## Starting one (the PM)

1. `python <kit>/tools/new_worktree.py -C <repo> <area>/<slug>` (prints path, branch). Never main checkout.
2. One message to coordinator: record ids, branch, topic session. It releases held pulls (skill backlog-ops), sets hold per record: `python <kit>/tools/backlog.py -C <its worktree> set <ID> --batch <branch>` (recorded decision, audited lead write). Wait for reply naming each.
3. Brief in scratch from `brief-template.md` beside this file, every placeholder filled: records, worktree, scope, off limits, session key if `session:`, your session name. Split or new class: never small; design (parts, one job each, dependency direction, net size) reviewed before build. Adds checks; never restates, cuts a skill's.
4. `python <kit>/tools/launch.py topic --worktree <worktree> --brief <brief file> --slug <slug> [--account <n>|auto] --plan` shows window; without `--plan` starts (`<kit>/docs/launcher.md` "Topic"). Refuses non-linked worktree, missing brief, live same name.
5. Name to owner. Stop by name when owner done: `python <kit>/tools/launch.py stop --name topic-<slug>`.

## What the hold does

- Held from other branches: `next` passes it (`held for the batch on <branch>`); any pull onto another branch refused, `--reason` or not.
- Own-branch pull takes whole batch first: rank, cap no refusal, no `preempt` line. Other refusals stand: live pull elsewhere, fold-in guard, resources, `requires:`.
- Lift: coordinator's own step, `set <ID> --unset batch`, when owner drops a record or topic abandoned (then releases session's pulls).

## When the session reports

Session runs touched tests, sends branch, tip, results, records; one gate after review, on coordinator's ask. Forward to coordinator as work: review, verdict, land, close. Neither you nor session lands.
