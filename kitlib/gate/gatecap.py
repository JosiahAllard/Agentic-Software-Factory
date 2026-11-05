"""The session gate cap (docs/gate.md "The session gate cap"): before a branch gate books its
machine-lock run, it waits while its agent session already has the configured number of gate runs
of the same repository in flight (booked, waiting or held).

A run is the session's when its recorded ancestors hold the session's nearest agent host
(kitlib.lock.store.owns): a coordinator and its in-process subagents share one host. The owner
(role owner, no agent host above the caller) counts nothing and never waits. An agent with no
readable host counts every gate run of the repository (fail closed). A landing's runs
(priority "land") are not counted; a landing and the daily full run never call wait_turn
(kitlib.gate.runner `gated`, with capped=True from `_gate_checkout` only, is the one caller).
Gates that wait at the cap book in the order they began waiting: each waiting gate keeps a file
in the lock store's WAITERS folder, and books only when the session's runs in flight and its
gates that began waiting before it leave room under the cap, so a run that ends lets one waiter
book, not every one. Counting and booking are two steps, so once its booking holds, the gate
counts the session's other gate runs that have started (hold their slots; not those still
queued in the lock) again (gave_way) and gives its slots back while those are at the cap.

The cap is the machine config's "coordinator_start".gate_cap {repo name: int}. Unset, 0 or null: no cap. A value the cap cannot use, or a machine
config that cannot be read, is said once as a WARNING and nothing is capped.
"""
import os
import random
import time

from kitlib.config import user as kuser
from kitlib.config.types import ConfigError
from kitlib.fs import replace as fsreplace
from kitlib.gate import budget
from kitlib.lock import store as lockstore
from kitlib.proc import process

KEY = 'coordinator_start'
CAP_KEY = 'gate_cap'
POLL_S = 5.0        # how often a waiting gate counts again; never below the lock's queue_poll_s
WAITERS = 'gatecap'     # the lock store's folder of gates waiting at the cap: <token>.json each
DROP_WAIT_S = 2.0       # how long a refused remove of a waiter file is retried
_WAITING = set()        # the tokens this process waits under now; its other files are leftovers


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


def _repo_waiters(store, repo):
    """Every live waiter file of repo, oldest first. A file whose process is surely gone is
    removed; one whose process cannot be told live is skipped, so a dead waiter never holds the
    line. So is one this process left (its pid and creation time, a token it no longer waits
    under: a remove that was refused), which it removes again."""
    folder = store.path(WAITERS)
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
                             and d.get('token') not in _WAITING):
            _drop(p)
            continue
        if live is True and str(d.get('repo') or '').lower() == want:
            out.append(d)
    return sorted(out, key=_order)


def waiters(store, who, repo):
    """The gates waiting at the cap for repo that the caller's session counts, oldest first, as
    in_flight counts runs: [] for the owner, the session's own by owns(), every one for an agent
    with no readable host."""
    hosted = _hosted(who)
    if not hosted and isinstance(who, dict) and who.get('role') == 'owner':
        return []
    return [d for d in _repo_waiters(store, repo) if not hosted or lockstore.owns(who, d)]


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
    """Returns once the session's gate runs of repo in flight, and its gates that began waiting
    at the cap before this one, number fewer than the cap: gates waiting at the cap book in the
    order they began (a waiter file each, WAITERS), so a run that ends lets one book, not all.
    Waiting, it says one line naming the runs in flight and nothing per poll; its waiter file is
    removed as it returns or raises. The caller is read only when some gate run or waiting gate
    of repo is live, and the cap only when one is the session's, so a gate with neither reads
    neither."""
    if not _gate_runs(store.live_runs(), repo) and not _repo_waiters(store, repo):
        return
    who = actor() if who is None else who
    mine = in_flight(store, who, repo)
    ahead = waiters(store, who, repo)
    if not mine and not ahead:
        return
    if cap is None:
        cap, warning = configured(repo, home)
        if warning:
            say(warning)
    if not cap:
        return
    if len(mine) + len(ahead) < cap:
        return
    whose = ('of this session' if _hosted(who) else
             '(all counted: this agent\'s host cannot be read)')
    say('the gate waits for the session gate cap: %d of %d gate runs %s in %s are in flight '
        '(%s)%s; it books when one ends and the gates that began waiting before it have '
        'booked' % (len(mine), cap, whose, repo,
                    ', '.join(str(r.get('id')) for r in mine) or 'none',
                    ', and %d gate(s) wait ahead of it' % len(ahead) if ahead else ''))
    interval = max(poll_s or POLL_S, store.settings.queue_poll_s)
    sleep = sleep or time.sleep
    me, path = _join(store, who, repo, ahead)
    try:
        while True:
            sleep(interval)
            n = len(in_flight(store, who, repo))
            if n >= cap:
                continue
            older = [d for d in waiters(store, who, repo)
                     if me is None or _order(d) < _order(me)]
            if n + len(older) < cap:
                return
    finally:
        if path:
            _WAITING.discard(me['token'])   # a file left by a refused remove is now a leftover
            _drop(path)


def gave_way(store, repo, slot, say, cap=None, who=None, home=None, poll_s=None, sleep=None):
    """Called once a branch gate's booking holds its slots (kitlib.lock.run Slot `slot`), before
    anything runs. wait_turn counts and the booking is made after it, so gates of one session that
    counted together can all have booked past the cap. The session's other gate runs of repo that
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
