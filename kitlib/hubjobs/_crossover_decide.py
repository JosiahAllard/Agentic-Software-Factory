"""kitlib.hubjobs._crossover_decide: the usage crossover's decisions, made by name for
kitlib.hubjobs.usage_crossover, the timed job that reads the files and processes and acts on
them (docs/launcher.md "Usage crossover"). Its name starts with '_', so kitlib.hubjobs.makers()
does not take it for a job.

  decide(key, entry, crossing, live, stall, quiet_s, accounts, newest, starts, running, landed,
         mode, now, note_at, cleared, asks, weeks) -> Decision
                                     one session's next step, pure: no file, process or clock read
  decide_pm(pms, crossing, stall, quiet_s, accounts, newest, starts, running, landed, mode, now,
            note_at, cleared, asks, points, recorded, names) -> Decision
                                     the PM's next step, pure (module docstring "The PM")
  decide_unlisted(key, sess, crossing, live, stall, quiet_s, accounts, starts, running, landed,
                  mode, now, files, cleared, asks, weeks) -> Decision
                                     an unlisted session's next step, pure ("Unlisted sessions")
  burn_eta(points, now) -> seconds | None
                                     when a week reaches 100% at its current burn rate, pure
  pick_target(accounts, current, running, landed, now, tier2) -> (account, why) | None

The trigger is the weekly window only (the plan gives no five-hour readings, and a five_hour a
file still holds is never read): a reading no older than
pmwatch.STALE_MINUTES at TRIGGER_WEEK% or more, or earlier, a fresh week at EARLY_FLOOR% or more
that burn_eta, from the account's readings of the last BURN_SPAN_S, puts at 100% within LEAD_S;
or a stall read: the session transcript's newest entry is a weekly-limit error (the host's
weekly_stop, not pm_watch's LIMIT event, which can lag an hour) whose reset lies ahead, with no
transcript write for QUIET_S.

A crossing asks first: the job pushes the session a checkpoint message and waits for its ready
record (hub_handoff_ready; kitlib.hubsite.api "Hand-offs"), newer by C-number than the newest it
noted when it asked. The states of a crossing, each written to disk before its action, so a hub
restart resolves each from live facts:

  asked       not delivered: deliver again. A newer `handoff` or `restart` record: ready. A newer
              `stop` record: cleared, the PM told. WAIT_READY_S with nothing: `no-ready` once, and
              it stays open. The week under EARLY_FLOOR% (a reset) or the session gone: cleared.
              The session stalled and the mode not full: cleared with `stalled`, left to the PM
              (it cannot reply).
  ready       waits until the record's state note was written after the ask and the transcript
              has been quiet QUIET_S (WAIT_READY_S of it: `no-ready` once); then notes the highest
              C-number, counts the start, then `launch.py handoff --session K --account N --state
              <path>` (no --state, and no wait, on the stall path: the entry's note). The entry
              moved to the job's own successor name with no successor running: failed.
  starting    successor live and sessions.json moved: started. Successor live and the entry not
              moved: the child died between the two, so the job finishes the file write and never
              runs handoff again. No successor after START_WAIT_S: backoff, then ready.
  started     a newer `checkin` record (the successor's prompt tells it to post one): stopping.
              WAIT_CHECKIN_S with nothing: `no-checkin` once; the old session stays idle, and
              nothing is ever stopped on a timeout. The old session gone: done.
  stopping    `launch.py stop --name old --successor new --pid P`; the old session gone: done. A
              stop is counted before its child: after a refused one, BACKOFF_S; STOP_TRIES of
              them: failed, the old session stays (the stall stop the same).
  stall-stop  on `full`, from any state before stopping, when the old session is stalled:
              `launch.py stop --name old --pid P`; then ready, with `stalled` set, starting on the
              entry's own note. A successor already up is named to stop, and the crossing goes
              back to started. An old session no longer stalled is not stopped (a hub restart can
              reach this state late): back to started, or cleared with no successor.

Before a start, an entry whose name differs from the one noted was moved by someone else: cleared.
A crossing clears on done, abort or failure, with a push; the file then holds no claim. One that
clears with the session left where it was (ready to stop, its successor gone, its start's entry
write with no successor, its stop refused, an unknown state) is remembered for its key as
{account, reset}: the trigger asks it nothing more on that account until that week resets. A
stall on normal is still pushed (`stalled`); on full such a key is neither stopped nor pushed.

Holds, nothing done: the PM session; no entry, stem or state note; several processes of the stem;
the old process's pid or creation time changed; a stale reading with no stall read; a reset within
RESET_NEAR_S; no room on any account (no stop and no start: `no-room` once per change, and a stalled
session stays and wakes at its reset); KEY_CAP starts for the key or MACHINE_CAP on the machine in
CAP_WINDOW_S, counted before the child runs; ASK_CAP new crossings asked for the key in
CAP_WINDOW_S (`cross-failed`); BACKOFF_S after a failed start; a stall read with
activity under QUIET_S old. No second start goes to an account until its reading is newer than the
job's last start there.

The target (pick_target; never `auto`, never accounts.pick): tier 1, a signed-in account under
TARGET_LIMIT% of its week whose week resets first; tier 2, the lowest week under TIER2_WEEK%. Not
the session's account, nor the account sessions.json still names for it (handoff refuses that
one). The week is accounts.week_state's (week_window): a recorded reset passed by
accounts.RESET_GRACE_S is 0% to a week on, fresh or stale, with or without a live session there,
since the hub computes it. A stale reading whose week has not reset counts only with no live
session on that account: an idle account's reading does not refresh. An unknown week never
counts. With no target, `no-room` names in its detail the accounts left out for an unknown or
stale reading, <key>:unknown:a1.a2 (no_room); detail <key> alone: every one is full.

The PM (decide_pm; docs/launcher.md "PM crossover") moves too, under the state key PM_DOC and the
push detail `pm`. Its process is the launched session coordinators.json names (pm_name, else
pm-session or its pm_session_id as the --resume or --session-id value); its account is pm_account. With no
pm_session_id or pm_account recorded, pm-crossover cannot move it: `pm-limit` once per change. A conversation
cannot resume on another account, so its successor starts fresh from its hand-off file through
`launch.py pm-crossover`, which confirms the new session and writes coordinators.json. The trigger
is a session's (the early one included). The target is tier 1 only (no account under TARGET_LIMIT% of its week: `no-room pm`). Its states:

  asked       the PM is pushed a checkpoint message (PM_CHECKPOINT: update HANDOFF.md, post
              hub_handoff_ready from pm with its path); as a session's, but a stall on full goes to
              ready with `stalled` set, since nothing is stopped first.
  ready       as a session's (the hand-off file written after the ask, the transcript quiet);
              then `launch.py pm-crossover --account N --name NEW [--state FILE]` (no --state when
              stalled: the new PM reads the hand-off file as it is). No room on tier 1: cleared
              with `no-room pm`, so the PM resumes and a later trigger asks again. The week reset
              (and no stall): cleared, as in asked.
  starting    coordinators.json naming NEW on account N: done (`crossed`), and the old PM's
              process is kept as pm_old, never counted unmanaged, until it ends; the owner closes
              its window. NEW running and not named: `cross-failed`, remembered. Nothing after
              START_WAIT_S: backoff, then ready.

In every state, coordinators.json naming another PM session than the one asked clears it. A stall
on normal is pushed `stalled` and left to the owner; on full the start goes ahead with no ask. The
caps, holds and remembered clears are a session's. A PM that is not a launched process (the
desktop app) is not moved: a `pm-limit` push, once per week window. A live session of no
sessions.json entry that is not an unlisted one below gets an `unmanaged` push the same way,
detail <name>:<reason> (kitlib.launcher.unlisted.REASONS), and is left alone; each run's note
lists them as never crossed. Every PM crossing
that ends without `crossed` pushes cross-cleared, cross-failed, no-room or stalled with detail
`pm`, and each of those texts tells the PM to resume its normal work.

Unlisted sessions (decide_unlisted): a consultant or deep-worker launch.py started, with no
sessions.json entry, read back from its window by kitlib.launcher.unlisted (its account, model,
repository, brief, requester, notes file). Its key: its name, a deep-worker's without the -a<N> of
a successor. It has no hand-off record to post, so it hands off through files in
<agent temp>/usage-crossover/<key>/ (the window's AGENT_TEMP, else the job's `temp`): the
checkpoint message (DEEP_CHECKPOINT, CONSULTANT_CHECKPOINT) names `ready`, which the session
writes when it has checkpointed, and its successor's first message names `checkin` (launch.py
--checkin). A file counts only when written after the ask (`ready`) or the start (`checkin`), and
a new crossing first sets the folder's ready, checkin and state.md aside as <file>.prev. The
note: a deep-worker's state note `state.md` in that folder, a consultant's own notes file. The
trigger, caps, target and remembered clears are a session's; the states differ thus:

  asked       the ready file stands for the ready record; there is no `stop` reply.
  ready       as a session's, the note for the state note; then a deep-worker starts at once, and a
              consultant (one session per name) goes to stop-first.
  stop-first  a consultant's `launch.py stop --name old --pid P` (no successor); the old session
              gone: ready, `stopped` set. A stopped crossing at a cap waits (`cross-failed` cap)
              rather than clearing: nothing else runs.
  starting    `launch.py deep ... --predecessor old --state state.md --checkin FILE` (named
              <key>-a<target>, in the old worktree) or `launch.py consultant ... --checkin FILE`
              (the same name), --force; its successor live: started.
  started     a deep-worker as a session's, on the check-in file. A consultant: the check-in file,
              `crossed`; WAIT_CHECKIN_S with none, `no-checkin` and cleared (the old one is gone).

A crossing that clears with its old session running and told to checkpoint delivers RESUME. On
notify, `would-cross` runs `launch.py deep|consultant ... --plan` and `stop --dry-run`. Several
unlisted sessions of one key with no crossing open: none crosses (`several`).
"""
import collections
import datetime
import math
import re

