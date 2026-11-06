"""The session gate cap (docs/gate.md "The session gate cap"): before a branch gate books its
machine-lock run, it waits while its agent session already has the configured number of gate runs
of the same repository in flight (booked, waiting or held).

A run is the session's when its recorded ancestors hold the session's nearest agent host
(kitlib.lock.store.owns): a coordinator and its in-process subagents share one host. The owner
(role owner, no agent host above the caller) counts nothing and never waits. An agent with no
readable host counts every gate run of the repository (fail closed). A landing's runs
(priority "land") are not counted; a landing and the daily full run never call wait_turn.
It has two callers: kitlib.gate.runner `gated` (capped=True, from `_gate_checkout` only), which
calls gave_way once its booking holds, and kitlib.gate.rerun `rerun_gate` (a targeted rerun),
which books straight after wait_turn and never calls gave_way: its turn names no run, and the
count nets it against the rerun's own run (a run of the same process that no turn names), so it
counts once; the turn lasts until the gate.py process exits (a reader then removes its file).
A gate counts and takes its turn in one step, under the lock store's MUTEX: with room under the
cap it writes a turn file (TURNS folder) before it returns to book, so the gates of a session
that start together (a burst) count each other's turns, and those over the cap wait before they
book a lock slot. A turn counts as a gate in flight until its run's record stands for it:
gave_way, called once the booking holds, writes the run's id into it, and a booking of the same
process that no turn names (one still queued) stands for one of that process's turns, never
beside it. Gates that wait at the cap book in the order they began waiting: each waiting gate
keeps a file in the lock store's WAITERS folder, and books only when the session's gates in
flight and its gates that began waiting before it leave room under the cap, so a run that ends
lets one waiter book, not every one. Once its booking holds, the gate counts the session's other
gate runs that have started (hold their slots; not those still queued in the lock) again
(gave_way) and gives its slots back while those are at the cap: a backstop for a count that
missed a gate (a record read while it was rewritten, a turn that could not be written, a gate of
a kit without turns).

The cap is the machine config's "coordinator_start".gate_cap {repo name: int}. Unset, 0 or null: no cap. A value the cap cannot use, or a machine
config that cannot be read, is said once as a WARNING and nothing is capped.

The gate queue (docs/gate.md "The gate queue"): a branch gate books with the note queue_note
(its tip and options), so a second gate of the same branch, tip, options and tree that would only
wait behind it is found (queued_twin), and a session's own queued gate of the branch at an older
tip is stopped before it starts (replace_stale). A held run is never touched.
"""
import collections
import contextlib
import os
import random
import re
import threading
import time

from kitlib.config import user as kuser
from kitlib.config.types import ConfigError
from kitlib.fs import replace as fsreplace
from kitlib.gate import budget
from kitlib.lock import api as lockapi
from kitlib.lock import queue as lockqueue
from kitlib.lock import store as lockstore
from kitlib.proc import process

KEY = 'coordinator_start'
CAP_KEY = 'gate_cap'
POLL_S = 5.0        # how often a waiting gate counts again; never below the lock's queue_poll_s
WAITERS = 'gatecap'     # the lock store's folder of gates waiting at the cap: <token>.json each
DROP_WAIT_S = 2.0       # how long a refused remove of a waiter file is retried
_WAITING = set()        # the tokens this process waits under now; its other files are leftovers
TURNS = 'gatecap-turns'     # the lock store's folder of gates that took their turn: <token>.json
MUTEX = 'gatecap'           # the store mutex a gate counts and takes its turn under
_TURNS = set()          # the tokens of this process's turns now; its other turn files are leftovers
_TURN = threading.local()   # .mine: this thread's turn (record, path), or None
NOTE = 'branch gate at '
_NOTE_TIP = re.compile(r'^branch gate at ([0-9a-f]{40})(?: |$)')


def configured(repo, home=None):
    """(cap or None, warning or None) for repo from the machine config. None: no cap."""
    try:
        data = kuser.user_config(home)
    except ConfigError as e:
        return None, 'WARNING: the session gate cap is not applied: %s' % e
    section = data.get(KEY)
    if not isinstance(section, dict):
        return None, None
    caps = section.get(CAP_KEY)
    if caps is None:
        return None, None
    if not isinstance(caps, dict):
        return None, ('WARNING: the session gate cap is not applied: %s.%s is not an object of '
                      'repo name to count' % (KEY, CAP_KEY))
    value = caps.get(repo)
    if value is None:
        value = caps.get(str(repo).lower())
    if value is None or value == 0:
        return None, None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, ('WARNING: the session gate cap is not applied: %s.%s[%s] is %r, not a '
                      'whole number 0 or more' % (KEY, CAP_KEY, repo, value))
    return value, None


