# Agent roster

Read from skill `coordinator-dispatch`, "Which role, which tier". Use only the roles the repo's coordinator profile lists. A tier (light, standard, strong, deep) becomes a model per host (`<kit>/docs/ROSTER.md`); "raise to strong" means dispatch with the strong tier's model.

## The coding ladder

| Role | Tier/effort | Takes |
|---|---|---|
| weak-worker | light/high | Low risk, small and well defined, sized by what the brief lists (named files and edits): doc lines, small additive changes with their test. Never moves, renames, merges or designs. |
| worker | standard/medium | Low and Medium risk: additive content, UI, pure logic with tests, behaviour-preserving moves and renames (the brief names **Moves and renames**; its equivalence check is not optional); a small known-cause fix even on a High path (its review stays strong); plan-only and sync/prep work (merge main into a branch, rebase, conflict-free sync) whatever the branch's risk. |
| strong-worker | strong/medium | High-risk build and fix work on standard paths: API changes, live fixes, ordinary tool features, generated-data wiring. |
| senior-worker | strong/high | Builds and designs in concurrency, networking, simulation or ordering, migrations, CI config, landing, gate or lock tools, build-cache correctness, codegen; known-area crash fixes; changes nobody can observe directly. Never plan-only. |
| deep-worker | deep/xhigh, own session | A race, crash root cause or subtle concurrency senior-worker failed at or plainly will (never a build or a plan). |

Not installed: weak-worker -> the worker; strong-worker -> the worker raised to strong. Never the host's general-purpose agent for code. An escalation reassigns upward with the findings: not a failure.

## Other roles

- analyst: the read type: design and branch review (skill `specialty-review`), research (`specialty-research`), art critique (`specialty-art-critique`). Builds and edits nothing.
- docs-writer: settled facts into docs, and specs extended from the owner's answers (skill `specialty-docs`). Docs only.
- lander: optional; spawned fresh per landing; submits one reviewed branch (or a batch), waits, reports.
- test-runner: runs test tiers in a worktree; PASS/FAIL plus failing lines. A pass/fail check whose log nobody needs: the cheapest role the profile lists (none: the worker).
- helper: dispatched by deep-worker and topic sessions only, for bulk menial reads and mechanical bulk writes.
- consultant: a long-lived session per repository (`consultant-<repo>` in your session list): engineering questions go there before the PM; owner matters still go to the PM, which logs its calls as delegated.
- Creative specialty skills (`specialty-tech-art`, `specialty-3d-assets`, `specialty-rigging`, `specialty-sound`): a worker type, `creative.md` beside the dispatch skill.

## Tier rules

- Tier and effort are pinned in each role's front matter. Override only upward, for risk, and say so in status; never the lander; the analyst only by the three rules below. Never a model above the strong tier's unless the owner asks on that task: the deep-worker session is the step up. Effort is not settable per dispatch: pick the role.
- The strong tier also takes security and measurement work, specs that read code (the docs-writer raised) and any change nobody here can observe. Never the lightest tier where a failure must be judged, except the analyst on a review by its rule.
- A host's built-in agents (general-purpose, explorer, planner) pin nothing: dispatch them on the standard tier, never strong; a thin answer is asked once more raised to strong, said in status. Work needing the strong tier is coding or design: a ladder role.
- **Reviewer model** (this rule's one home), for a design review and a code review alike: the analyst, pinned to the light tier at high effort. High risk or strict (`land.py <branch> --check-strict`) -> raise to strong whoever built it (`land.py` refuses a lighter review of either, `verdict.py add` of a High-risk one); this comes before the rows. The first row that fits: 1. Built by strong-worker, senior-worker or deep-worker -> raise to strong. 2. Built by worker or weak-worker -> no override: light as pinned. 3. Otherwise -> raise to strong. A light reviewer's blocking claim you dispute is settled by a fresh strong-tier review of the tip, never by your own reading (`verdict.py add` refuses a LAND over its `Verdict: SEND BACK`; light models overstated severity in `<kit>/docs/model-ab.md`, "Reviewer A/B on planted bugs").
- **Art critic**: the analyst with `specialty-art-critique` is always strong tier.
- **Research**: the analyst with `specialty-research`, raised to standard. Its output is concepts for the owner to pick; the chosen one goes to the docs-writer (`specialty-docs`) before any builder sees it.

## Deep-worker requests

Never a subagent dispatch. Write a self-contained brief file, then message the PM: the brief path, your session name as requester, and a slug; say in status when and why. The PM runs `python <kit>/tools/launch.py deep --plan` first. The session approves its design with you and reports to you by message; review its branch like any other, then tell the PM to stop it (`python <kit>/tools/launch.py stop --name deep-<slug>`).