from kitlib import pmwatch
from kitlib.hubjobs import coord_restart as C
from kitlib.launcher import accounts as A
from kitlib.launcher import unlisted as UL
from kitlib.launcher.settings import NOTIFY, FULL

TRIGGER_WEEK = 99              # % of the week; a five-hour window is never read
TARGET_LIMIT = A.HEADROOM_LIMIT
TIER2_WEEK = 98
RESET_NEAR_S = 60 * 60         # a reset this close is waited for
QUIET_S = 60                   # no transcript write this long: a stall read is a stall
WAIT_READY_S = 30 * 60         # after asking: `no-ready`
WAIT_CHECKIN_S = 30 * 60       # after the start: `no-checkin`
START_WAIT_S = 5 * 60          # no successor this long after a start: failed
KEY_CAP = 3                    # starts per key in CAP_WINDOW_S
ASK_CAP = 3                    # new crossings asked per key in CAP_WINDOW_S
MACHINE_CAP = 6                # starts on the machine in CAP_WINDOW_S
BACKOFF_S = C.BACKOFF_S
STOP_TRIES = len(BACKOFF_S)    # stops of one old session run, each after the last one's backoff
CAP_WINDOW_S = C.CAP_WINDOW_S
PM_DOC = '_pm'                 # the PM's key in the state file (no sessions.json key starts with _)
PM_DETAIL = 'pm'               # its push detail
EARLY_FLOOR = 95               # % of the week: the early trigger needs at least this
LEAD_S = 20 * 60               # a session or the PM crosses early when its week would reach 100% this soon
BURN_SPAN_S = 2 * 3600         # the readings burn_eta reads
BURN_MIN_S = 10 * 60           # fewer minutes of readings than this: no burn rate

ASKED, READY, STARTING, STARTED, STOPPING, STALL_STOP = (
    'asked', 'ready', 'starting', 'started', 'stopping', 'stall-stop')
STOP_FIRST = 'stop-first'      # an unlisted consultant: one session per name, so the old one stops before the start
# What a Decision does. The first six act, one per run, in this order
STOP_STALLED, STOP_OLD, FINISH, START, ASK, WOULD = (
    'stop-stalled', 'stop-old', 'finish', 'start', 'ask', 'would')
ADVANCE, CLEAR, HOLD = 'advance', 'clear', 'hold'
ACTS = (STOP_STALLED, STOP_OLD, FINISH, START, ASK, WOULD)
READY_KINDS = ('handoff', 'restart')

Live = collections.namedtuple('Live', 'name pid created')
PmLive = collections.namedtuple('PmLive', 'name pid created account')
Recorded = collections.namedtuple('Recorded', 'sid name account')     # coordinators.json's PM
_C_NO = re.compile(r'^C-(\d+)\Z')