def actor():
    """Who is asking (kitlib.lock.store.caller): read from this process's tree."""
    return lockstore.caller()


def _gate_runs(runs, repo):
    """The live gate runs of repo that a cap counts: not a landing's."""
    want = str(repo).lower()
    return [r for r in runs if r.get('gate') and r.get('priority') != 'land'
            and str(r.get('repo') or '').lower() == want]


def _hosted(who):
    """Whether the caller carries an agent host [pid, ctime] that owns() can match."""
    host = lockstore.actor_host(who)
    return isinstance(host, (list, tuple)) and len(host) == 2


def in_flight(store, who, repo, runs=None):
    """The session's live gate runs of repo (records), oldest first. [] for the owner (role owner,
    no agent host). An agent whose host cannot be read (a detached run, a tree or host registry
    that could not be read: kitlib.lock.store.caller) cannot tell its session's runs from
    another's, so every gate run of repo counts as its own (fail closed). A run this process is
    inside (its wrapper is among the caller's ancestors) is not counted: a gate nested in it
    never waits for itself."""
    hosted = _hosted(who)
    if not hosted and isinstance(who, dict) and who.get('role') == 'owner':
        return []
    runs = _gate_runs(store.live_runs() if runs is None else runs, repo)
    inside = [list(a) for a in (who.get('ancestors') if isinstance(who, dict) else None) or []]
    return [r for r in runs if (lockstore.owns(who, r) if hosted else True)
            and [r.get('pid'), r.get('ctime')] not in inside]


def started(r):
    """Whether live run record r has started (holds its slots): any state but `waiting`, a run
    still queued in the machine lock (kitlib.lock.run saves `held` once it takes its slots)."""
    return r.get('state') != 'waiting'


def _order(d):
    """A waiter file's place in line: when it began waiting (ns), then its token."""
    since = d.get('since')
    return (since if isinstance(since, int) else 0, str(d.get('token')))


def _drop(path):
    """Removes a waiter file, waiting out a reader that has it open (Windows refuses the remove
    then: kitlib.fs.replace.remove retries for DROP_WAIT_S); a remove still refused is let be."""
    try:
        fsreplace.remove(path, wait=DROP_WAIT_S)
    except OSError:
        pass


def _repo_files(store, repo, folder=WAITERS, live_tokens=None):
    """Every live file of repo in `folder` (waiter files, or turn files: TURNS with _TURNS),
    oldest first. A file whose process is surely gone is removed; one whose process cannot be
    told live is skipped, so a dead gate never holds the line. So is one this process left (its
    pid and creation time, a token not in live_tokens: a remove that was refused), which it
    removes again."""
    live_tokens = _WAITING if live_tokens is None else live_tokens
    folder = store.path(folder)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    want, me, out = str(repo).lower(), [os.getpid(), process.own_ctime()], []
    for n in names:
        if not n.endswith('.json'):
            continue
        p = os.path.join(folder, n)
        d = lockstore.read_json(p)
        if d is None:
            continue
        live = lockstore.record_state(d)
        if live is False or ([d.get('pid'), d.get('ctime')] == me
                             and d.get('token') not in live_tokens):
            _drop(p)
            continue
        if live is True and str(d.get('repo') or '').lower() == want:
            out.append(d)
    return sorted(out, key=_order)


def _sessions(who, files):
    """The files (waiter or turn records) the caller's session counts, as in_flight counts runs:
    [] for the owner, the session's own by owns(), every one for an agent with no readable
    host."""
    hosted = _hosted(who)
    if not hosted and isinstance(who, dict) and who.get('role') == 'owner':
        return []
    return [d for d in files if not hosted or lockstore.owns(who, d)]


def waiters(store, who, repo):
    """The gates waiting at the cap for repo that the caller's session counts, oldest first."""
    return _sessions(who, _repo_files(store, repo))


