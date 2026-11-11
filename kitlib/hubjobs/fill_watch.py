"""kitlib.hubjobs.fill_watch: the hub's fill watch (docs/hub-fill.md): a live coordinator with a
free slot and startable work is nudged, once per change (stage 1); what was told is kept in
fill-watch.json, so a hub restart does not nudge again (stage 2); PULL_AFTER_S into an episode
the hub pulls for it (stage 3); the nudge names the auto-pull stand-down when one holds (stage
4); a stand-down, or slots the pull left free, tell the PM once per episode (stage 5).

  make(sources) -> jobs.Job | None   the job; None when launcher.repos names no repository or
                                     launcher.state_dir is not set
  FillWatch(sessions, live, read, deliver, claims=None, standdown=None, store=None, now=time.time,
            pull=None, handoffs=None, waits=None, quiet=None)
                                     its run(ctx): one pass over the live coordinators
  read_slots(settings, key, entry, session_live=None)
                                     (cap, in_flight, offered ids in offer order) of one
                                     coordinator; coord_restart.BacklogUnread when it cannot be read
  read_waits(settings, key, entry, session_live=None)
                                     [(id, priority, live pull id, its priority, its branch)] of one
                                     coordinator: the waits notices' pairs; BacklogUnread as above
  waits_text(key, rows)              the waits notices' push
  auto_pull(settings, key, entry, ids, free, liveness=None, stop=None, ...)
                                     ([(id, branch)], [(id, code)]): trees made and records pulled
  pull_record(root, rid, branch, sessions, liveness=None)
                                     one hub pull: None, or the code of why not
  pull_text(key, pulled, skipped)    the auto-pull's push
  standdown(top, survey=None)        the stand-down reason of the repository whose main checkout is
                                     `top`, or None; StandDownUnread when it cannot be read
  read_limits(top, cfg=None)         (backlog.fill_queued_max, backlog.fill_idle_max) of `top`
  read_survey(top)                   (queued, idle) of `top`; StandDownUnread when unread
  read_hold(path)                    the PM's holds (fill-hold.json), {} when none; StandDownUnread
  hold_reason(data, repo, now)       `hold machine`, `hold repo` or None
  set_hold(path, repo, reason, until, now), clear_hold(path, repo)
                                     write or drop one hold (repo None: the machine's)
  fill_hold(settings, repo, machine, reason, until, clear, now) -> lines
                                     launch.py fill-hold: set, clear or list the holds
  usage_reason(rows, limit=None)     `usage all>=N%` when every account is at the limit, or None
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
  offered    `next --all` for the same sessions (plan_narrow.offer_for, cap None, no fetch), so
             every collision, hold, other session's record and unmet `requires:` is already
             passed over.
The offer judges session liveness as `next` does (coord_restart.session_liveness, from the run's
own sessions.json and process listing; FillWatch.liveness).
free = cap - in_flight (at least 0). Free and something offered: one deliver to the coordinator's
sessions.json key, `[hub push] fill: N free, M startable: <ids> route:<key>`, then
`auto-pull stood down: <reason>` when a stand-down holds, and one line of what to do; record ids,
counts and the kit's own reason codes only, never free text.

Stand-down (stage 4): read once per repository per run, and only for a coordinator that is free
with something offered. `standdown(top)` reads the main checkout's `.kit.json`:
  backlog.fill_queued_max (default 3): 0 is `off` (always stood down, nothing surveyed); else
             `queued N>=MAX` when N branches wait to land: the verdict queue's entries not expired
             (kitlib.landing.verdicts), and every worktree branch, live or not, whose tip has a green
             gate-OK record (classify.gate_state) and is not in main as fetched; each branch once.
             A tip git cannot place against main counts.
  backlog.fill_idle_max (default 2): `idle N>=MAX` when N worktrees are class B or C under
             `--classify` with a dead bar of IDLE_AFTER_S (2 h, fixed); L trees never count. 0 is
             always stood down.
A stand-down that cannot be read is `unread` (fails closed: stood down) and a note names why;
that includes a `--classify` run that reports a read it could not make (its `unreadable`, or an L
tree whose reason is `unreadable: ...`), since such a tree would hide an idle one. The one
exception: an unreadable L tree --classify gives an idle time of IDLE_AFTER_S or more (only a
process it could not read might hold it) counts idle, or neither idle nor unread when its head is
in main as fetched (a landed tree awaiting removal).
With no repository stand-down, a coordinator whose newest hand-off record (the hub's
`handoffs`) is not a check-in is stood down as `handoff <kind>`, unless its live session started
after the record's `at` (live_names' start): a session started since is a new one, check-in or
not. Records that cannot be read are `handoff unread`.
Two stand-downs come first and are quiet (QUIET), read once per repository per run for every live
coordinator not claimed, before its waits notices and slots: no nudge, no waits notice, no pull,
no PM event; the key's told state is dropped, so the episode starts afresh once they end, and its
waits pairs are kept as they were. A PM hold (fill-hold.json in launcher.state_dir, written by
`launch.py fill-hold`): {"machine": hold, "repos": {launcher.repos name: hold}}, a hold {reason,
set_at, until?} (epoch seconds; no until: until cleared), gives `hold machine` or `hold repo`
while it is on; a file that cannot be read or has another shape is `unread` (the nudge and waits
go on, the nudge naming it; nothing is pulled), and `fill-hold --machine --clear` removes it. The
reason is free text and goes no further than the file and the listing. Usage
(make's): every signed-in account's week (accounts.week_rows: its newest reading, however stale,
since usage only falls at the reset; a passed reset is 0%) at or over USAGE_LIMIT% gives
`usage all>=N%`; one account under it, or one with no reading (headroom, as accounts.pick counts
it: the owner's fail-open), or no signed-in account, or readings that cannot be read, gives none.

Auto-pull (stage 3), for a key already told this episode, PULL_AFTER_S after `since` and once per
episode: with a stand-down, nothing is pulled; else `pulled` (= since) is written to the state
file first, then auto_pull takes up to `free` of the offered ids in order: a branch
`<key lowercased>/fill-<id lowercased>`, its worktree made by new_worktree (no wait), then
pull_record pulls the record onto it under Session PULL_SESSION with the record's own session's
offer (plan.Context.offer_session); a record it does not pull has its tree and branch removed,
but for one whose write may have landed (`unsure`). A `not-open` or `unsure` skip counts as a
slot taken, so the coordinator's cap holds when both pull at once.
One push to the coordinator names what was pulled and skipped (pull_text). Stage 5: a stand-down,
or slots the pull left free, is one `fill` event to the PM (push.JOB_EACH), detail
`<key>:<free>:<why>`, `pm` (= since) marking it told for the episode.

One push per change: the job keeps, in fill-watch.json under launcher.state_dir (FileState), per
key: `told` [free, ids, reason] last told, `since` (epoch seconds: the first nudge of the
episode, reset to now when in_flight rises above the last read, and when a nudge is written whose
free count is above the one told: the nudge that names a newly freed slot) and `in_flight` (the
last read), and, once set, `pulled` and `pm` (the `since` of the episode pulled for and told to
the PM). It pushes when (free, ids, reason) differ from `told`; a key with no free slot,
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

Waits notices, for every live coordinator not claimed, free slot or not: read_waits gives the
pairs (H, L) where `next --all` passes H over on a live pull L that it outranks (offer.outranks:
ranked above L and in a strictly higher priority band, or a pre-emption; a tie within a band is
no pair). A pair first seen is one push to the coordinator (waits_text: `p<n> H waits on p<m>
pull L on B (lands first)`, a notice only: the live pull is never stopped or overridden), told
once written or uncertain, else tried again next run. A pair still there WAITS_PM_AFTER_S (24 h)
after first sight is one `fill` event to the PM, detail `<key>:waits:<H>:<L>`. The state is
fill-watch.json's `_waits`: {key: {"H>L": {since (first sight, epoch seconds: a record's
pulled_at is a date only), told, pm}}}, absent when empty. A key read drops the pairs no longer
there; a key with no live coordinator drops all; a claimed, unread or unreached key keeps them.

Outcomes (kitlib.hubsite.jobs): a nudge is a success whose note names the keys; nothing to nudge
is a Skip. A store or cap that cannot be read is a note. A sessions.json or crossover state that
cannot be read (LaunchError), or a process listing that times out or fails (RuntimeError, OSError),
is a Skip with a note: never a failure, so the runner never pushes `failing` to the PM for them.
An auto-pull is a success whose note names it; one that raises is a note. Nothing else goes to
the PM here but the `fill` event.
"""
import copy
import datetime
import numbers
import os
import subprocess
import time

