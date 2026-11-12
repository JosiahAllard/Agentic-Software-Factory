# Action Hub: push notifications and store upkeep

### Push notifications

When the owner answers a question or marks a to-do done, the server tells the session that asked and
the PM, so neither has to be waiting (`kitlib/hubsite/push.py` docstring is the source). It is not the
store's git push (the `push` setting).

- Who runs it: `hub.py serve` on the live store asks the host adapter for its delivery (`push(settings)`;
  on Claude Code `hosts/claude/adapter/peer.py` `HubPush`) and the launcher's `state_dir` (`tools/hub.py`
  `_push_delivery`). One `PushWorker` thread per server (`kitlib/hubsite/serve.py` `build`) then watches
  the store's events. A scratch store (`serve --store`), a host with no delivery or an unset
  `launcher.state_dir` serves without it, with one note line: `push notifications off: <why>`. A worker
  that fails to start leaves the server serving (`push notifications did not start: ...`).
- What fires: a question answered (or answered again; `answered`: not bound to a record, its filing
  failed, or filed with no `filed_as`), a bound question's answer filed into its record (`filed`, nothing
  while the filing is under way), an answer already given made a standing rule (`standing`, to the PM:
  docs/hub-questions.md "A standing rule"), a to-do done (or done again), a done to-do reopened, a to-do the owner added (to the
  PM only), a story the owner submitted (`submitted`, PM only), a skill edit the owner submitted
  (`submitted`), a note to the PM the owner sent (`sent`, PM only), an approval the owner approved or denied (`decided`), a file approval the hub applied, committed on its branch (`applied`, in place of `decided` when the approve applied it), an approved one the hub tried and could not apply, once per try (`unapplied`, its `apply.at`; docs/hub-approvals.md "Applying"), and an approval still approved and not
  consumed 4 hours before it expires (`expiring`, `push.EXPIRING_LEAD_S` before `approvals.expires_at`:
  30 days after the decision for a file approval, 24 hours for an op one), the one reminder. A phase
  that goes back from `expiring` to `decided` (an approval whose life grew, at a deploy) fires nothing. Each item has a fingerprint (a question's phase, `answer.at`, a digest of the
  answer and, once a standing rule, `standing.at`; a to-do's status and `done_at`; a story's status and
  `submitted_at`, and a skill edit's too; a note's status and `sent_at`; an approval's status, `decided_at` and phase, `decided`
  then `expiring`, read at the worker's clock each round, and once there is one its `commit` and its failed `apply.at`), and only a changed fingerprint fires, so
  `pm_ack` alone never does. An approval's phase ending (consumed, expired, a denial's 24 hours past)
  drops what was still owed and sends nothing.
- Who hears: the item's `from`, unless that is the PM's own (`pm`, the owner, empty), and the PM, once;
  for an approval, the coordinator key its `repo` names (docs/hub-pm.md "Who handles a pending item") and the PM; for
  a skill edit, the kit's coordinator (`Api.kit_session_key`) and the PM. A
  launcher key is resolved through sessions.json: its recorded `name`, else, when no live session has
  that name, a live session of the entry's own stem (`<stem>-a<N>[-r<k>]`, `kitlib.launcher.lookup`
  `of_stem`; the newest if several, logged `ambiguous`), so a successor sessions.json never recorded
  still hears; `pm` through coordinators.json's `pm_session_id` (a
  `launch.py pm-relaunch` fork's own id), then the names `Project Manager` and `pm-session`; its `pm_name`
  counts as a PM sender, never as a fallback target.
