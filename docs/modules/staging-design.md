# Staging design

Low-level design of `kitlib/staging`: the promotion of a staging branch to the main branch between daily full runs. The high-level design is ARCHITECTURE.md (the Work layer; "What a landing runs") and wins on a conflict. The staging branch itself, and what a landing does with it, is docs/gate-landing.md "Staging branch"; the daily full run's own promotion is docs/daily-full.md "Promotion".

## Contract

- **Staging on** means `"staging".branch` set in `.kit.json` as `<remote>/<main>` holds it (`kitlib.dailyfull.promote.staging_read`), never as the working tree or the staging branch holds it. Off, nothing here does anything: `tools/promote.py` exits 2 and the hub job says nothing.
- **Main moves only to a tested commit.** A promotion tests the staging tip S against `<remote>/<main>` M with the merged-tree gate's selection, and on a green moves main to exactly S: one plain push (no force, no lease, no `--no-verify`), read back with `ls-remote`. A later staging tip is never pushed in S's place.
- **The pre-push guard decides.** The push runs the repository's pre-push hook like any other; with staging on the guard lets main move to S only with S's promotion record on base M (or S's daily record). A refusal by the guard ends the attempt `refused`, and nothing retries it.
- **One run per repository.** The promote lock is the run's first act; a second run exits 5 and reads, touches and records nothing.
- **No landing is mid-merge while main moves.** The push and the main checkout's sync run under the landing lock, taken with the promoter's own pid.
- **No machine-lock slot is held while waiting on a landing.** The gate's slot is released before the landing lock is asked for.
- **Every attempt ends.** Each phase either moves on or ends; an attempt a dead run left is moved on from its phase by the next run, never restarted.
- `kitlib/dailyfull` never imports `kitlib/staging`; this package uses `dailyfull`'s fetch, tree preparation and `promote.promote`.

## Packages and files

| Path | Holds |
|---|---|
| `staging/changes.py` | What is untested (first-parent `M..<remote>/<staging>`, with the oldest committer time) and whether a promotion is due |
| `staging/record.py` | The phase record: one folder per attempt, its phases, reading the attempts back |
| `staging/culprit.py` | The culprit hunt after a red: the landings of M..S, the suspects, the probes in order, the verdict |
| `staging/revert.py` | The revert of one landing as a commit object on the staging tip, and its local `revert/<sha10>` branch |
| `staging/advance.py` | `Mover`: the phases after the test, the landing lock, the push and its retries, the sync |
| `staging/promote.py` | `run` (one promotion run), `look` (the hub job's look), `status_lines` |
| `hubjobs/staging_promote.py` | The hub's timed job that starts a run and announces the ends |
| `tools/promote.py` | The thin CLI: `[-C DIR] [--force]`, `--status` |

## Promotion

`promote.run(main, cfg)`, in order:

1. **The promote lock** (`lock_for`: a `kitlib.fs.treelock` file `kit-staging-promote.lock` in the repository's git common dir, so every checkout shares it). Held: BUSY (exit 5). The OS frees it when its holder dies; the next run says `took over`.
2. **A fetch**, then the staging setting `<remote>/<main>` writes. Off: USAGE.
3. **Recovery.** An attempt in a live phase (record.LIVE) is moved on from that phase ("Phases and recovery"), and the run ends there.
4. **Due?** (`changes.due`): `"staging".promote_after_landings` untested commits (default 4), or the oldest of them `promote_after_hours` old (default 6); never with none. `--force` skips only this check.
5. **Held?** A tip that already ended in a hold (record.HOLDS: red, refused, diverged, failed, error) is never tested again, so a tip that keeps failing is not gated on every hub tick ("Errors" says how a hold is cleared). M not an ancestor of S ends `diverged`.
6. **The gate**: `kitlib.gate.runner.merged_gate(main, cfg_at_M, M, full, slug 'promote', tree <worktrees_dir>/promote)`, the tree made with `dailyfull.prepare` at S. The gate and hook config are M's, as a landing reads them. `full` (every step in full) when M..S changes the gate's config (`gate.reuse.config_hits`) or git cannot list the change, as at a landing. The slot is booked now or never (`BOOK`: the lock's `no_wait`), so a detached run never waits out a hub tick and background runs never queue behind it. A machine-lock refusal (busy, timeout, reserved, hold, no lock) is a skip that records nothing; the next tick asks again. FAILED ends `red`, written before anything else, and then hunts its culprit under the same lock ("Culprit and revert"); any other refusal (`NO_STEP` included) ends `error`; a gate that tested another commit than S ends `error`.
7. **The promotion record** of S on M (`write_record`: `gate_ok.record_tested_pass(kind 'promotion', base=M, config_rev=M, source, full)`). Not written: the attempt ends `failed` and main is never pushed.
8. **The attempt** starts at `tested` and `advance.Mover` moves it on.

The gate's slot is booked and released inside `merged_gate` (`slot=None`: `gated` books it and releases it before it returns), so it is free before step 8 asks for the landing lock (tested: `tests/staging/test_promote.py` "slot released before take").

## The phase record

One folder per attempt, `<gate log base>/promote/<stamp>-<sha10>/promote.json` (`kitlib.records.place.log_base`: `"landing".log_dir`, else the machine's `<kit temp>/<repo>-gate`). The stamp is local time to the microsecond, so attempts of one second read back in the order they started. It is never in the daily run's results folder: a promotion green is never a daily green, and never makes the daily run skip as `unchanged`.

Fields (schema 1): `schema`, `via` (`promotion`), `sha` (S), `base` (M, full hash), `phase`, `started`, `updated`, `pid`, `log_dir`, `why`, `tries` (the pushes tried), `announced`, `history` (`[[phase, time], ...]`). A red attempt adds `culprit` (`verdict`, `end`, `merge`, `by`, `suspects`, `probes` (`[{sha, what, verdict, red, why}]`), `red` (the steps hunted), `mode`; while the hunt runs only `verdict` `hunting`, `pid` and `red`) and, once a culprit is found, `revert` (`branch`, `base`, `commit`, `note`; or `why` and `base` when none was made).

| Phase | Kind | Meaning |
|---|---|---|
| `tested` | live | Green and its promotion record written; main not moved |
| `pushing` | live | The push is being made (or a run died making it) |
| `pushed` | live | Main is S on the remote; the main checkout not synced yet |
| `done` | end | Main is S, the main checkout synced as far as it safely can |
| `red` | end, hold | A gate step failed at S |
| `behind` | end | Main moved after the test; S is tested again next time |
| `refused` | end, hold | The pre-push guard refused the push |
| `diverged` | end, hold | Main has commits the staging branch lacks |
| `failed` | end, hold | The push could not be made or verified in `PUSH_TRIES` tries, or the promotion record was not written |
| `error` | end, hold | The gate refused to run at S, or tested another commit, or M's config cannot be read |

Records are written atomically, and only by the process holding the promote lock (the hub job's `announced` mark included).

## Phases and recovery

`Mover.advance(attempt)` moves a live attempt on until it ends or waits. A recovering run calls the same thing, so recovery is the normal path from that phase.

- **tested.** Fetch and read M again. Still the attempt's base: take the landing lock, then fetch and read M once more under it, and push only if it is still the base: the lock wait can be long, and the daily run's own promotion moves main without the landing lock. M moved to an ancestor of S: `behind` (retested next time). M already holds S: `done`. M elsewhere: `diverged`. M cannot be read: the attempt stays `tested` (the next run tries again).
- **pushing (recovered).** The same read of M under the landing lock: still the base, the push is made again; M already holds S (the dead run's push landed): `pushed`; else `behind` or `diverged` as above, never a push that the guard would refuse.
- **The landing lock.** `LandingLock.take` refuses at once while anyone holds it (it has no queue), so the mover polls every `LOCK_POLL_S` (5 s) up to `LOCK_WAIT_S` (900 s). A holder `kitlib.proc` says is gone is broken (`break_lock`, which refuses while a merge is in progress), except a stuck landing's (`records.landing.is_stuck`): that is cleared by hand only, the lander's own rule (`Lander.held_by` and `gate_refusal` ask the same predicate), so the attempt stays at its phase at once, without waiting. Not given in time: the attempt stays at its phase and the next run tries again. It is held to the end and released on every exit, an exception included.
- **pushing.** `dailyfull.promote.promote`: a fetch; `current` (no push) when main already holds S; else the one plain push, read back with `ls-remote`. Never a raw push and never a force. `promoted` or `current`: `pushed`. Git's output carrying the kit pre-push hook's refusal (`pre-push: refused`): `refused`, at once and with no retry, except a refusal that says only that the hook could not read what it judges by (`advance.TRANSIENT`, `pre-push: refused, cannot ...`: the remotes, git's ancestry, an object list), which counts as a failed push below. `diverged`: `diverged`. `off-staging`: `behind`. Any other failure is pushed again, up to `PUSH_TRIES` (3) in all with `PUSH_PAUSE_S` (15 s) between; then `failed`. A recovered `pushing` counts the tries made before.
- **pushed.** The main checkout's local main is fast-forwarded where that is safe (`kitlib.landing.push.sync_checkout`, the landing's own step; the main checkout's HEAD is always main), then `done`. A sync that cannot be made is a WARNING line with the `git merge --ff-only` to run; it never undoes the move.

There is no `synced` phase: `pushed` goes straight to `done` once the sync has been tried.

## Culprit and revert

`culprit.after_red`, called by `run` once a red attempt is written, still under the promote lock. It finds the landing of M..S that turned S red and prepares its revert for the coordinator to review. It replaces the "REVERT DUE" line of the staging plan's v3: nothing is reverted, pushed or landed by itself, and no reviewer is skipped.

- **The steps hunted** are the red run's FAIL steps less the gate's own (`dailyfull.bisect.NOT_STEPS` via `bisectable`): a red of the gate's own or an infra step alone is not hunted, and the attempt gets no `culprit`.
- **The landings** are every first-parent commit of M..S (`changes.first_parent`, the listing `untested` reads), each with the paths it changed against its first parent.
- **The suspects** are the landings any red step selects, as one set across every red step: a step's globs and content rule (`steps.selects`, read at S, `always` and `manual` set aside); a step with neither, or one M's gate lacks, takes every landing. A landing that changed a failing test's file (`rerun.cover_step`, the file of its class in that step's folder) or a path under a failing runner's scope is a hit, and the hits are the suspects when there are any.
- **The order.** S is probed first: green is a flake and the hunt ends. With one suspect its revert onto S (`revert.make`) is probed: green confirms it (`culprit <sha10> (confirmed by revert)`). Otherwise (several suspects, none, a revert that conflicts or is empty, or one still red) `dailyfull.bisect.search` runs over M and the landings, at most `DEFAULT_PROBES` probes, S's verdict reused: the first red landing is the culprit (`by bisect`); M red too ends `on-main` with no revert; the cap ends `capped` naming the range left.
- **A probe** is `bisect.Prober` (gate_mode `bisect`, slug `promote-hunt`, the failing tests alone first, then the whole steps) in the promotion's own tree. The narrowed probe reruns S's named failing tests; a whole-step probe runs that commit's own gate in the daily form (`dailyfull.plan`: each step's `daily_cmd`, else `full_cmd`, else `cmd`, no `merged_args`), as the daily bisect does. Never the promotion's own argv: a change-selecting cmd (`kit_tests.py --select` against M) selects nothing at M or at a revert, so a red already on main, or a revert that only drops the selection, would probe green. Its lines are said as `promote: hunt`. The tree is put back at S after every probe off the chain, and at the end.
- **The budget.** The whole hunt has `HUNT_BUDGET_S` (4 h), its slot waits included: each probe's slot waits for at most the time left (`wait_s`, never `no_wait`), so a probe queues behind other work as the daily bisect does, and the hunt never holds the machine past the budget. None left, or a probe given up after it ran out, ends `inconclusive (budget)`. A probe that cannot run (the lock refused, the tree could not be switched) ends `inconclusive`.
- **The revert** (`revert.make`) is a commit object made with `git merge-tree --write-tree --merge-base <landing> <S> <landing>^1` and `git commit-tree -p <S>`: it undoes only that landing's own changes (mainline 1) and keeps everything landed after it. Its author and committer are the kit's (`revert.AUTHOR`), never the machine's; its message names the landing and ends with `Reverts-landing: <full sha>`, so the landing that lands it reopens what that landing closed ("Reopen at a revert" in Gaps). No working tree or index is touched. A conflict or a revert with nothing to undo makes no commit and is said as `NO REVERT`; a conflict's line names the paths and the revert to make by hand on a branch from the staging tip: `git revert -m 1 <landing>` for a merge, `git revert <landing>` for a commit pushed straight to staging (`revert.by_hand`).
- **The branch.** `revert.prepare` makes the local branch `revert/<landing sha10>` in the main checkout at the revert onto `<remote>/<staging>` as it is now, fetched again (S's revert reused when staging has not moved). A branch of that name at another commit is left as it is and the line says so. Nothing is pushed. The line `promote: REVERT PREPARED - local branch ...` and the hub item `promote-revert` hand it to the coordinator, who reviews it, gates it and lands it (`new_worktree.py --existing revert/<sha10>`) like any branch.
- **A dead hunt.** A run that dies mid-hunt leaves `culprit.verdict` `hunting`; the next look, which holds the promote lock, marks it `stopped` and announces the red.
- **By hand.** `tools/promote.py --force` runs the hunt in the foreground after a red, within the same budget.

## The trigger

The hub job `staging-promote` (`kitlib/hubjobs/staging_promote.py`, every 600 s) runs no gate itself. For each repository `launcher.repos` names it calls `promote.look`: no fetch, the promote lock probed and let go.

- Staging off there: nothing, said nowhere. The promote lock held (a run's pid alive): `busy`, nothing.
- Under the lock it announces each attempt that ended red, refused, diverged, failed or error and is not yet announced: one job push to the PM, word `promote-<end>`, detail `<repo>:<sha10>`, then the record is marked announced. A red with a revert branch prepared is announced as `promote-revert` instead, detail `<repo>:<landing sha10>` (`record.Attempt.event`). A newer push of the same word replaces one the hub still owes (the `promote-<end>` words are not in the push worker's `JOB_EACH`): a known limit. `promote-revert` is in `JOB_EACH`, owed once per detail, so each prepared revert is its own item.
- Owed (an attempt left live, or a promotion due at a tip not held): one detached `tools/promote.py -C <repo>` (`kitlib.lock.detach`). That run takes the promote lock itself before anything else, so two starts never run twice.
- A repository whose config or git cannot be read, or a start that failed, is a note beside a start, else the job's failure (hub health shows it).

## State

The promote lock (in the git common dir), the attempts folder, the promotion's own tree (`<worktrees_dir>/promote`), the gate's log folder (`land-promote` under the gate log base), and the promotion records `gate_ok.record_tested_pass` writes.

## Errors

A step before the gate that cannot be made (the fetch, the staging read, the tree) is REFUSED with nothing recorded. Every attempt that starts ends in one of the phases above, and a hold stops promotion at that tip; the hub job announces it once.

How a hold is cleared: a new staging tip (any landing) is a fresh attempt, tested from the start. `red`, `diverged`, `failed` and `error` need nothing else (the landing carries the fix, or the cause was passing). `refused` needs the owner's action first: the refusal line in the attempt's `why` names what the guard refused, and a new tip would meet the same refusal until that is dealt with. To retry the same tip, the owner deletes that attempt's folder (`<gate log base>/promote/<stamp>-<sha10>/`); nothing else clears a hold, and `--force` skips only the due check.

Exit codes: 0 done, behind, not due or a skip; 1 red; 2 usage or off; 4 held, diverged, refused, failed or error; 5 busy.

## Gaps

- **`promote_args` (slice E).** A repository's own promotion arguments are not read yet: every promotion runs the merged gate's selection as the kit computes it. A repository whose patch needs `promote_args` must not switch to staging before slice E lands (docs/gate-landing.md "Switching a repo").
- **`suspects_cmd` (slice E).** A repository's own suspect rule is not read yet: the suspects are the kit's ("Culprit and revert"), from M's gate steps and the failing tests. No schema or `.kit.json` setting exists for it until slice E.
- **Reopen at a revert (slice D1).** A revert of a landing on staging carries a `Reverts-landing: <merge sha>` line, and the landing that lands it reopens what that landing closed (`kitlib.backlog.reopen`, docs/backlog-store.md "Reopen at a revert"). The landing must name a full sha on the first-parent chain of the base the revert merged onto; anything else is a NOTE and reopens nothing. Records the close unblocked are not blocked again. Each branch of a stacked batch now closes with its own merge, so a revert of one branch's landing finds its closes; reverting an earlier branch of a batch landed before this finds nothing (those closes all name the batch's last merge), and a NOTE says so.

## Adding to it

A new end phase goes in `record.ENDS` (and `HOLDS` and `ANNOUNCE` when it holds or is announced), gets its event word in the hub job's `EVENTS`, and its exit code in `promote.CODES`. A new trigger condition goes in `changes.py` and is read by both `run` and `look`. A new hunt end goes in `culprit.py` beside `FOUND`..`BUDGET`, with its step in `hunt`; a new field of a hunt's verdict in the record goes in `after_red` and this doc's "The phase record".

## Tests

`tests/staging`: due at the count and age boundaries and their settings; the phase record and its order; each phase's move and recovery, the push retries, a guard refusal, the landing lock busy, dead and timed out; a promotion green, red, held, behind, diverged, skipped by the machine lock, and refused by a real pre-push hook on real git; the hunt's order, suspects and budget with stand-in probes (`test_culprit.py`), the revert and its branch (`test_revert.py`, on the fake git only; real git's `merge-tree --merge-base` is covered by its rows in `tests/git/test_fake_differential.py`), and the hunt run for real after a red: confirmed by revert and by bisect, on-main, the revert on a staging tip that moved, its slot waits, a hunt that raises releasing its slot, a dead hunt stopped (`test_promote.py` Hunt), and the probes' daily form (`HuntDailyForm`). `tests/hubjobs/test_staging_promote.py`: the trigger and `promote-revert`.

## Reference

docs/gate-landing.md "Staging branch", docs/daily-full.md "Promotion", docs/gate-landing-policy.md, docs/kit-json.md (`staging.*`).