class Decision(object):
    """`action` one of ACTS, ADVANCE, CLEAR, HOLD. `note` the text for health and the log;
    `target` the account a start or a would-cross goes to; `word`, `detail` the push and `mark`
    what says it changed (default the word); `phase`, `fields` an ADVANCE's next state, or a
    CLEAR's {account, reset} to remember (`remember` asks _step to fill them)."""

    def __init__(self, action, note, target=None, word=None, detail=None, mark=None, phase=None,
                 fields=None, remember=False):
        self.action, self.note, self.target = action, note, target
        self.word, self.detail, self.mark = word, detail, mark or word
        self.phase, self.fields = phase, dict(fields or {})
        self.remember = remember

    def __repr__(self):
        return 'Decision(%r, %r, target=%r, word=%r, phase=%r)' % (
            self.action, self.note, self.target, self.word, self.phase)


def _hold(note, word=None, detail=None, mark=None):
    return Decision(HOLD, note, word=word, detail=detail, mark=mark)


def _epoch(text):
    t = A.parse_time(text)
    return None if t is None else t.timestamp()


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def week_state(data, now):
    """(pct, reset epoch, passed) of a reading's week: accounts.week_state at epoch `now`; (None,
    None, False) when it is unknown. A recorded reset passed by accounts.RESET_GRACE_S is 0% (passed
    True), its reset moved on whole weeks, even on a stale reading."""
    pct, reset, passed = A.week_state(data, datetime.datetime.fromtimestamp(now, datetime.timezone.utc))
    if pct is None:
        return None, None, False
    return float(pct), reset.timestamp(), passed


def week_window(data, now):
    """(pct, reset epoch) of a reading's week (week_state); (None, None) when it is unknown."""
    return week_state(data, now)[:2]


def is_fresh(data, now):
    """True for a reading no older than pmwatch.STALE_MINUTES whole minutes."""
    updated = _epoch(data.get('updated_at')) if isinstance(data, dict) else None
    return updated is not None and (now - updated) // 60 <= pmwatch.STALE_MINUTES


def is_stalled(stall, quiet_s, now):
    """True when the stall read `stall` (account or None, reset epoch) lies ahead and the
    transcript has been quiet QUIET_S. An unknown quiet time is not quiet."""
    return stall is not None and stall[1] > now and quiet_s is not None and quiet_s >= QUIET_S


def c_number(doc):
    """The number of a hand-off record's id, or -1."""
    m = _C_NO.match(str((doc or {}).get('id')))
    return int(m.group(1)) if m else -1


def newer(doc, high):
    """`doc` when its C-number is above `high` (None: none noted), else None."""
    return doc if doc is not None and c_number(doc) > (-1 if high is None else high) else None


def recent(times, now):
    return sorted(t for t in (times or []) if _number(t) and now - t < CAP_WINDOW_S)


