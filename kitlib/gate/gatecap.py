"""The session gate cap (docs/gate.md "The session gate cap"): before a branch gate books its
machine-lock run, it waits while its agent session already has the configured number of gate runs
of the same repository in flight (booked, waiting or held).

A run is the session's when its recorded ancestors hold the session's nearest agent host
(kitlib.lock.store.owns): a coordinator and its in-process subagents share one host. The owner
(role owner, no agent host above the caller) counts nothing and never waits. An agent with no
readable host counts every gate run of the repository (fail closed). A landing's runs
(priority "land") are not counted; a landing and the daily full run never call wait_turn
(kitlib.gate.runner `gated`, with capped=True from `_gate_checkout` only, is the one caller).
Counting and booking are two steps, so once its booking holds, the gate counts again (gave_way)
and gives its slots back while the session is at the cap.

The cap is the machine config's "coordinator_start".gate_cap {repo name: int}. Unset, 0 or null: no cap. A value the cap cannot use, or a machine
config that cannot be read, is said once as a WARNING and nothing is capped.

The gate queue (docs/gate.md "The gate queue"): a branch gate books with the note queue_note
(its tip and options), so a second gate of the same branch, tip, options and tree that would only
wait behind it is found (queued_twin), and a session's own queued gate of the branch at an older
tip is stopped before it starts (replace_stale). A held run is never touched.
"""
import random
import re
import time

from kitlib.config import user as kuser
from kitlib.config.types import ConfigError
from kitlib.gate import budget
from kitlib.lock import api as lockapi
from kitlib.lock import queue as lockqueue
from kitlib.lock import store as lockstore

KEY = 'coordinator_start'
CAP_KEY = 'gate_cap'
POLL_S = 5.0        # how often a waiting gate counts again; never below the lock's queue_poll_s
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


def wait_turn(store, repo, say, cap=None, who=None, home=None, poll_s=None, sleep=None):
    """Returns once the session has fewer than the cap gate runs of repo in flight. Waiting, it
    says one line naming the runs in flight and nothing per poll. The cap and the caller are read
    only when some gate run of repo is live, so a gate with none in flight reads neither."""
    if not _gate_runs(store.live_runs(), repo):
        return
    who = actor() if who is None else who
    mine = in_flight(store, who, repo)
    if not mine:
        return
    if cap is None:
        cap, warning = configured(repo, home)
        if warning:
            say(warning)
    if not cap or len(mine) < cap:
        return
    whose = ('of this session' if _hosted(who) else
             '(all counted: this agent\'s host cannot be read)')
    say('the gate waits for the session gate cap: %d of %d gate runs %s in %s are '
        'in flight (%s); it books when one ends' % (
            len(mine), cap, whose, repo, ', '.join(str(r.get('id')) for r in mine)))
    interval = max(poll_s or POLL_S, store.settings.queue_poll_s)
    sleep = sleep or time.sleep
    while True:
        sleep(interval)
        if len(in_flight(store, who, repo)) < cap:
            return


def gave_way(store, repo, slot, say, cap=None, who=None, home=None, poll_s=None, sleep=None):
    """Called once a branch gate's booking holds its slots (kitlib.lock.run Slot `slot`), before
    anything runs. wait_turn counts and the booking is made after it, so gates of one session that
    counted together can all have booked past the cap. The session's other gate runs of repo in
    flight are counted again, now that this one's record is visible to theirs: at the cap, this
    gate gives its slots back (released `not started`, which the branch gate budget does not
    count), says one line, pauses a random part of a poll (two that gave way together do not book
    together again) and returns True; the caller waits its turn again and books again, unless a
    stop came while the run was given back (stopped): then it pauses nothing and the caller ends
    the gate stopped. False: it runs. A run already asked to stop is never given back (False): a
    give-back would settle the run and drop the stop, and the gate would book again under a new
    id; the caller's own stop check ends it. Of gates that hold together, the one that counts
    last sees every other's record it can read: a soft limit, since a record being rewritten can
    read as missing (Store.live_runs), so a count can miss a live gate. The caller and the cap
    are read only when another gate run of repo is live."""
    others = [r for r in _gate_runs(store.live_runs(), repo) if r.get('id') != slot.id]
    if not others:
        return False
    if cap is None:
        cap, warning = configured(repo, home)
        if warning:
            say(warning)
    if not cap:
        return False
    who = actor() if who is None else who
    mine = [r for r in in_flight(store, who, repo) if r.get('id') != slot.id]
    if len(mine) < cap:
        return False
    whose = ('of this session' if _hosted(who) else
             '(all counted: this agent\'s host cannot be read)')
    if slot.stop_asked():
        return False
    say('the gate gives back run %s for the session gate cap: %d of %d other gate runs %s in %s '
        'are in flight (%s); it books again when one ends' % (
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
