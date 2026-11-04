"""kitlib.hubjobs.fill_watch: the hub's fill watch (docs/hub-fill.md): a live coordinator with a
free slot and startable work is nudged, once per change (stage 1); what was told is kept in
fill-watch.json, so a hub restart does not nudge again (stage 2); the nudge names the auto-pull
stand-down when one holds (stage 4). Auto-pull (stage 3) and the PM fill event (stage 5) are not
built.

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository or
                                     launcher.state_dir is not set
  FillWatch(sessions, live, read, deliver, claims=None, standdown=None, store=None, now=time.time)
                                     its run(ctx): one pass over the live coordinators
  read_slots(settings, key, entry)   (cap, in_flight, offered ids in offer order) of one
                                     coordinator; coord_restart.BacklogUnread when it cannot be read
  standdown(top, survey=None)        the stand-down reason of the repository whose main checkout is
                                     `top`, or None; StandDownUnread when it cannot be read
  read_limits(top, cfg=None)         (backlog.fill_queued_max, backlog.fill_idle_max) of `top`
  read_survey(top, cfg)              (queued, idle) of `top`; StandDownUnread when unread
  nudge_text(key, free, ids, reason=None)
                                     the push
  FileState(path), MemoryState()     the told state's store: read() -> dict, write(dict)

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
sessions.json key, `[hub push] fill: N free, M startable: <ids> route:<key>`, then
`auto-pull stood down: <reason>` when a stand-down holds, and one line of what to do; record ids,
counts and the kit's own reason codes only, never free text.

Stand-down (stage 4): read once per repository per run, and only for a coordinator that is free
with something offered. `standdown(top)` reads the main checkout's `.kit.json`:
  backlog.fill_queued_max (default 3): 0 is `off` (always stood down, nothing surveyed); else
             `queued N>=MAX` when N branches wait to land: the verdict queue's entries not expired
             (kitlib.gate.verdicts), and every worktree branch, live or not, whose tip has a green
             gate-OK record (classify.gate_state) and is not in main as fetched; each branch once.
             A tip git cannot place against main counts.
  backlog.fill_idle_max (default 2): `idle N>=MAX` when N worktrees are class B or C under
             `--classify` with a dead bar of IDLE_AFTER_S (2 h, fixed); L trees never count. 0 is
             always stood down.
A stand-down that cannot be read is `unread` (fails closed: stood down) and a note names why;
that includes a `--classify` run that reports a read it could not make (its `unreadable`, or an L
tree whose reason is `unreadable: ...`), since such a tree would hide an idle one.

One push per change: the job keeps, in fill-watch.json under launcher.state_dir (FileState), per
key: `told` [free, ids, reason] last told, `since` (epoch seconds: the first nudge of the
episode, reset to now when in_flight rises above the last read, and when a nudge is written whose
free count is above the one told: the nudge that names a newly freed slot) and `in_flight` (the
last read). It pushes when (free, ids, reason) differ from `told`; a key with no free slot,
nothing offered, or no live session is dropped, so the state coming back nudges again. A key the
usage crossover claims, a key whose store cannot be read, and the keys a hub stop leaves unreached
keep their state untouched. A deliver that is written or
uncertain counts as told; one not written, with no live session, with no push object, or that
raises is tried again next run. The file is written only when it changes. A file that cannot be
read or does not hold that shape raises LaunchError before anything is sent or written: a failed
run, which the runner pushes to the PM as `failing` at the third. It is never read as empty, so a
bad file cannot nudge every coordinator again or restart `since` (the auto-pull's timer).

A coordinator the usage crossover is moving (usage_crossover.claims, the hold coord-restart reads)
is never nudged, and what was told it stays as it was: it was told to checkpoint and stand down.

Outcomes (kitlib.hubsite.jobs): a nudge is a success whose note names the keys; nothing to nudge
is a Skip. A store or cap that cannot be read is a note. A sessions.json or crossover state that
cannot be read (LaunchError), or a process listing that times out or fails (RuntimeError, OSError),
is a Skip with a note: never a failure, so the runner never pushes `failing` to the PM for them.
Nothing else goes to the PM here.
"""
import numbers
import subprocess
import time

from kitlib.hubjobs import coord_restart
from kitlib.hubsite import jobs

NAME = 'fill-watch'
EVERY_S = 300
FIRST_S = 120                   # after the hub starts, as coord-restart
NOTE_MAX = 300
STATE_FILE = 'fill-watch.json'
IDLE_AFTER_S = 7200             # a worktree idle this long counts toward backlog.fill_idle_max
UNREAD = 'unread'
OFF = 'off'
IDLE_CLASSES = ('B', 'C')
DO_LINE = ('A hub fill watch saw a free slot and startable records: run `backlog.py next`, pull and '
           'dispatch to fill your slots (coordinator role, Slots), unless the PM holds new work or '
           'you are checkpointing or standing down. No reply is needed.')


class StandDownUnread(Exception):
    """The stand-down's config, main, verdict queue or worktrees could not be read."""


