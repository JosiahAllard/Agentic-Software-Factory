---
name: coordinator-dispatch
description: Use as a coordinator before any dispatch - pulling the record, the worktree, writing a brief, choosing the role and tier, a creative (art, 3D, sound) brief, assigning shared resources or a measurement, requesting a deep-worker session, continuing or resuming agents, and the daily prune and weekly tool-set upkeep runs.
---

# Coordinator: dispatch

Roles named bare; host's instructions map role name to dispatch name.

## Before the agent starts

- **Pull at dispatch**, never after work: `python <kit>/tools/backlog.py -C <worktree> pull <ID>` (`--session <key>`: session-owned). Offer moves: finished work on an unpulled record dropped from it needs a pre-emption to close. Unpriced: pull refused; price first (skill backlog-pricing); builder never prices.
- **Bundle** same-area open records onto the branch, still reviewable; never high-risk change with unrelated work.
- **One workstream = one branch = one worktree**: `python <kit>/tools/new_worktree.py -C <repo> <branch>` unless profile says otherwise. Dispatch into it; never ask host for an isolated one (confines writes there).
- Another session's worktree: writes by its owning agent or a fresh agent dispatched into it; never you with `-C` from outside, never to route round a refusal.
- Running topic session owns its records' files: no work on them.
- `.kit.json` or other owner-only path (`--find kit-json`, key `owner.paths`): owner via PM, never a builder.
- Redesign or mockup-built record slices: mockups -> element checklist on umbrella record, one line per element, each owned by a slice; closes when every element ticked against a landed slice (`<kit>/docs/backlog.md` "Umbrella records").

## The brief

| Field | What it holds |
|---|---|
| Role | role; why raised (roster.md) |
| Record | ID, observed failure, true when done; no machinery record lacks (option, new tool, seam) |
| Design | area design-doc section + "design first: report your design and wait for approval" (skill builder, step 3); small change: approved design (home, contract, files). Split or new class: never small, never set by docs; design (parts, one job each, dependency direction, net size) reported, reviewed |
| Read | exact files, sections (ranges in large file), known entry symbols (`path:Symbol`), code/tool/test extended; doc topics to look up, never pasted rule. No paths: about 35 builder calls |
| Scope | files theirs; files not, and whose |
| Risk | level, why: risk-map path or why higher (unlisted no reason) |
| Resources | locks, ports, devices assigned |
| Tests | touched unit tests while building; no gate before review; after verdict, one gate on reviewed tip |
| Owner | owner's binding decisions, paraphrased, no ruling id |
| Limits | no land, merge or push |

- Brief names extra checks, overridden steps; never restates or shortens a skill checklist. No-tests brief keeps skill builder's "Delete stale tests": commit says what covers it now.
- Repo brief template (`--find brief`): follow it.
- Review (analyst): `python <kit>/tools/review_pack.py -C <repo> <branch>` first call, plus approved design.
- Docs-writer: facts as keyword lines, `ID | status | area | keywords | ref`, max 300 chars each (evidence, deletion: `specialty-docs` `doc-writing.md`, "Findings and doc edits").
- Owner-approved change: author asks from worktree (skill hub-ops, step 5), files at approval's base; hub commits on that branch once approved; author never commits it.
- Specialty task (`specialty-*` in `<kit>/content/skills/`): "Load skill `<name>` first"; role at least the skill's `Dispatch:` minimum. Creative work: `creative.md` beside this file, first.

## Which role, which tier

`roster.md` beside this file: roles, coding ladder, tier, effort, reviewer model, deep-worker requests, consultant, research, pass/fail routing. Role tiers, host tier-to-model map: `<kit>/docs/ROSTER.md`.

## Shared resources and measurements

- Serialise profile's unshareables: test machine, database, ports, deploy target, lock files (`--find lock`). None given, collision possible: stop, ask.
- Quoted-number run owns the resource: pause all, run, release. Design, arms, decision rule written before first run; counts with sample size; rule applied literally; after-the-fact reading stated apart.
- Private scratch folder per agent. Check processes before trusting "nothing is running".

## While agents run

- Review hand-back at once; continue same agent while prompt cache warm (`<kit>/docs/ROSTER.md`: longer-window role). Few rounds per artefact (profile may set count), then reseed from short written summary; analyst on review, strong/deep coder with long context: fresh from one. Never park large-context agent for small fix.
- Message queued to busy agent may go unread: resend in full to a report that missed it.
- Background run dies with its agent: replacement checks logs first. Before resuming/replacing a failed agent: what it left running (jobs, lock runs, ports, worktree).
- Few long-running agents: each notification costs a turn over whole context.
- **Agents that die together** (API error, usage limit): shared resources first (orphan processes, stale locks, ports, main vs remote); continue each with short state summary, most valuable first. Usage limit: tell PM, wait for its word.
- Host's built-in planner may lack write tool: plan in its report. Never assume one host's agents, tools, file names on another.

## Upkeep

Daily prune, stale worktrees, weekly tool-set run: `upkeep.md` beside this file.