- **Filed answers** (`filed`): who hears depends on what the filing did to the record, the question's
  `filed_as` (docs/hub-questions.md "Filing the answer"):
  - `pulled` (someone is working on the record): its asker alone, until it picks it up; when the PM
    asked, the PM, sent once, for its information.
  - `freed` (the record was parked or blocked and is now open) and `held` (the record still waits on
    more): one recipient, by who is live (the delivery's `resolve`; a delivery with no `resolve` counts
    as none live, and a key whose `resolve` raises as not live, logged; `push.py` `_live_session`): the
    asker while it is live, until it picks it up; else the coordinator of the record's repository or of
    the question's repo (each through the repository-to-key map an approval's push uses, `Api.repo_key`,
    then as it is) while one is live, sent once; else, for `freed`, the PM, sent once, to start a
    coordinator, and for `held`, the asker all the same (it also reads it at its next start). The PM that
    asked is told once. Until 2026-10-27 `held` went to nobody and `freed` only to the PM, on the
    assumption that a live session would find the answer by itself; live coordinators did not, and owner
    answers sat unread (KIT-HUB-FILED-ANSWER-NO-PUSH). The PM start's digest still counts the answers
    filed since the last start that are still `filed`, and those the hub marked done (`done_by` `hub`,
    named apart; docs/pm-start.md "Filed answers"); a logged one, or one its asker marked done, drops
    out.

  The phase a question goes through while it is being filed is skipped: a filed answer is judged against
  the question as it was before the answer (a first answer or a re-answer). An answer that is a standing
  rule also goes to the PM, whatever its `filed_as`, and a standing rule made of an answer already pushed
  adds the PM to the push still owed for it (`push.py` `_fire_filed`).
