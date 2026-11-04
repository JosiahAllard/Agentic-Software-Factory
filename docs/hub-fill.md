# Hub fill watch

The Action Hub's `fill-watch` timed job (`kitlib/hubjobs/fill_watch.py`; docs/action-hub.md "Push
notifications", the Timed jobs item). It tells a live coordinator when it has a free slot and
`next` has startable work. Coordinators under-fill their slots, and their hourly fill-check cron
is the only other backstop. The owner's design has five stages:

| Stage | What | State |
| --- | --- | --- |
| 1 | The watch and the nudge | built |
| 2 | What was told survives a hub restart (`fill-watch.json`) | built |
| 3 | Auto-pull: the hub pulls into a free slot the coordinator leaves empty | built |
| 4 | Stand-down: limits that stop the auto-pull, named in the nudge | built |
| 5 | The PM fill event: the PM hears when a slot stays empty | built |

## The watch

Every 300 s (first run 120 s after the hub starts), for each coordinator in sessions.json (an
entry with a truthy `agent`; inference: a string `agent` matches too) whose `name` is a live
session (the process listing `coord-restart` uses), read through `coord_restart.read_offer`, the
reader `coord-restart`'s backlog wake shares:

- **Cap**: `.kit.json` `backlog.cap` of the repository's main checkout (`opener.repo_cap`, default
  5). Inference: this is the cap the coordinator role calls "your cap"; no other key holds one.
- **In flight**: the coordinator's live pulls. These are the store's pulled records that still
  have a branch (`offer.in_flight_split`) and whose `session:` is the coordinator's
  sessions.json key. A record with no `session:` counts for the repository's main coordinator,
  the entry whose key is the repository's name. Inference: one live pull is about one slot
  (Known limits).
- **Startable**: the offer `backlog.py next --all` gives the same sessions (`plan.offer_for`, no
  cap, no fetch), in offer order. A collision, a hold, another session's record and an unmet
  `requires:` are already passed over. It judges session liveness as `next` does: another
  session's record whose session is not running reserves no scope, so it holds back nothing of
  this coordinator's. The run builds that liveness from its own sessions.json and process listing
  (`coord_restart.session_liveness`).
- **Free** = cap - in flight, never below 0.

A coordinator the usage crossover is moving (`usage_crossover.claims`, the hold `coord-restart`
reads) is passed over with a note: it was told to checkpoint and stand down. What was told it
stays as it was while it is claimed ("Saved state").

A store or a cap that cannot be read is a note in the job's health, never a failure. A run is a
skip with a note, never a failure, when sessions.json or the usage crossover's state cannot be
read (a LaunchError) or the process listing times out or fails (a RuntimeError or an OSError).
So none of them, however long it lasts, makes the hub push `job fill-watch failing` to the PM.

## The nudge

A coordinator with a free slot and at least one startable record is sent one message, through
the hub's delivery object to its sessions.json key:

    [hub push] fill: 2 free, 3 startable: EX-A, EX-B, EX-C route:<key>
    auto-pull stood down: queued 3>=3
    A hub fill watch saw a free slot and startable records: run `backlog.py next`, pull and dispatch
    to fill your slots (coordinator role, Slots), unless the PM holds new work or you are
    checkpointing or standing down. No reply is needed.

The second line is there only while a stand-down holds ("Stand-down"). The `unless` clause defers
to holds the hub cannot see (a PM hold on new work, a checkpoint the hub was not told of).

At most 5 ids are shown, then `+N more`. It carries record ids, counts and the kit's own
stand-down codes, never free text. Nothing goes to the PM.

One push per change: the job keeps what it last told each coordinator (free, startable ids and
stand-down reason) and pushes again only when one of them differs. A coordinator with no free
slot, nothing startable, or no live session is forgotten, so the same state coming back is nudged
again. A deliver written or uncertain counts as told; one not written, with no live session, or
that fails is tried at the next run.

## Saved state

