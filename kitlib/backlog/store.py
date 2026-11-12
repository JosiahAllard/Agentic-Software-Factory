"""Where records live and how a write reaches them (docs/backlog-store.md).

Three backends behind one interface (`load`, `publish`, `history`, `removal`, `touched`,
`landed`):

  ref     `backlog/<ID>.md` on `refs/backlog/main`, a ref with no history shared with main
  branch  the same on a visible `backlog` branch
  main    `<dir>/<ID>.md` files in a linked worktree (today's behaviour; landing carries them);
          in the PM data branch's worktree each write also commits its own record paths

A write (`Store.write`) on a ref store is a compare-and-swap loop. Each attempt loads a fresh
snapshot (fetching first), plans the change against it, validates the result, builds a commit
whose parent is exactly the snapshot's tip, and publishes it only if the store still points at
that tip: `push --force-with-lease=<ref>:<tip>` with a remote, `update-ref <ref> <new> <tip>`
without one. A lost race re-runs the whole attempt, plan included, so a write never lands on a
store it did not validate against. A write that fails validation publishes nothing. A ref store
never touches a working tree or an index other than its own temporary one. The main backend has
no compare-and-swap (check the bytes, then write), so it writes only in a linked worktree.

Writers on one machine queue instead of racing: a write holds the store's write lock (the
backend's `lock_path()`, kitlib.fs.writelock) from its first load to its publish. A ref store's
lock is per store in the repository's git common dir, so every worktree of one clone shares it; a
second clone, and every other machine, is covered by the compare-and-swap alone. The lock bounds
its own hold: no attempt or re-read starts HOLD_S seconds after it was taken, and the backend's
`hold_bound_s` is the most a write can then hold it (Store.longest_hold, the wait of a caller that
can afford it). After a lost race whose new tip the backend read, the next load re-reads with
pauses (GUARD_PAUSES) until it reaches that tip, so a stale read of the remote is not planned on.

The parts: store.py (the write loop: Store, Change, the plan notes), store_read.py (snapshots,
validation and the history reads), store_ref.py (the ref and branch backends), store_folder.py
(the main backend), store_live.py (where the live store is) and store_open.py (opening one).
"""
import contextlib
import contextvars
import random
import time

from kitlib.fs import writelock

from . import record as rmod
from . import store_read as sread
from .errors import Failure, Refusal, StoreBusy, Usage

STORE_REF = 'refs/backlog/main'
STORE_DIR = 'backlog'
BRANCH = 'backlog'
ATTEMPTS = 5
# The store write lock (Store.write, docs/backlog.md "The store write lock").
LOCK_WAIT = 90          # seconds a write waits for another local writer by default (backlog.py
                        # runs under an agent's shell timeout of about 120 s)
LOCK_PAUSE = 0.2        # seconds between tries of a held store lock
HOLD_S = 120            # no attempt and no guard step starts this long after the lock was taken
GUARD_PAUSES = (0.5, 1, 2, 4)   # the re-reads after a lost race until the load reaches its tip
LOCAL_S = 30            # allowance for one attempt's local work (plan, build, checks)
BACKOFF_S = 0.3         # the most a lost race sleeps, times the attempt number


class Conflict(Exception):
    """The store moved between load and publish: the attempt is re-run. `remote_tip`: the tip
    the store moved to, when the backend read it (None: not known)."""

    def __init__(self, said='', remote_tip=None):
        Exception.__init__(self, said)
        self.remote_tip = remote_tip


# --- the write loop -------------------------------------------------------------------------------

class Change(object):
    """What a plan decided: `writes` {stem: new text, or None to delete}, the commit subject, the
    lines to print, and the ruling it cites."""

    def __init__(self, writes, subject, lines=(), ruling=None):
        self.writes = dict(writes)
        self.subject = subject
        self.lines = list(lines)
        self.ruling = ruling


_PLAN_NOTES = contextvars.ContextVar('backlog_plan_notes', default=None)