def is_coordinator(key, entry):
    return (not key.startswith('_') and isinstance(entry, dict) and bool(entry.get('agent'))
            and bool(entry.get('name')))


def nudge_text(key, free, ids, reason=None):
    from kitlib.hubsite import push
    shown = coord_restart.shown_ids([push.field(i) for i in ids])
    lines = ['[hub push] fill: %d free, %d startable: %s route:%s'
             % (free, len(ids), shown, push.field(key))]
    if reason:
        lines.append('auto-pull stood down: %s' % reason)
    lines.append(DO_LINE)
    return '\n'.join(lines)


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


# --- stand-down (stage 4) ---------------------------------------------------------------------

def _config(top):
    from kitlib import config as kconfig
    try:
        return kconfig.load(top)
    except (kconfig.ConfigError, OSError) as e:
        raise StandDownUnread('the .kit.json of %s cannot be read (%s)' % (top, e))


def read_limits(top, cfg=None):
    """(backlog.fill_queued_max, backlog.fill_idle_max) of the checkout `top` (schema defaults 3
    and 2); StandDownUnread when its .kit.json cannot be read or fails the schema."""
    backlog = (cfg if cfg is not None else _config(top)).section('backlog')
    return backlog['fill_queued_max'], backlog['fill_idle_max']


def read_survey(top, cfg):
    """(queued, idle) of the repository whose main checkout is `top` (module docstring), `cfg` its
    Config (gate_state's gate_once). Read-only. StandDownUnread when main as fetched, the git
    common dir, the verdict queue or the worktrees cannot be read."""
    from kitlib.coordstate import render
    from kitlib.gate import verdicts
    from kitlib.git import refs
    from kitlib.git import repo
    from kitlib.worktree import classify
    main, why = render.main_as_fetched(top)
    if not main:
        raise StandDownUnread('main cannot be read (%s)' % why)
    common, why = repo.common_dir(top)
    if not common:
        raise StandDownUnread('the git common dir cannot be read (%s)' % why)
    try:
        queued = {e['branch'] for e in verdicts.entries(common)
                  if not verdicts.expired(common, e, main)}
    except verdicts.VerdictError as e:
        raise StandDownUnread('the verdict queue cannot be read (%s)' % e)
    got = classify.classify(top, dead_after_s=IDLE_AFTER_S)
    if 'error' in got.data:
        raise StandDownUnread('the worktrees cannot be classified (%s)' % got.data['error'])
    if got.data.get('unreadable'):      # every tree was made L: idle would read 0
        raise StandDownUnread('--classify could not read %s' % got.data['unreadable'])
    for t in got.data['trees']:
        if t['class'] == 'L' and str(t.get('reason', '')).startswith('unreadable'):
            raise StandDownUnread('--classify could not read %s (%s)' % (t['path'], t['reason']))
    idle = 0
    for t in got.data['trees']:
        if t['class'] in IDLE_CLASSES:
            idle += 1
        if t['class'] in ('A', 'E', 'O') or not t.get('branch') or t['branch'] in queued:
            continue
        gate = t.get('gate') or classify.gate_state(top, t['head'], cfg)
        if gate != 'green':
            continue
        landed, _why = refs.is_ancestor(t['head'], main, top)
        if not landed:                  # not in main, or git cannot tell: counted
            queued.add(t['branch'])
    return len(queued), idle


def standdown(top, survey=None):
    """The reason auto-pull stands down for the repository whose main checkout is `top`, or None
    (module docstring): `off`, `queued N>=MAX` or `idle N>=MAX`. `survey(top, cfg)` -> (queued,
    idle) is a test seam (default read_survey). StandDownUnread when anything cannot be read."""
    cfg = _config(top)
    queued_max, idle_max = read_limits(top, cfg)
    if queued_max == 0:
        return OFF
    queued, idle = (survey or read_survey)(top, cfg)
    if queued >= queued_max:
        return 'queued %d>=%d' % (queued, queued_max)
    if idle >= idle_max:
        return 'idle %d>=%d' % (idle, idle_max)
    return None


# --- the told state (stage 2) -----------------------------------------------------------------

def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def check_state(data, where):
    """`data` when it is the told state's shape (module docstring), else LaunchError naming
    `where` and the first key that is not."""
    from kitlib.launcher.settings import LaunchError
    if not isinstance(data, dict):
        raise LaunchError('%s must hold a JSON object' % where)
    for key, e in data.items():
        told = e.get('told') if isinstance(e, dict) else None
        ok = (isinstance(e, dict) and set(e) == {'told', 'since', 'in_flight'}
              and isinstance(told, list) and len(told) == 3 and _count(told[0])
              and isinstance(told[1], list) and all(isinstance(i, str) for i in told[1])
              and (told[2] is None or isinstance(told[2], str))
              and isinstance(e['since'], numbers.Real) and not isinstance(e['since'], bool)
              and _count(e['in_flight']))
        if not ok:
            raise LaunchError('%s: key %r is not {"told": [free, [ids], reason|null], "since": '
                              'seconds, "in_flight": count}: remove the file or fix the key'
                              % (where, key))
    return data


