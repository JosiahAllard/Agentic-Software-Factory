"""kitlib.hubjobs.fill_watch: the hub's fill watch, stage 1: a live coordinator with a free slot
and startable work is nudged, once per change (docs/hub-fill.md).

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository or
                                     launcher.state_dir is not set
  FillWatch(sessions, live, read, deliver)
                                     its run(ctx): one pass over the live coordinators
  read_slots(settings, key, entry)   (cap, in_flight, offered ids in offer order) of one
                                     coordinator; coord_restart.BacklogUnread when it cannot be read
  nudge_text(key, free, ids)         the push

Every EVERY_S the job reads sessions.json and the process listing (coord_restart's: a listing
that times out is a Skip). A coordinator is an entry with a truthy `agent` whose `name` is a live
session. For each one, read_slots gives:
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

Outcomes (kitlib.hubsite.jobs): a nudge is a success whose note names the keys; nothing to nudge
is a Skip. A store that cannot be read is a note, never a failure. Nothing goes to the PM here.
"""
import subprocess

from kitlib.hubjobs import coord_restart
from kitlib.hubsite import jobs

NAME = 'fill-watch'
EVERY_S = 300
FIRST_S = 120                   # after the hub starts, as coord-restart
NOTE_MAX = 300
# kitlib.hubsite.push's Sent states (imported there only at run time: push is heavy)
WRITTEN, NOT_WRITTEN, UNCERTAIN, NO_SESSION = 'written', 'not-written', 'uncertain', 'no-live-session'
TOLD = (WRITTEN, UNCERTAIN)
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
    """(cap, in_flight, ids) of the coordinator of sessions.json entry `key` (module docstring)."""
    from kitlib.backlog import opener, plan
    from kitlib.backlog import offer as omod
    from kitlib.backlog.errors import Refusal
    from kitlib.config import ConfigError
    from kitlib.launcher.settings import LaunchError
    repo = (entry or {}).get('repo')
    root = coord_restart.store_root(settings, repo)
    sessions = coord_restart.wake_sessions(key, entry)
    ids, snap, holding = [], None, None
    try:
        cap = opener.repo_cap(settings.repo_path(repo))
        for session in sessions:
            ctx = opener.open_store(root, fetch=False, session=session)
            if snap is None:            # one read of the store and its live pulls for both offers
                snap = ctx.store.load()
                holding, _dead, _why = omod.in_flight_split(snap, ctx.root, ctx.remote)
            offers, _passed, _holding, _own = plan.offer_for(snap, ctx, cap=None, holding=holding)
            ids.extend(o.rec.id for o in offers if o.rec.id not in ids)
    except (Refusal, ConfigError, LaunchError, omod.PartitionError, OSError) as exc:
        raise coord_restart.BacklogUnread('the store could not be read (%s)' % type(exc).__name__)
    in_flight = sum(1 for r in holding or () if r.get('session') in sessions)
    return cap, in_flight, ids


def live_names():
    """The names of the live sessions (coord_restart's listing); TimeoutExpired passes up."""
    from kitlib.launcher import lookup
    names = {lookup.rc_name(p.get('CommandLine')) for p in lookup.launched(coord_restart.list_processes())}
    names.discard(None)
    return names


class FillWatch(object):
    """run(ctx) is the job. `sessions()` the sessions.json dict, `live()` the live session names,
    `read(key, entry)` (cap, in_flight, ids) or BacklogUnread, `deliver(key, text)` a Sent state
    (None: no push object)."""

    def __init__(self, sessions, live, read, deliver):
        self.sessions, self.live, self.read, self.deliver = sessions, live, read, deliver
        self.told = {}                  # key -> (free, ids) last told, in memory only

    def __call__(self, ctx):
        sessions = self.sessions()
        try:
            live = self.live()
        except subprocess.TimeoutExpired as e:
            return coord_restart.skip_timed_out(e)
        nudged, notes, seen = [], [], set()
        for key in sorted(sessions):
            entry = sessions[key]
            if ctx.stop.is_set():
                break
            if not is_coordinator(key, entry) or entry['name'] not in live:
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
            if sent in TOLD:
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

    job = FillWatch(lambda: state.read_sessions(s), live_names,
                    lambda key, entry: read_slots(s, key, entry), deliver)
    return jobs.Job(NAME, EVERY_S, job, first_s=FIRST_S)