@contextlib.contextmanager
def plan_notes():
    """Collect the lines a plan adds with add_plan_note while it runs (a list, in order, each
    once). Store.write and a dry run put them before the change's own lines."""
    notes = []
    token = _PLAN_NOTES.set(notes)
    try:
        yield notes
    finally:
        _PLAN_NOTES.reset(token)


def add_plan_note(line):
    """A line the running plan's caller prints with the change (plan.refuse_machine_paths's
    machine-config WARNING); dropped when no plan_notes collector is open."""
    notes = _PLAN_NOTES.get()
    if notes is not None and line not in notes:
        notes.append(line)


@contextlib.contextmanager
def notes_on_refusal(notes):
    """Re-raise a Refusal (any of kitlib.backlog.errors) raised inside with each line of `notes`
    its message does not already hold added on a line of its own, as commit.py adds its machine
    config WARNING to the stop lines: a plan that stops for another reason still prints it. The
    same exception object, so its type and attributes are kept."""
    try:
        yield
    except Refusal as exc:
        msg = str(exc)
        extra = ''.join('\n' + n for n in notes if n not in msg)
        if extra:
            exc.args = (msg + extra,) + tuple(exc.args[1:])
        raise


def with_plan_notes(change, notes):
    """`change` with `notes` it does not already print put before its lines."""
    change.lines = [n for n in notes if n not in change.lines] + change.lines
    return change


class Store(object):
    """A backend plus the schema rules. `before_publish(attempt)` is a test hook run between the
    plan and the publish of every attempt. `lock_wait`: the seconds a write waits for the store
    lock (LOCK_WAIT; the hub's record writes set a short one, its job threads longest_hold()).
    `on_wait(holder)`: called once, before the wait, when another writer holds the lock (the CLI
    prints a line)."""

    def __init__(self, backend, rules, session=None):
        self.backend = backend
        self.rules = rules
        self.session = session
        self.before_publish = None
        self.lock_wait = LOCK_WAIT
        self.on_wait = None

    def longest_hold(self):
        """The most seconds one write can hold this store's lock: a wait this long outlasts any
        holder that is still alive."""
        return getattr(self.backend, 'hold_bound_s', HOLD_S + LOCAL_S)

    def load(self):
        return self.backend.load(self.rules)

    def history(self, rid, snap):
        return self.backend.history(rid, snap)

    def removal(self, rid, snap):
        return self.backend.removal(rid, snap)

    def uncommitted_removal(self, rid):
        return self.backend.uncommitted_removal(rid)

    def removed(self, snap):
        return self.backend.removed(snap)

    def touched(self, snap):
        return self.backend.touched(snap)

    def message(self, change):
        lines = [change.subject, '']
        lines.append('Session: %s' % (self.session or '-'))
        if change.ruling:
            lines.append('Ruling: PM %s' % change.ruling)
        return '\n'.join(lines) + '\n'

    def write(self, plan):
        """Run `plan(snapshot) -> Change` against a fresh snapshot and publish it by
        compare-and-swap, re-planning after every lost race, all under the store lock. Returns
        (change, new tip). StoreBusy when another local writer held the lock for the whole
        wait."""
        where = getattr(self.backend, 'lock_path', None)
        lock = where() if where is not None else None
        with contextlib.ExitStack() as stack:
            if lock is not None:
                self._take(stack, lock)
            return self._write(plan)

    def _take(self, stack, lock):
        """Enter the store lock on `stack`: at once when it is free, else after on_wait(holder)
        for up to lock_wait seconds."""
        what = 'backlog store write (session %s)' % (self.session or '-')
        try:
            stack.enter_context(writelock.locked(lock, 0, what, LOCK_PAUSE))
            return
        except writelock.Busy as exc:
            if self.on_wait is not None:
                self.on_wait(exc.holder)
        except writelock.WriteLockError as exc:
            raise Failure('cannot take the backlog store lock: %s; nothing was written' % exc)
        try:
            stack.enter_context(writelock.locked(lock, self.lock_wait, what, LOCK_PAUSE))
        except writelock.Busy as exc:
            raise StoreBusy('another backlog write on this machine holds the store lock (%s, %s); '
                            'waited %s s; nothing was written' % (lock, exc.holder, exc.wait),
                            lock, exc.holder, exc.wait)
        except writelock.WriteLockError as exc:
            raise Failure('cannot take the backlog store lock: %s; nothing was written' % exc)

    def _write(self, plan):
        deadline = time.monotonic() + HOLD_S
        last, tips, moved_to = '', [], None
        for attempt in range(ATTEMPTS):
            if attempt and time.monotonic() >= deadline:
                break
            snap = self._load_after(moved_to, deadline)
            sread.require_readable(snap)
            with plan_notes() as notes, notes_on_refusal(notes):
                change = plan(snap)
            change = with_plan_notes(change, notes)
            if not change.writes:
                return change, snap.tip
            with notes_on_refusal(notes):
                check_change(snap, change)
            if self.before_publish is not None:
                self.before_publish(attempt)
            try:
                return change, self.backend.publish(snap, change.writes, self.message(change))
            except Conflict as exc:
                last, moved_to = str(exc), exc.remote_tip
                tips.append('planned %s remote %s' % (_short(snap.tip), _short(moved_to)))
                if getattr(self.backend, 'fetch', True) is False:
                    self.backend.fetch = True        # the local copy was stale: re-read the remote
                if attempt + 1 < ATTEMPTS and time.monotonic() < deadline:
                    time.sleep(random.uniform(0.05, BACKOFF_S) * (attempt + 1))
        if len(tips) < ATTEMPTS:
            moved = ('the store moved under %d attempts in %d s, the most one write may hold the '
                     'store lock' % (len(tips), HOLD_S))
        else:
            moved = 'the store moved under %d attempts in a row' % len(tips)
        raise Failure('%s; nothing was written (%s; tips per attempt: %s)'
                      % (moved, last or 'compare-and-swap lost', ', '.join(tips)))

    def _load_after(self, moved_to, deadline):
        """A snapshot. After a lost race to `moved_to` (a tip the backend read), re-read after each
        of GUARD_PAUSES while the load has not reached it (a stale read of the remote) and the
        hold has time left; then plan on the last load anyway, which the publish checks."""
        snap = self.load()
        reached = getattr(self.backend, 'has_reached', None)
        if moved_to is None or reached is None:
            return snap
        for pause in GUARD_PAUSES:
            if reached(snap, moved_to) or time.monotonic() >= deadline:
                break
            time.sleep(pause)
            snap = self.load()
        return snap


