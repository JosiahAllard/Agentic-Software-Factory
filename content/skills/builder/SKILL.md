---
name: builder
description: Use when you build or fix on a branch - as worker, senior-worker, strong-worker, deep-worker or topic. Covers the design-first flow, where code goes, root-cause fixes, tests, commits, test runs and the gate, fold-ins, the report, bulk work to the helper, and what you never do.
---

# Builder

Caller: the coordinator (deep-worker: the requester; topic session: the PM, the owner deciding in your session). Kit-wide rules: your kit header.

## Flow

| Step | Do | Then |
|---|---|---|
| 1 Start | `python <kit>/tools/preflight.py -C <worktree>`; read the brief whole; the repo profile (path: your role file) at the brief's sections plus **Branches and worktrees**, **Build and test**, **Shared resources that must be serialised**. No profile: `<kit>/content/default-profile.md`, the repo's README and instructions file. | Brief missing something: ask, never guess. |
| 2 Read the design | The area's section of the end-goal design the profile's **Review** section names (else `ARCHITECTURE.md`, else the README) and of the other design docs it names; then the code you change (`codemap.py --symbol NAME [path]`: one function or class). | None for the area: say so in the design. |
| 3 Design | About 15 lines at most: home (module, class or function, and the design section that puts it there); contract (inputs, outputs, errors); what it replaces or deletes; tests extended, added, deleted; files; a split or new class (never small, never set by the docs): parts, one job each, dependency direction, net size. Narrow your record's scope to those files: `python <kit>/tools/backlog.py -C <worktree> set <ID> --scope <path> [--scope <path>]`. | Stop, report the design; build only once your caller approves. Brief carries an approved design (home, contract, files; split or new class: its parts too): build. |
| 4 Build | The approved design, nothing else. | Design does not fit the code, or cause unclear after one honest attempt: stop, report. |
| 5 Test | Touched unit tests only (Tests). | |
| 6 Commit | Commit, below. | |
| 7 Report | Report, below. | |
| 8 Review and gate | Fix review findings; re-run touched tests; the one gate when your caller asks (Tests). | |

## Design rules

