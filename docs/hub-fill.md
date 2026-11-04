# Hub fill watch

The Action Hub's `fill-watch` timed job (`kitlib/hubjobs/fill_watch.py`; docs/action-hub.md "Push
notifications", the Timed jobs item). It tells a live coordinator when it has a free slot and
`next` has startable work. Coordinators under-fill their slots, and their hourly fill-check cron
is the only other backstop. The owner's design comes in three stages; only stage 1, the watch and
the nudge, is built.

## The watch

Every 300 s (first run 120 s after the hub starts), for each coordinator in sessions.json (an
entry with a truthy `agent`) whose `name` is a live session (the process listing `coord-restart`
uses; a listing that times out is a skip):

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

A store that cannot be read is a note in the job's health, never a failure.

## The nudge

A coordinator with a free slot and at least one startable record is sent one message, through
the hub's delivery object to its sessions.json key:

    [hub push] fill: 2 free, 3 startable: EX-A, EX-B, EX-C route:<key>
    A hub fill watch saw a free slot and startable records: run `backlog.py next`, pull and dispatch ...

At most 5 ids are shown, then `+N more`. It carries record ids and counts only, never free text.
Nothing goes to the PM.

One push per change: the job remembers the (free, startable ids) it last told each coordinator,
and pushes again only when they differ. A coordinator with no free slot, nothing startable, or
no live session is forgotten, so the same state coming back is nudged again. A deliver written or
uncertain counts as told; one not written, with no live session, or that fails is tried at the
next run.

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
- What was told is kept in memory: a hub restart nudges each coordinator once more.

## Not built yet

Stages 2 and 3 of the design (backlog record KIT-HUB-FILL-WATCH):

- **Auto-pull**: 20 minutes after a nudge with the slot still empty, the hub pulls up to the free
  count itself.
- **Stand-down**: auto-pull stops while either limit holds. Both limits are `.kit.json` keys, not
  in the schema yet:
  - 3 or more branches queued but not landed;
  - 2 or more worktrees idle for 2 hours.
- **PM fill event**: raised when a slot stays empty.
