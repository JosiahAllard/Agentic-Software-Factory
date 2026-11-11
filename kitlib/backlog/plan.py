"""The write verbs as plans: `plan_<verb>(snap, ctx, ...) -> Change`, and what every plan shares.

A plan only decides. It reads the snapshot it is given, refuses (Usage, Refusal, NotFound,
Failure) or returns the records to write, and never touches a file, a ref or the screen. The
store runs it again after a lost race, so a plan is a pure function of the snapshot and its
arguments (plus the git probes it names: branch liveness, the `Context.liveness` probe of a
pulled branch's worktree, the `Context.quiet` probe of a quiet release, `requires: commit:`,
and the diff of the branch `add --found-on` names).
`next`'s narrowing (plan_narrow `_narrower`) reads three more, all read-only: the worktree list,
the status of a live pull's worktree, and the tracked tree (`ls-tree`) of the code main.

This file holds the plans' Context and the checks every verb shares. The verbs live by family:
plan_edit (add, set, clear-review), plan_claim (pull and release, and stopping and restoring a
pull), plan_hold (block, unblock, set-status, unpark and the owner's answer), plan_end (close,
retire and delete) and plan_citations (uncite). plan_narrow holds the scope and resource rules
they share: the offer, live pulls, fold-ins and collisions.
"""
import datetime
import re

from kitlib.git import cmd, refs
from kitlib import machinepaths

from . import offer as omod
from . import policy
from . import record as rmod
from .errors import Ended, Failure, NotFound, Refusal, Usage
from .store import add_plan_note
from .store_read import PLAIN_ID

MIN_REASON = 12

# Context.offer_session unset: `session` is the store's.
_STORE_SESSION = object()


class SharedPaths(object):
    """A sequence of shared paths `read()` gives, called on first use (iterating, `len`, truth)."""

    def __init__(self, read):
        self._read, self._paths = read, None

    def _get(self):
        if self._paths is None:
            self._paths = tuple(self._read())
        return self._paths

    def __iter__(self):
        return iter(self._get())

    def __len__(self):
        return len(self._get())


