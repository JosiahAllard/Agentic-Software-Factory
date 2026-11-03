# Agent review, 2026-10-11

A review of the 15 agent definitions of that date, with usage, refusal and miss-history data behind it. The A/B measurements are in [model-ab.md](model-ab.md); this page is everything else from the same review. It is a dated record: the figures describe the window below, not today's fleet.

Status at the base of 2026-11-03: the roster has since changed. Of the 15 definitions reviewed, `reviewer`, `researcher`, `records`, `spec-writer`, `opus-worker` and the three art agents no longer exist. Today's roles are in `content/roles/` and docs/ROSTER.md (rendered from the role front matter): the analyst took review and research, the docs-writer took records and specs, the art work is the `specialty-*` skills, and opus-worker was renamed (strong-worker today). Each finding below carries its status in "Findings and their status". No finding was ever applied by the owner as written; "fixed" means the base no longer shows the problem.

Evidence labels:
- **measured** (M): from a script or log in the source notes. Not re-run for this page.
- **checked** (C): read at the file, in one read or grep, during the review.
- **inferred** (I): a lead, not a fact.
- (H): a text-match heuristic.

Sources, named by what they are: the usage refresh (window 2026-10-08 14:08 to 2026-10-11 18:17 UTC, 1,157 subagent runs), the definitions review (all 15 files), the miss-history study (two rounds), the replay scoring notes, a gate-speed study in another repository, and the refusal log. Those notes are not in this repository. Single-run figures are marked single-run.

## Summary

- The main cost was refusals, not the wrong model. 221 of 1,157 runs (19%) hit at least one block. 94% of block events were the kit's own hooks, not the safety classifier (M).
- The model in a definition was often not the model that ran (M): the lander was defined Haiku and 168 of 200 ran on Sonnet; the reviewer was defined Sonnet and 137 of 295 ran on Opus. Every mismatch matched a dispatch override. Editing a tier line does little until the dispatch habit changes.
- The tier evidence is thin: one run per cell and few cases. Real review history could not separate the reviewer tiers.
- The money was in the two Opus coder types: 65% of weighted usage. Lander on Sonnet was 1.7%, reviewer on Opus 6.2%.
- The coordinator replay is in [model-ab.md](model-ab.md) (single run per cell).

## Usage and dispatch overrides (M)

Window: 3.17 days, 1,157 runs, 4,237M tokens, 705M weighted units (wu = input + 1.25 cache write + 0.1 cache read + 5 output). Served model family by wu: Opus 543M (77%), Sonnet 134M, Haiku 28M. Every served id was `claude-<family>-5-5`. Dollars are in the next section.

| Agent | Runs | Defined | Served (runs) | Tokens, median |
|---|---|---|---|---|
| opus-worker | 135 | opus, medium | opus 128, haiku 7 | 7.57M |
| strong-worker | 95 | opus, high | opus 92, haiku 3 | 8.53M |
| worker | 210 | sonnet, medium | sonnet 191, haiku 14, opus 5 | 1.46M |
| reviewer | 295 | sonnet, high | sonnet 150, opus 137, haiku 8 | 0.56M |
| lander | 200 | haiku, medium | sonnet 168, haiku 32 | 0.25M |
| records | 99 | sonnet, low | sonnet 91, haiku 8 | 0.18M |
| researcher | 7 | sonnet, high | sonnet 6, opus 1 | 1.27M |
| test-runner | 8 | haiku, no effort key | sonnet 4, haiku 4 | 0.13M |
| spec-writer | 2 | sonnet, high | sonnet 1, opus 1 | 4.1M |
| general-purpose, Explore, Plan, claude-code-guide | 60, 14, 5, 27 | no definition here | general-purpose: opus 24, sonnet 19, haiku 17 | |
| asset-smith, tech-artist, sound-smith | 0 | | | |
| deep-worker, consultant, coordinator | not measured | | run as sessions | |