def cap_reached(key, starts, now):
    """The reason this key may not start now, or None."""
    mine = recent((starts or {}).get(key), now)
    if len(mine) >= KEY_CAP:
        return '%d starts in %d h for it already' % (len(mine), CAP_WINDOW_S // 3600)
    every = sum(len(recent(v, now)) for v in (starts or {}).values())
    if every >= MACHINE_CAP:
        return '%d starts in %d h on the machine already' % (every, CAP_WINDOW_S // 3600)
    return None


def _candidates(accounts, current, running, landed, now):
    """([(account, week, reset)] of the readable accounts pick_target weighs, {account: why} of
    those left out for want of a reading it may trust: no file, an unknown week, stale with a live
    session there and its week not reset, or none since the job's last start there)."""
    rows, unread = [], {}
    for acct in sorted(accounts, key=int):
        st = accounts[acct]
        if acct in current or not st.signed_in:
            continue
        if not isinstance(st.usage, dict):
            unread[acct] = 'no usage file'
            continue
        week, reset, passed = week_state(st.usage, now)
        if week is None:
            unread[acct] = 'seven_day unknown'
            continue
        if not passed and not is_fresh(st.usage, now) and running.get(acct):
            unread[acct] = 'stale'      # a week that has reset counts, live session or not
            continue
        last = (landed or {}).get(acct)
        updated = _epoch(st.usage.get('updated_at'))
        if last is not None and (updated is None or updated <= last):
            unread[acct] = 'no reading since the last start'
            continue
        rows.append((acct, week, reset))
    return rows, unread


def pick_target(accounts, current, running, landed, now, tier2=True):
    """(account, why) of the account to move a session to, or None. `accounts`: {account:
    AccountState}; `current`: the accounts to leave out; `running`: {account: live sessions};
    `landed`: {account: epoch of the job's last start there}; `tier2` False: tier 1 only (the PM).
    Module docstring "The target"."""
    rows, _ = _candidates(accounts, current, running, landed, now)
    tier1 = [r for r in rows if r[1] < TARGET_LIMIT]
    if tier1:
        pick = min(tier1, key=lambda r: r[2])
        return pick[0], 'under %d%%, its week resets first' % TARGET_LIMIT
    second = [r for r in rows if r[1] < TIER2_WEEK] if tier2 else []
    if second:
        pick = min(second, key=lambda r: r[1])
        return pick[0], 'the lowest week under %d%%' % TIER2_WEEK
    return None


def _skip(account, entry):
    """The accounts no target may be: the session's, and the one sessions.json names for it."""
    out = {str(account)}
    if isinstance(entry, dict) and entry.get('account') not in (None, ''):
        out.add(str(entry['account']))
    return out


def no_room(key, accounts, current, running, landed, now):
    """(note, detail, mark) of a no-room for `key` (pick_target found none). Every account it
    weighed full: 'no account has room', detail `key`. Some left out for an unknown or stale
    reading (they may have room): those named, detail `<key>:unknown:a1.a2`."""
    _, unread = _candidates(accounts, current, running, landed, now)
    if not unread:
        return 'no account has room', key, 'no-room'
    names = sorted(unread, key=int)
    tail = 'unknown:' + '.'.join('a%s' % a for a in names)
    note = 'no account has room it can read: %s' % ', '.join(
        'account %s %s' % (a, unread[a]) for a in names)
    return note, '%s:%s' % (key, tail), 'no-room:' + tail


def _no_room(key, accounts, current, running, landed, now):
    note, detail, mark = no_room(key, accounts, current, running, landed, now)
    return _hold(note, 'no-room', detail, mark)


def decide(key, entry, crossing, live, stall, quiet_s, accounts, newest, starts, running, landed,
           mode, now, note_at=None, cleared=None, asks=None, weeks=None):
    """One session's next step. `entry` its sessions.json entry (None: gone); `crossing` its open
    crossing or None; `live` [Live] of the processes of its stem; `stall` the stall read of the
    session in question, or None; `quiet_s` seconds since its transcript was written, or None;
    `accounts` {account: AccountState}; `newest` its newest hand-off record or None; `starts`
    {key: [epoch]}; `running`, `landed` as pick_target; `mode` launcher.usage_crossover;
    `note_at` the mtime of the crossing's state note (its ready record's), or None; `cleared` the
    {account, reset} of its last crossing that cleared with the session left where it was, or
    None; `asks` [epoch] of its asks; `weeks` {account: week readings} (burn_eta). Reads nothing
    itself."""
    if crossing:
        return _step(key, entry, crossing, live, stall, quiet_s, accounts, newest, starts, running,
                     landed, mode, now, note_at)
    return _trigger(key, entry, live, stall, quiet_s, accounts, starts, running, landed, mode, now,
                    cleared, asks, weeks)


def _cleared_now(cleared, acct, reset):
    """True when a crossing of this key cleared on this account in this week window."""
    return (isinstance(cleared, dict) and str(cleared.get('account')) == str(acct)
            and _number(cleared.get('reset')) and abs(cleared['reset'] - reset) < RESET_NEAR_S)


def _trigger(key, entry, live, stall, quiet_s, accounts, starts, running, landed, mode, now,
             cleared=None, asks=None, weeks=None):
    if not isinstance(entry, dict) or not entry.get('stem'):
        return _hold('no stem')
    if not entry.get('state'):
        return _hold('no state note in sessions.json')
    if not live:
        return _hold('not running')
    if len(live) > 1:
        return _hold('several processes of the stem: %s' % ', '.join(sorted(l.name for l in live)))
    acct = (stall[0] if stall and stall[0] else None) or C.name_account(live[0].name) or (
        None if entry.get('account') in (None, '') else str(entry['account']))
    return _trigger_on(key, acct, _skip(acct, entry), stall, quiet_s, accounts, starts, running, landed,
                       mode, now, cleared, asks, weeks)


def _trigger_on(key, acct, skip, stall, quiet_s, accounts, starts, running, landed, mode, now,
                cleared=None, asks=None, weeks=None):
    """The trigger for one running session on account `acct`; `skip` the accounts no target may
    be. A sessions.json entry's (_trigger) and an unlisted session's (decide_unlisted)."""
    stalled = is_stalled(stall, quiet_s, now)
    st = accounts.get(acct)
    week, week_reset, high, early, eta = _week_trigger(st.usage if st is not None else None,
                                                       (weeks or {}).get(acct), now)
    if not stalled and not high and not early:
        if week is not None and week >= TRIGGER_WEEK:
            return _hold('account %s: %d%% of its week, a stale reading, no stall read' % (acct, week))
        if stall is not None and stall[1] > now:
            return _hold('a stall read, but active under %d s ago' % QUIET_S)
        return _hold('under the trigger')
    reset = stall[1] if stalled else week_reset
    if reset - now < RESET_NEAR_S:
        return _hold('the week resets within %d min' % (RESET_NEAR_S // 60))
    if stalled and mode not in (FULL, NOTIFY):     # before the cleared hold, which says nothing
        return _hold('stalled, left to the PM (usage_crossover is not full)', 'stalled', key)
    if _cleared_now(cleared, acct, reset):
        return _hold('its crossing cleared on account %s this week: not asked again before the '
                     'week resets' % acct)
    why = cap_reached(key, starts, now)
    if why:
        return _hold(why, 'cross-failed', key, 'cap')
    pick = pick_target(accounts, skip, running, landed, now)
    if pick is None:
        return _no_room(key, accounts, skip, running, landed, now)
    target, reason = pick
    how = 'stalled' if stalled else _how(week, high, eta)
    note = 'account %s %s: to account %s (%s)' % (acct, how, target, reason)
    detail = '%s:a%s' % (key, target)
    if mode == NOTIFY:
        return Decision(WOULD, note, target, 'would-cross', detail,
                        'would-cross:%s:%s' % (target, 'stall' if stalled else 'week'))
    if stalled:                 # full (normal held above): it cannot reply to a checkpoint message
        return Decision(STOP_STALLED, note, target, 'stalled', detail)
    n = len(recent(asks, now))
    if n >= ASK_CAP:
        return _hold('%d asks in %d h for it already' % (n, CAP_WINDOW_S // 3600), 'cross-failed',
                     key, 'ask-cap')
    return Decision(ASK, note, target, 'crossing', detail)


def _gone(note, word='cross-cleared', detail=None, remember=False, mark=None):
    """A CLEAR. `remember`: the session stays where it is, so the trigger must not ask it again
    in this week window (_cleared_now). `mark`: as Decision (default the word)."""
    return Decision(CLEAR, note, word=word, detail=detail, mark=mark, remember=remember)


def _advance(phase, note, **fields):
    return Decision(ADVANCE, note, phase=phase, fields=fields)


def _step(key, entry, c, live, stall, quiet_s, accounts, newest, starts, running, landed, mode, now,
          note_at=None):
    return _remembered(_phase_step(key, entry, c, live, stall, quiet_s, accounts, newest, starts, running,
                                   landed, mode, now, note_at), c, stall, accounts, now)


def _remembered(d, c, stall, accounts, now):
    """`d`, with a remembering CLEAR's {account, reset} filled in (Decision `remember`)."""
    if d.action == CLEAR and d.remember:
        acct = str(c.get('account'))
        st = accounts.get(acct)
        reset = stall[1] if stall is not None and stall[1] > now else week_window(
            st.usage if st is not None else None, now)[1]
        d.fields = {} if reset is None else {'account': acct, 'reset': reset}
    return d


def _phase_step(key, entry, c, live, stall, quiet_s, accounts, newest, starts, running, landed,
                mode, now, note_at):
    phase = c.get('phase')
    if not isinstance(entry, dict):
        return _gone('its sessions.json entry is gone', detail=key)
    if mode == NOTIFY:
        if phase in (ASKED, READY):
            return _gone('usage_crossover is notify: crossing dropped', detail=key)
        return _hold('usage_crossover is notify: %s left as it is' % phase)
    old = [l for l in live if l.pid == c.get('old_pid') and l.created == c.get('old_created')]
    new = c.get('new_name')
    up = [l for l in live if new and l.name == new]
    moved = bool(new) and entry.get('name') == new
    args = (key, entry, c, live, old, up, moved)
    stall_now = bool(old) and is_stalled(stall, quiet_s, now)
    stalled = stall_now and mode == FULL
    if phase == ASKED:
        return _asked(args, stalled, stall_now, accounts, newest, running, landed, now)
    if phase == READY:
        return _ready(args, stalled, accounts, starts, running, landed, now, quiet_s, note_at)
    if phase == STARTING:
        return _starting(args, c, now)
    if phase == STARTED:
        return _started(args, stalled, newest, now)
    if phase == STOPPING:
        return _stopping(args, now)
    if phase == STALL_STOP:
        return _stall_stop(args, stalled, now)
    return _gone('unknown state %r' % (phase,), 'cross-failed', key, remember=True)


def _stall_first(key, entry, c, accounts, running, landed, now):
    """The step from asked or ready when the old session stalls: on to stall-stop with a target."""
    skip = _skip(c.get('account'), entry)
    pick = pick_target(accounts, skip, running, landed, now)
    if pick is None:
        return _no_room(key, accounts, skip, running, landed, now)
    return _advance(STALL_STOP, 'stalled: stop first, then account %s' % pick[0], stalled=True)


def _asked(args, stalled, stall_now, accounts, newest, running, landed, now):
    key, entry, c, live, old, _up, _moved = args
    if not old:
        return _gone('the session ended before its ready record', detail=key)
    if entry.get('name') != c.get('entry_name'):
        return _gone('sessions.json was moved by someone else', detail=key)
    if len(live) > 1:
        return _hold('several processes of the stem')
    rec = newer(newest, c.get('high_c'))
    if rec is not None and rec.get('kind') == 'stop':
        return _gone('it replied ready to stop: no crossing', detail=key, remember=True)
    if rec is not None and rec.get('kind') in READY_KINDS:
        return _advance(READY, 'ready record %s' % rec.get('id'), state_path=rec.get('state_path'))
    if stalled:
        return _stall_first(key, entry, c, accounts, running, landed, now)
    if stall_now:               # stopped at the limit before its reply: it cannot send one now
        return _gone('stalled before its ready record: left to the PM (usage_crossover is not '
                     'full)', 'stalled', key)
    st = accounts.get(str(c.get('account')))
    week, _ = week_window(st.usage if st is not None else None, now)
    if week is not None and week < EARLY_FLOOR:     # an early ask comes under TRIGGER_WEEK
        return _gone('the week reset: no crossing needed', detail=key)
    if not c.get('delivered'):
        return Decision(ASK, 'the checkpoint message again: not delivered', c.get('target'))
    if now - c.get('phase_at', now) >= WAIT_READY_S:
        return _hold('no ready record in %d min' % (WAIT_READY_S // 60), 'no-ready', key)
    return _hold('waiting for its ready record')


def _settled(key, c, quiet_s, note_at, now):
    """None when the old session has settled on its hand-off: its state note written after the
    ask and its transcript quiet QUIET_S. Else the hold; `no-ready` after WAIT_READY_S of it."""
    asked = c.get('asked_at')
    if not (_number(note_at) and _number(asked) and note_at > asked):
        why = 'its state note %s not written since the ask' % c.get('state_path')
    elif quiet_s is None or quiet_s < QUIET_S:
        why = 'its transcript written under %d s ago' % QUIET_S
    else:
        return None
    if now - c.get('phase_at', now) >= WAIT_READY_S:
        return _hold('%s, for %d min' % (why, WAIT_READY_S // 60), 'no-ready', key)
    return _hold('waiting: ' + why)


def _ready(args, stalled, accounts, starts, running, landed, now, quiet_s=None, note_at=None):
    key, entry, c, live, old, up, _moved = args
    if up:
        return _advance(STARTING, 'its successor is up')
    was_stalled = bool(c.get('stalled'))
    if was_stalled and live:
        return _gone('someone started it again', detail=key)
    if entry.get('name') != c.get('entry_name'):
        if c.get('new_name') and entry.get('name') == c['new_name']:
            return _gone('its start moved sessions.json to %s, which is not running%s'
                         % (c['new_name'], '; the old session stays' if old else ''),
                         'cross-failed', key, remember=True)
        return _gone('sessions.json was moved by someone else', detail=key)
    if not was_stalled:
        if not old:
            return _gone('the session ended before its successor started', detail=key)
        if len(live) > 1:
            return _hold('several processes of the stem')
        if stalled:
            return _stall_first(key, entry, c, accounts, running, landed, now)
        wait = _settled(key, c, quiet_s, note_at, now)
        if wait is not None:
            return wait
    why = cap_reached(key, starts, now)
    if why:
        return _gone(why, 'cross-failed', key)
    retry = c.get('retry_at')
    if _number(retry) and now < retry:
        return _hold('backoff after a failed start')
    skip = _skip(c.get('account'), entry)
    pick = pick_target(accounts, skip, running, landed, now)
    if pick is None:
        return _no_room(key, accounts, skip, running, landed, now)
    return Decision(START, 'to account %s (%s)' % pick, pick[0])


def _starting(args, c, now):
    _key, _entry, _c, _live, _old, up, moved = args
    if up and moved:
        return _advance(STARTED, 'its successor is up')
    if up:
        return Decision(FINISH, 'its successor is up, sessions.json not moved')
    if now - c.get('phase_at', now) < START_WAIT_S:
        return _hold('waiting for its successor')
    fails = int(c.get('fails') or 0) + 1
    wait = BACKOFF_S[min(fails, len(BACKOFF_S)) - 1]
    return _advance(READY, 'no successor in %d s: backoff %d s' % (START_WAIT_S, wait), fails=fails,
                    retry_at=now + wait)


def _started(args, stalled, newest, now):
    key, _entry, c, _live, old, up, _moved = args
    if not old:
        if up:
            return _gone('its old session has gone: done', 'crossed', c.get('new_name'))
        return _gone('both sessions have gone', 'cross-failed', key)
    if not up:
        return _gone('its successor has gone; the old session stays', 'cross-failed', key,
                     remember=True)
    if stalled:
        return _advance(STALL_STOP, 'the old session stalled: stopped with its successor up')
    rec = newer(newest, c.get('high_c'))
    if rec is not None and rec.get('kind') == 'checkin':
        return _advance(STOPPING, 'check-in record %s' % rec.get('id'))
    if now - c.get('phase_at', now) >= WAIT_CHECKIN_S:
        return _hold('no check-in in %d min; the old session stays idle' % (WAIT_CHECKIN_S // 60),
                     'no-checkin', key)
    return _hold('waiting for its check-in')


def _stop_or_wait(key, c, now, action, note):
    """The stop, or its backoff after a refused one; STOP_TRIES refused, the last backoff out:
    given up."""
    retry = c.get('stop_retry_at')
    if _number(retry) and now < retry:
        return _hold('backoff after a refused stop')
    tries = int(c.get('stop_tries') or 0)
    if tries >= STOP_TRIES:
        return _gone('its stop was refused %d times; the old session stays' % tries,
                     'cross-failed', key, remember=True)
    return Decision(action, note)


def _stopping(args, now):
    key, _entry, c, _live, old, up, _moved = args
    if not old:
        return _gone('its old session has gone: done', 'crossed', c.get('new_name'))
    if not up:
        return _gone('its successor has gone; the old session stays', 'cross-failed', key,
                     remember=True)
    return _stop_or_wait(key, c, now, STOP_OLD, 'its successor checked in')


def _stall_stop(args, stalled, now):
    key, _entry, c, _live, old, up, _moved = args
    if old and stalled:
        return _stop_or_wait(key, c, now, STOP_STALLED, 'stalled')
    if up:
        return _advance(STARTED, 'its successor is up')
    if old:
        return _gone('it is no longer stalled: not stopped', detail=key)
    return _advance(READY, 'the stalled session has gone: start on its own note', stalled=True)


def decide_unlisted(key, sess, crossing, live, stall, quiet_s, accounts, starts, running, landed, mode, now,
                    files=None, cleared=None, asks=None, weeks=None):
    """The next step of a launched consultant or deep-worker with no sessions.json entry (module
    docstring "Unlisted sessions"). `sess` its kitlib.launcher.unlisted.Session (None: not running
    now); `live` [Live] of the processes of its key (a deep-worker's -a<N> successors too);
    `files` {ready, checkin, note}: the mtimes of its crossing's ready and check-in files and of its
    note (a deep-worker's state note, a consultant's notes file), None when missing; the rest as
    decide. Reads nothing itself."""
    if crossing:
        return _remembered(_unlisted_phase(key, crossing, live, stall, quiet_s, accounts, starts, running,
                                           landed, mode, now, files or {}), crossing, stall, accounts, now)
    if sess is None:
        return _hold('not running')
    if len(live) > 1:
        return _hold('several processes of its name: %s' % ', '.join(sorted(l.name for l in live)))
    acct = (stall[0] if stall and stall[0] else None) or sess.account
    skip = {str(a) for a in (acct, sess.account, C.name_account(sess.name)) if a}
    return _trigger_on(key, acct, skip, stall, quiet_s, accounts, starts, running, landed, mode, now,
                       cleared, asks, weeks)


def _after(t, since):
    return _number(t) and _number(since) and t > since


def _unlisted_phase(key, c, live, stall, quiet_s, accounts, starts, running, landed, mode, now, files):
    phase = c.get('phase')
    stopped = bool(c.get('stopped') or c.get('stalled'))
    if mode == NOTIFY:
        if phase in (ASKED, READY) and not stopped:
            return _gone('usage_crossover is notify: crossing dropped', detail=key)
        return _hold('usage_crossover is notify: %s left as it is' % phase)
    mine = (c.get('old_pid'), c.get('old_created'))
    old = [l for l in live if (l.pid, l.created) == mine]
    up = [l for l in live if c.get('new_name') and l.name == c['new_name'] and (l.pid, l.created) != mine]
    others = [l for l in live if l not in old and l not in up]
    stall_now = bool(old) and is_stalled(stall, quiet_s, now)
    stalled = stall_now and mode == FULL
    args = (key, None, c, live, old, up, True)
    if phase == ASKED:
        return _u_asked(key, c, old, others, stalled, stall_now, accounts, running, landed, now, files)
    if phase == READY:
        return _u_ready(key, c, live, old, up, others, stopped, stalled, accounts, starts, running, landed,
                        now, quiet_s, files)
    if phase == STOP_FIRST:
        if old:
            return _stop_or_wait(key, c, now, STOP_OLD, 'stop first: a consultant is one session of its name')
        return _advance(READY, 'it has stopped: start it on account %s' % c.get('target'), stopped=True)
    if phase == STALL_STOP:
        return _stall_stop(args, stalled, now)
    if phase == STARTING:
        return _starting(args, c, now)
    if phase == STARTED:
        return _u_started(key, c, old, up, stalled, now, files)
    if phase == STOPPING:
        return _stopping(args, now)
    return _gone('unknown state %r' % (phase,), 'cross-failed', key, remember=True)


def _u_asked(key, c, old, others, stalled, stall_now, accounts, running, landed, now, files):
    if not old:
        return _gone('the session ended before its ready file', detail=key)
    if others:
        return _hold('several processes of its name')
    if _after(files.get('ready'), c.get('asked_at')):
        return _advance(READY, 'its ready file %s' % c.get('ready'))
    if stalled:
        return _stall_first(key, None, c, accounts, running, landed, now)
    if stall_now:
        return _gone('stalled before its ready file: left to the PM (usage_crossover is not full)', 'stalled', key)
    st = accounts.get(str(c.get('account')))
    week, _ = week_window(st.usage if st is not None else None, now)
    if week is not None and week < EARLY_FLOOR:
        return _gone('the week reset: no crossing needed', detail=key)
    if not c.get('delivered'):
        return Decision(ASK, 'the checkpoint message again: not delivered', c.get('target'))
    if now - c.get('phase_at', now) >= WAIT_READY_S:
        return _hold('no ready file in %d min' % (WAIT_READY_S // 60), 'no-ready', key)
    return _hold('waiting for its ready file')


def _u_ready(key, c, live, old, up, others, stopped, stalled, accounts, starts, running, landed, now, quiet_s,
             files):
    if up:
        return _advance(STARTING, 'its successor is up')
    if stopped and live:
        return _gone('someone started it again', detail=key)
    if not stopped:
        if not old:
            return _gone('the session ended before its successor started', detail=key)
        if others:
            return _hold('several processes of its name')
        if stalled:
            return _stall_first(key, None, c, accounts, running, landed, now)
        wait = _settled(key, c, quiet_s, files.get('note'), now)
        if wait is not None:
            return wait
    why = cap_reached(key, starts, now)
    if why and stopped:         # nothing runs: it starts once the cap allows
        return _hold('%s; it is stopped and starts once the cap allows' % why, 'cross-failed', key, 'cap')
    if why:
        return _gone(why, 'cross-failed', key)
    retry = c.get('retry_at')
    if _number(retry) and now < retry:
        return _hold('backoff after a failed start')
    skip = {str(c.get('account'))}
    pick = pick_target(accounts, skip, running, landed, now)
    if pick is None:
        return _no_room(key, accounts, skip, running, landed, now)
    if c.get('kind') == UL.CONSULTANT and not stopped:
        return _advance(STOP_FIRST, 'stop first, then account %s (%s)' % pick)
    return Decision(START, 'to account %s (%s)' % pick, pick[0])


def _u_started(key, c, old, up, stalled, now, files):
    checked = _after(files.get('checkin'), c.get('start_at'))
    late = now - c.get('phase_at', now) >= WAIT_CHECKIN_S
    if c.get('kind') == UL.CONSULTANT:      # stopped before its start: nothing more to stop
        if not up:
            return _gone('its successor has gone', 'cross-failed', key)
        if checked:
            return _gone('its successor checked in: done', 'crossed', c.get('new_name'))
        if late:
            return _gone('no check-in file in %d min; its successor runs' % (WAIT_CHECKIN_S // 60), 'no-checkin', key)
        return _hold('waiting for its check-in file')
    if not old:
        if up:
            return _gone('its old session has gone: done', 'crossed', c.get('new_name'))
        return _gone('both sessions have gone', 'cross-failed', key)
    if not up:
        return _gone('its successor has gone; the old session stays', 'cross-failed', key, remember=True)
    if stalled:
        return _advance(STALL_STOP, 'the old session stalled: stopped with its successor up')
    if checked:
        return _advance(STOPPING, 'its check-in file')
    if late:
        return _hold('no check-in file in %d min; the old session stays idle' % (WAIT_CHECKIN_S // 60),
                     'no-checkin', key)
    return _hold('waiting for its check-in file')


def burn_eta(points, now):
    """Seconds until a week reaches 100% at its current burn rate, or None: no rise, or under
    BURN_MIN_S of readings. `points` [(epoch, week %)] of its readings; those of the last
    BURN_SPAN_S since the last drop (a reset) count, the rate their first-to-last rise. Pure."""
    pts = sorted((float(pt[0]), float(pt[1])) for pt in (points or [])
                 if isinstance(pt, (list, tuple)) and len(pt) == 2 and _number(pt[0]) and _number(pt[1])
                 and now - BURN_SPAN_S <= pt[0] <= now)
    run = []
    for t, pct in pts:
        if run and pct < run[-1][1]:
            run = []
        run.append((t, pct))
    if len(run) < 2:
        return None
    (t0, p0), (t1, p1) = run[0], run[-1]
    if t1 - t0 < BURN_MIN_S or p1 <= p0:
        return None
    rate = (p1 - p0) / (t1 - t0)
    return max(0.0, (100.0 - p1) / rate - (now - t1))


def _week_trigger(data, points, now):
    """(week, reset, high, early, eta) of an account's reading `data`: `high` a fresh week at
    TRIGGER_WEEK% or more; `early` not high, a fresh week at EARLY_FLOOR% or more that burn_eta of
    `points` puts at 100% within LEAD_S (eta its seconds, else None)."""
    week, reset = week_window(data, now)
    fresh = week is not None and is_fresh(data, now)
    eta = burn_eta(points, now) if fresh and week >= EARLY_FLOOR else None
    high = fresh and week >= TRIGGER_WEEK
    early = not high and eta is not None and eta < LEAD_S
    return week, reset, high, early, eta


def _how(week, high, eta):
    """The trigger's words for a decision note: a week at the trigger, or early at its burn rate."""
    if high:
        return '%d%% of its week' % week
    return '%d%% of its week, 100%% in %d min at its burn rate' % (week, eta // 60)


def decide_pm(pms, crossing, stall, quiet_s, accounts, newest, starts, running, landed, mode, now,
              note_at=None, cleared=None, asks=None, points=None, recorded=None, names=()):
    """The PM's next step (module docstring "The PM"). `pms` [PmLive] of its launched processes;
    `crossing` its open crossing or None; `newest` its newest hand-off record; `points` its
    account's week readings (burn_eta); `recorded` coordinators.json's PM (Recorded); `names` the
    names of the live launched sessions; the rest as decide. Reads nothing itself."""
    if crossing:
        return _remembered(_pm_phase(pms, crossing, stall, quiet_s, accounts, newest, starts, running, landed,
                                     mode, now, note_at, recorded, names), crossing, stall, accounts, now)
    return _pm_trigger(pms, stall, quiet_s, accounts, starts, running, landed, mode, now, cleared,
                       asks, points, recorded)


def _pm_pick(acct, accounts, running, landed, now):
    return pick_target(accounts, {str(acct)}, running, landed, now, tier2=False)


def _recorded_sid(recorded):
    """coordinators.json's pm_session_id when pm-crossover can use it (relaunch.recorded_pm), else None."""
    sid = recorded.sid if recorded is not None else None
    return sid if isinstance(sid, str) and sid and not sid.startswith('<') else None


def _pm_trigger(pms, stall, quiet_s, accounts, starts, running, landed, mode, now, cleared, asks,
                points, recorded=None):
    if not pms:
        return _hold('no launched PM session')
    if len(pms) > 1:
        return _hold('several PM processes: %s' % ', '.join(sorted(p.name for p in pms)))
    stalled = is_stalled(stall, quiet_s, now)
    acct = (stall[0] if stall and stall[0] else None) or pms[0].account
    st = accounts.get(acct)
    week, week_reset, high, early, eta = _week_trigger(st.usage if st is not None else None, points, now)
    if not stalled and not high and not early:
        if week is not None and week >= TRIGGER_WEEK:
            return _hold('account %s: %d%% of its week, a stale reading, no stall read' % (acct, week))
        if stall is not None and stall[1] > now:
            return _hold('a stall read, but active under %d s ago' % QUIET_S)
        return _hold('under the trigger')
    reset = stall[1] if stalled else week_reset
    if reset - now < RESET_NEAR_S:
        return _hold('the week resets within %d min' % (RESET_NEAR_S // 60))
    if _recorded_sid(recorded) is None or recorded.account in (None, ''):
        return _hold('coordinators.json records no PM session id or account: pm-crossover cannot move it',
                     'pm-limit', PM_DETAIL)
    if stalled and mode not in (FULL, NOTIFY):
        return _hold('stalled, left to the owner (usage_crossover is not full)', 'stalled', PM_DETAIL)
    if _cleared_now(cleared, acct, reset):
        return _hold('its crossing cleared on account %s this week: not asked again before the '
                     'week resets' % acct)
    why = cap_reached(PM_DOC, starts, now)
    if why:
        return _hold(why, 'cross-failed', PM_DETAIL, 'cap')
    pick = _pm_pick(acct, accounts, running, landed, now)
    if pick is None:
        return _no_room(PM_DETAIL, accounts, {str(acct)}, running, landed, now)
    target, reason = pick
    how = 'stalled' if stalled else _how(week, high, eta)
    note = 'the PM: account %s %s: to account %s (%s)' % (acct, how, target, reason)
    detail = '%s:a%s' % (PM_DETAIL, target)
    if mode == NOTIFY:
        return Decision(WOULD, note, target, 'would-cross', detail,
                        'would-cross:%s:%s' % (target, 'stall' if stalled else 'week'))
    if stalled:                 # full: it cannot reply to a checkpoint message, and nothing is stopped
        return Decision(START, note, target, 'stalled', detail)
    n = len(recent(asks, now))
    if n >= ASK_CAP:
        return _hold('%d asks in %d h for it already' % (n, CAP_WINDOW_S // 3600), 'cross-failed',
                     PM_DETAIL, 'ask-cap')
    return Decision(ASK, note, target, 'crossing', detail)


def _pm_phase(pms, c, stall, quiet_s, accounts, newest, starts, running, landed, mode, now, note_at,
              recorded, names):
    phase = c.get('phase')
    new = c.get('new_name')
    rec = recorded or Recorded(None, None, None)
    if (phase == STARTING and new and rec.name == new and str(rec.account) == str(c.get('target'))
            and rec.sid != c.get('old_sid')):
        return _gone('coordinators.json names %s on account %s: done' % (new, c.get('target')),
                     'crossed', new)
    if rec.sid != c.get('old_sid'):
        return _gone('coordinators.json names another PM session: moved by someone else',
                     detail=PM_DETAIL)
    if mode == NOTIFY:
        if phase in (ASKED, READY):
            return _gone('usage_crossover is notify: crossing dropped', detail=PM_DETAIL)
        return _hold('usage_crossover is notify: %s left as it is' % phase)
    old = [p for p in pms if p.pid == c.get('old_pid') and p.created == c.get('old_created')]
    stall_now = bool(old) and is_stalled(stall, quiet_s, now)
    if phase == ASKED:
        return _pm_asked(c, old, pms, stall_now, accounts, newest, mode, now)
    if phase == READY:
        return _pm_ready(c, old, pms, stall_now, accounts, starts, running, landed, mode, now, quiet_s,
                         note_at, names)
    if phase == STARTING:
        if new and new in names:
            return _gone('its successor %s is up but coordinators.json does not name it' % new,
                         'cross-failed', PM_DETAIL, remember=True)
        return _starting((PM_DETAIL, None, c, [], old, [], False), c, now)
    return _gone('unknown state %r' % (phase,), 'cross-failed', PM_DETAIL, remember=True)


def _pm_asked(c, old, pms, stall_now, accounts, newest, mode, now):
    if not old:
        return _gone('the PM session ended before its hand-off', detail=PM_DETAIL)
    if len(pms) > 1:
        return _hold('several PM processes')
    rec = newer(newest, c.get('high_c'))
    if rec is not None and rec.get('kind') == 'stop':
        return _gone('it replied ready to stop: no crossing', detail=PM_DETAIL, remember=True)
    if rec is not None and rec.get('kind') in READY_KINDS:
        return _advance(READY, 'hand-off record %s' % rec.get('id'), state_path=rec.get('state_path'))
    if stall_now and mode == FULL:
        return _advance(READY, 'stalled before its hand-off: start on the hand-off file as it is',
                        stalled=True)
    if stall_now:
        return _gone('stalled before its hand-off: left to the owner (usage_crossover is not full)',
                     'stalled', PM_DETAIL)
    st = accounts.get(str(c.get('account')))
    week, _ = week_window(st.usage if st is not None else None, now)
    if week is not None and week < EARLY_FLOOR:
        return _gone('the week reset: no crossing needed', detail=PM_DETAIL)
    if not c.get('delivered'):
        return Decision(ASK, 'the checkpoint message again: not delivered', c.get('target'))
    if now - c.get('phase_at', now) >= WAIT_READY_S:
        return _hold('no hand-off record in %d min' % (WAIT_READY_S // 60), 'no-ready', PM_DETAIL)
    return _hold('waiting for its hand-off record')


def _pm_ready(c, old, pms, stall_now, accounts, starts, running, landed, mode, now, quiet_s, note_at,
              names):
    if c.get('new_name') and c['new_name'] in names:
        return _advance(STARTING, 'its successor is up')
    if not old:
        return _gone('the PM session ended before its successor started', detail=PM_DETAIL)
    if len(pms) > 1:
        return _hold('several PM processes')
    st = accounts.get(str(c.get('account')))
    week, _ = week_window(st.usage if st is not None else None, now)
    if week is not None and week < EARLY_FLOOR and not stall_now:
        return _gone('the week reset: no crossing needed', detail=PM_DETAIL)
    if not c.get('stalled'):
        if stall_now and mode == FULL:
            return _advance(READY, 'stalled: start on the hand-off file as it is', stalled=True)
        if stall_now:
            return _gone('stalled before its successor started: left to the owner '
                         '(usage_crossover is not full)', 'stalled', PM_DETAIL)
        wait = _settled(PM_DETAIL, c, quiet_s, note_at, now)
        if wait is not None:
            return wait
    why = cap_reached(PM_DOC, starts, now)
    if why:
        return _gone(why, 'cross-failed', PM_DETAIL)
    retry = c.get('retry_at')
    if _number(retry) and now < retry:
        return _hold('backoff after a failed start')
    pick = _pm_pick(c.get('account'), accounts, running, landed, now)
    if pick is None:            # the PM resumes; the trigger asks again once an account has room
        note, detail, mark = no_room(PM_DETAIL, accounts, {str(c.get('account'))}, running, landed, now)
        return _gone(note + ': the PM resumes', 'no-room', detail, mark=mark)   # the trigger's mark
    return Decision(START, 'to account %s (%s)' % pick, pick[0])
