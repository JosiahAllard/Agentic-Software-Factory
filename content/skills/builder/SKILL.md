---
name: builder
description: Use when you build or fix on a branch - as worker, senior-worker, strong-worker, deep-worker or topic. Covers the design-first flow, where code goes, root-cause fixes, tests, commits, test runs and the gate, fold-ins, the report, bulk work to the helper, and what you never do.
---

# Builder

Your caller is the coordinator (a deep-worker: the requester; a topic session: the PM, with the owner deciding in your session). The kit-wide rules are in your kit header.

## Flow

| Step | Do | Then |
|---|---|---|
| 1 Start | `python <kit>/tools/preflight.py -C <worktree>`; read the brief whole; read the repo profile (the path your role file names) at the sections the brief names plus **Branches and worktrees**, **Build and test** and **Shared resources that must be serialised**. No profile: `<kit>/content/default-profile.md`, with the repo's README and instructions file. | Brief missing something: ask, do not guess. |
| 2 Read the design | The design docs for the area: the end-goal design the profile's **Review** section names (else `ARCHITECTURE.md`, else the README) and the other design docs it names, at the area's section; then the code you will change (`codemap.py --symbol NAME [path]` reads one function or class). | None for the area: say so in the design. |
| 3 Design | Up to about 15 lines: the home (the module, class or function the work goes in, and the design section that puts it there); the contract (inputs, outputs, errors); what it replaces or deletes; the tests it extends, adds or deletes; the files; a split or a new class (never small, never set by the docs): its parts, one job each, dependency direction, net size. Narrow your record's scope to those files: `python <kit>/tools/backlog.py -C <worktree> set <ID> --scope <path> [--scope <path>]`. | Stop and report the design; build only once your caller approves it. A brief that carries an approved design (its home, contract and files; for a split or a new class, its parts too): build. |
| 4 Build | The approved design and nothing else. | The design does not fit the code, or the cause is unclear after one honest attempt: stop and report. |
| 5 Test | The touched unit tests only (Tests). | |
| 6 Commit | Commit, below. | |
| 7 Report | Report, below. | |
| 8 Review and gate | Fix review findings; re-run the touched tests; run the one gate when your caller asks (Tests). | |

## Design rules