- Every served model that differs from the definition matches the dispatch `model` override in the run's meta file. The harness substituted nothing. Runs with no override all served the definition's model (M).
- Lander: 150 of the 168 Sonnet runs carry an explicit `model: sonnet`. The 24 Haiku runs with no override began 2026-10-10, when the definition moved to Haiku. Production Haiku medium: 20 landed, 2 not (24 runs). Sonnet medium: 35 landed, 10 not (50 runs). The outcome is a text match (H) and the counts are too small to rank.
- Reviewer: 46% ran on Opus (kit 94 of 133, a second repository 30 of 50, a third 13 of 111). Verdicts were not scored in this window, so there is no evidence that Opus catches more.
- Haiku and downward overrides (worker 14, records 8) were mostly one Haiku A/B session of 51 runs (139M tokens).
- Overrides: docs/ROSTER.md then allowed overriding "only upward", so a Sonnet lander over a Haiku default counted as allowed. Why dispatchers passed `model` (habit, or an older brief template) is not shown in the notes (I). Today the lander is never overridden (content/skills/coordinator-dispatch/roster.md "Choosing").
- Weighted units, biggest lines: opus-worker on Opus 233M, strong-worker on Opus 223M, worker on Sonnet 85M, reviewer on Opus 44M, general-purpose on Opus 34M. Reviewer on Sonnet 22M, lander on Sonnet 12M, records on Sonnet 6M.
- Against the previous review (per-day rates): runs up 35%, tokens flat. Lander median 0.44M to 0.25M. Reviewer Opus share 74% to 46%. General-purpose Opus share 73% to 40%. Strong-worker median 16.9M to 8.5M. Opus-worker plan-shaped share 45% to 33% (within the noise of the shape heuristic, I).
- Runs ended without a usable report (H): opus-worker 8, strong-worker 5, worker 8. No capability escalation was found for worker: 0 confirmed of 19 candidate pairs (pairing by shared record id, H).

### Plan-shaped work on the Opus coders (M, shape tag H)

44 plan-shaped opus-worker runs hold 720M tokens (47% of the type). 39 strong-worker runs hold 387M (26%). The tag uses the first dispatch only, and the three biggest "plan" runs went on to build, so these shares are upper bounds. If plan-shaped opus-worker runs cost what worker plan runs do, about 630M tokens would be saved: a ceiling, not a forecast. Strong-worker plans are within its charter and nobody tested a cheaper tier on them. Today plan-only work goes to the worker (roster.md "Choosing").

## Subagent spend, measured in dollars

Method: every assistant message with its own usage block, deduplicated by message id, priced at that message's model. Subagent cache writes are all 5-minute (measured), so the 5-minute rate is the point value. This replaced an earlier estimate that was about 9% too high ($2,010 for the window) and is not kept.

Totals for the window (1,157 runs, 43,283 messages, measured): $1,828, $576 a day, $4,032 a week (computed; the week is scaled, so assumed). By model: opus $1,610 (88.1%), sonnet $209 (11.4%), haiku $8 (0.5%). By kind: cache write 48.5%, cache read 40.4%, output 11.0%.

| Type | Runs | Window $ | Share | $/run mean | $/run median |
|---|---|---|---|---|---|
| opus-worker | 135 | 703 | 38.5% | 5.21 | 3.46 |
| strong-worker | 95 | 652 | 35.7% | 6.86 | 3.91 |
| reviewer | 295 | 171 | 9.3% | 0.58 | 0.34 |
| worker | 210 | 145 | 7.9% | 0.69 | 0.33 |
| general-purpose | 60 | 104 | 5.7% | 1.74 | 0.27 |

The other eight types together are $55 (3.0%).

**Savings, price only** (computed, token counts held fixed, so a cheaper model that takes more turns saves less; "week" is the window scaled, assumed). None was applied as a blanket move.

| Move | Window / week | Basis |
|---|---|---|
| Opus-worker to sonnet, sync and routine work | $9.1 / $20 | measured line $699, 50% price cut |
| Opus-worker to sonnet, real plans | $118.8 / $262 | 34% of $699, half price |
| Same, all plan-shaped work (ceiling) | $292.8 / $646 | share 47% |
| Strong-worker down one step, mid case | $21.1 / $46.6 | measured line $650 |
| Strong-worker down one step, high case | $128 / $283 | assumed |
| Reviewer to sonnet, 25 / 50 / 75% of reviews | $16.9 / $33.8 / $83 (week 37.2 / 74.5 / 183) | measured $135; the 75% row assumes an 82% cut |
| general-purpose off opus | $49 / $108 | $98 on opus, same tokens at sonnet price |
| Explore and Plan off opus | $7 / $15.4 | $14 on opus, half price |
| Lander to haiku | $19 / $41.9 | $20 at sonnet price, $1 at haiku |

Two cautions: these are price ceilings, and the coordinator replay ([model-ab.md](model-ab.md)) shows sonnet matching the rules less often than opus, so a cheaper model that gets work wrong can cost more than it saves; and the sonnet lines assume the type's real work moves, while the replay tests the coordinator only.