from kitlib.hubjobs import coord_restart
from kitlib.hubsite import jobs
from kitlib.launcher import accounts as A

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
PULL_AFTER_S = 1200             # into an episode with no stand-down: the hub pulls (stage 3)
PULL_SESSION = 'hub-fill'       # the `Session:` of a hub pull's store commit
MARKS = ('pulled', 'pm')        # a key's episode marks: pulled, and the PM told
UNSURE = 'unsure'               # a hub pull whose write may have landed: its tree is kept
TAKES_SLOT = ('not-open', UNSURE)   # skips that still count against the free slots
_UNSET = object()
PULL_DO_LINE = ('The hub fill watch pulled these records for you, each on its own new worktree '
                'and branch: dispatch them (coordinator role, Slots). A skipped one was not '
                'pulled, and the code says why. No reply is needed.')
WAITS = '_waits'                # the told state's key of the waits notices (no sessions.json key)
WAITS_PM_AFTER_S = 86400        # a pair still waiting this long after first sight tells the PM
HOLD_FILE = 'fill-hold.json'    # the PM's holds on new work, in launcher.state_dir
HOLD = 'hold'
USAGE = 'usage'
QUIET = (HOLD, USAGE)           # stand-downs that stop the nudge too
USAGE_LIMIT = A.HEADROOM_LIMIT  # % of the week: every account at or over it stands down
WAITS_DO_LINE = ('A notice only: each record named outranks the live pull its scope collides with '
                 'and becomes eligible once that pull lands (capacity or an unmet requires may '
                 'still hold it). Never stop or override the live pull for it. No reply is '
                 'needed.')


class StandDownUnread(Exception):
    """The stand-down's config, main, verdict queue or worktrees could not be read."""


def is_coordinator(key, entry):
    return (not key.startswith('_') and isinstance(entry, dict) and bool(entry.get('agent'))
            and bool(entry.get('name')))


def nudge_text(key, free, ids, reason=None):
    from kitlib.hubclient import push
    shown = coord_restart.shown_ids([push.field(i) for i in ids])
    lines = ['[hub push] fill: %d free, %d startable: %s route:%s'
             % (free, len(ids), shown, push.field(key))]
    if reason:
        lines.append('auto-pull stood down: %s' % reason)
    lines.append(DO_LINE)
    return '\n'.join(lines)


