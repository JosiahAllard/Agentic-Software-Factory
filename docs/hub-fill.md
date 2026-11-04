# Hub fill watch

The Action Hub's `fill-watch` timed job (`kitlib/hubjobs/fill_watch.py`; docs/action-hub.md "Push
notifications", the Timed jobs item). It tells a live coordinator when it has a free slot and
`next` has startable work. Coordinators under-fill their slots, and their hourly fill-check cron
is the only other backstop. The owner's design has five stages:

| Stage | What | State |
| --- | --- | --- |
| 1 | The watch and the nudge | built |
| 2 | What was told survives a hub restart (`fill-watch.json`) | built |
| 3 | Auto-pull: the hub pulls into a free slot the coordinator leaves empty | coming |
| 4 | Stand-down: limits that stop the auto-pull, named in the nudge | built |
| 5 | The PM fill event: the PM hears when a slot stays empty | coming |

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
  `requires:` are already passed over.
- **Free** = cap - in flight, never below 0.

A coordinator the usage crossover is moving (`usage_crossover.claims`, the hold `coord-restart`
reads) is passed over with a note: it was told to checkpoint and stand down.

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
to holds the hub cannot see (a PM hold on new work, a hand-off).

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
- `since`: epoch seconds, when the episode began: the first nudge written, reset to now when the
  coordinator's in-flight count rises above the last one read (it pulled). Stage 3's timer.
- `in_flight`: the in-flight count last read.

A key is dropped when its coordinator is forgotten (above); a store that cannot be read keeps its
entry as it was. The file is written only when it changes, as a temp file then a replace.

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
a verdict queue that holds a bad file, and worktrees `--classify` cannot read.

A stand-down never stops the nudge: the coordinator still hears of its free slot, with the reason.

## Coming

Stages 3 and 5 are designed, not built (the rest of KIT-HUB-FILL-WATCH). They wait on
KIT-OFFER-DEAD-SESSION, so that the offer and the pull judge session liveness the same way.

- **Auto-pull (stage 3)**: 20 minutes after `since` with no stand-down, the hub pulls records
  itself, up to the free count, in offer order, once per episode.
  - Through the backlog library, never the shell (`pull_branch.plan_pull` with no reason and no
    collision allowance). So it never pulls past a collision, a hold or another session's record.
  - For each record it first makes the worktree (`kitlib.worktree.new.create`, slug
    `fill-<id in lower case>`, its branch and folder as that function names them, default host).
    A pull the store refuses removes the empty worktree it made, and the next record is tried.
  - It skips a coordinator with a pending hub hand-off (a stop, restart or hand-off not yet
    followed by a check-in): the nudge still goes, with the reason named.
  - One push to the coordinator lists what was pulled and what was skipped, with codes.
- **PM fill event (stage 5)**: a hub job push to the PM (`JOB_EACH['fill']`, one per repository),
  once per episode. It goes when the 20 minutes pass while stood down, or when the auto-pull leaves
  slots free.

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
  `unless` clause leaves it to the coordinator. Once built, the auto-pull would pull past such a
  hold: until there is one, the owner sets `backlog.fill_queued_max` 0 to stop it.
- The stand-down survey runs `--classify` (git reads and a process scan) at each run that has a
  coordinator to nudge, at most 12 times an hour per repository.
- No retry backoff: a deliver not written is tried every run, at most 12 times an hour.
- An offer whose ids only change order is a change and is pushed again. So is a stand-down count
  that moves (`queued 3>=3` to `queued 4>=3`).
