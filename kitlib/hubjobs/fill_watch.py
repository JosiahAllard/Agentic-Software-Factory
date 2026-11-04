"""kitlib.hubjobs.fill_watch: the hub's fill watch, stage 1: a live coordinator with a free slot
and startable work is nudged, once per change (docs/hub-fill.md).

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository or
                                     launcher.state_dir is not set
  FillWatch(sessions, live, read, deliver, claims=None)
                                     its run(ctx): one pass over the live coordinators
  read_slots(settings, key, entry)   (cap, in_flight, offered ids in offer order) of one
                                     coordinator; coord_restart.BacklogUnread when it cannot be read
  nudge_text(key, free, ids)         the push

Every EVERY_S the job reads sessions.json and the process listing (coord_restart's: a listing
that times out is a Skip). A coordinator is an entry with a truthy `agent` whose `name` is a live
session (an inference: a string `agent` matches too). For each one, read_slots gives, through
coord_restart.read_offer (the reader coord-restart's wake shares):
  cap        `.kit.json` backlog.cap of the repo's main checkout (opener.repo_cap). Inference:
             this is the cap the coordinator role calls "your cap"; no other key holds one.
  in_flight  the coordinator's live pulls: the store's pulled records with a branch
             (offer.in_flight_split's holding) whose `session:` is one of
             coord_restart.wake_sessions (no `session:` counts for the repo's main coordinator).
             Inference: one live pull is about one slot. The hub cannot see a host's running
             agents, so a pull waiting to land counts as busy and a reviewer is not seen.
  offered    `next --all` for the same sessions (plan.offer_for, cap None, no fetch), so every
             collision, hold, other session's record and unmet `requires:` is already passed over.
free = cap - in_flight (at least 0). Free and something offered: one deliver to the coordinator's
sessions.json key, `[hub push] fill: N free, M startable: <ids> route:<key>` and one line of what
to do; record ids and counts only, never free text.

One push per change: the job keeps, in memory, the (free, ids) it last told each key. It pushes
when they differ; a key with no free slot, nothing offered, or no live session is forgotten, so
the state coming back nudges again. A deliver that is written or uncertain counts as told; one
not written, with no live session, with no push object, or that raises is tried again next run.
A hub restart forgets what was told and nudges once more (a known limit).

A coordinator the usage crossover is moving (usage_crossover.claims, the hold coord-restart reads)
is never nudged: it was told to checkpoint and stand down.

Outcomes (kitlib.hubsite.jobs): a nudge is a success whose note names the keys; nothing to nudge
is a Skip. A store or cap that cannot be read is a note. A sessions.json or crossover state that
cannot be read (LaunchError), or a process listing that times out or fails (RuntimeError, OSError),
is a Skip with a note: never a failure, so the runner never pushes `failing` to the PM for them.
Nothing goes to the PM here.
"""
import subprocess
import time

from kitlib.hubjobs import coord_restart
from kitlib.hubsite import jobs

NAME = 'fill-watch'
EVERY_S = 300
FIRST_S = 120                   # after the hub starts, as coord-restart
NOTE_MAX = 300
DO_LINE = ('A hub fill watch saw a free slot and startable records: run `backlog.py next`, pull and '
           'dispatch to fill your slots (coordinator role, Slots). No reply is needed.')


def is_coordinator(key, entry):
    return (not key.startswith('_') and isinstance(entry, dict) and bool(entry.get('agent'))
            and bool(entry.get('name')))


def nudge_text(key, free, ids):
    from kitlib.hubsite import push
    shown = coord_restart.shown_ids([push.field(i) for i in ids])
    return ('[hub push] fill: %d free, %d startable: %s route:%s\n%s'
            % (free, len(ids), shown, push.field(key), DO_LINE))


def read_slots(settings, key, entry):
    """(cap, in_flight, ids) of the coordinator of sessions.json entry `key` (module docstring),
    read through coord_restart.read_offer: BacklogUnread when it cannot be read."""
    ids, holding, cap = coord_restart.read_offer(settings, key, entry, with_cap=True)
    sessions = coord_restart.wake_sessions(key, entry)
    return cap, sum(1 for r in holding if r.get('session') in sessions), ids