def pull_text(key, pulled, skipped):
    """The push of an auto-pull: `pulled` [(id, branch)] and `skipped` [(id, code)], ids,
    branches and codes only (each cut to a header field; a branch part by part)."""
    from kitlib.hubclient import push
    shown = ', '.join('%s as %s' % (push.field(rid), '/'.join(push.field(p) for p in b.split('/')))
                      for rid, b in pulled)
    lines = ['[hub push] fill: pulled %s route:%s' % (shown or 'none', push.field(key))]
    lines.extend('skipped %s %s' % (push.field(rid), push.field(code)) for rid, code in skipped)
    lines.append(PULL_DO_LINE)
    return '\n'.join(lines)


def waits_text(key, rows):
    """The push of the waits notices: `rows` [(id, priority, live pull id, its priority, its
    branch)], ids, priorities and branches only (each cut to a header field)."""
    from kitlib.hubclient import push
    lines = ['[hub push] waits: %d record(s) wait on a lower live pull route:%s'
             % (len(rows), push.field(key))]
    lines.extend('p%s %s waits on p%s pull %s on %s (lands first)'
                 % (push.field(hp), push.field(hid), push.field(lp), push.field(lid),
                    '/'.join(push.field(p) for p in str(branch).split('/')))
                 for hid, hp, lid, lp, branch in rows)
    lines.append(WAITS_DO_LINE)
    return '\n'.join(lines)


def read_waits(settings, key, entry, session_live=None):
    """[(id, priority, live pull id, its priority, its branch)]: the records of the coordinator of
    sessions.json entry `key` that `next --all` passes over on a live pull they outrank
    (offer.outranks, PassedOver.waits), each pair once, read as coord_restart.read_offer reads
    the offer (no fetch, every session of wake_sessions) and with `next`'s narrowing. BacklogUnread
    when it cannot be read, the live pulls' branches included."""
    from kitlib.backlog import opener, plan_narrow
    from kitlib.backlog import offer as omod
    from kitlib.backlog.errors import Refusal
    from kitlib.config import ConfigError
    root = coord_restart.store_root(settings, (entry or {}).get('repo'))
    rows, snap, holding = [], None, None
    try:
        for session in coord_restart.wake_sessions(key, entry):
            ctx = opener.open_store(root, fetch=False, session=session, probes=opener.NO_PROBES)
            ctx.session_live = session_live
            if snap is None:            # one read of the store and its live pulls
                snap = ctx.store.load()
                holding, _dead, why = omod.in_flight_split(snap, ctx.root, ctx.remote)
                if why:                 # every pull would hold its scope: no notice is sure
                    raise coord_restart.BacklogUnread('the branches could not be read (%s)'
                                                      % why)
            # As `next --all` reads it: no cap, and a record a narrowing clears is offered.
            _offers, passed, _holding, _own = plan_narrow.offer_for(snap, ctx, cap=None, holding=holding,
                                                                    narrow=True)
            for p in passed:
                for other in p.waits:
                    row = (p.rec.id, p.rec.get('priority'), other.id, other.get('priority'),
                           other.get('pulled_by') or '?')
                    if row not in rows:
                        rows.append(row)
    except (Refusal, ConfigError, omod.PartitionError, OSError) as exc:
        raise coord_restart.BacklogUnread('the store could not be read (%s)' % type(exc).__name__)
    return rows


def read_slots(settings, key, entry, session_live=None):
    """(cap, in_flight, ids) of the coordinator of sessions.json entry `key` (module docstring),
    read through coord_restart.read_offer with `session_live` (session liveness, as `next`
    judges it): BacklogUnread when it cannot be read."""
    ids, holding, cap = coord_restart.read_offer(settings, key, entry, with_cap=True,
                                                 session_live=session_live)
    sessions = coord_restart.wake_sessions(key, entry)
    return cap, sum(1 for r in holding if r.get('session') in sessions), ids


def live_names():
    """{name: start} of the live sessions (coord_restart's listing): the earliest creation time,
    epoch seconds, of the processes of that name; None when one of them cannot be read (it could
    be the oldest). TimeoutExpired passes up."""
    from kitlib.launcher import lookup
    starts = {}
    for p in lookup.launched(coord_restart.list_processes()):
        name = lookup.rc_name(p.get('CommandLine'))
        if name is not None:
            starts.setdefault(name, []).append(coord_restart.row_created(p))
    return {n: None if None in ts else min(ts) for n, ts in starts.items()}


def told_states():
    """The kitlib.hubclient.push Sent states that count as told: written and uncertain (push is
    imported here, at run time, as usage_crossover.make does)."""
    from kitlib.hubclient import push
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


def read_survey(top):
    """(queued, idle) of the repository whose main checkout is `top` (module docstring). Read-only.
    StandDownUnread when main as fetched, the git common dir, the verdict queue or the worktrees
    cannot be read."""
    from kitlib.coordstate import render
    from kitlib.landing import verdicts
    from kitlib.records import verdict_entry
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
                  if not verdict_entry.expired(common, e, main)}
    except verdict_entry.VerdictError as e:
        raise StandDownUnread('the verdict queue cannot be read (%s)' % e)
    got = classify.classify(top, dead_after_s=IDLE_AFTER_S)
    if 'error' in got.data:
        raise StandDownUnread('the worktrees cannot be classified (%s)' % got.data['error'])
    if got.data.get('unreadable'):      # every tree was made L: idle would read 0
        raise StandDownUnread('--classify could not read %s' % got.data['unreadable'])
    for t in got.data['trees']:
        if _unreadable(t) and not _dead(t):
            raise StandDownUnread('--classify could not read %s (%s)' % (t['path'], t['reason']))
    idle = 0
    for t in got.data['trees']:
        if t['class'] in IDLE_CLASSES:
            idle += 1
        elif _unreadable(t):            # dead past the bar, and only a process we could not read
            landed, _why = refs.is_ancestor(t['head'], main, top)
            if landed:                  # a landed tree awaiting removal: neither idle nor live
                continue
            idle += 1                   # not in main, or git cannot tell: idle
        if t['class'] in ('A', 'E', 'O') or not t.get('branch') or t['branch'] in queued:
            continue
        gate = t.get('gate') or classify.gate_state(top, t['head'])
        if gate != 'green':
            continue
        landed, _why = refs.is_ancestor(t['head'], main, top)
        if not landed:                  # not in main, or git cannot tell: counted
            queued.add(t['branch'])
    return len(queued), idle


