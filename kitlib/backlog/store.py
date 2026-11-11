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

The parts: store.py (the write loop: Store, Change, the plan notes), store_read.py (snapshots,
validation and the history reads), store_ref.py (the ref and branch backends), store_folder.py
(the main backend), store_live.py (where the live store is) and store_open.py (opening one).
"""
import contextlib
import contextvars
import random
import time

from . import record as rmod
from . import store_read as sread
from .errors import Failure, Refusal, Usage

STORE_REF = 'refs/backlog/main'
STORE_DIR = 'backlog'
BRANCH = 'backlog'
ATTEMPTS = 5


class Conflict(Exception):
    """The store moved between load and publish: the attempt is re-run."""


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
    plan and the publish of every attempt."""

    def __init__(self, backend, rules, session=None):
        self.backend = backend
        self.rules = rules
        self.session = session
        self.before_publish = None

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
        compare-and-swap, re-planning after every lost race. Returns (change, new tip)."""
        last = ''
        for attempt in range(ATTEMPTS):
            snap = self.load()
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
                last = str(exc)
                if getattr(self.backend, 'fetch', True) is False:
                    self.backend.fetch = True        # the local copy was stale: re-read the remote
                time.sleep(random.uniform(0.05, 0.3) * (attempt + 1))
        raise Failure('the store moved under %d attempts in a row; nothing was written (%s)'
                      % (ATTEMPTS, last or 'compare-and-swap lost'))


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