1. **Fit, never bolt on.** Work goes in the design's home. Home cannot take it: reshape it in your design, for review; never a new layer, wrapper, flag, layering grant or parallel path beside what exists. No home in the design: ask your caller.
2. **Reuse first.** Before writing a function, class, tool, check, test double or fixture: search (`codemap.py`, `doc_index.py --tools`, the repo's shared test-support and data files), extend what exists. A second copy is a defect.
3. **Fix the root cause.** State the mechanism you believe and the observation that would prove you wrong; look for it. Fix once, in one function covering the cases, never a patch per case; several symptoms or records of one cause: one fix. Mark inferences: "I observed" and "I expect" differ.
4. **Build only for what was observed.** Unless the brief names it: no option, config key, hook, seam, abstraction, generalisation, public name or member (method, attribute, parameter) only tests use; no hardening against a problem or misuse nobody observed. Smallest = least new machinery in the right place, never fewest changed lines.
5. **No god classes.** A file or class past the profile's size line (its **Review** section; default about 1,500 non-test lines), or that your change takes past it: its split goes in your design before more goes in. The profile may exempt files whose size follows a fixed surface. A split moves code, never grows it: parts sum to about the whole, no cycle, no new static or global mutable state, one job each. A new constructor, or one you add a parameter to: at most 8; past that, a config object.
6. **Facts in data, logic in the library.** A fact the code needs lives in the repo's data files, never a doc table copied into code. A repo's own tool imports the kitlib logic the kit has; fork a kitlib module only when it cannot serve, and say why.
7. **Clean up as you go.** A class or module in your area that needs it (god class, copy, dead code, stale or duplicate test): clean it on your branch; widen your record's scope (`set <ID> --scope`). Fold-in limit, cleanups and found defects together: same job, same module or seam, no other pulled record, no owner decision, under about 600 changed lines and 10 files. Bigger: ask your caller. Anything else you notice: your report; the coordinator decides on a record (skill backlog-ops step 2).
8. **Your scope.** Files outside your brief and fold-ins: untouched. Docs land with the code they describe, same branch: name the artefact, source every figure, cite sections by title, grep before re-pointing; a sentence the code contradicts: your report (findings and doc edits: `<kit>/content/skills/specialty-docs/doc-writing.md`).
9. **Shared resources** exactly as your brief assigns (locks, ports, devices). Two uses that could collide, not assigned: stop, ask. Heavy work beyond the gate: the machine lock (skill machine-lock-run).

## Tests

- **One test per behaviour.** Never add a test where one already covers the behaviour: extend it (a case in its table) or rely on it. Test what your change owns, once, at the cheapest layer that sees it; never re-check what another layer reports (an API's own error log, a check script's own verdict).
- **Thin.** Check a state or a return value. Log text only for a token a reader consumes, named as a constant (the profile's log-token list, if any). A test double used in two files: the repo's shared test-support. Test data made for its test, never a copy held equal to a source file. Tests bind port 0.
- **Delete stale tests**: one testing removed behaviour or duplicating another goes; the commit says what covers it now.
- **Red first.** Show the new case fails at the base: `python <kit>/tools/redfirst.py -C <worktree> --tests FILE[::FILTER]` (`<kit>/docs/redfirst.md`); never revert tracked files to get red. Name the observable absent without your change.
- **While building**: touched tests only, the repo selector's pick for the diff, without smoke (kit: `python <kit>/tools/kit_tests.py -C <worktree> --select`); no selector: the test files the change edits. Outside the lock queue; never a gate. A filtered run (`gradlew test --tests`, `dotnet test --filter`) the guard sends to the lock: book `--kind quick-test --expect <estimate>` (skill machine-lock-run step 4). A selection reported as `full suite: ...`: the machine lock (skill machine-lock-run).
- **The gate**: once, after review, on the reviewed tip, when your caller asks: `python <kit>/tools/gate.py -C <worktree>`, selected by the diff (`<kit>/docs/gate-run.md` "The final gate"). Run it to a log; read the log. Waiting: `<kit>/docs/gate-records.md` "Waiting on a gate: who arms what" (your own gate: no Monitor). At most 5 gate runs per branch, then ask. Red: one `python <kit>/tools/gate.py -C <worktree> --rerun` (counts toward the 5). FLAKE lands the tip (report the `backlog.py add` line it prints). REAL, or a test failed twice: real; the tip never lands; fix it. Base moved: skill branch-sync.

## Commit

`python <kit>/tools/commit.py -C <worktree> -m "<what and why>" --model "<model name>" <path> ...`: explicit paths; small commits, at each milestone and before any revert, stash or checkout experiment. Unsure of your model: say so; never amend to change a trailer. Write targets: literal paths. A multi-slice record: each slice's last commit carries `Part-of: <ID>`; only the final slice writes `Closes: <ID>`. Keep a state note in your scratch folder. An owner-only path is never yours to commit: ask for an approval (skill hub-ops, step 5); the hub commits it on your branch once approved.

## Report

Answer first: what is newly true. Then: what you confirmed and how, what stays inferred; branch and commits; the touched-test lines proving each claim (the gate line only once asked); each fold-in (what, where, why); anything outside your scope. Each Done-when item MET or UNMET, with evidence (test name, path or grep). No process narrative.

## Dispatching a subagent

Where your role allows. Brief: role, scope, worktree, branch, shared resources, validation. A writer goes into the worktree you prepared, never a second checkout the host makes (it would confine its writes there). A built-in planner may have no write tool: it returns its answer in its report. Before resuming or replacing a failed agent: check what it left running. Never assume one host's agents, tools or file names on another.

## Bulk work to the helper

Only where `<kit>/docs/ROSTER.md` maps the light tier to a cheaper model than yours; start-up plus your check: about 15-25k tokens (an estimate).
- Bulk read: a log or file over about 1,000 lines to summarise, a search over 20+ files, a whole-tree listing.
- Bulk write: one exact mechanical change (rename, table or fixture rows, reformat) across 10+ files or 100+ lines; any script stays with you.
- The brief names the worktree, every file, the exact change or question; leave those files alone while it runs. You own the result: re-read each cited file:line you act on, read the whole diff, run the touched tests, commit yourself.

## Never

- Land, merge to the main branch, push, force-push or rewrite its history; `reset --hard` or `git clean`.
- Redo a refused sync, a tool's `REFUSED:` or a guard or hook block another way (a `git merge --no-commit` finished with commit.py, another tool, another form): stop; report the exact command and message to your caller.
- Weaken a test or a check to get green, or rerun a failing run other than by its one targeted rerun; any other failing run is real.
- Review your own work as if independent, or keep going on a theory the evidence contradicts.
- Write the owner's clear-session marker.

Details: skill shell-and-worktree-traps; `<kit>/docs/gate.md`.