What was told is kept in `fill-watch.json` in the launcher's state folder (`launcher.state_dir`,
beside `coord-restart.json`), so a hub restart does not nudge again. One entry per sessions.json
key:

- `told`: `[free, [ids], reason or null]`, the last nudge written.
- `since`: epoch seconds, when the episode began. It is the first nudge written, reset to now:
  - when the coordinator's in-flight count rises above the last one read (it pulled);
  - when a nudge is written whose free count is above the one told (a slot freed), so stage 3's
    20 minutes run from the nudge that names the slot. A nudge for new ids alone keeps it.
- `in_flight`: the in-flight count last read.
- `pulled`, once set: the `since` of the episode the hub auto-pulled for ("Auto-pull").
- `pm`, once set: the `since` of the episode the PM was told of ("The PM fill event").

A key is dropped when its coordinator is forgotten (above). Its entry stays as it was while its
store cannot be read, while the usage crossover claims it, and when a hub stop ends the run before
the job reaches it. The file is written only when it changes, as a temp file then a replace.

A file that cannot be read, or whose entries are not that shape, fails the run before anything is
sent or written: the runner pushes `job fill-watch failing` to the PM at the third failure. It is
never read as empty, so a bad file cannot nudge every coordinator again or restart `since`. To
recover, fix the entry the failure names or remove the file: every live coordinator is then nudged
once more.

## Stand-down

Read once per repository per run, and only for a coordinator that has a free slot and startable
work (`standdown()`). It reads the main checkout's `.kit.json` (docs/kit-json.md):

- `backlog.fill_queued_max` (default 3): stood down, `queued N>=MAX`, while N branches wait to
  land. Each branch is counted once, whether it is:
  - in the verdict queue and not expired (`kitlib.gate.verdicts`: landed or its branch gone);
  - or on a worktree, live or not, whose tip has a green gate-OK record (`classify.gate_state`)
    and is not in main as fetched. A tip git cannot place against main counts.
- `backlog.fill_idle_max` (default 2): stood down, `idle N>=MAX`, while N worktrees are class B
  (finished) or C (half-done) under `worktree_remove.py --classify` with a 2 h dead bar (fixed, not
  a key). L (live or kept) trees never count.

`backlog.fill_queued_max` 0 turns auto-pull off: the reason is `off` and nothing is surveyed. It is
the off switch; there is no other. `backlog.fill_idle_max` 0 also stands it down always, but runs
the survey.

A stand-down that cannot be read fails closed: the reason is `unread` (stood down) and the job's
note names why. That covers a `.kit.json` the schema refuses, main as fetched, the git common dir,
a verdict queue that holds a bad file, and worktrees `--classify` cannot read. It also covers a
`--classify` run that could not make one of its reads: a run-wide one (the process scan, the
machine lock or the landing lock: every tree is then L) or one tree's (an L tree whose reason is
`unreadable: ...`, such as its status). Either would hide an idle tree and read the stand-down
open. One case is not `unread`: an unreadable L tree idle 2 h or more (only a process the scan
could not read might hold it) counts idle. When its head is already in main as fetched it counts
neither idle nor unread: it is a landed tree awaiting removal.

