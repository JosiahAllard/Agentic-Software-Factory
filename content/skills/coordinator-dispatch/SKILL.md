---
name: coordinator-dispatch
description: Use as a coordinator before any dispatch - pulling the record, the worktree, writing a brief, choosing the role and tier, a creative (art, 3D, sound) brief, assigning shared resources or a measurement, requesting a deep-worker session, continuing or resuming agents, and the daily prune and weekly tool-set upkeep runs.
---

# Coordinator: dispatch

Roles are named bare; your host's instructions say how a role name becomes its dispatch name.

## Before the agent starts

- **Pull at dispatch**, never after the work: `python <kit>/tools/backlog.py -C <worktree> pull <ID>` (`--session <key>` for a session-owned record). The offer moves: finished work on an unpulled record that dropped out of it needs a pre-emption to close. Unpriced record refuses the pull: price it first (skill backlog-pricing); a builder never prices.
- **Bundle** other open records of the same area onto the branch, keeping it reviewable; never mix a high-risk change with unrelated work.
- **One workstream = one branch = one worktree**: `python <kit>/tools/new_worktree.py -C <repo> <branch>` unless the profile names another way. Dispatch into the worktree you prepared, never ask the host for an isolated one (it would confine the agent's writes there).
- Writes in another session's worktree: by the agent that owns it, or a fresh agent dispatched into it; never by you with `-C` from outside, never to route round a refusal.
- A topic session owns its records' files while it runs: dispatch no work on them.
- A change to `.kit.json` or another owner-only path (`--find kit-json`, key `owner.paths`): to the owner through the PM, never to a builder.
- Slicing a redesign or mockup-built record: walk the mockups into an element checklist on the umbrella record, one line per element, each owned by a slice; it closes when every element is ticked against a landed slice (`<kit>/docs/backlog.md` "Umbrella records").

## The brief

| Field | What it holds |
|---|---|
| Role | the role and, where raised, why (roster.md) |
| Record | the ID, the observed failure, what is true when done; never machinery the record does not need (an option, a new tool, a seam) |
| Design | the area's design-doc section and "design first: report your design and wait for approval" (skill builder, step 3); or, for a small change, the approved design itself: home, contract, files. A split or a new class is never small, never set by the docs: its design (parts, one job each, dependency direction, net size) is reported and reviewed |
| Read | exact files and sections (ranges in a large file), the entry symbols you know (`path:Symbol`), the existing code, tool or test the change extends; doc topics to look up, never a pasted rule. No paths: the builder spends about 35 calls |
| Scope | the files that are theirs; the files that are not, and whose |
| Risk | the level and why: the risk-map path, or why higher (unlisted is no reason) |
| Resources | the locks, ports and devices assigned |
| Tests | touched unit tests while building; no gate before your review; after your verdict, the one gate on the reviewed tip |
| Owner | the owner's binding decisions, as a paraphrase, no ruling id |
| Limits | no land, merge or push |

- A brief names its extra checks and the steps it overrides; never restates or shortens a skill's checklist. A no-tests brief keeps skill builder's "Delete stale tests" rule: the commit says what covers it now.
- Repo has a brief template (`--find brief`): follow it.
- Review (analyst) brief: `python <kit>/tools/review_pack.py -C <repo> <branch>` as its first call, plus the approved design.
- Docs-writer brief: facts as keyword lines, `ID | status | area | keywords | ref`, at most 300 characters each (evidence and deletion rules: `specialty-docs` `doc-writing.md`, "Findings and doc edits").
- Owner-approved change: the author asks from its worktree (skill hub-ops, step 5), files left at the approval's base; the hub commits it on that branch once approved; the author never commits it.
- Task matching a specialty skill (`specialty-*` in `<kit>/content/skills/`): the brief says "Load skill `<name>` first" and names a role at least the skill's `Dispatch:` line's minimum. Creative work: `creative.md` beside this file, first.

## Which role, which tier

`roster.md` beside this file: roles, coding ladder, tier and effort rules, reviewer model, deep-worker requests, consultant, research and pass/fail routing. Each role's tier and each host's tier-to-model map: `<kit>/docs/ROSTER.md`.

## Shared resources and measurements

- Serialise what the profile says cannot be shared: test machine, database, ports, deploy target, lock files (`--find lock`). None given and a collision possible: stop, ask.
- A run whose number will be quoted owns the resource: pause everyone, run, release. Design, arms and decision rule written before the first run; counts carry their sample size; the rule applied literally; any after-the-fact reading stated apart.
- A private scratch folder per agent. Before trusting "nothing is running", look at the processes.

## While agents run

- Review a hand-back at once; continue the same agent while its prompt cache is warm (`<kit>/docs/ROSTER.md` names the role with a longer window). A few rounds per artefact (the profile may set the count), then reseed from a short written summary; an analyst on a review, or a strong- or deep-tier coder with a long context, starts fresh from one. Never park a large-context agent for a small fix.
- A message queued to a busy agent may never be read: a report that missed it gets it again in full.
- A background run dies with its agent: its replacement checks the logs first. Before resuming or replacing a failed agent, look at what it left running: jobs, lock runs, ports, its worktree.
- Keep few agents long-running: each notification costs you a turn over your whole context.
- **Agents that die together** (API error, usage limit): check shared resources first (orphaned processes, stale locks, ports, main against the remote), then continue each with a short state summary, most valuable first. Usage limit: tell the PM, wait for its word.
- A host's built-in planner may have no write tool: brief it to return the plan in its report. Never assume one host's agents, tools or file names on another.

## Upkeep

Daily prune, stale worktrees, weekly tool-set run: `upkeep.md` beside this file.