def _unreadable(t):
    return t['class'] == 'L' and str(t.get('reason', '')).startswith('unreadable')


def _dead(t):
    """True when --classify gave an unreadable L tree an idle time past the dead bar: its only
    unread part is a process that might hold it (classify gives `idle_s` to no other)."""
    idle_s = t.get('idle_s')
    return (isinstance(idle_s, numbers.Real) and not isinstance(idle_s, bool)
            and idle_s >= IDLE_AFTER_S)


def standdown(top, survey=None):
    """The reason auto-pull stands down for the repository whose main checkout is `top`, or None
    (module docstring): `off`, `queued N>=MAX` or `idle N>=MAX`. `survey(top)` -> (queued,
    idle) is a test seam (default read_survey). StandDownUnread when anything cannot be read."""
    cfg = _config(top)
    queued_max, idle_max = read_limits(top, cfg)
    if queued_max == 0:
        return OFF
    queued, idle = (survey or read_survey)(top)
    if queued >= queued_max:
        return 'queued %d>=%d' % (queued, queued_max)
    if idle >= idle_max:
        return 'idle %d>=%d' % (idle, idle_max)
    return None


# --- the PM's hold and usage (quiet stand-downs) ----------------------------------------------

def _is_hold(h):
    return (isinstance(h, dict) and isinstance(h.get('reason'), str)
            and _seconds(h.get('set_at')) and (h.get('until') is None or _seconds(h['until'])))


def read_hold(path):
    """The holds in `path` (module docstring), {} when there is no file; StandDownUnread when it
    cannot be read or holds another shape (fails closed)."""
    from kitlib.launcher import state
    from kitlib.launcher.settings import LaunchError
    try:
        data = state.read_json(path)
    except LaunchError as e:
        raise StandDownUnread('the PM hold cannot be read (%s)' % e)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise StandDownUnread('the PM hold %s holds another shape' % path)
    repos = data.get('repos', {})
    if (set(data) - {'machine', 'repos'} or ('machine' in data and not _is_hold(data['machine']))
            or not isinstance(repos, dict) or not all(_is_hold(h) for h in repos.values())):
        raise StandDownUnread('the PM hold %s holds another shape' % path)
    return data


def _on(h, now):
    return h is not None and (h.get('until') is None or now < h['until'])


def hold_reason(data, repo, now):
    """`hold machine` or `hold repo` while that hold is on at `now` (epoch seconds), else None."""
    if _on(data.get('machine'), now):
        return '%s machine' % HOLD
    if _on(data.get('repos', {}).get(repo), now):
        return '%s repo' % HOLD
    return None


def set_hold(path, repo, reason, until, now):
    """Write the hold of `repo` (None: the machine's), replacing one already there."""
    from kitlib.launcher import state
    data = read_hold(path)
    h = {'reason': reason, 'set_at': now}
    if until is not None:
        h['until'] = until
    if repo is None:
        data['machine'] = h
    else:
        data.setdefault('repos', {})[repo] = h
    state.write_json(path, data)


def clear_hold(path, repo):
    """Drop the hold of `repo` (None: the machine's); True when there was one."""
    from kitlib.launcher import state
    data = read_hold(path)
    if repo is None:
        had = data.pop('machine', None) is not None
    else:
        had = data.get('repos', {}).pop(repo, None) is not None
        if 'repos' in data and not data['repos']:
            del data['repos']
    if had:
        state.write_json(path, data)
    return had


def _when(t):
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(t))


def fill_hold(settings, repo, machine, reason, until, clear, now):
    """launch.py fill-hold: set (`reason`, optional `until` an ISO time, local without an offset),
    clear, or with neither list the holds; `repo` a launcher.repos name or `machine` True. Lines;
    LaunchError when refused."""
    from kitlib.launcher import state
    from kitlib.launcher.settings import LaunchError
    path = state.path(settings, HOLD_FILE)
    if repo is not None and repo not in settings.repos:
        raise LaunchError('%s is not a name in launcher.repos.' % repo)
    where = 'the machine' if machine else repo
    try:
        if clear:
            if reason is not None or until is not None:
                raise LaunchError('--clear takes no --reason or --until.')
            return ['Cleared the hold on %s.' % where if clear_hold(path, None if machine else repo)
                    else 'No hold on %s.' % where]
        if reason is not None:
            end = None
            if until is not None:
                t = A.parse_time(until)
                if t is None:
                    raise LaunchError('--until %s is not an ISO time.' % until)
                end = t.timestamp()
                if end <= now:
                    raise LaunchError('--until %s has passed.' % until)
            set_hold(path, None if machine else repo, reason, end, now)
            return ['Held %s from the fill watch%s.' % (where, ' until %s' % _when(end) if end
                                                       else ' until cleared')]
        if until is not None:
            raise LaunchError('--until needs --reason.')
        data = read_hold(path)
    except StandDownUnread as e:
        if clear and machine:           # the way out of an unreadable file: it goes, every hold
            try:                        # with it (the watch then holds nothing)
                os.remove(path)
            except OSError as oe:
                raise LaunchError('%s cannot be removed: %s' % (path, oe))
            return ['Removed the unreadable %s, every hold with it (%s).' % (HOLD_FILE, e)]
        raise LaunchError('%s. `launch.py fill-hold --machine --clear` removes the file and every '
                          'hold in it.' % e)
    rows = ([('machine', data['machine'])] if 'machine' in data else []) + sorted(
        data.get('repos', {}).items())
    return ['%s: %s (set %s, %s)%s' % (name, h['reason'], _when(h['set_at']),
                                       'until %s' % _when(h['until']) if 'until' in h
                                       else 'until cleared', '' if _on(h, now) else ' ended')
            for name, h in rows] or ['No hold.']