class MemoryState(object):
    """The told state kept in memory (a test's, or a job with no file)."""

    def __init__(self):
        self.data = {}

    def read(self):
        import copy
        return copy.deepcopy(self.data)

    def write(self, data):
        import copy
        self.data = copy.deepcopy(data)


class FileState(object):
    """The told state in the JSON file `path` (launcher.state's read_json and write_json: an
    atomic replace). No file is an empty state; a bad one raises LaunchError."""

    def __init__(self, path):
        self.path = path

    def read(self):
        from kitlib.launcher import state
        data = state.read_json(self.path)
        return {} if data is None else check_state(data, self.path)

    def write(self, data):
        from kitlib.launcher import state
        state.write_json(self.path, data)


class FillWatch(object):
    """run(ctx) is the job. `sessions()` the sessions.json dict, `live()` the live session names,
    `read(key, entry)` (cap, in_flight, ids) or BacklogUnread, `deliver(key, text)` a Sent state
    (None: no push object), `claims()` the sessions.json keys the usage crossover is moving
    (usage_crossover.claims; default none), `standdown(key, entry)` the stand-down reason or None
    (default none; a raise is `unread`), `store` the told state (default MemoryState), `now()`
    epoch seconds."""

    def __init__(self, sessions, live, read, deliver, claims=None, standdown=None, store=None,
                 now=time.time):
        self.sessions, self.live, self.read, self.deliver = sessions, live, read, deliver
        self.claims = claims or (lambda: set())
        self.standdown = standdown or (lambda key, entry: None)
        self.store = store if store is not None else MemoryState()
        self.now = now

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
        loaded = self.store.read()      # a bad file raises here: nothing sent, nothing written
        told = {k: dict(v) for k, v in loaded.items()}
        told_ok = told_states()
        nudged, notes, seen, reasons = [], [], set(), {}
        unvisited = set(sessions)
        for key in sorted(sessions):
            entry = sessions[key]
            if ctx.stop.is_set():       # a hub stop: the keys not reached keep their state
                seen |= unvisited
                break
            unvisited.discard(key)
            if not is_coordinator(key, entry) or entry['name'] not in live:
                continue
            if key in claimed:          # the usage crossover is moving it: no nudge to dispatch
                notes.append('%s: being moved by the usage crossover' % key)
                seen.add(key)           # its state stands untouched while it is claimed
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
            reason = self._reason(key, entry, reasons, notes)
            now = self.now()
            had = told.get(key)
            if had is not None and in_flight != had['in_flight']:
                if in_flight > had['in_flight']:
                    had['since'] = now  # it pulled: the episode starts again
                had['in_flight'] = in_flight
            state = [free, list(ids), reason]
            if had is not None and had['told'] == state:
                continue
            try:
                sent = self.deliver(key, nudge_text(key, free, ids, reason))
            except Exception as e:      # noqa: BLE001 - a host fault: tried again next run
                notes.append('%s: nudge not sent (%s)' % (key, type(e).__name__))
                continue
            if sent in told_ok:
                if had is None:
                    told[key] = {'told': state, 'since': now, 'in_flight': in_flight}
                else:
                    if free > had['told'][0]:
                        had['since'] = now      # this nudge names a newly freed slot
                    had['told'] = state
                nudged.append('%s (%d free, %d startable%s)'
                              % (key, free, len(ids), ', stood down: %s' % reason if reason else ''))
            else:
                notes.append('%s: nudge %s, tried again next run' % (key, sent or 'not sent'))
        for key in list(told):
            if key not in seen:
                del told[key]
        if told != loaded:
            self.store.write(told)
        note = '; '.join((['nudged %s' % ', '.join(nudged)] if nudged else []) + notes)[:NOTE_MAX]
        if not nudged:
            return jobs.Skip(note or 'no coordinator has a free slot and startable work')
        return note

    def _reason(self, key, entry, reasons, notes):
        """The stand-down reason of `entry`'s repository, read once per run; `unread` (stood down)
        and a note when the read raises."""
        repo = entry.get('repo')
        if repo not in reasons:
            try:
                reasons[repo] = self.standdown(key, entry)
            except Exception as e:      # noqa: BLE001 - fails closed: stood down, the why noted
                notes.append('%s: stand-down unread (%s: %s)' % (key, type(e).__name__, e))
                reasons[repo] = UNREAD
        return reasons[repo]


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

    def stood_down(key, entry):
        return standdown(s.repo_path(entry.get('repo')))

    job = FillWatch(lambda: state.read_sessions(s), live_names,
                    lambda key, entry: read_slots(s, key, entry), deliver, claims,
                    standdown=stood_down, store=FileState(state.path(s, STATE_FILE)))
    return jobs.Job(NAME, EVERY_S, job, first_s=FIRST_S)