- What it says: a fixed header, `[hub push] <kind> <id> <status> repo:<repo> from:<from>
  route:<recipient> attempt:<k>`, and one line of what to do; never a title or an answer, since
  the session takes the message as a prompt. The one exception is the owner's note on a done to-do: one
  further line, framed as data, whitespace-collapsed, JSON-quoted and cut at 400 characters. A to-do's line is `A done mark is not proof: verify each
  step on disk, then: hub todo ack <id>`: the owner marks a to-do done even when a step inside it failed.
  A filed answer's line names the record (`<repo>:<id>`) and what to do: pick it up (`pulled`, the
  asker), read it with no ack needed (`pulled` to the PM that asked, and `freed` to the PM, which also
  says to start a coordinator of the record's repository), and, for a standing rule, log it in the
  ledger (`hub questions log <id> --if-version N`), which acknowledges it. A `freed` or `held` line says
  first what the filing did: `frees record <repo>:<id>` (open to take up), or `does not free record
  <repo>:<id>: the record stays as it was (still parked or blocked if it was), so do not take it up as
  freed`; then read it (`hub pending --for <asker>`, which acknowledges it), or, to a coordinator or the
  PM that asked, `hub view <id>` with no ack needed (a coordinator's names the asker that is not running).
- Until when: each recipient is sent each item until it is acknowledged. The asking session acknowledges
  by picking the item up (`pending --for` or `wait --pending --for` hands it over), or by `todos ack`;
  the PM by `todos ack` for a to-do and by `questions log` or `questions done` for a question; for a
  question the hub filed into its record (status `filed`) asked by another session, by the filing itself,
  since its asker reads it; once the asker of a filed answer has it (its pick-up, or the PM that asked:
  the first written try), the worker marks the question done (docs/hub-questions.md "Done by the hub"); for a filed answer's note to the PM that asked it, its `freed` ping and its
  push to a record's coordinator, by the first written try; for a standing rule, only by logging it (a standing rule is never
  marked done); for a story, any change of its status (`stories set`), for a skill edit, any change
  of its status (`skill-edit done` or `fail`, the owner's withdraw), and for a note to the PM, its
  reply (`to-pm reply`). An approval's push is sent once to each: the first written try acknowledges it.
  An item withdrawn, reopened or answered again replaces or ends its push. A session that picked up a
  first answer or done mark before the worker saw it is not sent it. A question's phase is
  `push.question_phase`: an answer to a bound question that is neither filed nor failed is `filing` and
  fires nothing, a filed one with a `filed_as` is `filed`, and one filed with no `filed_as` (its record
  could not be read) counts as `answered`: it fires one `answered` push, with `filed` in its header, and
  its asker is sent it until it picks it up, logs it or marks it done. An answer whose filing failed
  (`filing` set) is `answered` too. One message lists at most 20 items (`push.MAX_ITEMS`) and a `+N more`
  line; the rest wait for the next slot. Retries come 2, 5, 15, 30 and 60 minutes after a try, then
  hourly, and stop a day after the first try with one `gave-up` line (`push.SCHEDULE_S`,
  `push.GIVE_UP_S`). A write whose end is unknown waits for the next slot like any other outcome.
- How it is sent on Claude Code: one message is two JSON lines written once to the live session's peer
  pipe (an auth frame with that session's peer token, then a user frame wrapped as a cross-session
  message in prompting mode), by a hidden helper in a kill-on-close job
  (`hosts/claude/adapter/peer_worker.py`), so a blocked write never blocks the server. The pipe, pid and
  creation time come from the session's registry record at send time (docs/launcher.md "Pipe
  addresses"); only a record whose pid still runs with that creation time counts, because `launch.py
  stop` leaves records behind. The token is read from the key file beside the record only when sending,
  checked against the record's creation time, and never logged or stored. The hub publishes no registry
  record of its own and gets no receipt: `written` means the bytes reached the pipe, not that the session
  read them. Two live sessions of one name: the one `pipes.json` names, else the newest, logged
  `ambiguous`. Whether a desktop session wakes on a push is unproven; a session also reads `pending
  --for <key>` at each start, which catches what a push missed.
- The PM watcher: every `push.WATCHER_POLL_S` (30 s) the worker's thread reads the heartbeat pm_watch
  writes beside its state file (`kitlib.pmwatch.heartbeat_file`; the state file is the `pm_watch`
  config's `state_file`, else `kitlib.pmwatch.default_state_file`). A heartbeat at most
  `push.WATCHER_STALE_S` (300 s) old is `up`, an older one `down`, none readable `none`. When a heartbeat
  this worker saw fresh goes stale or missing, it pushes the PM once `[hub push] watcher down
  age:<seconds|none> route:pm attempt:<k>` and a line to start pm_watch again (docs/pm-watch.md); a fresh
  heartbeat acknowledges it, a written try ends it, and a try not written is retried on the same
  schedule. It fires again only after the heartbeat was fresh again, and never on a server that has not
  seen a fresh one (it is not kept across a restart). A pm_watch run with its own `--state` path is not
  seen. The `session-watch` job stands in for the heartbeat: while its health block in `jobs` has fewer
  than 3 failures and a success at most 300 s old (`kitlib.pmwatch.session_job_healthy`, read through
  `Api.jobs_seen`) the watcher is `up` and counts as fresh, so no `watcher down` push is sent while the
  job covers; a job reader that raises covers nothing.
- Timed jobs: each job is a module of `kitlib/hubjobs` with `make(sources)`, found there by
  `kitlib.hubjobs.makers` (pkgutil; a module named `_...` is a helper, not a job) and run by
  `kitlib.hubsite.jobs`; adding one needs no edit to a shared file (the `kitlib.hubjobs` docstring
  says how). The server never imports them: `tools/hub.py serve` builds its Sources with
  `kitlib.hubjobs.machine_sources`, which hands the makers to `servesources.machine_sources(jobs=)`. The
  machine's server runs them all, a scratch store (`--store`) none; one whose module
  will not import or whose `make` raises is a start-up note and the hub serves without it. The plugin
  refresh is one (docs/plugin-source.md "Hub refresh"). The server adds one job of its own, `refile`
  (`kitlib.hubsite.refile`, every 30 s, made by `serve.build` before the makers' jobs whenever the hub has a
  backlog view), which files bound answers and parks bound asks again (docs/hub-questions.md
  "Filing the answer"). At a job's third failure in a row the worker
  pushes the PM once `[hub push] job <name> failing route:pm attempt:<k>`, and `... recovered` at its
  next success (`job_event`). `usage-crossover`, `coord-restart` and `fill-watch` list processes with a 15 s timeout
  (`coord_restart.PROC_TIMEOUT_S`; the session start keeps 4 s); a listing that still times out is a `Skip`
  (no failure counted, no push; the next run lists again). `usage-crossover` lists before it writes anything;
  `coord-restart`'s boot step (`launch.py after-reboot`, its boot record) runs first and is reported in the Skip note. A job may push a word of its own with `ctx.event(word, detail=None)`;
  a detail is one more header field, `[hub push] job <name> <word> detail:<detail> route:pm
  attempt:<k>`, cut to the header alphabet like every field (a sha or a count, never prose). Each job
  owes at most one push: a newer event replaces one not yet written, its detail with it, except a
  word in `push.JOB_EACH` (`unblocked`, `approval-expired`, `fill`, `stale-lock` and the usage crossover's words), owed once per detail, so two
  records unblocked in one round are two pushes. `unblock-conditions` (`kitlib/hubjobs/unblock_conditions.py`, every 300 s) reads
  each `launcher.repos` repository's store with no fetch and, on each blocked record, each `until`
  line it may act on (docs/backlog.md "block --until"); a met condition clears that one member in one
  store commit under `Session: hub-condition` (`recordwrite.condition_met`, which re-reads the record
  and writes nothing when the line is gone) and pushes `unblocked` with detail `<repo>:<ID>`. An `owner`
  hold keeps one line per ask: a met line of several drops only that line, with no push (the note says
  `line met, still held: <repo>:<ID>`), and the last one clears the member. An `approval:A-n decided`
  line is met once the approval is approved, denied or consumed (`conditions.DECIDED`). One whose
  approval expired undecided stays, and the job pushes `approval-expired`, detail `<repo>:<ID>:<A-n>`,
  once per line while the hub runs (a hub restart pushes it again: a known limit); the push tells the
  PM to withdraw the approval, which drops the line, or to request a new one with `--record`. A
  to-do, question or approval is read from its hub store document on disk, a record from the named
  repository's store, a plugin folder under `pluginsource` `export_dir`. Free text, a member the
  record is not blocked on, an `owner` member on anything but a to-do, a question or an approval
  (`conditions.OWNER_KINDS`), a condition
  holding a machine path, a path that is not on a local drive after its `%VAR%` is filled (on Linux, not absolute or
  starting with `//`; a UNC path, a `..` part; never probed), and a condition that cannot be read are never acted on; a
  record counts as closed only when a commit removed it. Nothing met is a skip, and a store that
  cannot be read or a refused write is a failure once the pass has done the rest; a write whose
  commit landed with the index left stale (`IndexStale`) is an unblock, pushed and counted. A stopped coordinator learns of the
  opened record from `coord-restart`'s backlog wake; nothing else is pushed. That wake reads the offer
  with session liveness as `backlog.py next` does (`coord_restart.session_liveness`, from the run's own
  sessions.json and process listing), so another session's record whose session is not running holds
  back nothing it offers.
  `fill-watch` (`kitlib/hubjobs/fill_watch.py`, every 300 s, docs/hub-fill.md) sends a live coordinator
  with a free slot and startable work one `[hub push] fill: N free, M startable: <ids>` message, once per change,
  that says to pull and dispatch unless the PM holds new work or it is checkpointing or standing down,
  and names the auto-pull stand-down when one holds; what it told is kept in `fill-watch.json`.
  Twenty minutes into an episode with no stand-down the hub pulls up to the free count itself, each record
  on its own new worktree under Session `hub-fill`, and tells the coordinator what it pulled; a stand-down,
  or slots the pull left free, push `fill` to the PM once per episode, detail `<key>:<free>:<why>`.
  A PM hold (`launch.py fill-hold`) or the usage stand-down (every account at its week limit) sends nothing:
  no nudge, no waits notice, no pull, no PM event (docs/hub-fill.md "PM hold and usage").
  `session-watch` (`kitlib/hubjobs/session_watch.py`, docs/pm-watch.md "Hub session-watch job") runs
  pm_watch's checks every 60 s, first at 30 s, and pushes at most once a run: one event as its kind
  (`idle`, `stopped`, `limit`, `usage`, `reset`, `bgfail`, `locknotice`, `remote`, `error`) with its
  subject (session, run or notice id, check) as the detail, `usage` and `reset` with
  `<account>:<window>:<percentage now>` (`2:seven_day:96`), several as `events` with their count. The event lines are free text, so they go in its health `note` (the last 20 pushed, with
  times), never in a push.
  `usage-crossover` (`kitlib/hubjobs/usage_crossover.py`, every 300 s, first at 60 s, before
  `coord-restart`'s first run) moves a session to another account as its account nears the weekly
  limit, the PM included: a fresh PM on the target through `launch.py pm-crossover`, and a
  launched consultant or deep-worker with no sessions.json entry through hand-off files
  (docs/launcher.md "Usage crossover"); `unmanaged`'s detail is `<name>:<reason>`. It is on `notify` until the owner sets
  `launcher.usage_crossover`. Its words are all in `push.JOB_EACH`: `crossing`, `would-cross`,
  `crossed`, `cross-cleared`, `cross-failed`, `stalled`, `no-ready`, `no-checkin`, `no-room`,
  `pm-limit`, `unmanaged`. It reads the hub's own hand-off records through `Sources.handoffs`, which
  `serve.build` sets to the newest of each session.
- Files, in `launcher.state_dir`: `push-outbox.json` (what is still to send, rewritten after each round
  that changed it, so a server ended without a clean close loses at most that round; read again at
  start: an entry whose item changed while the server was down fires afresh, one whose item is gone is
  dropped) and `push-undelivered.jsonl` (one line per try not written, per `ambiguous` pick and per
  give-up, a `watcher down` try as kind `watcher`, id `down`, a job push as kind `job`, id its name;
  never a token or any item text; rotated
  once to `.1` past 1 MiB, `push.LOG_MAX`).
- Status: `GET /api/v1/snapshot` carries `push`, null when the server runs no worker, else
  `PushWorker.status()`: `last` (the state of the latest delivery to the PM, null before the first),
  `state` (`down` until the worker's start has finished, once its thread has ended, or when `last` is not
  `written`; else `up`) and `undelivered` (outbox items some recipient has not yet had a written try of).
  Only the PM's result sets `last`: an asking session that has ended (`no-live-session`, routine) counts
  in `undelivered` but never makes delivery `down`. `last` and `state` change only when a delivery to the
  PM is tried, so with an empty outbox the line shows the last state indefinitely, and an item given up
  appears only in `push-undelivered.jsonl`. A retry's outcome shows at the next change or wait timeout,
  not at once. The snapshot and `GET /api/v1/health` also carry `watcher`: null with no worker, else
  `PushWorker.watcher_status()`, `{"state": "up"|"down"|"none", "age": <seconds or null>}` at the
  worker's latest read (a status that raises is `down` with a null age); the tunnel's health leaves it
  out (docs/hub-serving.md "Serving it"). GET health (local) also carries `jobs`: null with no job, else `{<name>: {every_s,
  runs, failures, running, last_at, ok_at, note, error}}`, which `hub.py status` prints as its `jobs:`
  line. `GET /api/v1/health` also carries `push`, the snapshot's value from
  `Api.push_seen`: null with no worker, else `{"state", "last", "undelivered"}`.
- Stopping: `Hub.close` tells the worker to begin no further send before the store closes and joins it
  after, for at least `push.STOP_S` (15 s), longer than a host's delivery can take (Claude:
  `peer.WRITE_TIMEOUT_S` plus the helper's reap and pump joins), so the last outbox write is not cut off.
- Known limits:
  - An answer made in the moments before the server closes that the worker had not yet seen is not pushed
    after a restart: the outbox holds only what the worker saw. The PM's `pending --for pm` at its next
    start still shows it.
  - An approval's 20-hour reminder (`expiring`) is not sent when the server is down across the 20-hour
    mark after its `decided` push was delivered. The approval stays pending for its coordinator and the
    PM until it is consumed or expires.
  - A filed answer's live check is read once, when the filing is seen: an asker that ends after it is
    still tried (and reads it at its next start), and a coordinator started after it is not told.
  - `session-watch` drops the events it saw while pm_watch's heartbeat was fresh and not stepped aside
    (`yielded`), so those of the last 300 s before a pm_watch stopped are not pushed, and one at the
    hand-over, before the job reads the `yielded` heartbeat, can reach neither; it does not see a pm_watch run with
    its own `--state`, so both push then, unless pm_watch's sessions file, alerts and notice
    folders are the job's and it steps aside; with no push worker its events are not pushed. While it
    covers, a stopped pm_watch's BRANCH and HUB lines reach no one, and no `watcher down` push says so
    (docs/pm-watch.md "Hub session-watch job").

Tests: tests/hubsite/test_push.py, tests/host/test_claude_peer.py, tests/launcher/test_pipes.py.

### Store upkeep

Kit git calls through `kitlib.git.cmd` run with `maintenance.auto=false` and `gc.auto=0`
(`AUTO_MAINTENANCE_OFF`). Git for Windows runs the auto maintenance a commit or fetch starts in the
foreground, so it ran under the call's 30 s timeout, and a gc killed there left
`objects/maintenance.lock` behind. With that lock present git skips maintenance without a word,
and loose objects pile up. So the hub packs the store itself. The `store-upkeep` job
(`kitlib/hubjobs/store_upkeep.py`) runs every 24 h, first 15 min after the hub starts, on the git
common dir of the PM data worktree only:

1. It writes the commit graph with Bloom filters (`commit-graph write --reachable --changed-paths
   --split`), with no timeout.
2. It runs `gc` only when `count-objects -v` counts 6700 loose objects or more. That is git's own
   `gc.auto` default, not measured for this store. The command is `-c gc.writeCommitGraph=false -c
   gc.packRefs=false -c pack.threads=2 gc --quiet`, with no timeout. It runs in the job of a
   machine-lock slot (kind `build`, 2 cores, slug `store-upkeep`) that holds that common dir's
   landing resource, so no landing runs beside it: the gc ends with the slot, also when the hub
   ends. When the lock gives no slot within 10 min, or an owner or game hold is in place, the gc
   waits for the next pass.

The job deletes nothing itself. A `.pack` with no `.idx` that is more than a day old is a finding:
each pass names it in its health note. The pass pushes `findings` with their count as the detail
only when the count differs from the one last pushed, or when that push is a day old (`REPUSH_S`).
The last pushed state is kept in `store-upkeep-pushed.json` in the git common dir, so a hub restart
does not push it again, and an event the pass no longer finds is forgotten, so it pushes again when
it returns. The pushes are made at the end of the pass, after the commit-graph and gc steps, also
when a step failed or the pass was stopped.

A `maintenance.lock` more than 12 h old is the sign the fault came back. When no git process that
could hold it is alive (a git whose command line names `maintenance` or `gc`, or one whose command
line cannot be read and which is still listed a moment later), each pass names it in its note and
pushes `[hub push] job store-upkeep stale-lock detail:<hours>h` to the PM, once and then a day later
while the lock stays (the age changes every hour and is no change; the same state file holds it). It is never removed by the hub: moving it aside is a planned window's work. With such a git
alive the pass only names it in its note. When the processes cannot be listed, it still pushes.

A failed git step or another lock refusal is a job failure; the other step still runs.