1. **Fit, never bolt on.** The work goes in the home the design gives it. A home that cannot take it is reshaped, in your design, for review; never a new layer, wrapper, flag, layering grant or parallel path beside what exists. No home in the design is a question for your caller.
2. **Reuse first.** Before writing a function, class, tool, check, test double or fixture, search for one (`codemap.py`, `doc_index.py --tools`, the repo's shared test-support and data files) and extend it. A second copy is a defect.
3. **Fix the root cause.** State the mechanism you believe is at work and the observation that would prove you wrong, then look for it. Fix it once, in one function that covers the cases, never a patch per case; several symptoms or records of one cause are one fix. Mark inferences: "I observed" and "I expect" differ.
4. **Build only for what was observed.** No option, config key, hook, seam, abstraction, generalisation, public name or member (method, attribute, parameter) only tests use, and no hardening against a problem or misuse nobody observed, unless the brief names it. Smallest means the least new machinery in the right place, never the fewest changed lines.
5. **No god classes.** A file or class past the profile's size line (its **Review** section; default about 1,500 non-test lines), or one your change would take past it, gets its split in your design before more goes in; the profile may list files exempt because their size follows a fixed surface. A split moves code and never grows it: its parts sum to about the whole, with no cycle among them, no new static or global mutable state, and one job each. A new constructor, or one you give another parameter, takes at most 8; past that, a config object.
6. **Facts in data, logic in the library.** A fact the code needs lives in the repo's data files, never a doc table copied into code. A repo's own tool imports kitlib logic the kit has; fork a kitlib module only when it cannot serve, and say why.
7. **Clean up as you go.** A class or module in your area that needs it (a god class, a copy, dead code, a stale or duplicate test): clean it on your branch and widen your record's scope to it (`set <ID> --scope`). Fold-in limit, cleanups and found defects together: the same job in the same module or seam, no other pulled record, no owner decision, under about 600 changed lines and 10 files. Bigger: ask your caller. Everything else you notice: your report; the coordinator decides on a record (skill backlog-ops step 2).
8. **Your scope.** Files outside your brief and fold-ins stay untouched. Docs land with the code they describe, on the same branch: name the artefact, give every figure a source, cite sections by title, grep before re-pointing; a sentence the code contradicts goes in your report (findings and doc edits: `<kit>/content/skills/specialty-docs/doc-writing.md`).
9. **Shared resources** exactly as your brief assigns (locks, ports, devices); two uses that could collide and are not assigned: stop and ask. Heavy work beyond the gate takes the machine lock (skill machine-lock-run).

## Tests

- **One test per behaviour.** Never add a test where one already covers the behaviour: extend it (a case in its table) or rely on it. Test what your change owns, once, at the cheapest layer that sees it; never re-check what another layer already reports (an API's own error log, a check script's own verdict).
- **Thin.** Check a state or a return value. Assert log text only for a token a reader consumes, named as a constant (the profile's log-token list where it has one). A test double needed in two files goes in the repo's shared test-support. Test data is made for its test, never a copy held equal to a source file. Tests bind port 0.
- **Delete stale tests**: one that tests removed behaviour or duplicates another goes, and the commit says what covers it now.
- **Red first.** Show the new case fails at the base: `python <kit>/tools/redfirst.py -C <worktree> --tests FILE[::FILTER]` (`<kit>/docs/redfirst.md`); never revert tracked files to get red. Name the observable that would be absent without your change.
- **While building**, run only the touched tests: the tests the repo's selector picks for the diff, without smoke (for the kit, `python <kit>/tools/kit_tests.py -C <worktree> --select`); with no selector, the test files the change edits. They run outside the lock queue and are never a gate. A filtered run (`gradlew test --tests`, `dotnet test --filter`) the guard sends to the lock books `--kind quick-test --expect <estimate>` (skill machine-lock-run step 4); a selection the tool reports as `full suite: ...` takes the machine lock (skill machine-lock-run).
- **The gate** runs once, after review, on the reviewed tip, when your caller asks: `python <kit>/tools/gate.py -C <worktree>`, which selects by the diff (`<kit>/docs/gate-run.md` "The final gate"). Run it to a log and read the log. Waiting: `<kit>/docs/gate-records.md` "Waiting on a gate: who arms what" (your own gate: no Monitor). At most 5 gate runs per branch, then ask. A red gate: one `python <kit>/tools/gate.py -C <worktree> --rerun` (it counts toward the 5); FLAKE lands the tip (report the `backlog.py add` line it prints); REAL, or a test that failed twice, is real: the tip never lands; fix it. Base moved: skill branch-sync.

## Commit

`python <kit>/tools/commit.py -C <worktree> -m "<what and why>" --model "<model name>" <path> ...`: explicit paths, small commits, at each milestone and before any revert, stash or checkout experiment. Unsure of your model: say so; never amend to change a trailer. Write targets are literal paths. One slice of a multi-slice record: its last commit carries `Part-of: <ID>`; only the final slice writes `Closes: <ID>`. Keep a state note in your scratch folder. An owner-only path is never yours to commit: ask for an approval (skill hub-ops, step 5), and the hub commits it on your branch once approved.

## Report

The answer first: what is true now that was not before. Then: what you confirmed and how, what stays inferred; branch and commits; the touched-test lines proving each claim (the gate line only once asked); each fold-in (what, where, why); anything outside your scope. Each Done-when item is MET or UNMET, with evidence (a test name, path or grep). No process narrative.

## Dispatching a subagent

Where your role allows: the brief names role, scope, worktree, branch, shared resources and validation. A writer goes into the worktree you prepared, never a second checkout the host makes (the host would confine its writes there). A built-in planner may have no write tool: it returns its answer in its report. Before resuming or replacing a failed agent, check what it left running. Never assume one host's agents, tools or file names on another.

## Bulk work to the helper

Only where `<kit>/docs/ROSTER.md` maps the light tier to a cheaper model than yours; start-up plus your check costs about 15-25k tokens (an estimate).
- Bulk read: a log or file over about 1,000 lines to summarise, a search over 20+ files, a whole-tree listing.
- Bulk write: one exact mechanical change (a rename, table or fixture rows, a reformat) across 10+ files or 100+ lines; any script stays with you.
- The brief names the worktree, every file and the exact change or question; leave those files alone while it runs. You own the result: re-read each cited file:line you act on, read the whole diff, run the touched tests and commit yourself.

## Never

- Land, merge to the main branch, push, force-push or rewrite its history; `reset --hard` or `git clean`.
- Weaken a test or a check to get green, or rerun a failing run other than by its one targeted rerun; any other failing run is real.
- Review your own work as if independent, or keep going on a theory the evidence contradicts.
- Write the owner's clear-session marker.

Details: skill shell-and-worktree-traps; `<kit>/docs/gate.md`.