def count(store, who, repo):
    """(runs, turns): the session's live gate runs of repo (in_flight), and the number of its
    gates that took their turn (a turn file, TURNS) and that no run of theirs stands for yet.
    A turn that names its run (gave_way wrote it) is that run. A turn that names none stands
    beside a run of the same process that no turn names (a booking still queued, or a rerun's
    whole run) only by as many as that process has turns over such runs, so a gate is never
    counted twice; with one gate per process that is exact, and threads of one process each keep
    their own turn. A turn of a process the caller is inside is not counted (in_flight)."""
    runs = in_flight(store, who, repo)
    inside = [list(a) for a in (who.get('ancestors') if isinstance(who, dict) else None) or []]
    turns = [t for t in _sessions(who, _repo_files(store, repo, TURNS, _TURNS))
             if [t.get('pid'), t.get('ctime')] not in inside]
    named = {t.get('run') for t in turns if t.get('run')}
    free = collections.Counter((t.get('pid'), t.get('ctime')) for t in turns if not t.get('run'))
    loose = collections.Counter((r.get('pid'), r.get('ctime')) for r in runs
                                if r.get('id') not in named)
    return runs, sum(max(0, k - loose[p]) for p, k in free.items())


def _take(store, who, repo):
    """Writes this thread's turn file (TURNS): a gate admitted under the cap, counted as in
    flight until its run stands for it. One that cannot be written is let be: the gate books
    uncounted until its record is written, as before turns."""
    token = '%d-%s' % (os.getpid(), os.urandom(6).hex())
    rec = {'token': token, 'repo': repo, 'since': time.time_ns(), 'pid': os.getpid(),
           'ctime': process.own_ctime(), 'run': None,
           'ancestors': list((who.get('ancestors') if isinstance(who, dict) else None) or [])}
    path = store.path(TURNS, token + '.json')
    _TURNS.add(token)
    try:
        lockstore.create_json(path, rec)
    except OSError:
        _TURNS.discard(token)
        return
    _TURN.mine = (rec, path)


def _leave():
    """Removes this thread's turn file, if any: its gate has ended or waits its turn again."""
    mine, _TURN.mine = getattr(_TURN, 'mine', None), None
    if mine:
        _TURNS.discard(mine[0]['token'])    # a file left by a refused remove is now a leftover
        _drop(mine[1])


def _bind(run_id):
    """Writes run_id into this thread's turn file: its booking holds, so the run is the turn."""
    mine = getattr(_TURN, 'mine', None)
    if not mine or mine[0].get('run'):
        return
    rec = dict(mine[0], run=run_id)
    try:
        lockstore.write_json(mine[1], rec)
    except OSError:
        return          # left naming no run: it stands for its process's run while that lives
    _TURN.mine = (rec, mine[1])


def _guarded(store, step, say):
    """step() under the store mutex MUTEX; unguarded when the mutex is not had in time (within
    the store's store_lock_s), as the count was before it, and then say(line) once."""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(store.lock(MUTEX))
        except OSError as e:
            say('the gate counts for the session gate cap without the store mutex %r (%s): gates '
                'of the session that count at the same moment may book past the cap'
                % (MUTEX, e))
        return step()


def _join(store, who, repo, ahead):
    """Writes this gate's waiter file, after every one in `ahead`; (record, path), or (None, None)
    when it cannot be written: the gate then waits behind every waiter it sees."""
    since = max([time.time_ns()] + [d['since'] + 1 for d in ahead
                                    if isinstance(d.get('since'), int)])
    token = '%d-%s' % (os.getpid(), os.urandom(6).hex())
    rec = {'token': token, 'repo': repo, 'since': since, 'pid': os.getpid(),
           'ctime': process.own_ctime(),
           'ancestors': list((who.get('ancestors') if isinstance(who, dict) else None) or [])}
    path = store.path(WAITERS, token + '.json')
    _WAITING.add(token)
    try:
        lockstore.create_json(path, rec)
    except OSError:
        _WAITING.discard(token)
        return None, None
    return rec, path