def _short(tip):
    return tip[:10] if tip else '-'


def check_change(snap, change):
    """Usage when the change would add a finding: a FAIL `check` would report on the store after
    the change (check.fail_findings: every record and store-wide check bar the ledger's), or a new
    dangling `item:` blocker. Findings the store already had do not block a write."""
    from .check import fail_findings                 # check imports this module
    records = dict(snap.by_id)
    for stem, text in change.writes.items():
        if text is None:
            records.pop(stem, None)
            continue
        old = snap.by_id.get(stem)
        records[stem] = rmod.parse(old.path if old is not None else '%s.md' % stem, text, stem)
    after = sread.Snapshot(list(records.values()), snap.tip, snap.where, snap.rules)
    before = {(f.check, f.path, f.message) for f in fail_findings(snap)}
    new = [f for f in fail_findings(after) if (f.check, f.path, f.message) not in before]
    # A new `item:` blocker naming nothing would hold its record forever (a WARN at rest only,
    # since `close` and `retire` rewrite their dependents).
    before = {(f.path, f.message) for f in sread.dangling_items(snap.by_id)}
    new.extend(f for f in sread.dangling_items(records) if (f.path, f.message) not in before)
    if new:
        raise Usage('the write would leave the store invalid; nothing was written:\n' +
                    '\n'.join('  %s: %s' % (f.path, f.message) for f in new))