def live_names():
    """The names of the live sessions (coord_restart's listing); TimeoutExpired passes up."""
    from kitlib.launcher import lookup
    names = {lookup.rc_name(p.get('CommandLine')) for p in lookup.launched(coord_restart.list_processes())}
    names.discard(None)
    return names


def told_states():
    """The kitlib.hubsite.push Sent states that count as told: written and uncertain (push is
    imported here, at run time, as usage_crossover.make does)."""
    from kitlib.hubsite import push
    return (push.WRITTEN, push.UNCERTAIN)


class FillWatch(object):
    """run(ctx) is the job. `sessions()` the sessions.json dict, `live()` the live session names,
    `read(key, entry)` (cap, in_flight, ids) or BacklogUnread, `deliver(key, text)` a Sent state
    (None: no push object), `claims()` the sessions.json keys the usage crossover is moving
    (usage_crossover.claims; default none)."""

    def __init__(self, sessions, live, read, deliver, claims=None):
        self.sessions, self.live, self.read, self.deliver = sessions, live, read, deliver
        self.claims = claims or (lambda: set())
        self.told = {}                  # key -> (free, ids) last told, in memory only

    def __call__(self, ctx):
        from kitlib.launcher.settings import LaunchError
        try:
            sessions = self.sessions()
        except LaunchError as e:
            return jobs.Skip(('sessions.json cannot be read: %s' % e)[:NOTE_MAX])
        try:
            claimed = self.claims()
        except LaunchError as e:
            return jobs.Skip(('the usage crossover state cannot be read: %s' % e)[:NOTE_MAX])
        try:
            live = self.live()
        except subprocess.TimeoutExpired as e:
            return coord_restart.skip_timed_out(e)
        except (RuntimeError, OSError) as e:
            return jobs.Skip(('process listing failed (%s): skipped, tried again next run'
                              % type(e).__name__)[:NOTE_MAX])
        told_ok = told_states()
        nudged, notes, seen = [], [], set()
        for key in sorted(sessions):
            entry = sessions[key]
            if ctx.stop.is_set():
                break
            if not is_coordinator(key, entry) or entry['name'] not in live:
                continue
            if key in claimed:          # the usage crossover is moving it: no nudge to dispatch
                notes.append('%s: being moved by the usage crossover' % key)
                continue
            try:
                cap, in_flight, ids = self.read(key, entry)
            except coord_restart.BacklogUnread as e:
                notes.append('%s skipped: %s' % (key, e))
                seen.add(key)           # unread: what was told stands
                continue
            free = max(0, cap - in_flight)
            if not free or not ids:
                continue
            seen.add(key)
            state = (free, tuple(ids))
            if self.told.get(key) == state:
                continue
            try:
                sent = self.deliver(key, nudge_text(key, free, ids))
            except Exception as e:      # noqa: BLE001 - a host fault: tried again next run
                notes.append('%s: nudge not sent (%s)' % (key, type(e).__name__))
                continue
            if sent in told_ok:
                self.told[key] = state
                nudged.append('%s (%d free, %d startable)' % (key, free, len(ids)))
            else:
                notes.append('%s: nudge %s, tried again next run' % (key, sent or 'not sent'))
        for key in list(self.told):
            if key not in seen:
                del self.told[key]
        note = '; '.join((['nudged %s' % ', '.join(nudged)] if nudged else []) + notes)[:NOTE_MAX]
        if not nudged:
            return jobs.Skip(note or 'no coordinator has a free slot and startable work')
        return note


def make(sources):
    from kitlib.launcher import settings as lsettings
    from kitlib.launcher import state
    from kitlib.launcher.settings import LaunchError
    s = lsettings.load(getattr(sources, 'home', None))
    if not s.repos:
        return None
    try:
        s.state_dir()
    except LaunchError:
        return None

    def deliver(key, text):             # sources.push is read at each run
        if getattr(sources, 'push', None) is None:
            return None
        return sources.push.deliver(key, text).state

    def claims():
        from kitlib.hubjobs import usage_crossover      # it imports coord_restart
        return usage_crossover.claims(s, time.time())

    job = FillWatch(lambda: state.read_sessions(s), live_names,
                    lambda key, entry: read_slots(s, key, entry), deliver, claims)
    return jobs.Job(NAME, EVERY_S, job, first_s=FIRST_S)