def wait_turn(store, repo, say, cap=None, who=None, home=None, poll_s=None, sleep=None):
    """Returns once this gate has taken its turn: the session's gates of repo in flight (its
    gate runs, and its gates that took their turn and have not booked: count) and its gates that
    began waiting at the cap before this one number fewer than the cap. The count and the turn
    (a turn file, TURNS) are one step under the store mutex MUTEX, so gates of one session that
    start together (a burst) count each other and no more than the cap book; the rest wait
    before they book. Gates waiting at the cap book in the order they began (a waiter file each,
    WAITERS), so a gate that ends lets one book, not all. Waiting, it says one line naming the
    runs in flight and nothing per poll; its waiter file is removed as it returns or raises.
    This thread's turn from before (a gate that gave its slot back, or ended) is removed first.
    The cap is read first: with none, the caller is not read and no turn is taken; the owner
    takes none either. A mutex not had within the store's store_lock_s: it counts without it,
    as before turns, and says so in one line."""
    _leave()
    if cap is None:
        cap, warning = configured(repo, home)
        if warning:
            say(warning)
    if not cap:
        return
    who = actor() if who is None else who
    if not _hosted(who) and isinstance(who, dict) and who.get('role') == 'owner':
        return

    def first():
        mine, turns = count(store, who, repo)
        ahead = waiters(store, who, repo)
        if len(mine) + turns + len(ahead) < cap:
            _take(store, who, repo)
            return None
        return mine, turns, ahead, _join(store, who, repo, ahead)
    unguarded = []

    def once(line):                     # the mutex timed out: said once per wait, not per poll
        if not unguarded:
            unguarded.append(line)
            say(line)
    waiting = _guarded(store, first, once)
    if waiting is None:
        return
    mine, turns, ahead, (me, path) = waiting
    whose = ('of this session' if _hosted(who) else
             '(all counted: this agent\'s host cannot be read)')
    ids = [str(r.get('id')) for r in mine] + (['%d about to book' % turns] if turns else [])
    say('the gate waits for the session gate cap: %d of %d gate runs %s in %s are in flight '
        '(%s)%s; it books when one ends and the gates that began waiting before it have '
        'booked' % (len(mine) + turns, cap, whose, repo, ', '.join(ids) or 'none',
                    ', and %d gate(s) wait ahead of it' % len(ahead) if ahead else ''))
    interval = max(poll_s or POLL_S, store.settings.queue_poll_s)
    sleep = sleep or time.sleep

    def again():
        runs, more = count(store, who, repo)
        n = len(runs) + more
        if n >= cap:
            return False
        older = [d for d in waiters(store, who, repo) if me is None or _order(d) < _order(me)]
        if n + len(older) >= cap:
            return False
        _take(store, who, repo)
        return True
    try:
        while True:
            sleep(interval)
            if _guarded(store, again, once):
                return
    finally:
        if path:
            _WAITING.discard(me['token'])   # a file left by a refused remove is now a leftover
            _drop(path)


def gave_way(store, repo, slot, say, cap=None, who=None, home=None, poll_s=None, sleep=None):
    """Called once a branch gate's booking holds its slots (kitlib.lock.run Slot `slot`), before
    anything runs. It first writes the run's id into this thread's turn file (wait_turn): the run
    stands for the turn from now on. A backstop for a count that missed a gate (a record read
    while it was rewritten, a turn that could not be written, a gate of a kit without turns):
    the session's other gate runs of repo that
    have started (hold their slots: started(); a run still queued in the lock is not counted, so
    a gate is never held back at its turn by siblings queued behind it) are counted again, now
    that this one's record is visible to theirs: at the cap, this
    gate gives its slots back (released `not started`, which the branch gate budget does not
    count), says one line, pauses a random part of a poll (two that gave way together do not book
    together again) and returns True; the caller waits its turn again and books again, unless a
    stop came while the run was given back (stopped): then it pauses nothing and the caller ends
    the gate stopped. False: it runs. A run already asked to stop is never given back (False): a
    give-back would settle the run and drop the stop, and the gate would book again under a new
    id; the caller's own stop check ends it. Of gates that hold together, the one that counts
    last sees every other's record it can read: a soft limit, since a record being rewritten can
    read as missing (Store.live_runs), so a count can miss a live gate. The caller and the cap
    are read only when another gate run of repo has started."""
    _bind(slot.id)
    others = [r for r in _gate_runs(store.live_runs(), repo)
              if r.get('id') != slot.id and started(r)]
    if not others:
        return False
    if cap is None:
        cap, warning = configured(repo, home)
        if warning:
            say(warning)
    if not cap:
        return False
    who = actor() if who is None else who
    mine = [r for r in in_flight(store, who, repo) if r.get('id') != slot.id and started(r)]
    if len(mine) < cap:
        return False
    whose = ('of this session' if _hosted(who) else
             '(all counted: this agent\'s host cannot be read)')
    if slot.stop_asked():
        return False
    say('the gate gives back run %s for the session gate cap: %d of %d other gate runs %s in %s '
        'have started (%s); it books again when one ends' % (
            slot.id, len(mine), cap, whose, repo, ', '.join(str(r.get('id')) for r in mine)))
    slot.release(1, budget.NOT_STARTED)
    if stopped(slot):
        return True
    interval = max(poll_s or POLL_S, store.settings.queue_poll_s)
    (sleep or time.sleep)(random.uniform(0, interval))
    return True


