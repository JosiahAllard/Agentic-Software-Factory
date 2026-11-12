---
name: coordinator-dispatch
description: Use as a coordinator before any dispatch - pulling the record, the worktree, writing a brief, choosing the role and tier, a creative (art, 3D, sound) brief, assigning shared resources or a measurement, requesting a deep-worker session, continuing or resuming agents, and the daily prune and weekly tool-set upkeep runs.
---

# Coordinator: dispatch

Roles named bare; host instructions map them to dispatch names.

## Before the agent starts

- **Pull at dispatch**, never after: `python <kit>/tools/backlog.py -C <worktree> pull <ID>` (`--session <key>`: session-owned). Finished work on an unpulled record gone from the offer: pre-emption. Unpriced: refused; price first (skill backlog-pricing); builder never prices.
- **Bundle** same-area open records, still reviewable; never high-risk with unrelated work.
- **One workstream = one branch = one worktree**: `python <kit>/tools/new_worktree.py -C <repo> <branch>` unless profile differs; dispatch into it, never a host-isolated one.
- Another session's worktree: written only by its agent or one dispatched in; never you via `-C`, never round a refusal.
- Running topic session's record files: no dispatch.
- `.kit.json`, owner-only paths (`--find kit-json`, key `owner.paths`): owner via PM, never a builder.
- Redesign/mockup slices: umbrella record's element checklist, one line per element, each slice-owned; closes when all ticked against landed slices (`<kit>/docs/backlog.md` "Umbrella records").

## The brief

Fields:
- Role: role; why raised (roster.md).
- Record: ID, observed failure, done state; no unneeded machinery (option, tool, seam).
- Design: design-doc section + "design first: report your design and wait for approval" (skill builder, step 3); small change: approved home, contract, files. Split or new class: never small; design (parts, one job each, dependency direction, net size) reviewed.
- Read: exact files, sections, ranges, `path:Symbol` entries, code/tool/test extended; doc topics, never pasted rules. No paths: ~35 builder calls.
- Scope: their files; others' files, whose.
- Risk: level; risk-map path or why higher (unlisted no reason).
- Resources: assigned locks, ports, devices.
- Tests: touched tests while building; no gate before review; one gate on reviewed tip after verdict.
- Owner: binding decisions, paraphrased, no ruling id.
- Limits: no land, merge or push.

Rules:
- Extra checks, overridden steps only; never restates or shortens a skill checklist. No-tests brief keeps skill builder's "Delete stale tests" (commit names current cover).
- Repo brief template (`--find brief`) followed.
- Review (analyst): `python <kit>/tools/review_pack.py -C <repo> <branch>` first, + approved design.
- Docs-writer: keyword lines `ID | status | area | keywords | ref`, max 300 chars (`specialty-docs` `doc-writing.md`, "Findings and doc edits").
- Owner-approved change: author asks from worktree (skill hub-ops, step 5), files at approval's base; hub commits on approval, author never.
- Specialty (`specialty-*` in `<kit>/content/skills/`): "Load skill `<name>` first"; role >= its `Dispatch:` minimum. Creative: `creative.md` beside this file, first.

## Which role, which tier

`roster.md` beside this file: roles, tiers, reviewer model, deep-worker requests, routing. Tiers, host model maps: `<kit>/docs/ROSTER.md`.

## Shared resources and measurements

- Serialise profile's unshareables (test machine, database, ports, deploy target, lock files: `--find lock`). Unlisted, collision possible: stop, ask.
- Quoted-number run owns the resource: pause all, run, release. Design, arms, decision rule written first; counts with sample size; rule applied literally; later readings apart.
- Scratch folder per agent. "Nothing running": check processes.

## While agents run

- Review hand-backs at once; same agent while cache warm (`<kit>/docs/ROSTER.md`: longer-window role). Profile's round count per artefact, then reseed from written summary; review analyst, long-context strong/deep coder: fresh. Never park a large-context agent for a small fix.
- Busy agent may miss queued message: resend in full.
- Background run dies with agent: replacement reads logs first; before resume/replace check its jobs, lock runs, ports, worktree.
- Few long-running agents: each notification costs a full-context turn.
- **Agents that die together** (API error, usage limit): shared resources first (orphans, stale locks, ports, main vs remote); continue each with short summary, most valuable first. Usage limit: tell PM, wait.
- Host planner may lack write tool: plan in report. Never assume one host's agents, tools, file names on another.

## Upkeep

Daily prune, stale worktrees, weekly tool-set run: `upkeep.md` beside this file.