def usage_reason(rows, limit=None):
    """`usage all>=N%` when every row (account, week pct or None, reset) is at or over `limit`%
    (default USAGE_LIMIT), else None. A row's age never makes it unknown (week_rows keeps a stale
    reading's value). No rows, or a row with no reading, is headroom: the owner's fail-open;
    failing closed on unknown would be this one line."""
    limit = USAGE_LIMIT if limit is None else limit
    if not rows or any(pct is None for _n, pct, _r in rows):
        return None
    if all(pct >= limit for _n, pct, _r in rows):
        return '%s all>=%d%%' % (USAGE, limit)
    return None


# --- the told state (stage 2) -----------------------------------------------------------------

def _count(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _seconds(v):
    return isinstance(v, numbers.Real) and not isinstance(v, bool)


def check_state(data, where):
    """`data` when it is the told state's shape (module docstring), else LaunchError naming
    `where` and the first key that is not."""
    from kitlib.launcher.settings import LaunchError
    if not isinstance(data, dict):
        raise LaunchError('%s must hold a JSON object' % where)
    for key, e in data.items():
        if key == WAITS:
            _check_waits(e, where)
            continue
        told = e.get('told') if isinstance(e, dict) else None
        ok = (isinstance(e, dict) and set(e) - set(MARKS) == {'told', 'since', 'in_flight'}
              and isinstance(told, list) and len(told) == 3 and _count(told[0])
              and isinstance(told[1], list) and all(isinstance(i, str) for i in told[1])
              and (told[2] is None or isinstance(told[2], str))
              and _seconds(e['since']) and _count(e['in_flight'])
              and all(_seconds(e[m]) for m in MARKS if m in e))
        if not ok:
            raise LaunchError('%s: key %r is not {"told": [free, [ids], reason|null], "since": '
                              'seconds, "in_flight": count}, with "pulled" and "pm" (seconds) '
                              'optional: remove the file or fix the key' % (where, key))
    return data


def _check_waits(e, where):
    from kitlib.launcher.settings import LaunchError
    ok = isinstance(e, dict) and all(
        isinstance(pairs, dict) and all(
            isinstance(p, dict) and set(p) == {'since', 'told', 'pm'} and _seconds(p['since'])
            and isinstance(p['told'], bool) and isinstance(p['pm'], bool)
            for p in pairs.values())
        for pairs in e.values())
    if not ok:
        raise LaunchError('%s: key %r is not {key: {"ID>PULL": {"since": seconds, "told": bool, '
                          '"pm": bool}}}: remove the file or fix the key' % (where, WAITS))


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
    """run(ctx) is the job. `sessions()` the sessions.json dict, `live()` the live session names
    (a set, or live_names' {name: start}, whose starts end a stale hand-off),
    `read(key, entry)` (cap, in_flight, ids) or BacklogUnread, `deliver(key, text)` a Sent state
    (None: no push object), `claims()` the sessions.json keys the usage crossover is moving
    (usage_crossover.claims; default none), `standdown(key, entry)` the stand-down reason or None
    (default none; a raise is `unread`), `store` the told state (default MemoryState), `now()`
    epoch seconds, `pull(key, entry, ids, free, stop)` the auto-pull -> ([(id, branch)],
    [(id, code)]) (None: no auto-pull, the watch alone), `handoffs()` {session key: its newest
    hand-off record} (None: not read; a raise or a None answer is `handoff unread`),
    `waits(key, entry)` read_waits' rows or BacklogUnread (None: no waits notices), `quiet(key,
    entry)` the quiet stand-down (QUIET) or None (default none; a raise is `unread`).
    `liveness` is the last run's session liveness (coord_restart.session_liveness), which
    make's read, waits and pull pass on."""

    def __init__(self, sessions, live, read, deliver, claims=None, standdown=None, store=None,
                 now=time.time, pull=None, handoffs=None, waits=None, quiet=None):
        self.sessions, self.live, self.read, self.deliver = sessions, live, read, deliver
        self.waits = waits
        self.quiet = quiet or (lambda key, entry: None)
        self.claims = claims or (lambda: set())
        self.standdown = standdown or (lambda key, entry: None)
        self.store = store if store is not None else MemoryState()
        self.now = now
        self.pull = pull
        self.handoffs = handoffs
        self.liveness = None
        self._hands = _UNSET            # this run's hand-off read (_handoff)
        self._started = {}              # this run's {live session name: its start or None}

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
        self.liveness = coord_restart.session_liveness(sessions, live)  # this run's, fresh
        self._hands = _UNSET
        self._started = live if isinstance(live, dict) else {}   # a set of names: no starts
        loaded = self.store.read()      # a bad file raises here: nothing sent, nothing written
        told = copy.deepcopy(loaded)
        told_ok = told_states()
        nudged, notes, seen, reasons, acted, waited = [], [], set(), {}, [], []
        quiets = {}                     # this run's quiet stand-down per repository
        wseen = set()                   # the keys whose waits pairs stand (read, unread, kept)
        unvisited = set(sessions)
        for key in sorted(sessions):
            entry = sessions[key]
            if ctx.stop.is_set():       # a hub stop: the keys not reached keep their state
                seen |= unvisited
                wseen |= unvisited
                break
            unvisited.discard(key)
            if not is_coordinator(key, entry) or entry['name'] not in live:
                continue
            if key in claimed:          # the usage crossover is moving it: no nudge to dispatch
                notes.append('%s: being moved by the usage crossover' % key)
                seen.add(key)           # its state stands untouched while it is claimed
                wseen.add(key)
                continue
            quiet = self._quiet(key, entry, quiets, reasons, notes)
            if quiet:                   # no nudge, no waits, no pull, no PM event
                notes.append('%s: stood down (%s)' % (key, quiet))
                wseen.add(key)          # its waits pairs stand; its told state is dropped, so
                continue                # the episode starts afresh once the stand-down ends
            if self.waits is not None:
                wseen.add(key)
                self._waits(ctx, key, entry, told, waited, notes, told_ok)
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
            if had is None or had['told'] != state:
                self._nudge(key, free, ids, reason, state, in_flight, now, told, nudged, notes,
                            told_ok)
            if self.pull is not None and told.get(key) is not None:
                self._fill(ctx, key, entry, told, free, ids, reason, now, acted, notes)
        for key in list(told):
            if key != WAITS and key not in seen:
                del told[key]
        pairs = told.get(WAITS, {})
        for key in list(pairs):         # a key not seen (no live coordinator) drops its pairs
            if (self.waits is not None and key not in wseen) or not pairs[key]:
                del pairs[key]
        if WAITS in told and not pairs:
            del told[WAITS]
        if told != loaded:
            self.store.write(told)
        note = '; '.join((['nudged %s' % ', '.join(nudged)] if nudged else []) +
                         (['auto-pull %s' % ', '.join(acted)] if acted else []) +
                         (['waits %s' % ', '.join(waited)] if waited else []) + notes)[:NOTE_MAX]
        if not nudged and not acted and not waited:
            return jobs.Skip(note or 'no coordinator has a free slot and startable work')
        return note

    def _nudge(self, key, free, ids, reason, state, in_flight, now, told, nudged, notes, told_ok):
        """One nudge of `key`; what it told goes into `told` when the deliver counts as told."""
        had = told.get(key)
        try:
            sent = self.deliver(key, nudge_text(key, free, ids, reason))
        except Exception as e:          # noqa: BLE001 - a host fault: tried again next run
            notes.append('%s: nudge not sent (%s)' % (key, type(e).__name__))
            return
        if sent in told_ok:
            if had is None:
                told[key] = {'told': state, 'since': now, 'in_flight': in_flight}
            else:
                if free > had['told'][0]:
                    had['since'] = now          # this nudge names a newly freed slot
                had['told'] = state
            nudged.append('%s (%d free, %d startable%s)'
                          % (key, free, len(ids), ', stood down: %s' % reason if reason else ''))
        else:
            notes.append('%s: nudge %s, tried again next run' % (key, sent or 'not sent'))

    def _waits(self, ctx, key, entry, told, waited, notes, told_ok):
        """The waits notices of `key` (module docstring): a pair first seen is one push, told
        once written or uncertain; one still there WAITS_PM_AFTER_S after first sight is one
        `fill` event to the PM. A read that raises BacklogUnread keeps the pairs as they were."""
        from kitlib.hubclient import push
        try:
            rows = self.waits(key, entry)
        except coord_restart.BacklogUnread as e:
            notes.append('%s waits skipped: %s' % (key, e))
            return
        now = self.now()
        had = told.get(WAITS, {}).get(key, {})
        pairs, fresh = {}, []
        for row in rows:
            pid = '%s>%s' % (row[0], row[2])
            if pid in pairs:
                continue
            pairs[pid] = dict(had.get(pid) or {'since': now, 'told': False, 'pm': False})
            if not pairs[pid]['told']:
                fresh.append((pid, row))
        if fresh:
            try:
                sent = self.deliver(key, waits_text(key, [row for _pid, row in fresh]))
            except Exception as e:      # noqa: BLE001 - a host fault: tried again next run
                sent = 'not sent (%s)' % type(e).__name__
            if sent in told_ok:
                for pid, _row in fresh:
                    pairs[pid]['told'] = True
                waited.append('%s (%s)' % (key, ' '.join(pid for pid, _row in fresh)))
            else:
                notes.append('%s: waits %s, tried again next run' % (key, sent or 'not sent'))
        for pid, p in sorted(pairs.items()):
            if not p['pm'] and now - p['since'] >= WAITS_PM_AFTER_S:
                h, l = pid.split('>', 1)
                ctx.event('fill', '%s:waits:%s:%s' % (push.field(key), push.field(h),
                                                       push.field(l)))
                p['pm'] = True
        told.setdefault(WAITS, {})[key] = pairs

    def _fill(self, ctx, key, entry, told, free, ids, reason, now, acted, notes):
        """Stages 3 and 5 for a key already nudged this episode (module docstring): PULL_AFTER_S
        into it, a stand-down tells the PM once; with none the hub pulls once, the mark written
        before the pull, and slots it left free tell the PM once."""
        from kitlib.hubclient import push
        e = told[key]
        since = e['since']
        if now - since < PULL_AFTER_S or ctx.stop.is_set():
            return
        if reason:
            if e.get('pm') != since:
                ctx.event('fill', '%s:%d:%s' % (push.field(key), free,
                                                push.field(reason.split()[0])))
                e['pm'] = since
            return
        if e.get('pulled') == since:
            return
        e['pulled'] = since
        self.store.write(told)          # first: a hub that dies in the pull never pulls it again
        try:
            pulled, skipped = self.pull(key, entry, list(ids), free, ctx.stop)
        except Exception as exc:        # noqa: BLE001 - the episode's one try is spent: a note
            notes.append('%s: auto-pull failed (%s: %s)' % (key, type(exc).__name__, exc))
            pulled, skipped = [], []
        if pulled or skipped:
            try:
                sent = self.deliver(key, pull_text(key, pulled, skipped))
            except Exception as exc:    # noqa: BLE001 - the pull stands; the next nudge follows
                sent = 'not sent (%s)' % type(exc).__name__
            if sent not in told_states():
                notes.append('%s: pull push %s' % (key, sent or 'not sent'))
        acted.append('%s (pulled %s%s)' % (key, ' '.join(r for r, _b in pulled) or 'none',
                                           ', skipped %d' % len(skipped) if skipped else ''))
        left = free - len(pulled) - sum(1 for _r, c in skipped if c.split('+')[0] in TAKES_SLOT)
        if left > 0 and e.get('pm') != since:
            ctx.event('fill', '%s:%d:unfilled' % (push.field(key), left))
            e['pm'] = since

    def _quiet(self, key, entry, quiets, reasons, notes):
        """The quiet stand-down (QUIET) of `entry`'s repository, read once per run, or None. A read
        that raises is noted and makes the repository's stand-down `unread` (fails closed: the
        nudge names it and nothing is pulled), so it is not quiet."""
        repo = entry.get('repo')
        if repo not in quiets:
            try:
                quiets[repo] = self.quiet(key, entry)
            except Exception as e:      # noqa: BLE001 - fails closed through `unread`
                notes.append('%s: stand-down unread (%s: %s)' % (key, type(e).__name__, e))
                quiets[repo] = None
                reasons[repo] = UNREAD
        return quiets[repo]

    def _reason(self, key, entry, reasons, notes):
        """The stand-down reason of `entry`'s repository, read once per run; `unread` (stood down)
        and a note when the read raises. With none, a pending hand-off of `key` (_handoff)."""
        repo = entry.get('repo')
        if repo not in reasons:
            try:
                reasons[repo] = self.standdown(key, entry)
            except Exception as e:      # noqa: BLE001 - fails closed: stood down, the why noted
                notes.append('%s: stand-down unread (%s: %s)' % (key, type(e).__name__, e))
                reasons[repo] = UNREAD
        if reasons[repo] is None and self.handoffs is not None:
            return self._handoff(key, entry, notes)
        return reasons[repo]

    def _handoff(self, key, entry, notes):
        """`handoff <kind>` when the newest hand-off record of `key` is not a check-in (a hand-off,
        restart or stop) and the live session `entry` names did not start after its `at`: pending.
        A session started after it is a new one (a `launch.py start` or a coord-restart wake writes
        no check-in), so None; a start or an `at` that cannot be read keeps it pending. `handoff
        unread` when the records cannot be read (read once per run), else None."""
        from kitlib.hubclient import push
        if self._hands is _UNSET:
            try:
                self._hands = self.handoffs()
            except Exception as e:      # noqa: BLE001 - fails closed: stood down, the why noted
                notes.append('hand-offs unread (%s)' % type(e).__name__)
                self._hands = None
            else:
                if not isinstance(self._hands, dict):
                    notes.append('hand-offs unread (the hub gives no read yet)')
        if not isinstance(self._hands, dict):
            return 'handoff unread'
        rec = self._hands.get(key)
        kind = rec.get('kind') if isinstance(rec, dict) else None
        if rec is None or kind == 'checkin':
            return None
        at = coord_restart.item_epoch(rec)
        started = self._started.get(entry.get('name'))
        if at is not None and started is not None and started > at:
            return None                 # a session started since: that hand-off is over
        return 'handoff %s' % push.field(kind)


# --- auto-pull (stage 3) ----------------------------------------------------------------------

def fill_branch(key, rid):
    """The branch a hub pull of `rid` for the coordinator of sessions.json key `key` makes:
    `<key>/fill-<id>`, both lowercased (new_worktree's `<area>/<slug>`)."""
    return '%s/fill-%s' % (key.lower(), rid.lower())


class _Skip(Exception):
    """A record pull_record does not pull; `code` says why."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def pull_record(root, rid, branch, sessions, liveness=None):
    """Pull record `rid` onto `branch` in the backlog store of the checkout `root` (fetched first),
    the store commit under Session PULL_SESSION and the offer the record's own session's, among
    `sessions` (coord_restart.wake_sessions), with `liveness` the session liveness. None when it
    is pulled, else the code of why not: `gone` (no such record), `held` (owner-held: the
    `pull-held` row every role is refused), `not-open`, `not-theirs` (its session is not one of
    `sessions`), `refused` (the planner's refusal: not in the offer, a collision, a hold),
    `store` (the store is the implicit one or cannot be read, or the write surely did not land:
    Busy, Dirty, Overtaken), or UNSURE (any other Failure: the write may have landed)."""
    from kitlib.backlog import opener, policy, pull_branch
    from kitlib.backlog import offer as omod
    from kitlib.backlog.errors import Busy, Dirty, Failure, IndexStale, NotFound, Overtaken, Refusal
    from kitlib.config import ConfigError

    def plan(snap):
        rec = snap.get(rid)
        if rec is None:
            raise _Skip('gone')
        if 'pull-held' in policy.sort('pull', {}, rec):
            raise _Skip('held')
        if rec.get('session') not in sessions:
            raise _Skip('not-theirs')
        if rec.get('state') != 'open':
            raise _Skip('not-open')
        ctx.offer_session = rec.get('session')
        return pull_branch.plan_pull(snap, ctx, rid)
    try:
        ctx = opener.open_store(root, fetch=True, session=PULL_SESSION, branch=branch,
                                probes=opener.NO_PROBES)
        if getattr(ctx.store.backend, 'implicit', False):
            return 'store'
        ctx.session_live = liveness
        ctx.store.write(plan)
    except _Skip as skip:
        return skip.code
    except IndexStale:                  # committed; only the PM data worktree's index is stale
        return None
    except NotFound:
        return 'gone'
    except (Busy, Dirty, Overtaken):    # refused before anything landed
        return 'store'
    except Failure:                     # the write may have landed: never assume it did not
        return UNSURE
    except Refusal:
        return 'refused'
    except (omod.PartitionError, ConfigError, OSError):
        return 'store'
    return None


def create_tree(main, branch):
    """new_worktree's `<area>/<slug>` from main as fetched, in the repository's worktrees_dir,
    without waiting for a landing in flight (that is a refusal)."""
    from kitlib.worktree import new
    return new.create(main, branch, wait=False)


def remove_fresh(main, branch):
    """Remove the worktree on `branch` that auto_pull just made, and the branch while its tip is
    still the tree's head: `git worktree remove` (never forced: anything written in it keeps it)
    and `git update-ref -d` against that head. True when both went."""
    from kitlib.worktree import remove_checks
    try:
        trees = remove_checks.worktree_entries(remove_checks.worktree_listing(main))
    except remove_checks.GitReadError:
        return False
    tree = next((t for t in trees if t['branch'] == branch), None)
    if tree is None or not tree['head']:
        return False
    rc, _out, _err = remove_checks.git(main, 'worktree', 'remove', tree['path'])
    if rc != 0:
        return False
    rc, _out, _err = remove_checks.git(main, 'update-ref', '-d', 'refs/heads/' + branch, tree['head'])
    return rc == 0


def auto_pull(settings, key, entry, ids, free, liveness=None, stop=None, create=None, pull=None,
              remove=None):
    """([(id, branch)], [(id, code)]): the coordinator of sessions.json entry `key` gets up to
    `free` of `ids` (its offer, in order) pulled, each on its own new worktree made first
    (`create(main, branch)`, default create_tree) and pulled onto it (`pull(store root, id,
    branch, sessions, liveness)`, default pull_record). A record not pulled has its tree removed
    (`remove(main, branch)`, default remove_fresh; `+tree-kept` when it could not be) and the next
    is tried; an UNSURE pull keeps its tree. A code in TAKES_SLOT (`not-open`: pulled since the
    offer was read, most likely by the coordinator; UNSURE) counts as a slot taken, so the
    coordinator's cap holds when both pull at once. Codes besides pull_record's: `branch`
    (new_worktree would refuse the name), `worktree` (the tree was not made). `stop` set ends it between records. LaunchError or
    coord_restart.BacklogUnread when the repository or its store cannot be found."""
    from kitlib.worktree import new
    create = create or create_tree
    pull = pull or pull_record
    remove = remove or remove_fresh
    repo = (entry or {}).get('repo')
    main = settings.repo_path(repo)
    root = coord_restart.store_root(settings, repo)
    sessions = coord_restart.wake_sessions(key, entry)
    pulled, skipped, taken = [], [], 0
    for rid in ids:
        if len(pulled) + taken >= free or (stop is not None and stop.is_set()):
            break
        branch = fill_branch(key, rid)
        if not new.branch_ok(branch, False):
            skipped.append((rid, 'branch'))
            continue
        try:
            made = create(main, branch)
        except Exception:               # noqa: BLE001 - not made: skipped, the next tried
            made = None
        if made is None or made.code != new.OK:
            skipped.append((rid, 'worktree'))
            continue
        try:
            code = pull(root, rid, branch, sessions, liveness)
        except BaseException:
            remove(main, branch)        # a fault of the pull: its tree goes with it
            raise
        if code is None:
            pulled.append((rid, branch))
            continue
        if code in TAKES_SLOT:
            taken += 1                  # the coordinator's pull, or one that may have landed
        if code == UNSURE or not remove(main, branch):
            code += '+tree-kept'        # unsure: its record may be pulled onto this branch
        skipped.append((rid, code))
    return pulled, skipped


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

    def quiet(key, entry):              # the PM's hold, then usage
        now = job.now()
        held = hold_reason(read_hold(state.path(s, HOLD_FILE)), entry.get('repo'), now)
        if held:
            return held
        try:
            rows = A.week_rows(s, datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
        except Exception:               # noqa: BLE001 - unread readings are unknown: fails open
            rows = None
        return usage_reason(rows)

    def stood_down(key, entry):
        return standdown(s.repo_path(entry.get('repo')))

    def handoffs():                     # sources.handoffs is read at each run: the hub sets it
        read = getattr(sources, 'handoffs', None)
        return read() if read is not None else None

    # read and pull take the run's own session liveness (job.liveness), set before either runs
    job = FillWatch(lambda: state.read_sessions(s), live_names,
                    lambda key, entry: read_slots(s, key, entry, session_live=job.liveness),
                    deliver, claims, standdown=stood_down,
                    store=FileState(state.path(s, STATE_FILE)),
                    pull=lambda key, entry, ids, free, stop: auto_pull(
                        s, key, entry, ids, free, job.liveness, stop),
                    handoffs=handoffs,
                    waits=lambda key, entry: read_waits(s, key, entry, session_live=job.liveness),
                    quiet=quiet)
    return jobs.Job(NAME, EVERY_S, job, first_s=FIRST_S)