Corrections to the first-pass figures: replay run 1 totalled 121 match, 18 mismatch, 1 safety break over 140 scored answers (run 2 is in model-ab.md); the smallest model's 12 misses are 8 delayed hand-ups, 2 harm caught, 2 case-04 noise; the Sonnet cache read price in model-ab.md is $0.10 (corrected there).

## Context and cache findings

Same 1,157 runs.

- A run starts with 8k to 48k tokens of prompt, but its first turn costs $0.02 to $0.12, because most of it is a shared cached prefix. Start-up is cheap; later re-reads cost more. In dollars (computed): start-up 2.6%, growth after the first turn 49.6%, re-reading the start-up prompt 13.2%, cache-miss rewrites 23.5%, output 11.0%.
- A cache-miss rewrite is a turn that writes more than half its prompt back to the cache after turn 1. There were 1,316 of them in 43k turns, in 438 of 1,157 runs. 70% of rewrite turns follow a gap of 5 to 60 minutes (median gap 587 s), which fits a 5-minute cache life. Of the rewrite cost after gaps of 5 minutes or more, about 66% came from agents resumed by a message after a hand-back or idle, and about 10% from waiting on a gate or test. 54% of late continuations (313 of 583) came 5 to 10 minutes after the last turn.
- Subagents get a 5-minute cache even on a subscription; the main conversation gets one hour. The per-agent-definition setting `experimental: cacheTtl: 1h` is now in the rendered agents `strong-worker.md`, `weak-worker.md` and `worker.md` under `hosts/claude/shell/agents/` (docs/ROSTER.md "The one-hour prompt cache"; guarded by `tests/agents/test_agents.py`). The all-subagents setting `subagentPromptCacheTtl` (or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`) is not used. No run's usage block has been read to confirm one-hour cache writes.

Pricing computations, untested (assumed: every 5-to-60-minute rewrite becomes a full cache hit and nothing else changes): a one-hour cache for opus-worker nets about $174 a week; for strong-worker it costs about $74 a week more; for worker it nets about $45 a week; the whole fleet on one hour nets about $42 a week, near break-even. Late continuations that rewrite cost about $525 a week (computed, partly assumed); sending them sooner would save roughly half (I). Starting a fresh agent past a break-even context size saves at most about $114 a week (upper bound). Reading file ranges instead of whole large files targets tool-result growth of about $617 a week, a share of which is the saving; not measured separately. The "continue within 5 minutes or start fresh" and "read ranges" rules are in the coordinator-dispatch skill.

## Safety-check proposals

None built or tested: an automatic log of refusals and classifier denials using a documented hook event for auto-mode denials (whether it fires for subagent calls is undocumented); a pre-tool hook that checks a dispatch against a refusal ledger; and a red-first route in a worktree tool for when the base is unpushed or the landing lock is held. No record exists for any of them.

## Refusals (M for counts, H for class)

328 block events in 221 runs, by a strict start-of-message match. By source: kit hooks 307 (94%), safety classifier 21 (6%). By repository: kit 175, a second repository 104, a third 45, a fourth 4.

| Agent | Runs (refused) | Events | Main-checkout write | Unbounded wait | Unplaceable command | Wrapped gate or pipe | Other | Classifier |
|---|---|---|---|---|---|---|---|---|
| worker | 210 (52) | 71 | 29 | 12 | 6 | 10 | 11 | 3 |
| opus-worker | 135 (34) | 65 | 32 | 2 | 8 | 7 | 13 | 3 |
| strong-worker | 95 (29) | 47 | 28 | 3 | 7 | 1 | 4 | 4 |
| lander | 200 (42) | 45 | 3 | 30 | 3 | 1 | 3 | 5 |
| reviewer | 295 (31) | 39 | 27 | 0 | 4 | 0 | 4 | 3 |
| general-purpose | 60 (15) | 34 | 22 | 1 | 5 | 0 | 5 | 1 |
| records | 99 (12) | 18 | 4 | 4 | 4 | 3 | 2 | 1 |
| Explore, Plan, test-runner | 27 (6) | 9 | 2 | 0 | 2 | 0 | 4 | 1 |
| all | 1,157 (221) | 328 | 147 | 52 | 39 | 22 | 46 | 21 |

- The class columns are a text match (H). The Classifier column is a count inside the event total.
- Main-checkout writes are 45% of all events. The cause is probably workers running `python -` or `New-Item` from the wrong working directory (I).
- Most lander blocks (30 of 45) are the unbounded-wait rule (foreground sleep or until loops), though the lander definition already forbade polling.
- Classifier denials by label: Auto-Mode Bypass 7, Modify Shared Resources 5, Irreversible Local Destruction 3, and one each of CI Bypass, Security Test Removal, Credential Exploration, PII Data Handling, Interfere With Workloads.
- The refusal log (185 rows for the same days, reported refusals only) agrees: the kit's own hooks. Rows per day: 11, 6, 78, 90; it grew sharply from 2026-10-10, when the kit hooks and the plugin dispatch names came in.
- Stop-like reports in the first 250 characters (H, upper bound): worker 57 of 210, opus-worker 36, strong-worker 33, lander 42, records 47, reviewer 29. For worker, 37 of the 57 were refusal or hook stops, 11 gate red, 4 parked for a decision.

## Findings and their status

The definitions review found 13 cross-cutting findings and wrote one section per file. Row-level cause attributions in it are partly inference. Status is read at the base (main 13771e0b, 2026-11-03). "Gone" means the definition the finding was about no longer exists. Findings marked "open" have no backlog record; the owner has not ruled on any of them.

| Id | Finding (short) | Status at base and proof |
|---|---|---|
| A1 | Builders write only to a literal absolute path | Fixed for weak-worker (`content/roles/weak-worker.md` "Write targets are literal paths"); open for the other builders |
| A2 | No `python -c` or stdin python; write a script file | Fixed: `content/skills/shell-and-worktree-traps/SKILL.md` item 5 (stdin here-string trap) |
| A3 | No sleep or poll loops | Fixed: same skill, item 12 (a foreground `sleep` or poll loop is refused; wait as docs/gate.md says) |
| A4 | Reviewer never checks out, switches, restores or resets | Open: `content/skills/specialty-review/SKILL.md` and `content/roles/analyst.md` carry no such ban |
| A5 | Records may use backlog.py | Gone: the records type became the docs-writer |
| A6 | Lander spawning a helper just to wait; keep the Agent tool? | Open: `content/roles/lander.md` front matter still lists `Agent` |
| A7 | A refused backlog write is reported, not retried with `--reason` | Fixed in part: skill `backlog-ops` "Refusals" (a refusal is final; one form-only retry by a fresh agent) |
| A8 | Name `sync_base.py` for merges | Fixed: `content/skills/branch-sync/SKILL.md` step 3 |
| A9 | Do not re-emit other agents' text | Open, not re-checked; no wording found |
| A10 | "Gate green" on every commit becomes one full gate per branch | Fixed: `content/skills/builder/SKILL.md` item 11 (the gate comes after review, when the coordinator asks) |
| B1 | Bare "Never" lines in 11 files | Not re-checked; the files were regenerated since |
| B2 | Reword the pid-kill sentence to "do not kill; report" | Open, owner decision: "end only processes you started, by pid" is still in lander.md, weak-worker.md and test-runner.md |
| B3 | One shared builder block | Fixed in part: skill `builder` is the shared builder text |
| B4 | Reconcile lander.md with the land-branch skill | Not re-checked; lander.md runs `land.py ... --expect-tip <tip>` |
| B5 | One rule for who closes a record | Not re-checked |
| B6 | Stale names: `new_phase`, conventions digest, refusal-logging line | Fixed for `new_phase` (no match in `content/`); the consultant's conventions digest is now defined (`content/roles/consultant.md` step 1, via the profile's Records section) |
| B7 | Duplicated art-agent text, GPU line | Gone: the art agents are `specialty-*` skills |
| B8, B9 | Repo-tools block copied to about 14 files; doc-writing block twice | Not re-checked; low value |
| C1 | Decide where tier overrides come from | Fixed: tier and effort are pinned in role front matter and "override only upward" is in roster.md "Choosing" |
| C2 | Lander: keep haiku, medium | Kept: `hosts/claude/shell/agents/lander.md` is haiku, medium; never overridden |
| C3 | Reviewer tier text for the owner's split | Fixed: roster.md "Reviewer model" (built 2026-10-18) |
| C4 | Plan-shaped work on Opus coders: wording first, any tier move a trial | Fixed in part: plan-only goes to the worker (roster.md "Choosing"); no tier trial run |
| C5 | Trim unused tools (AskUserQuestion, Artifact on coordinator; Agent and EnterWorktree on test-runner) | Open: `hosts/claude/shell/agents/coordinator.md` still lists AskUserQuestion and Artifact; test-runner lists EnterWorktree |
| C6 | Deep-worker auto-compact window 500k against about 366k seen | Dropped: the deep-worker front matter no longer sets a window (launcher-set, "1M via launcher") |
| C7 | Test-runner has no effort key; note it in docs/ROSTER.md | Fixed: docs/ROSTER.md lists test-runner effort as `-` |
| C8, C9 | No tier change for coordinator, consultant, deep-worker, builders; Haiku coding untouched | Unchanged |
| D1, D2 | Worker's text routes networking to the wrong tier; "plan" means two things | Fixed: the worker, strong-worker and senior-worker descriptions now name each other's scope and send plan-only work to the worker |
| D3 | Coordinator description said it "serialises landings" | Fixed: `content/roles/coordinator.md` says it queues every LAND verdict at once |
| D4 | Coordinator has no fallback if dispatched as a subagent | Open, not re-checked |
| D5 | Lander description lists the reviewed tip sha; reviewer description lists the gate report paths | Open for the lander description (it omits the tip); the reviewer is gone |
| F1 | The kit has no `.claude/coordinator-profile.md` | Fixed: `.claude/coordinator-profile.md` exists |

Dropped on check at the review: the state note is covered by `docs/ops/*.local.md` in the ignore file (still true at the base).

## Thin evidence behind standing decisions

- **Lander on Haiku** rests on 2 cases (a clean merge, and a DECISIONS.md conflict), 1 run per cell, over two rounds. The definition was edited after round 1 and re-tested on the same two cases, so round 2 measures fit to those cases (I). The Sonnet "miss" is Sonnet following the brief over the definition's STOP rule: a precedence test, not a capability test. The 48-run lander cell used effort low, a different setup from the medium decision.
- **Reviewer split** rests on 7, 8 and 9 of 10 planted bugs for Sonnet high, Haiku high and Opus, over 8 diffs, one review per model per diff. The A/B page says 1 to 2 catch gaps are noise. Every arm missed a silent data loss.
- This records how thin they are; it is not a request to re-run them.

A firmer test would cost (estimate from the per-run figures in model-ab.md; thinking tokens are hidden, so understated): lander, 8 cases x 3 arms x 5 repeats = 120 runs, about 16M raw tokens, and it would not fix a definition that disagrees with the brief; reviewer, 20 diffs with 30 or more planted bugs x 3 repeats x 3 arms = 180 reviews, about 141M raw tokens, about 8 hours in series, the Opus arm about two thirds of the price-weighted cost. Both test the wrong thing in part: planted bugs are small and local, the real misses sat outside the diff. The money is in the two Opus coder types (65%); extra measurement belongs there, if anywhere.

## Reviewer miss history (measured unless marked)

- Round 1 found 4 traceable post-landing escapes: 2 true detection misses and 2 defects the reviewer named but rated non-blocking. Round 2 weakened this: case 1's blame points to the squashed first commit and a direct commit, not the reviewed branch (unproven), and case 3 is probably an in-branch catch.
- Kit denominator: 109 first-parent merges; 84 with a reviewer run found (51 Opus only, 27 Sonnet only, 6 both); 25 with none found. The 25 is an upper bound because the join missed at least one reviewed branch.
- Of 27 fix-worded commits blamed, 0 were clean post-landing misses (16 in-branch review fixes, 8 on the squashed first commit, 3 interactions).
- Result: real history cannot separate the tiers. It does not contradict the planted-bug A/B and does not support it either. The two round-1 misses were consequences outside the diff (a changed root read by a stub tool; a new tool's path text tripping a repo-wide lint), which the planted-bug A/B does not test (I).
- Not done: full blame of 430 commits (about an hour), the join for another repository (174 of 964 matched), the check of case 3.

## What could not be measured

- Main sessions (coordinator, project manager, consultant) and deep-worker sessions; compaction, state-note and consultant recommendations.
- Review verdict quality in the new window, and whether stop-like runs were re-dispatched except by shared record id.
- Served model id per run in the older A/B tables (see model-ab.md).
- Any per-tier reviewer miss rate: zero certain misses in 84 reviewed branches is a floor, not a rate.
- Whether refusals would drop after the rule additions; which dispatch habit sent the lander to Sonnet.
- Haiku sync and Haiku coding at larger samples; the Haiku helper cost (the helper role now exists, `content/roles/helper.md`; its cost is not measured).
- The coordinator's tier beyond one file-based run per cell: no live session, tools, long context or compaction.

## Open A/B work

Not started, no backlog record: the reviewer split's one-month re-check (the split was built 2026-10-18), the Haiku helper cost measure, a live coordinator pilot (the owner has not decided), an opus-worker trial for plan and sync first dispatches (I; opus-worker is now strong-worker). Sync on Haiku: decision open.