def stopped(slot):
    """Whether released `slot`'s run was asked to stop before it ended: Run.finish keeps the stop
    request it found as the ended record's `stopped_by`, then removes the request."""
    return bool((getattr(slot, 'ended', None) or {}).get('stopped_by'))


# ---------------------------------------------------------------- the gate queue

def queue_note(head, full=False, only=None, base=None):
    """The note a branch gate's lock run is booked with: `branch gate at <head>`, then its options
    as given (` --full`, ` --only a,b`, ` --base X`). Two gates of one note gate the same thing.
    Never `landing: ...` (kitlib.gate.stop reads that prefix as a landing's run)."""
    note = NOTE + head
    if full:
        note += ' --full'
    if only:
        note += ' --only ' + (only if isinstance(only, str) else ','.join(only))
    if base:
        note += ' --base %s' % base
    return note


def note_tip(note):
    """The tip of a queue_note, or None for any other note (a rerun's none, a landing's)."""
    m = _NOTE_TIP.match(note) if isinstance(note, str) else None
    return m.group(1) if m else None


def _waiting(store, rid):
    """Whether run rid's queue entry waits for its slots (state `waiting`). An entry that holds,
    none, or a queue that cannot be read: False, so nothing is refused or stopped on it."""
    try:
        e = lockqueue.entry_of(lockqueue.load(store), rid)
    except (OSError, ValueError):
        return False
    return bool(e) and e.get('state') == 'waiting'


def queued_twin(store, repo, branch, note, tree, runs=None):
    """The id of a live gate run of repo that waits in the queue (its entry `waiting`) for the
    same branch, note (queue_note) and tree (runner.tested_tree, not None), or None. Such a run
    gates exactly what this gate would: a second booking only waits behind it. A run already
    asked to stop (gate.py stop, the stale-run reap, replace_stale) is about to end with nothing
    run, so it is no twin. `runs`: the live runs, when already read. A soft check: the queue is
    read once, before this gate books."""
    if not (branch and note and tree):
        return None
    for r in _gate_runs(store.live_runs() if runs is None else runs, repo):
        rid = r.get('id')
        if (r.get('branch') == branch and r.get('note') == note and r.get('tree') == tree
                and rid and _waiting(store, rid) and not lockapi.stop_asked(store, rid)):
            return rid
    return None


def replace_stale(store, repo, branch, head, say, who=None):
    """Stops the session's own queued branch gates of `branch` booked at another tip than `head`
    (their note's tip, note_tip): a gate of the branch's old tip that has not started is not
    needed once the branch moved. Only a run whose queue entry still waits is stopped
    (kitlib.lock.api.stop_waiting: checked and written under the queue mutex the lock starts runs
    under), so its gate ends stopped with nothing run, or `cancelled` with nothing run when it
    started holding just after; a held run is never touched. Another session's runs, a rerun's or
    a landing's (no queue_note), and every run when the caller's host cannot be read are left
    alone. Says one line per run stopped, or per run that ended before its stop was written. The
    ids stopped. Never raises."""
    done = []
    try:
        if not branch or not head:
            return done
        runs = [r for r in _gate_runs(store.live_runs(), repo) if r.get('branch') == branch
                and note_tip(r.get('note')) not in (None, head)]
        if not runs:
            return done
        who = actor() if who is None else who
        if not _hosted(who):
            return done
        by = who.get('name') or who.get('session')
        for r in in_flight(store, who, repo, runs=runs):
            rid = r.get('id')
            asked = lockapi.stop_waiting(store, rid, by, who.get('session'))
            if asked is None:
                continue
            old = note_tip(r.get('note'))[:10]
            if asked:
                done.append(rid)
                say('the gate replaces queued run %s of %s at %s (the branch is at %s now): it is '
                    'stopped before it starts' % (rid, branch, old, head[:10]))
            else:
                say('the gate could not replace queued run %s of %s at %s: it has ended' % (
                    rid, branch, old))
    except Exception as e:          # noqa: BLE001 - a stale run left queued never fails the gate
        say('the gate could not check for its queued runs at an older tip: %s: %s' % (
            type(e).__name__, e))
    return done