class Context(object):
    """What a plan needs beyond the snapshot. `branch` None: the branch checked out in `root`.
    `code_main` (None: `main_branch`) is the branch code lands on, which `add --found-on`
    measures a branch's diff from (`.kit.json` backlog.code_main), as last fetched from
    `code_remote` (None: `remote`; the main checkout's remote in the PM data worktree,
    opener.open_store). `liveness` (None: no probe) is `liveness(branch) -> (why it is live, None)
    or (None, tip)`: whether the worktree on a pulled branch is dead, so a pull its holder left
    may be blocked or released by anybody (stop_pull).
    `quiet` (None: no probe) is `quiet(branch, main) -> (why it is not quiet, None) or
    (None, seconds quiet)`, the probe of a quiet release (plan_release), `main` the code main's
    commit (None: unread). Both come from the caller (opener.open_store's `probes`, the worktree
    package's for a caller that stops another branch's pull); with no probe, such a stop is
    refused as live, and nothing here reaches up for one.
    `cap` (None: offer.DEFAULT_CAP; a callable: read on first use) is the most records `next` offers and `pull` takes in order
    (`.kit.json` backlog.cap, opener.repo_cap); `next --cap` overrides it for that call.
    `resources` (None: none; a callable: read on first use) is {name: capacity} (`.kit.json`
    backlog.resources, opener.repo_resources).
    `shared` (None: none; a callable: read on first use) is `[(path, why)]`, the `.kit.json`
    backlog.shared_paths entries (opener.repo_shared_paths); `shared_paths` is those with no
    `why`, the paths every scope collision check here exempts (scope.members_collide)."""

    def __init__(self, store, root, remote='origin', main_branch='main', branch=None, today=None,
                 code_main=None, code_remote=None, liveness=None, cap=None, resources=None,
                 shared=None, quiet=None):
        self.store = store
        self.root = root
        self.remote = remote
        self.main_branch = main_branch
        self.code_main = code_main or main_branch
        self.code_remote = code_remote or remote
        self.branch = branch
        self.today = today
        self.liveness = liveness
        self.quiet = quiet
        # omod.SessionLiveness for an offer (None: another session's record always reserves);
        # set by tools/backlog.py and the hub's jobs (coord_restart.read_offer,
        # fill_watch.pull_record), each with its own reads, so a test never reads the machine.
        self.session_live = None
        # The session a library caller offers and pulls for while the store commits under its
        # own (the hub's fill watch: the coordinator's key, Session hub-fill); unset: the store's.
        # Set, it is `session` for every reader of ctx.session, not only the offer.
        self.offer_session = _STORE_SESSION
        self._cap = cap
        self._resources = resources
        self._histories = (None, {})    # (the last store tip asked about, {rid: history})
        self._shared = shared
        self._unmet_base = None         # the code main `unmet` resolved, once, on first need
        self._live = (None, None)       # (the snapshot, offer.live_branches' names for it)

    @property
    def shared_entries(self):
        """`[(path, why)]`, a callable given for it called on first read, as `cap`."""
        if callable(self._shared):
            self._shared = self._shared()
        return list(self._shared or [])

    @property
    def shared_paths(self):
        """The shared paths a scope collision exempts: the entries with no `why`, as a SharedPaths
        that reads them on first use. The scope checks ask it only of a pair that collides
        without it, so an offer or a pull that meets no collision never reads the config (as
        `next --all` never reads the cap)."""
        return SharedPaths(lambda: [p for p, why in self.shared_entries if why is None])

    @property
    def cap(self):
        """The cap, a callable given for it called on first read (opener.open_store: so only an
        offer reads `.kit.json`, and a verb that makes none never fails on it)."""
        if callable(self._cap):
            self._cap = self._cap()
        return omod.DEFAULT_CAP if self._cap is None else self._cap

    @property
    def resources(self):
        """{name: capacity}, a callable given for it called on first read, as `cap`."""
        if callable(self._resources):
            self._resources = self._resources()
        return self._resources or {}

    def capacity(self, snap):
        """`resources` when a record of `snap` names one, else {} with no config read: a store
        with no `uses:` line behaves, and fails, exactly as it did without them."""
        return self.resources if any(r.has('uses') for r in snap.readable) else {}

    @property
    def session(self):
        if self.offer_session is not _STORE_SESSION:
            return self.offer_session
        return self.store.session

    def date(self):
        return self.today or datetime.date.today().isoformat()

    def current_branch(self):
        if self.branch:
            return self.branch
        name, why = refs.current_branch(self.root)
        if name is None:
            raise Failure('cannot read the branch checked out in %s: %s' % (self.root, why))
        if not name:
            raise Failure('HEAD is detached in %s, so no branch can be named as the one that '
                          'pulls; pass `--as <branch>`' % self.root)
        return name

    def history(self, snap):
        """`history(rid)` of the store at `snap`, one git log per rid for this Context and snap:
        offer's readers (closed_reader, ended_reader, unmet_requires) all ask through it."""
        # Keyed on the store's tip (snap.tip), not the snapshot object: RefBackend.history reads
        # HEAD and ignores snap, so an answer cached under an older tip must not outlive a write.
        if self._histories[0] != ('tip', snap.tip):
            self._histories = (('tip', snap.tip), {})
        seen = self._histories[1]

        def history(rid):
            if rid not in seen:
                seen[rid] = self.store.history(rid, snap)
            return seen[rid]
        return history

    def live_branches(self, snap):
        """`(names, None)` or `(None, why)`, offer.live_branches asked once per snapshot (a command
        reads the branch set several times over the one snapshot it loaded; a Context a caller
        keeps across writes asks again under each new snapshot). A failed probe is not kept."""
        if self._live[0] is not snap:
            names, why = omod.live_branches(self.root, self.remote)
            if names is None:
                return None, why
            self._live = (snap, names)
        return self._live[1], None

    def in_flight_split(self, snap):
        """offer.in_flight_split(snap) over this Context's one live-branch read of `snap`."""
        return omod.in_flight_split(snap, self.root, self.remote,
                                    live=lambda: self.live_branches(snap))

    def unmet(self, snap):
        """`unmet(rec)` -> `[(member, why)]` (offer.unmet_requires). The code main it checks a
        `commit:` member against costs git spawns, so it is resolved at most once per Context and
        only when a record names a `commit:` requirement (offer.unmet_requires reads it only
        there)."""
        hist = self.history(snap)

        def unmet(rec):
            base = None
            if any(m.startswith('commit:') for m in rec.all('requires')):
                if self._unmet_base is None:
                    self._unmet_base = refs.default_base(self.root, self.code_remote,
                                                         self.code_main)[0]
                base = self._unmet_base
            return omod.unmet_requires(rec, snap, hist, self.root, base)
        return unmet


# --- shared ---------------------------------------------------------------------------------------

def require(snap, rid):
    rec = snap.get(rid)
    if rec is None:
        raise NotFound('no record with `id: %s` in %s' % (rid, snap.where))
    return rec


_ENDS = {'closes': 'closed', 'retires': 'retired', 'deletes': 'deleted'}


