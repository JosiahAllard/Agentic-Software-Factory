# Redesign work list

Every change the code needs to match the design: ARCHITECTURE.md (the high-level design) and `docs/modules/` (the low-level designs). One session builds the whole list as one batch on one branch; this file is written for that session and its reviewers. It is a plan, not a set of backlog records, and it is deleted when the last item lands.

## How to read an entry

Each entry has an id, a title and: **Serves** (the design section), **Files** (what it touches; `file:line` is today's tree), **Change**, **Done when** (each line a test name, a path or a grep that prints nothing), **Risk and tier** (the builder role; High follows the risk map in docs/agent-profile.md), **Owner** (an approval the item needs), **After** (items that must be in first).

## Rules for the whole batch

1. **A move is behaviour-neutral.** Old name to new name and nothing else; a behaviour change is its own item. The check is docs/agent-profile.md "Moves and renames" (as amended by W-047): every changed line is a listed substitution or a moved block; the test-name list is the same before and after (`kit_tests.py --list`); for a move that touches `kitlib/guard` or a name the guard matches, the guard's decisions over `tests/guard` are identical.
2. **No stub at an old `kitlib` path.** Every importer changes in the same commit. Entry-point paths in `tools/` and their exit codes do not change.
3. **Tests move with their package** to `tests/<new pkg>/`, test names unchanged.
4. **Each item updates what names its files**: the reference docs, docs/INDEX.md, docstrings. An item is not done while a doc names a path it removed.
5. **The module map file is owner-only**, so each item's map change is written in the owner's patch with that item, not committed by the builder; the owner applies it. Until W-038 the `module-map` gate step only warns.
6. **Design first for senior-worker items**: the builder writes its design, a reviewer checks it against the low-level design, then it builds.
7. **No full-suite runs, with one exception.** An item runs the tests its change can break (`kit_tests.py --select`); the daily full run after the landing is the full check. The exception: an item that changes a gate config file runs every step in full once, at its landing. W-025 and W-038 change the `.kit.json` gate config, so their landings run in full once.
8. **A file split is by responsibility**: the names in the low-level design's "Packages and files" table are the target; no function changes in a split.

## Owner approvals the batch needs

| What | Items |
|---|---|
| `docs/module-map.json` (each item's edits, in the owner's patch with it: Rule 5) | every item that changes a package's edges; W-038 |
| `.kit.json` (the `module-map` step stops being warn-only) | W-038 |
| `.kit.json` (`landing.gate_once`, `landing.shared_paths` go) | W-025 |
| `docs/agent-profile.md`, `AGENTS.md`, `.claude/coordinator-profile.md` | W-047 |
| Changes under `kitlib/guard/` | W-002, W-007, W-009, W-012, W-014, W-015, W-016, W-017, W-021, W-030, W-031, W-042, W-044, W-045, W-046 |
| Changes under `kitlib/lock/` | W-001, W-007, W-008, W-009, W-017, W-030, W-037, W-040 |
| `kitlib/gate/strict_defaults.py` (one path each: `kitlib/platform/**`, `kitlib/landing/**`) | W-001, W-023 |
| `.kit.json` (a listed test path follows its test) | W-036 |

`kitlib/backlog/roles.py` is not touched by any item. `kitlib/gate/strict_defaults.py` stays where it is and gains one path with each package it must cover: `kitlib/platform/**` (W-001) and `kitlib/landing/**` (W-023).

## Order

Items in one lane go in the order shown; lanes in one phase can be built side by side. Two items that share a file are in one lane or in different phases, and every "After" item is in an earlier phase or earlier in the same lane. Phases 1 and 2 are one lane each: nearly every item in them touches one of the shared files below.

| Phase | Lanes |
|---|---|
| 0. Deletions and leaf fixes | W-040 · W-041, W-042, W-045, W-008 · W-043 · W-018 · W-029 |
| 1. Base and facts | W-001, W-003, W-004, W-006, W-005, W-007, W-009, W-010, W-011 |
| 2. Services | W-002, W-012, W-013, W-014, W-015, W-016, W-017, W-019, W-020 |
| 3. Work | A: W-021, W-026, W-022, W-025, W-023, W-024 · B: W-027 · C: W-028 |
| 4. Guard and apps | A: W-030, W-031 · B: W-032, W-033, W-034, W-035 · C: W-036 · D: W-037 |
| 5. Docs and enforcement | W-044, W-047, W-046, then W-039, then W-038 last |

Files that more than one item touches within a phase (the reason for the lanes; not exhaustive, so a builder checks the Files lines of the items beside it): `kitlib/pmstart/__init__.py` (W-005, W-006, W-009, W-010, W-014, W-019, W-036), `kitlib/guard/context.py` (W-002, W-007, W-009, W-012), `kitlib/guard/prepush.py` (W-009, W-012, W-015), `kitlib/pluginsource/__init__.py` (W-002, W-005, W-006, W-020, W-041), `kitlib/platform/api.py` (W-001, W-006, W-045), `kitlib/proc/console.py` (W-001, W-002, W-003), `kitlib/host/hookmain.py` (W-002, W-007, W-030), `kitlib/coordinatorstart/__init__.py` (W-006, W-007, W-009), `kitlib/delegations/run.py` (W-004, W-009, W-015, W-028), `kitlib/commit/__init__.py` (W-011, W-015, W-016, W-027), `kitlib/hubsite/approvals.py` (W-013, W-015), `kitlib/hubsite/apiapprovals.py` (W-015, W-016), `kitlib/hubsite/serve.py` (W-014, W-032, W-034, W-035), `tools/hub.py` (W-015, W-016, W-032, W-033), `kitlib/kitcmd/__init__.py` (W-041, W-042), `kitlib/hubjobs/fill_watch.py` (W-021, W-023), `kitlib/backlog/plan.py` (W-021, W-026), `kitlib/gate/rerun.py` (W-022, W-023), `kitlib/gate/land.py` (W-015, W-025, W-023; W-024 splits it after its move), `kitlib/reviewpack/__init__.py` (W-023, W-025, W-029). W-046 follows W-047 because the go-live file goes only once the profile stops citing it.

## Items

### Base and facts

**W-001 `platform` owns the OS code; `proc` asks it**
- Serves: ARCHITECTURE.md "Layers" (base), "Dependency rules" 8; docs/modules/base-design.md.
- Files: `kitlib/platform/windows/__init__.py:4-5` (re-exports `proc.cpu`, `proc.process`), `kitlib/platform/linux/contain.py:472` (imports `proc.job`), `kitlib/proc/{win32,cpu,process,pipe,shield,winwatch,console,job,tcp}.py`, `kitlib/platform/api.py`, `tests/proc`, `tests/platform`.
- Change: the Win32 bindings and every Windows primitive move under `kitlib/platform/windows/`. `proc` keeps the OS-neutral face and calls `platform.<primitive>`; `proc.job.Job` is the backend's. What the Linux containment needs from `proc.job` moves into `platform`.
- Done when: `grep -rnE "kitlib\.proc|from \.\.+proc" kitlib/platform` prints nothing; `grep -rn "ctypes" kitlib/proc` prints nothing; `tests/proc`, `tests/platform`, `tests/lock` pass; the Linux ratchet baseline (`tests/linux-baseline.txt`) has not grown.
- Risk and tier: High (process containment and kills). senior-worker.
- Owner: lock; `kitlib/gate/strict_defaults.py` (adds `kitlib/platform/**`).

**W-002 OS tests only in `platform`**
- Serves: "Dependency rules" 8.
- Files: the 28 uses of `sys.platform` or `os.name` outside `kitlib/platform`: `ledger/safepath.py` (4), `redfirst/verdict.py`, `pluginsource/__init__.py`, `machinesetup/linuxbox.py`, `launcher/host.py`, `guard/place.py`, `ci/ratchet.py` (2 each), `tooldeps/selfrun.py`, `redfirst/run.py`, `proc/winwatch.py`, `proc/console.py`, `pluginsource/claude.py`, `machinesetup/extras.py`, `host/hookmain.py`, `guard/rules_run.py`, `guard/context.py`, `gate/detect.py`, `config/user.py`, `kitlib/__init__.py` (1 each).
- Change: each reads `platform.NAME` or calls a primitive. `ci/ratchet.py` stays as it is if it must run before the kit boots; then it is the one listed exception.
- Done when: new test `tests/platform/test_no_os_checks.py` passes (it fails on a `sys.platform` or `os.name` in `kitlib` outside `kitlib/platform` and its exception list); the guard's decisions over `tests/guard` are identical.
- Risk and tier: Medium. worker.
- Owner: guard. After: W-001.

**W-003 The console guard takes its incident folder from a provider**
- Serves: "Dependency rules" 3; docs/modules/base-design.md.
- Files: `kitlib/proc/console.py:295-303, 601-668` (four late imports of `config.user`), `kitlib/config/user.py`, `tests/proc`.
- Change: `proc.console` gets `set_log_dir(provider)`; `config.user` registers one that gives the kit's work temp folder; with none, the OS temp folder.
- Done when: `grep -n "config" kitlib/proc/console.py` shows no import; tests `tests/proc/test_console_log.py::test_no_provider_uses_os_temp` and `::test_config_registers_provider` pass.
- Risk and tier: High path (`kitlib/proc`), small change. worker, fresh reviewer.

**W-004 `fs.writelock` takes its lock file; placement moves to `git`**
- Serves: "Layers" (base: `fs` imports only `platform`, `proc`); docs/modules/base-design.md, git-design.md.
- Files: `kitlib/fs/writelock.py:28` (imports `git.repo`), new `kitlib/git/lockplace.py`, callers `kitlib/ledger/write.py:47-79`, `kitlib/pending/__init__.py:54-64`, `kitlib/delegations/run.py:697-719`.
- Change: `writelock.locked(lock_file, ...)` takes the lock file's path. `git.lockplace.lock_path(file, prefix)` says where it goes (the common dir in a checkout, beside the file otherwise) and raises `NoPlace`.
- Done when: `grep -rnE "kitlib\.git|from \.\.git" kitlib/fs` prints nothing; `tests/fs`, `tests/ledger`, `tests/pending`, `tests/git/test_lockplace.py` pass.
- Risk and tier: Medium (a write lock shared by three writers). strong-worker.

**W-005 One package for scheduled tasks: `schedtask`**
- Serves: "Layers" (base: `schedtask`); docs/modules/base-design.md.
- Files: `kitlib/launcher/pythonw.py` (whole), `kitlib/worktree/task.py` (`NOT_THE_STORE_ALIAS`, `PYTHONW_IS`, `quoted`; `AT`, `argument` and `description` stay), `kitlib/pmstart/consoletasks.py` (`read_tasks`, `xml_input`, `words`), `kitlib/machinesetup/extras.py` (`Host`, `pythonw`, the register and delete helpers); importers `kitlib/machinesetup/{agenttemp,extras,procwatch,worktreesweep}.py`, `kitlib/hubsite/autostart.py:28-30`, `kitlib/pluginsource/__init__.py:738,1572,1632`, `kitlib/pmwatch/__init__.py:716,720`, `kitlib/pmstart/__init__.py:1067`.
- Change: those helpers become `kitlib/schedtask/`, over `platform`'s scheduler primitives. `worktree/task.py` keeps only the sweep task's own name, schedule and command; `pmstart/consoletasks.py` keeps only the check.
- Done when: `kitlib/launcher/pythonw.py` is gone; `grep -rnE "worktree\.task import .*(NOT_THE_STORE_ALIAS|PYTHONW_IS|quoted|CARRIER|SINGLE_QUOTES)" kitlib` prints nothing; `grep -rnE "machinesetup|launcher" kitlib/schedtask` prints nothing; `tests/schedtask` passes.
- Risk and tier: Medium. strong-worker. After: W-006.

**W-006 `proc.list_processes`**
- Serves: docs/modules/base-design.md (the one process listing).
- Files: `kitlib/pmstart/__init__.py:21-62` (`TIMEOUT`, `_PS`, `list_processes`); importers `kitlib/backlog/offer.py:644`, `kitlib/launcher/lookup.py:11`, `kitlib/pluginsource/__init__.py:717`, `kitlib/hubjobs/coord_restart.py`, `kitlib/pmwatch/__init__.py`, `kitlib/coordinatorstart/__init__.py`.
- Change: the listing moves to `kitlib/proc/listing.py`, its OS half to `platform`.
- Done when: `grep -rn "pmstart.list_processes" kitlib` prints nothing; `tests/proc/test_listing.py` passes.
- Risk and tier: High path, mechanical. worker, fresh reviewer.

**W-007 `host` takes the agent names, the session mark and the plugin name**
- Serves: "Layers" (facts: `host`); docs/modules/host-design.md.
- Files: `kitlib/agentname.py`, `kitlib/sessionmark.py` (to `kitlib/host/`), `PLUGIN_NAME` in `kitlib/lockmcp/__init__.py` (to `kitlib/host/registry.py`); importers: `agentstart`, `agentview`, `backlog/{audit,policy}.py`, `coordinatorstart`, `coordstate/log.py`, `guard/{backlogrole,context,gaterole,generated,refusal_log,rolepolicy,rules_base,rules_edit}.py`, `host/hookmain.py`, `launcher/{linuxwin,settings,start,unlisted,window}.py`, `strayrestore`, `tooldeps/childenv.py`, `worktree/marks.py`, `tests/content/test_role_names.py`.
- Done when: `ls kitlib/*.py` prints only `__init__.py`, `clock.py`, `ident.py`, `kitroot.py`; `grep -rn "lockmcp" kitlib/host` prints nothing; guard decisions identical.
- Risk and tier: Medium, a move. worker.
- Owner: guard.

**W-008 `content` takes the roster**
- Serves: docs/modules/content-design.md.
- Files: `kitlib/roster/__init__.py` (to `kitlib/content/roster.py`); importers `kitlib/content/__init__.py:225`, `kitlib/hubsite/derived.py:23`; `tests/roster` (to `tests/content`).
- Done when: `ls kitlib/roster` fails; `tests/content` passes.
- Risk and tier: Low, a move. worker.

**W-009 `config` owns the PM-data record and the PM settings**
- Serves: "Layers" (facts: `config`), "Data ownership"; docs/modules/config-design.md.
- Files: `kitlib/pmdata/__init__.py` (to `kitlib/config/pmdata.py`); `kitlib/pmstart/__init__.py` (`KEY`, `load_config`, `_read`, `machine_config`, `known_repos`, `_toplevel`, to `kitlib/config/pm.py`); importers: `config/pointer.py:32`, `guard/{context.py:102,prepush.py:50,refusal_log.py:161,rules_push.py:6,rules.py:144}`, `backlog/{at_landing.py:37,audit.py:69,pull_branch.py:20,store.py:29}`, `hubsite/{gitsync.py:170,serve.py:232,settings.py:19,derived.py:23,push.py:413}`, `hubjobs/{store_commits.py:38,store_upkeep.py:61,reminders.py:45}`, `pmstart`, `coordinatorstart`, `hoststart`, `delegations/run.py:635`, `pmdata/init.py`.
- Change: `kitlib/pmdata/` keeps only `init.py` (the tool that makes the branch).
- Done when: `grep -rnE "kitlib\.pmdata|import pmdata" kitlib --include=*.py` prints only lines in `kitlib/pmdata/`; `grep -rnE "pmstart\.(load_config|known_repos|_toplevel|_read)" kitlib` prints nothing; guard decisions identical.
- Risk and tier: High path, a move with many importers. strong-worker.
- Owner: guard.

**W-010 `git` holds only git: prep and overlap move to `worktree`**
- Serves: "Layers" (facts: `git` imports only the base); docs/modules/git-design.md, worktree-design.md.
- Files: `kitlib/git/prep.py` (to `kitlib/worktree/prep.py`), `kitlib/git/overlap.py` (to `kitlib/worktree/overlap.py`), `kitlib/git/fake/config.py:321` (reads `config.user.home_dir`); callers `tools/branch_overlap.py`, `tools/new_worktree.py`, `kitlib/worktree/new.py`, `kitlib/pmstart/__init__.py:893`.
- Change: the two files move; the fake git takes the home folder from its caller or the environment.
- Done when: `grep -rnE "kitlib\.(config|host|backlog|worktree)|from \.\.(config|host|backlog|worktree)|kitroot" kitlib/git --include=*.py` prints nothing.
- Risk and tier: High path (`kitlib/git`), a move. worker, fresh reviewer.

**W-011 `attribution` takes the model and co-author helpers**
- Serves: docs/modules/host-design.md, ledger-design.md (the ledger does not import `commit`).
- Files: `kitlib/commit/__init__.py` (`MODEL_ENV`, `coauthor`, `resolve_model`, and the settings they read), `kitlib/ledger/vcs.py:24`, `kitlib/hubsite/gitsync.py:169` (reads `commit.KIT_ROOT`; use `kitroot.root()`).
- Done when: `grep -rnE "kitlib\.commit|import commit" kitlib/ledger kitlib/hubsite/gitsync.py` prints nothing; `tests/commit`, `tests/ledger`, `tests/attribution` pass.
- Risk and tier: High path (`kitlib/commit`), small. worker, fresh reviewer.

### Services

**W-012 `records` owns record places, verification and the verdict entry**
- Serves: "Contracts" (Gate-OK record), "Data ownership"; docs/modules/records-design.md.
- Files: `kitlib/gate/result.py` (`kit_folder`, `repo_key`), `kitlib/gate/runner.py` (`log_base`, `fallback_log_base`, `log_folder`, `repo_name`, `verify_record`), `kitlib/gate/verdicts.py` (the entry's path, read and expiry); importers `kitlib/records/gate_ok.py:122`, `kitlib/landed/__init__.py:37`, `kitlib/modulemap/scopelog.py:46`, `kitlib/guard/context.py:485`, `kitlib/guard/prepush.py:456`, `kitlib/backlog/plan.py:2212,2272`.
- Change: new `kitlib/records/place.py` and `verdict_entry.py`; `verify` joins `records/gate_ok.py`. `gate` and `landing` write through `records`.
- Done when: `grep -rnE "kitlib\.gate|from \.\.gate" kitlib/records kitlib/landed kitlib/modulemap kitlib/guard --include=*.py` prints nothing; `tests/records`, `tests/landed`, `tests/gate`, `tests/guard/test_prepush*.py` pass.
- Risk and tier: High (`records`, `gate`). strong-worker.
- Owner: guard.

**W-013 `hubvalidate` takes the page check, to-do steps and the API's constants**
- Serves: docs/modules/hub-design.md (the split).
- Files: `kitlib/hubsite/pagecheck.py`, `kitlib/hubsite/todosteps.py` (to `kitlib/hubvalidate/`); constants in `kitlib/hubsite/api.py` (`AGENT_STORY_STATUSES`, `HANDOFF_KINDS`, `QUESTION_CLASSES`, `WAIT_MAX_S`, `todo_steps`) and `approvals.py` (`MAX_CONTENT_BYTES`, `MAX_FILES`); importers `kitlib/hubmcp/server.py:31-38`, `kitlib/maintlint/{page,publish}.py`.
- Done when: `grep -rnE "hubsite\.(pagecheck|todosteps)" kitlib` prints nothing; `tests/hubvalidate` passes.
- Risk and tier: Medium, a move. worker.

**W-014 `hubclient`: what other code needs to talk to the hub**
- Serves: "Layers" (services: `hubclient`), "Action Hub"; docs/modules/hub-design.md.
- Files: `kitlib/hubsite/{client,settings,localask,codestale}.py` (to `kitlib/hubclient/`); the token read in `hubsite/auth.py` (`folder`, `read_token`, `NO_POINTER`, `ROLES`); the errors in `hubsite/serve.py` (`NotRunning`, `ServeError`, `VIA_MCP`); the push words in `hubsite/push.py` (`WRITTEN`, `UNCERTAIN`, `PM_KEY`, `field`); the hub helpers in `kitlib/pmstart/__init__.py:123-190` (`hub_settings`, `_hub_client`, `NoHubToken`, `HUB_SITE_TIMEOUT_S`). Importers: `autotasks/tick.py:253,303`, `backlog/audit_check.py:285,327`, `backlog/offer.py:739`, `delegations/run.py:56-57`, `hubmcp/server.py:33-37`, `launcher/relaunch.py:45`, `maintlint/{backlog.py:120,133,publish.py:17,29,52}`, `pmstart/__init__.py:165-279`, `pmwatch/__init__.py:532,717-718`, `kitlib/hubjobs/*`.
- Done when: `grep -rnE "kitlib\.hubsite|from \.\.hubsite|import hubsite" kitlib --include=*.py` prints only lines in `kitlib/hubsite/` and `kitlib/hubjobs/`; `tests/hubclient` passes.
- Risk and tier: High path (`kitlib/hubmcp`). strong-worker. After: W-013.

**W-015 `ownerfiles`: one home for the owner-file rules**
- Serves: "Contracts" (Owner files), "Dependency rules" 6; docs/modules/owner-files-design.md.
- Files: `kitlib/guard/ownercommits.py` (to `ownerfiles/rules.py`), `kitlib/hubsite/approvals.py` (to `ownerfiles/approvals.py`), `kitlib/commit/approval.py` (to `ownerfiles/apply.py`), `kitlib/hubapprove/` (to `ownerfiles/request.py`), `parse_merge_tree` and its owner-merge helpers in `kitlib/commit/__init__.py` (to `ownerfiles/rules.py`). Importers: `commit/__init__.py:192,544,585`, `gate/land.py:251,2491`, `reviewpack/__init__.py:70`, `worktree/new.py:202`, `delegations/run.py:55`, `hubmcp/server.py:28,32`, `hubsite/apiapprovals.py`, `hubjobs/unblock_conditions.py:160`, `guard/prepush.py`, `tools/hub.py`.
- Change: a move. The standing rules it carries stay as they are: per-commit judgement, the merge-tree rule for sync merges, the approval that holds until its change is on main with a 30-day backstop.
- Done when: `ls kitlib/guard/ownercommits.py kitlib/commit/approval.py kitlib/hubsite/approvals.py kitlib/hubapprove` all fail; `grep -rnE "guard\.ownercommits|guard import ownercommits" kitlib tools` prints nothing; `tests/ownerfiles`, `tests/gate/test_land_owner.py`, `tests/guard/test_prepush_owner.py` pass with test names unchanged.
- Risk and tier: High (owner-file rules, `guard`, `commit`). senior-worker.
- Owner: guard. After: W-014.

**W-016 The hub applies an approved change**
- Serves: "Contracts" (Owner files: no agent writes, stages or commits an owner-only path, proposed and awaiting the owner's decision); docs/modules/owner-files-design.md. A change of behaviour, not a move.
- Why: an agent that applies an approved owner file is refused by the host's own permission classifier as self-modification, so today an approval stalls until the owner is at the machine.
- Files: `kitlib/ownerfiles/apply.py`, `kitlib/hubsite/apiapprovals.py` (the decide route), `tools/hub.py` (`approvals apply`, an owner verb), `kitlib/hubmcp/server.py` (`hub_approval_request` names the worktree), `kitlib/commit` and `tools/commit.py` (the agent form of `--owner-approval` goes), `kitlib/guard/rules_owner.py` (the apply verb is the owner's), `content/skills/` (hub-ops, builder: the approval steps), docs/commit.md "Owner approval", docs/hub-approvals.md.
- Change: on the owner's approval the hub server makes the approval's commit in the worktree the request names, with the checks `commit.py --owner-approval` runs today, and tells the asking session by push. The owner can run the same apply from an outside terminal. A change that cannot be applied shows on the approval with the reason and stays approved. The design must say how the apply and an agent's own git calls in that worktree keep out of each other's way (the private index, a refusal on a staged or changed approved path).
- Done when: `tests/ownerfiles/test_apply.py::test_approve_commits_in_named_worktree`, `::test_commit_passes_owner_rules`, `::test_unappliable_stays_approved_with_reason`, `::test_put_back_on_refusal` pass; `tests/commit` has a test that an agent's `--owner-approval` is a usage error naming the hub; `grep -rn "owner-approval" content/skills` prints nothing.
- Risk and tier: High. senior-worker, design first.
- Owner: guard; the owner has decided the behaviour. After: W-015.

**W-017 Owner-only tool names live with their owners**
- Serves: "Layers" (the guard imports nothing from the apps layer); docs/modules/lock-design.md, guard-design.md.
- Files: `kitlib/guard/rules_owner.py:11` (imports `lockmcp`), `kitlib/guard/hub.py:34` (imports `hubmcp.OWNER_ONLY`); the names move to `kitlib/lock/` and `kitlib/hubvalidate/`.
- Done when: `grep -rnE "import (lockmcp|hubmcp)|kitlib\.(lockmcp|hubmcp)|from \.\.(lockmcp|hubmcp)" kitlib/guard` prints nothing; guard decisions identical.
- Risk and tier: High path, small. worker, fresh reviewer.
- Owner: guard, lock. After: W-013.

**W-018 The test child's audit is handed in**
- Serves: "Dependency rules" 3; docs/modules/tests-design.md.
- Files: `kitlib/pytests/child.py:23` (imports `tooldeps.deps`), `tools/pytests.py`.
- Done when: `grep -rn "tooldeps" kitlib/pytests` prints nothing; `tests/pytests` passes.
- Risk and tier: Low. worker.

**W-019 The launcher owns session and account facts**
- Serves: docs/modules/sessions-design.md.
- Files: `kitlib/pmstart/__init__.py:504-605` (`STORE_DIR`, `rc_name`, `cli_names`, `runs_cli`, `linux_clis`, `runs_a_cli`, `session_images`, `live_session_names`, `down_entries`, to `kitlib/launcher/lookup.py`); `kitlib/pluginsource/claude.py` and `machinesetup`'s `dedupe`, `default_config_dirs` (to `kitlib/launcher/accounts.py`); `kitlib/launcher/relaunch.py:45` (push words from `hubclient`); `kitlib/launcher/autostart.py` (deleted: its task is retired; `TASK_NAME`, `LINUX_JOB`, `in_worktree` go to their one reader). Importers: `hubjobs/{coord_restart,usage_crossover}.py`, `coordinatorstart`, `pmwatch`, `machinesetup/{__init__,extras,plugin,inventory}.py`, `hubsite/autostart.py:30`, `platform/linux/schedule.py`, `pmstart/consoletasks.py:29`, `tools/launch.py`.
- Done when: `ls kitlib/launcher/autostart.py kitlib/pluginsource/claude.py` both fail; `grep -rnE "kitlib\.pmstart|import pmstart|from \.\.pmstart" kitlib/launcher kitlib/backlog kitlib/pluginsource` prints nothing.
- Risk and tier: Medium. strong-worker. After: W-005, W-006, W-014.

**W-020 `pluginsource` reaches nothing above it and is split by responsibility**
- Serves: "Layers" (services: `pluginsource`), "Dependency rules" 7; docs/modules/install-design.md.
- Files: `kitlib/pluginsource/__init__.py` (1,681 lines).
- Change: split into `layout.py`, `export.py`, `state.py`, `refresh.py`, `prune.py`; its imports of `machinesetup` and `pmstart` are gone through W-005, W-006 and W-019.
- Done when: `grep -rnE "machinesetup|pmstart" kitlib/pluginsource --include=*.py` prints nothing; no .py file in `kitlib/pluginsource/` is over 1,500 lines; `tests/pluginsource` passes with test names unchanged.
- Risk and tier: Medium. strong-worker. After: W-005, W-006, W-019.

### Work

**W-021 The backlog takes its worktree and verdict facts as probes**
- Serves: "Dependency rules" 3; docs/modules/backlog-design.md, worktree-design.md.
- Files: `kitlib/backlog/plan.py:2167-2330` (`quiet_hold`, `_queued_verdict`, `_quiet_activity`, to `kitlib/worktree/quiet.py`), `kitlib/backlog/opener.py:69-71,159-164` (`liveness`); callers that open a store with a context: `tools/backlog.py`, `kitlib/dailyfull/file.py`, `kitlib/hubjobs/{coord_restart,fill_watch,unblock_conditions}.py`, `kitlib/hubsite/recordwrite.py`, `kitlib/maintlint/`, `kitlib/backlog/at_landing.py:740`.
- Change: `plan.Context` keeps its `liveness` and `quiet` probes and loses their defaults; the opener takes the probes as a required argument; `worktree.quiet` supplies them. The verdict-queue read comes from `records` (W-012).
- Done when: `grep -rnE "kitlib\.(worktree|gate|landing)|from \.\.(worktree|gate|landing)" kitlib/backlog --include=*.py` prints nothing; `tests/worktree/test_quiet.py` holds the quiet-hold tests under their old names; a test that the opener without probes raises.
- Risk and tier: High (`kitlib/backlog`; the release of a live pull). senior-worker. After: W-012.

**W-022 Scratch-tree helpers live in `worktree`**
- Serves: docs/modules/worktree-design.md, gate-design.md.
- Files: `kitlib/redfirst/run.py` (`make_tree`, `cleanup_here`, `leftover_line`, to `kitlib/worktree/scratch.py`), `kitlib/gate/rerun.py:813-824`.
- Done when: `grep -rn "redfirst" kitlib/gate --include=*.py` shows no import; `tests/gate/test_rerun*.py`, `tests/redfirst` pass.
- Risk and tier: High path (`kitlib/gate`), a move. strong-worker.

**W-023 `landing` is its own package**
- Serves: "Layers" (work: `gate`, `loadnode`, `landing`); docs/modules/landing-design.md, gate-design.md.
- Files: `kitlib/gate/{land,submit,verdicts,carry,cleanup}.py` (to `kitlib/landing/`), `kitlib/hubsite/landings.py` (to `kitlib/landing/feed.py`); the late imports between `gate/runner.py`, `reuse.py`, `rerun.py` and `land`; importers `tools/land.py`, `tools/gate.py`, `tools/verdict.py`, `kitlib/coordstate/render.py:116-213`, `kitlib/hubjobs/{fill_watch.py:320,store_upkeep.py:64}`, `kitlib/reviewpack/__init__.py:626`; `tests/gate/test_land*.py`, `test_verdict*.py`, `test_submit*.py` (to `tests/landing`). `kitlib/gate/strict_defaults.py` stays where it is and gains `kitlib/landing/**`.
- Change: a move. `gate` no longer imports `loadnode`, `backlog` or the hub; `landing` imports them.
- Done when: `ls kitlib/gate/land.py` fails; `grep -rnE "kitlib\.(landing|loadnode|backlog|hubsite)|from \.\.(landing|loadnode|backlog|hubsite)" kitlib/gate --include=*.py` prints nothing; `grep -rnE "kitlib\.hubsite|from \.\.hubsite" kitlib/landing` prints nothing; `tests/landing`, `tests/gate`, `tests/loadnode` pass with test names unchanged.
- Risk and tier: High. senior-worker.
- Owner: `kitlib/gate/strict_defaults.py`, lock. After: W-012, W-014, W-015, W-025.

**W-024 The landing is split by step**
- Serves: "Dependency rules" 7; docs/modules/landing-design.md "Packages and files".
- Files: `kitlib/landing/land.py` (a path that exists only after W-023 moves `kitlib/gate/land.py` there; 3,438 lines today in `kitlib/gate/land.py`).
- Change: `lander.py` (the order of steps), `checks.py`, `merge.py`, `mergedgate.py`, `push.py`, `after.py`.
- Done when: no .py file in `kitlib/landing/` is over 1,500 lines; `tests/landing` passes with test names unchanged.
- Risk and tier: High, behaviour-neutral. senior-worker. After: W-023.

**W-025 A landing runs only what its work touches**
- Serves: "Contracts" (What a landing runs); docs/modules/landing-design.md. A standing owner rule that the code does not yet follow.
- Files: `kitlib/gate/land.py:1438-1466` (`once_escalate`, `strict_escalate`), its `gate_once`, `select_refusal` and `full_needed`; `kitlib/gate/reuse.py` (`config_hits`); `kitlib/config/schema.py:279-280`; readers of `gate_once` in `kitlib/reviewpack` and `kitlib/worktree/classify.py`; `.kit.json`; docs/gate-run.md ("Gate once at landing", "Shared paths"), docs/gate-landing.md (steps 4 and 6), docs/gate-landing-policy.md ("Strict paths"), docs/kit-json.md; `content/skills/` (builder, land-branch, branch-review) wherever they ask for a full run before a landing.
- Change: (1) every landing's merged gate runs in default mode: each step picked by the merge's change, in its selected form, with reuse. (2) A strict-path landing runs the same selection with `--fresh`, never `--full`. (3) A landing whose diff hits a gate config file runs `--full` once on the merged tree; its tip needs no full record. (4) No tip needs a full record to land. (5) `landing.gate_once` and `landing.shared_paths` have no reader left: the schema keeps both as retired keys that load, are ignored and warn once, so other repositories' files keep loading, and the kit's own `.kit.json` drops them.
- Done when: `tests/landing` (or `tests/gate` if built before W-023) has `test_plain_landing_runs_selected`, `test_strict_landing_runs_selected_fresh`, `test_gate_config_landing_runs_full_once`, `test_no_full_record_needed`, `test_retired_keys_load_and_warn`; `grep -rnE "once_escalate|GATE ONCE" kitlib` prints nothing; docs/gate-landing-policy.md "Strict paths" names no full run.
- Risk and tier: High (what gates main). senior-worker, design first.
- Owner: `.kit.json`.

**W-026 `backlog/plan.py` and `store.py` are split by responsibility**
- Serves: "Dependency rules" 7; docs/modules/backlog-design.md "Packages and files".
- Files: `kitlib/backlog/plan.py` (3,064 lines), `kitlib/backlog/store.py` (1,658).
- Change: plans by family (add and set, pull and release, block, close, narrowing); the store by backend and by read and write.
- Done when: no .py file in `kitlib/backlog/` is over 1,500 lines; `tests/backlog` passes with test names unchanged.
- Risk and tier: High path, behaviour-neutral. strong-worker, fresh reviewer. After: W-021.

**W-027 `commit` is split by responsibility**
- Serves: "Dependency rules" 7; docs/modules/commit-design.md.
- Files: `kitlib/commit/__init__.py` (1,554 lines): `run.py`, `checks.py`, `merge.py`, `message.py`.
- Done when: no .py file in `kitlib/commit/` is over 1,500 lines; `tests/commit` passes with test names unchanged.
- Risk and tier: High path, behaviour-neutral. strong-worker, fresh reviewer. After: W-011, W-015.

**W-028 A delegated run's app checks are handed in**
- Serves: "Dependency rules" 3; docs/modules/owner-files-design.md.
- Files: `kitlib/delegations/run.py:55-57` (hub imports), `:607` (`landed`), `:635-645` (`pmstart.checklist`); its entry point in `tools/`.
- Change: the PM-start checklist check is passed in by the entry point; hub errors come from `hubclient`, approvals from `ownerfiles`.
- Done when: `grep -rnE "pmstart|hubsite" kitlib/delegations --include=*.py` shows no import; `tests/delegations` passes.
- Risk and tier: Medium. worker. After: W-014, W-015.

**W-029 The trailers rule lives in `trailerscheck`**
- Serves: "Contracts" (Trailers); docs/modules/checks-design.md.
- Files: `kitlib/reviewpack/trailers.py` (to `kitlib/trailerscheck/`), `kitlib/trailerscheck/__init__.py:25`, `kitlib/reviewpack/__init__.py`, `tools/trailers.py`.
- Change: a move. The rule stays a warning everywhere: a commit without a Co-Authored-By line is never refused.
- Done when: `grep -rn "reviewpack" kitlib/trailerscheck` prints nothing; `tests/trailerscheck`, `tests/reviewpack` pass.
- Risk and tier: Low. worker.

### Guard and apps

**W-030 `hookrun`: the hook runner and the kit header**
- Serves: "Layers" (guard: `hookrun`), "Contracts" (Hook entry); docs/modules/guard-design.md, host-design.md.
- Files: `kitlib/host/{hookmain,header,headerfollow}.py` (to `kitlib/hookrun/{main,header,headerfollow}.py`); `boot/hook.py`; each `hosts/<name>/adapter/`; `kitlib/agentview/__init__.py:41`; the header and hook tests in `tests/host` (to `tests/hookrun`).
- Done when: `grep -rnE "kitlib\.(guard|pluginsource|config)|from \.\.(guard|pluginsource|config)" kitlib/host --include=*.py` prints nothing; each host's conformance test passes; guard decisions identical; a hook call with a broken kit still denies (`tests/boot`).
- Risk and tier: High (`boot/`, the hook path). senior-worker.

**W-031 `guard/commands.py` is split by tool family**
- Serves: "Dependency rules" 7; docs/modules/guard-design.md.
- Files: `kitlib/guard/commands.py` (1,736 lines).
- Done when: no .py file in `kitlib/guard/` is over 1,500 lines; guard decisions over `tests/guard` identical; test names unchanged.
- Risk and tier: High, behaviour-neutral. strong-worker, fresh reviewer.
- Owner: guard.

**W-032 The hub server is handed its jobs**
- Serves: "Dependency rules" 3; docs/modules/hub-design.md.
- Files: `kitlib/hubsite/serve.py`, `kitlib/hubsite/selfrestart.py` (import `hubjobs`), `kitlib/hubjobs/__init__.py` (discovery), `tools/hub.py` (`serve`).
- Done when: `grep -rnE "import hubjobs|kitlib\.hubjobs|from \.\.hubjobs" kitlib/hubsite` prints nothing; `tests/hubsite`, `tests/hubjobs` pass.
- Risk and tier: Medium. strong-worker.

**W-033 `tools/hub.py` holds no logic**
- Serves: "Layers" (entry points parse, call, print, exit); docs/modules/hub-design.md (`hubsite/cli/`).
- Files: `tools/hub.py` (1,797 lines: setup, skill edits, approvals, checks, pages, stop and restart); new `tools/_hub_args.py` (the argparse surface).
- Change: each verb's work moves to `kitlib/hubsite/cli/`; the tool parses, calls and prints.
- Done when: new test `tests/hubsite/test_hub_tool_thin.py` passes (it fails when `tools/hub.py` is over 500 lines, or when `tools/hub.py` or `tools/_hub_args.py` imports a `kitlib.hubsite` module other than `kitlib.hubsite.cli`).
- Risk and tier: Medium. strong-worker. After: W-014, W-015, W-032.

**W-034 `hubsite/serve.py` and `hubjobs/usage_crossover.py` are split by responsibility**
- Serves: "Dependency rules" 7; docs/modules/hub-design.md.
- Files: `kitlib/hubsite/serve.py` (1,883 lines), `kitlib/hubjobs/usage_crossover.py` (1,867).
- Done when: neither folder has a .py file over 1,500 lines; `tests/hubsite`, `tests/hubjobs` pass with test names unchanged.
- Risk and tier: `serve.py` Medium, strong-worker; `usage_crossover.py` High (it stops and restarts live sessions), senior-worker. Behaviour-neutral. After: W-032.

**W-035 The session starts keep only their checks**
- Serves: docs/modules/sessions-design.md.
- Files: `kitlib/pmwatch/__init__.py:411,531,678,720` (imports `pmstart`), `:532,717-718` (imports `hubsite`); `kitlib/hubsite/{derived.py,push.py}` and the serve modules (`serve.py` and the files W-034 split from it), `kitlib/hubjobs/{coord_restart.py:94,reminders.py:45,usage_crossover.py:176}` (import `pmstart`).
- Change: after W-006, W-009, W-014 and W-019 nothing but the session starts reads `pmstart`; `KIT_ROOT` readers use `kitroot.root()`.
- Done when: `grep -rnE "^\s*(from|import) .*pmstart" kitlib --include=*.py` (imports only) prints only lines in `kitlib/pmstart/`, `kitlib/coordinatorstart/`, `kitlib/hoststart/`; `tests/pmstart`, `tests/pmwatch` pass.
- Risk and tier: Medium. strong-worker. After: W-006, W-009, W-014, W-019.

**W-036 `machinesetup` takes `stubs`; `autotasks` owns its cadence**
- Serves: "Layers" (apps); docs/modules/install-design.md.
- Files: `kitlib/stubs/` (to `kitlib/machinesetup/memory.py` and its small outcome type); importers `kitlib/machinesetup/repo.py:332-567`, `kitlib/pmstart/__init__.py:790-852`, `tools/kit_stubs.py`; `AUTO_EVERY_MINUTES` in `kitlib/machinesetup/extras.py` (to `kitlib/autotasks/`; reader `kitlib/hubsite/apisched.py:102`); `main_checkout` read from `worktree.remove` by `kitlib/autotasks/tick.py:224` and `kitlib/machinesetup/worktreesweep.py:31` (use `git.repo`); the `tests/stubs/test_memory.py` path in `.kit.json` (to `tests/machinesetup/test_memory.py`).
- Done when: `ls kitlib/stubs` fails; `grep -rn "machinesetup" kitlib/hubsite --include=*.py` shows no import; `grep -rn "worktree" kitlib/autotasks --include=*.py` shows no import.
- Risk and tier: Low to Medium. worker.
- Owner: `.kit.json`.

**W-037 `lock/run.py`, `platform/linux/contain.py` and `worktree/remove.py` are split by responsibility**
- Serves: "Dependency rules" 7; docs/modules/lock-design.md, base-design.md, worktree-design.md.
- Files: `kitlib/lock/run.py` (2,145 lines), `kitlib/platform/linux/contain.py` (2,158), `kitlib/worktree/remove.py` (1,768).
- Done when: none of the three folders has a .py file over 1,500 lines; `tests/lock`, `tests/platform`, `tests/worktree` pass with test names unchanged.
- Risk and tier: High (the lock, containment), behaviour-neutral. senior-worker.
- Owner: lock. After: W-001.

### Enforcement

**W-038 The module map is the design**
- Serves: "Layers", "Dependency rules" 1; docs/modules/checks-design.md.
- Files: `docs/module-map.json`, `docs/module-ceilings.json`, `docs/module-architecture.md` (generated), `.kit.json` (the `module-map` step loses `"warn": true`).
- Change: one module per package as ARCHITECTURE.md's tables name them, each with its layer; the edges are exactly the allowed ones; the ratchets list is empty and the ceilings file holds none; the "same layer, any direction" entries go. The page is regenerated.
- Done when: `python tools/module_map.py -C . --check` prints `module_map: OK`; the map's `ratchets` list is empty; the existing test `tests/modulemap/test_kit_map.py`, extended, passes (every edge in the kit's map goes to a lower layer, or to a same-layer package ARCHITECTURE.md's row names).
- Risk and tier: Medium. strong-worker.
- Owner: `docs/module-map.json`, `.kit.json`. After: every other code item.

**W-039 A test holds the size line**
- Serves: "Dependency rules" 7.
- Files: new `tests/modulemap/test_size_line.py`.
- Change: fails on a `kitlib` .py file over 1,500 lines outside a short listed set (the fake git's files).
- Done when: the test passes on the batch's tip.
- Risk and tier: Low. worker. After: the split items.

### Deletions

**W-040 Delete `kitlib/lock/resource.py`**
- Serves: docs/modules/lock-design.md (a landing books its turn in the slot queue; nothing calls the resource lock).
- Files: `kitlib/lock/resource.py`, `tests/lock/test_resource.py`; mentions in docs/gate-landing.md, docs/lock-api.md, docs/linux-support-plan.md.
- Done when: `grep -rnE "lock\.resource|lock/resource" --include=*.py --include=*.md .` prints nothing.
- Risk and tier: Low. worker.
- Owner: lock.

**W-041 Delete the pinned-release path**
- Serves: "Packaging" (no pinned release); the owner's decision.
- Files: `kitlib/kitrelease/`, `kitlib/packageexport/`, `tools/kit_release.py`, `tests/kitrelease/`, `tests/packageexport/`; the `PM_KIT_RELEASE_ID` check in `boot/kitboot.py`; readers in `hosts/codex/adapter/{content,kit,workflow_canary}.py`, `hosts/codex/shell/mcp.json`, `mcp.linux.json`, `kitlib/host/registry.py`, `kitlib/kitcmd/__init__.py`, `kitlib/pluginsource/__init__.py`, `kitlib/tooldeps/childenv.py`, `tools/kit_tests.py`, `tests/selection.json`; docs/kitboot.md, docs/plugin-source.md, docs/blender-mcp.md, docs/test-tools.md, hosts/codex/README.md.
- Change: the kit boots from the folder it is in, checked, and nothing else. The Codex entries keep booting from the export.
- Done when: `grep -rnE "kitrelease|packageexport|PM_KIT_RELEASE_ID|kit_release" --include=*.py --include=*.json --include=*.md .` prints only this file; `tests/boot`, `tests/codex`, each host's conformance test pass.
- Risk and tier: High path (`boot/`, a host adapter). strong-worker.

**W-042 Delete the one-shot migrations**
- Serves: the owner's decision.
- Files: `kitlib/config/move.py`, `tools/config_move.py`, `tests/config/test_config_move.py`, `tests/config/test_move.py`, `tests/guard/test_jail_config_move.py`; `kitlib/ledger/migrate.py`, `kitlib/ledger/move.py`, the `split-move`, `migrate-report` and `rule-apply` verbs of `tools/ledger.py`, `tests/ledger/test_migrate.py`, `tests/ledger/test_move.py`; mentions in `kitlib/config/user.py`, `kitlib/delegations/load.py`, `kitlib/autotasks/tick.py`, `kitlib/kitcmd/__init__.py`, `kitlib/machinesetup/machineconfig.py`, `hosts/codex/adapter/dispatch.py`, the guard's owner-tool list, docs/kit-json.md ("Moving the machine config into the PM repo"), docs/ledger.md, docs/install.md, docs/guard-owner.md, docs/guard-jail-roles.md, docs/delegations.md.
- Done when: `grep -rnE "config_move\b|split-move|migrate-report|rule-apply|ledger\.migrate" --include=*.py --include=*.md .` prints only this file.
- Risk and tier: Medium. worker, fresh reviewer for the guard list.
- Owner: guard.

**W-043 `templates/` holds only what a setup step copies or a reader uses**
- Serves: docs/modules/install-design.md ("Adding to it").
- Files: `templates/common/`, `templates/stacks/*/`; the copy lists in `kitlib/machinesetup/repo.py` and `kitlib/stackskill/`.
- Change: delete each template file no setup step copies and no reader uses, or wire it in if it should be copied. A template a role or docs/install.md reads (a file the owner or an agent drafts or copies from by hand) counts as used.
- Done when: new test `tests/templates/test_all_copied.py` passes (every file under `templates/` is named by a copy list, or by its `templates/...` path in a role or docs/install.md).
- Risk and tier: Low. worker.

### Docs

**W-044 Code stops citing the two plans as designs**
- Serves: ARCHITECTURE.md (the one design).
- Files: 30 `kitlib`, `tools`, `hosts` files and 25 test files cite docs/multi-host-plan.md ("decision N", "section 2.x"); 37 and 38 cite docs/linux-support-plan.md.
- Change: each docstring cites ARCHITECTURE.md or the low-level design that now holds the point. docs/multi-host-plan.md is then deleted (it is all built; its history is in git). docs/linux-support-plan.md is cut down to its open slices.
- Done when: `grep -rn "multi-host-plan" --include=*.py --include=*.md .` prints only this file; `grep -rn "linux-support-plan" kitlib tools boot hosts` prints nothing.
- Risk and tier: Low, bulk and mechanical. worker with the helper.
- Owner: guard (docstrings only).

**W-045 Stale comments**
- Files: "the hub site design, section N" in `kitlib/hubsite/*.py` docstrings (that doc is gone: cite docs/modules/hub-design.md or the hub's topic doc); `kitlib/platform/api.py:3-4` (calls the Linux backend a stub); `kitlib/guard/hub.py:184-185`; `kitlib/gate/runner.py:81`.
- Done when: `grep -rn "hub site design" kitlib` prints nothing.
- Risk and tier: Low. weak-worker.
- Owner: guard (one comment).

**W-046 Doc merges and fixes**
- Change, one bullet each:
  - docs/flaky-tests.md becomes a section of docs/test-tools.md; the three code files and three test files that cite it follow.
  - docs/backlog-store.md loses its finished "Migration" section.
  - optional/bridge/STATUS.md is merged into optional/bridge/README.md.
  - docs/guard-v2.md: its still-open rows move to docs/guard-fallback.md's known limits; its seven code and 24 test citations follow; the file goes.
  - docs/specialty-skills.md: what still holds moves to docs/content.md; the file goes.
  - docs/go-live.md goes once docs/agent-profile.md no longer cites it (W-047); its open switches move to the docs that own them.
  - docs/action-hub.md cites two bullet leads ("Timed jobs", "Filed answers") as section titles.
  - docs/launcher.md "Hub restart" credits the refused-stop backoff to the coordinator restart job; it is the usage crossover's.
  - docs/new-worktree.md cites `WAIT_S`, a name no code has.
  - docs/shipping.md understates the record-id form the scrub matches and omits one key type.
  - docs/content.md: the MCP server lists omit the Blender server; the docs-writer tier is wrong.
  - docs/test-tools.md: two dated test counts disagree.
  - README.md: "What works today" says the guard refuses a heavy run outside the lock (it warns by default); "Who it is for" says the kit needs Windows (it runs on Windows and Linux).
- Done when: `ls docs/flaky-tests.md docs/guard-v2.md docs/specialty-skills.md docs/go-live.md optional/bridge/STATUS.md` fails for every file; `grep -rnE "flaky-tests|guard-v2|specialty-skills|go-live|bridge/STATUS" --include=*.py --include=*.md .` prints only this file; `grep -n "^## Migration" docs/backlog-store.md` and `grep -n "WAIT_S" docs/new-worktree.md` print nothing; `grep -nE "a heavy run outside|needs Windows" README.md` prints nothing; `tests/docs/test_kit_index.py` passes (the index lists exactly the tracked docs).
- Risk and tier: Low. weak-worker or docs-writer.
- After: W-047 (the go-live bullet only).

**W-047 Owner-only docs follow the design**
- Files: `docs/agent-profile.md`, `AGENTS.md`, `.claude/coordinator-profile.md`.
- Change (the builder drafts, the owner applies): the profile's "Moves and renames" no longer asks for a stub at an old path, `tests/oldpaths` (it does not exist) or a full gate on base and tip, and asks instead for the selected tests and an equal test-name list; "Build and test" and "Known flakes" stop presenting `--full` as a branch's routine; the risk map names the new packages (`kitlib/hookrun/`, `kitlib/landing/`, `kitlib/ownerfiles/`, `kitlib/hubclient/`); `AGENTS.md` points at `docs/modules/` beside ARCHITECTURE.md.
- Done when: `grep -n "oldpaths\|go-live" docs/agent-profile.md` prints nothing.
- Risk and tier: Low. The owner's files.
- Owner: all three files.

## Not in this batch

- The Linux work still open in docs/linux-support-plan.md (peer messaging and the bridge, CI legs, the live proofs). It is a plan of its own.
- `kitlib/hubsite/web/index.html` (3,726 lines): the size line is for Python.
- Host names inside `kitlib/launcher` and `kitlib/machinesetup` (account folders, the plugin runner). They are today's one live host; moving them behind the host contract is a design question for a later round.

## Doc audit

What each doc is, and whether it agrees with the code, as of the base of this branch. Method: every path, dotted name, identifier and section citation in every doc was checked against the tree, and nine reviewers sampled each doc family's claims against the code.

**Design**: ARCHITECTURE.md (rewritten here as the end goal), `docs/modules/*.md` (new).

**Reference, agrees with the code, kept** (each how-to now sits under a low-level design that points at it): action-hub, allow-rules, backlog, backlog-store, blender-mcp, branch-overlap, commit, content, coord-state, daily-full, delegations, doc-tools, engineering-rulings, gate, gate-run, gate-records, gate-rerun-checkout, gate-landing, gate-landing-policy, guard, guard-core, guard-rules, guard-jail-roles, guard-fallback, guard-owner, guard-hooks, hub-approvals, hub-fill, hub-mcp, hub-pm, hub-push, hub-questions, hub-records, hub-remote-access, hub-serving, hub-skill-pages, hub-using, hub-views, install, kit-json (generated from the schema), kitboot, landed, launcher, ledger, load-nodes, lock-api, lock-mcp, lock-release, machine-setup, maint-lint, module-map, module-architecture (generated), new-host, new-worktree, pending, plugin-source, pm-data-branch, pm-start, pm-watch, preflight, redfirst, repo-checks, review-pack, ROSTER (generated tables), shipping, stray-restore, sync-base, test-tools, worktree-paths, worktree-remove; README.md, content/README.md, hosts/codex/README.md, optional/bridge/README.md. Small stale spots: fixed in this branch (guard-rules, guard-jail-roles, kitboot) or listed in W-046. The gate docs describe today's full run at a landing; W-025 changes them with the code.

**Runbook, kept**: pm-first-run, windows-token-leak.

**Plan**: multi-host-plan (all built; five of seven sampled points no longer match the code; retired by W-044), linux-support-plan (built through most slices; three open; kept for those), go-live (W-046), guard-v2 (built, with open rows; W-046), specialty-skills (decided and built; W-046), flaky-tests (a how-to that belongs in test-tools; W-046).

**Dated record, kept as dated**: agent-review, model-ab, jev-fit-audit.

**Stale audit, retired in this branch**: subtraction-audit (every candidate done).

**Owner-only**: agent-profile (cites `tests/oldpaths`, which does not exist; W-047), AGENTS.md, CLAUDE.md.