With no repository stand-down, a coordinator with a pending hub hand-off is stood down as
`handoff <kind>`. Pending means its newest hand-off record is a stop, restart or hand-off, not a
check-in, and the live session sessions.json names did not start after it. A session started
after the record (process creation time against the record's `at`) is a new one: the hand-off is
over, whether or not it wrote a check-in (a `launch.py start` or a `coord-restart` wake writes
none). A record whose `at`, or a session whose start, cannot be read stays pending. Records the
hub cannot read (or a hub that gives the job no read) stand it down as `handoff unread`.

A stand-down never stops the nudge: the coordinator still hears of its free slot, with the reason.

## Auto-pull

For a coordinator already nudged this episode, 20 minutes after `since` with no stand-down, the
hub pulls records itself, up to the free count, in offer order, once per episode. `pulled` is
written to `fill-watch.json` before the first pull, so a hub that dies mid-pull never pulls that
episode again.

- Each record gets its own worktree first (`kitlib.worktree.new.create`, without waiting on a
  landing in flight), in the repository's worktrees folder. Its branch is
  `<sessions.json key in lower case>/fill-<id in lower case>` (`kit/fill-foo` for key `kit`).
- The record is then pulled onto that branch through the backlog library, never the shell
  (`pull_branch.plan_pull`, with no reason and no collision allowance), store fetched first. The
  store commit's `Session:` is `hub-fill`. The offer it checks is the one for the record's own
  `session:` (`plan.Context.offer_session`), with the same session liveness as the watch. So it
  never pulls past a collision, a hold or another session's record. A plain pull is a LOW row: it
  needs no audit line, and the audit check passes it.
- A record not pulled is skipped with a code, and its tree and branch are removed (`git worktree
  remove`, never forced, and the branch deleted only while its tip is still the tree's head). The
  next record is then tried. Codes: `gone` (no such record), `held` (owner-held), `not-open`,
  `not-theirs` (its session is not this coordinator's), `refused` (the planner's refusal: not in
  the offer, a collision, a hold), `store` (the store cannot be read, or the write surely did not
  land: busy, a dirty data worktree, overtaken; or the store is the implicit one), `unsure` (the
  write may have landed), `branch` (an invalid branch name), `worktree` (the tree was not made). A
  code ends in `+tree-kept` when the tree was not removed: it could not be, or the code is
  `unsure`, whose record may now be pulled onto that branch (check the store before removing it).
- A `not-open` record of this coordinator's sessions was pulled (or closed) since the offer was
  read, most likely by the coordinator itself: it counts as a slot taken, so the hub never pulls
  past the coordinator's cap when both pull at once.
- One push to the coordinator lists what was pulled (id and branch) and what was skipped (id and
  code), and asks it to dispatch them.

A pull that raises is a note in the job's health; the episode's one try is spent.

## The PM fill event

One hub job push to the PM (`JOB_EACH['fill']`), once per episode per coordinator key, with detail
`<key>:<free slots>:<why>`. It goes 20 minutes into an episode when:

- a stand-down holds: `<why>` is the stand-down's code (`off`, `queued`, `idle`, `unread` or
  `handoff`);
- or the auto-pull leaves slots free: `<why>` is `unfilled` and the count is the slots left.

`pm` in `fill-watch.json` marks it sent for the episode.

## Known limits

- Inference: the cap is `backlog.cap`. A coordinator that runs fewer or more agents than that
  key is judged against the key.
- Inference: a live pull stands for an agent in flight. The hub cannot see a host's running
  agents, so:
  - a pull waiting to land counts as busy, though the role says it holds no slot. The watch
    over-counts busy slots and can stay quiet when a slot is free; it does not nudge when every
    slot is busy.
  - reviewers, test runners and other agents with no pulled record of their own are not seen.
    While they run, the watch can count a slot free that the coordinator is using.
- A PM hold on new work has no machine-readable form, so the hub cannot see it. The nudge's
  `unless` clause leaves it to the coordinator. The auto-pull pulls past such a hold: until
  there is one, the owner sets `backlog.fill_queued_max` 0 to stop it.
- The auto-pull runs only the checks named in "Auto-pull": the store's own refusals and the
  hub's `held`. It writes no coordinator log note.
- The auto-pull's branch area is the sessions.json key in lower case, not a configured prefix
  (none exists; `new_worktree.py` takes the area from its caller).
- The stand-down survey runs `--classify` (git reads and a process scan) at each run that has a
  coordinator to nudge, at most 12 times an hour per repository.
- No retry backoff: a deliver not written is tried every run, at most 12 times an hour.
- An offer whose ids only change order is a change and is pushed again. So is a stand-down count
  that moves (`queued 3>=3` to `queued 4>=3`).