def require_shown(snap, ctx, rid):
    """require() for `show`: a missing id says which store commit removed it (closed, retired or
    deleted; sha, date, session, subject, the reason; never an author name) as Ended, exit 0;
    NotFound (exit 1) stays for an id no commit removed, or none that can be read. A main store's
    record whose file HEAD tracks and the worktree no longer holds says so (closed or retired
    here - MainBackend writes commit nothing - or removed by hand). An id that is
    not plain (a glob or path character) is not looked up, and a history git cannot read (an
    unborn HEAD, a failing git) still leaves the plain NOT FOUND, with a line saying so."""
    rec = snap.get(rid)
    if rec is not None:
        return rec
    plain = 'no record with `id: %s` in %s' % (rid, snap.where)
    gone_in = '%s is no longer in %s' % (rid, snap.where)
    if not PLAIN_ID.match(rid):
        raise NotFound('%s\n  history not searched: `%s` is not a plain id' % (plain, rid))
    try:
        pending = ctx.store.uncommitted_removal(rid)
        gone = None if pending else ctx.store.removal(rid, snap)
    except Failure as exc:
        raise NotFound('%s\n  the store\'s history could not be read: %s' % (plain, exc))
    if pending:
        raise Ended('%s\n  its file is tracked at HEAD but missing from this worktree (closed or '
                    'retired here, or removed by hand); no commit names it yet' % gone_in)
    if gone is None:
        raise NotFound('%s\n  no commit in the store\'s history removed it: the id is unknown'
                       % plain)
    how = _ENDS.get(gone.subject.split(' ', 1)[0], 'removed')
    said = '%s\n  %s by %s on %s (session %s): %s' % (gone_in, how, gone.sha, gone.when,
                                                   gone.session or '-', gone.subject)
    why = re.match(r'(?:closes|retires|deletes) %s: (.+)$' % re.escape(rid), gone.subject)
    raise Ended(said + ('\n  reason: %s' % why.group(1) if why else ''))


def _text(pairs, body):
    """The record text of `pairs` and `body`. An `until` line whose member left `blocked_on` is
    dropped here (record.prune_until), so every write that clears a blocker drops its condition."""
    return rmod.render(rmod.prune_until(pairs), body)


def clears_owner_blocker(rec, after):
    return 'owner' in rec.all('blocked_on') and 'owner' not in after


def refuse_machine_paths(field, *texts):
    """Usage when free text carries a machine path: it reaches a record or a commit subject, and
    history cannot be fixed once it lands (commit.checks.machine_path_error, same rule: the
    machine config's `machine_path_folders` and a user profile). Empty texts read no machine
    config. A machine config it cannot read searches user-profile paths only, and its WARNING ends the
    refusal or, with no refusal, is printed with the write (store.add_plan_note)."""
    if not any(texts):
        return
    folders, note = machinepaths.folders()
    found = []
    for t in texts:
        found += [f for f in machinepaths.find(t or '', folders) if f not in found]
    if not found:
        if note:
            add_plan_note(note)
        return
    raise Usage('refused: `%s` carries a machine path: %s. tools/machine_paths.py fails a '
                'file with one. Write %%VAR%%\\... (the variable that applies), or leave it out.'
                % (field, ', '.join(found)) + ('\n' + note if note else ''))


def reason_error(reason, flag):
    text = ' '.join((reason or '').split())
    if len(text) < MIN_REASON:
        return ('`%s` needs at least %d characters of reason: it is the only trace the change '
                'leaves' % (flag, MIN_REASON))
    return None


def shared_of(ctx):
    """`ctx.shared_paths` (`.kit.json` backlog.shared_paths), () for a context that has none: the
    one set every scope collision check of a plan exempts (docs/backlog.md "Shared paths")."""
    return getattr(ctx, 'shared_paths', ())


def refuse_top(rid, verb, rows):
    """A write planned without the role check (`role_checked` false: a library caller, not
    tools/backlog.py or an owner route) refuses when it takes a row only the owner and the PM may
    take (policy.top_only of sort's `rows`): the role check decides those, not the plan."""
    top = policy.top_only(rows)
    if top:
        raise Refusal('`%s`: this %s is %s (kitlib/backlog/roles.py), and no role check ran: '
                      'make it with tools/backlog.py, whose role check decides it'
                      % (rid, verb, policy.row_text(top[0])))


def branch_name_error(name, root):
    r = cmd.call(['check-ref-format', '--branch', name], root)
    if r.rc != 0 or cmd.text(r.out).strip() != name:
        return '`%s` is not a branch name' % name
    return None


def _append_line(body, line):
    """`body` with `line` appended as its own paragraph."""
    body = (body or '').rstrip('\n')
    return (body + '\n\n' if body.strip() else '') + line + '\n'
