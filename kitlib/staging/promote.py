"""kitlib.staging.promote: one promotion run of a repository with a staging branch
(tools/promote.py; docs/modules/staging-design.md "Promotion").

run(main, cfg) tests the staging tip S against `<remote>/<main>` M with the merged-tree gate's
selection (kitlib.gate.runner.merged_gate on a detached tree of its own, `<worktrees_dir>/promote`,
escalated to every step in full when M..S changes the gate's config, as a landing is), and on a
green writes S's promotion record and moves main to S (kitlib.staging.advance). In order:

  1. The promote lock (lock_for: a kitlib.fs.treelock file in the repository's git common dir,
     so every checkout shares it), before anything else, recovery included. Held by another run:
     BUSY, nothing read, touched or recorded.
  2. A fetch, then the staging setting `<remote>/<main>` writes (kitlib.dailyfull.promote
     staging_read). Off: USAGE (OFF), nothing recorded.
  3. An attempt a run left unfinished (kitlib.staging.record LIVE) is moved on from its phase
     (advance.Mover): this run does nothing else.
  4. Due? (kitlib.staging.changes: "staging".promote_after_landings untested landings, or the
     oldest promote_after_hours old; `force` skips only this check.) Not due: OK, nothing recorded.
  5. S already ended in a hold (record.HOLDS: red, refused, diverged, failed, error): never
     tested again; a new staging tip is a fresh attempt.
     M not an ancestor of S: an attempt that ends diverged.
  6. The gate, its slot booked now or never (BOOK: no_wait). A machine-lock refusal (busy,
     timeout, reserved, hold, no lock): a skip, nothing recorded (the next hub tick asks again).
     FAILED: ends red, then the culprit hunt and its revert branch (kitlib.staging.culprit
     after_red, `hunter`), still under the promote lock, its probes' slots waiting within
     culprit.HUNT_BUDGET_S. Any other refusal: ends error.
  7. OK: the promotion record of S on M (`write_record`,
     gate_ok.record_tested_pass(kind 'promotion')): the pre-push guard lets main move to S only
     with it. Not written: ends failed. Written: an attempt at tested, then advance.

The gate's machine-lock slot is booked and released inside merged_gate (slot=None: gated books
it and releases it before returning), so no slot is held while advance waits for the landing lock.

Outcome codes: OK (done, behind, not due, a skip, a wait), FAILED (red), USAGE (staging off),
REFUSED (held, diverged, refused, failed, error, or a step before the gate that could not be
made), BUSY (another run holds the promote lock). Output goes through `say`; nothing here prints.
"""
import os
import time

import kitlib.config as kconfig
from kitlib import gate as G
from kitlib import dailyfull as D
from kitlib.config.load import load_at
from kitlib.config.types import ConfigError
from kitlib.dailyfull import promote as P
from kitlib.fs.treelock import TreeBusy, TreeLock, TreeLockError, describe
from kitlib.gate import rerun
from kitlib.gate import reuse
from kitlib.gate import runner
from kitlib.git import repo
from kitlib.records import gate_ok, place
from kitlib.staging import advance as A
from kitlib.staging import changes as C
from kitlib.staging import culprit as CU
from kitlib.staging import record as R

LOCK_FILE = 'kit-staging-promote.lock'
SLUG = 'promote'                # the gate's log folder (land-promote) and the tree's folder name
BOOK = {'no_wait': True}        # the gate's slot: now or never (a busy machine is a skip)
OFF = ('promote: OFF - %s/%s names no "staging".branch, so there is nothing to promote '
       '(docs/gate-landing.md "Staging branch")')
CODES = {'done': G.OK, 'behind': G.OK, 'red': G.FAILED}     # every other end: REFUSED


def _quiet(_line):
    pass


class RecordNotWritten(Exception):
    """The promotion record could not be written."""


def lock_for(main):
    """`(TreeLock, None)`: the promote lock of the repository at `main`, keyed on its git common
    dir (one per repository, whichever checkout asks), or `(None, why)`."""
    common, why = repo.common_dir(main)
    if not common:
        return None, 'no git common dir for %s: %s' % (main, why)
    return TreeLock(os.path.join(common, LOCK_FILE), 'promotion of %s' % main), None


def folder(cfg, main, say=_quiet):
    """The attempts' folder: `<gate log base>/promote` (kitlib.records.place.log_base of the main
    checkout's config)."""
    return os.path.join(place.log_base(cfg, main, place.repo_name(cfg, main),
                                       lambda t: say('promote: note: gate logs: %s' % t)), R.DIR)


def tree_of(cfg, main):
    """The promotion's own detached tree: `<worktrees_dir>/promote`."""
    return os.path.normpath(os.path.join(main, cfg.repo['worktrees_dir'], SLUG))


def write_record(main, sha, base, cfg_base, started, out, full):
    """The promotion record of `sha` tested on `base` (gate_ok.record_tested_pass, kind
    'promotion'); RecordNotWritten when it is not written. `full`: the run was escalated (every
    step in full); else it was the merged gate's selection, and the record says so."""
    try:
        gate_ok.record_tested_pass(main, sha, 'promotion', gate_ok.gate_config(cfg_base),
                                   runner.kit_identity(), place.repo_name(cfg_base, main),
                                   started, runner.record_steps(out.facts['results']),
                                   base=base, config_rev=base, source=out.facts.get('source'),
                                   full=full)
    except Exception as e:      # noqa: BLE001 - any failure leaves main where it is
        raise RecordNotWritten('%s: %s' % (type(e).__name__, e))


def code_of(a):
    return G.OK if a.live() else CODES.get(a.phase, G.REFUSED)


def run(main, cfg, say=_quiet, force=False, book_kw=None, git=D.git, gate=runner.merged_gate,
        record=write_record, mover=None, clock=time.time, hunter=CU.after_red):
    """One promotion run of the repository whose main checkout is `main` (its config `cfg`): a
    G.Outcome (the module docstring). `book_kw`: more of the gate's booking (kitlib.lock.run
    book), over BOOK: now or never (the hunt's probes drop no_wait). `gate`, `record`,
    `mover(main, cfg2, staging, git, say)`, `clock` and `hunter(ctx, attempt, out, say)` are
    stand-ins a test passes."""
    lock, why = lock_for(main)
    if lock is None:
        return G.Outcome(G.REFUSED, ['REFUSED: the promote lock: %s' % why])
    try:
        lock.acquire()
    except TreeBusy as e:
        return G.Outcome(G.BUSY, ['promote: BUSY - another promotion run holds %s (%s); this run '
                                  'read and recorded nothing' % (e.path, describe(e.record))])
    except TreeLockError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: the promote lock: %s' % e])
    try:
        if lock.took_over:
            say('promote: took over the promote lock from a run that is gone (%s)'
                % describe(lock.took_over))
        book = dict(BOOK, **(book_kw or {}))
        return _held(main, cfg, say, force, book, git, gate, record, mover or A.Mover, clock,
                     hunter)
    finally:
        lock.release()


def _held(main, cfg, say, force, book, git, gate, record, mover, clock, hunter):
    why = D.fetch(main, cfg.remote, git)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    two, why = P.staging_read(main, cfg, git)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    if two is None or not kconfig.staging_on(two):
        return G.Outcome(G.USAGE, [OFF % (cfg.remote, P.main_of(cfg))])
    staging = kconfig.work_base(two)
    where = folder(cfg, main, say)
    found = R.attempts(where)
    move = mover(main, two, staging, git, say)

    left = R.unfinished(found)
    if left is not None:
        lines = ['promote: a run that is gone left %s at %s; moving it on' % (
            left.sha[:10], left.phase)]
        try:
            lines += move.advance(left)
        except R.RecordError as e:
            return G.Outcome(G.REFUSED, lines + ['REFUSED: %s' % e])
        return G.Outcome(code_of(left), lines + [R.line(left)])

    got, why = C.untested(main, two, staging, git)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    count, oldest = got
    landings, hours = C.thresholds(two)
    if not count:
        return G.Outcome(G.OK, ['promote: nothing to promote - %s/%s holds every commit of %s/%s'
                                % (cfg.remote, P.main_of(cfg), cfg.remote, staging)])
    now = clock()
    if not force and not C.due(count, oldest, now, landings, hours):
        return G.Outcome(G.OK, [C.not_due_line(count, oldest, now, landings, hours)])

    tip, base = _sha(main, git, cfg.remote, staging), _sha(main, git, cfg.remote, P.main_of(cfg))
    if not tip or not base:
        return G.Outcome(G.REFUSED, ['REFUSED: %s/%s or %s/%s cannot be read' % (
            cfg.remote, staging, cfg.remote, P.main_of(cfg))])
    held = R.held(found, tip)
    if held is not None:
        return G.Outcome(G.REFUSED, ['promote: HELD - %s ended %s and is never tested again; a '
                                     'new landing on %s, or the owner, moves it on' % (
                                         tip[:10], held.phase, staging), R.line(held)])
    try:
        return _test(main, cfg, two, staging, tip, base, where, count, say, book, git, gate,
                     record, move, hunter)
    except R.RecordError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])


def _sha(main, git, remote, branch):
    rc, sha, _ = git(main, 'rev-parse', '--verify', '-q', 'refs/remotes/%s/%s' % (remote, branch))
    return sha if rc == 0 and sha else None


def _ended(where, tip, base, phase, why, log_dir=None):
    a = R.start(where, tip, base, phase, why=why, log_dir=log_dir)
    return G.Outcome(code_of(a), ['promote: %s - %s' % (phase.upper(), why), R.line(a)])


def _test(main, cfg, two, staging, tip, base, where, count, say, book, git, gate, record, move,
          hunter):
    if git(main, 'merge-base', '--is-ancestor', base, tip)[0] != 0:
        return _ended(where, tip, base, 'diverged', '%s/%s (%s) has commits that are not on %s '
                      '(%s)' % (cfg.remote, P.main_of(cfg), base[:10], staging, tip[:10]))
    wt = tree_of(cfg, main)
    sha, why = D.prepare(main, wt, tip, cfg.remote, git=git, fetched=True)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    try:
        cfg_base = load_at(main, base)      # the base's gate and hook, as a landing reads them
    except ConfigError as e:
        return _ended(where, tip, base, 'error', 'the config at %s/%s (%s): %s' % (
            cfg.remote, P.main_of(cfg), base[:10], e))
    changed, why = runner.changed_between(wt, base, tip)
    hits = reuse.config_hits(cfg_base, changed) if changed is not None else None
    full = changed is None or bool(hits)
    if full:
        say('promote: GATE CONFIG: %s: every step runs in full' % (
            'the change touches the gate\'s config (%s)' % ', '.join(hits[:5]) if hits else
            'git cannot list what changed (%s)' % why))
    say('promote: %d landing(s) on %s, testing %s against %s/%s at %s' % (
        count, staging, tip[:10], cfg.remote, P.main_of(cfg), base[:10]))
    started = gate_ok.utc()
    out = gate(main, cfg_base, base, full=full, slug=SLUG, say=say,
               book_kw=book, tree=wt)
    log_dir = out.facts.get('log_dir')
    cause = D.SKIP_CAUSES.get(out.code)
    if cause is not None:
        return G.Outcome(G.OK, ['promote: SKIPPED (%s) - %s; nothing recorded, the next run asks '
                                'again' % (cause, '; '.join(out.lines))], skipped=cause)
    if out.code == G.FAILED:
        why = out.facts.get('verdict') or 'the gate failed'
        a = R.start(where, tip, base, 'red', why=why, log_dir=log_dir)
        ctx = {'main': main, 'cfg': cfg, 'cfg_base': cfg_base, 'staging': staging, 'wt': wt,
               'tip': tip, 'base': base, 'full': full, 'book': book, 'git': git,
               'since': rerun.since_of(started), 'log_dir': log_dir}
        lines = ['promote: RED - %s' % why] + hunter(ctx, a, out, say)
        return G.Outcome(code_of(a), lines + [R.line(a)], log_dir=log_dir)
    if out.code != G.OK:
        return _ended(where, tip, base, 'error', '; '.join(out.lines) or 'the gate refused',
                      log_dir)
    if out.facts.get('head') != tip:
        return _ended(where, tip, base, 'error', 'the gate tested %s, not %s' % (
            (out.facts.get('head') or '?')[:10], tip[:10]), log_dir)
    try:
        record(main, tip, base, cfg_base, started, out, full)
    except RecordNotWritten as e:
        return _ended(where, tip, base, 'failed', 'green, but its promotion record was not '
                      'written: %s' % e, log_dir)
    a = R.start(where, tip, base, 'tested', log_dir=log_dir)
    lines = ['promote: GREEN at %s (%s)' % (tip[:10], 'every step in full' if full else
                                            'the merged gate\'s selection')]
    lines += move.advance(a)
    return G.Outcome(code_of(a), lines + [R.line(a), 'logs: %s' % log_dir], log_dir=log_dir)


def look(main, cfg, announce, git=D.git, clock=time.time):
    """The hub job's look at one repository (kitlib.hubjobs.staging_promote): `(verdict, note)`.
    No fetch: the local `<remote>/*` refs as they are. Verdicts:
      off      staging is off at `<remote>/<main>` (or it cannot be read): nothing, note None
      busy     a promotion run holds the promote lock: nothing
      start    a run is owed: an attempt is unfinished, or a promotion is due and its tip is not
               held; the caller starts tools/promote.py
      idle     nothing is due, or the tip is held
      error    git or the lock could not say (note: why)
    The ends a hub item is owed for (record.unannounced) are given to `announce(attempt)` while
    the promote lock is held, and marked announced once it returns: one each, whatever the
    verdict but off and busy. A red attempt whose culprit hunt is still marked hunting is from a
    run that is gone (a live one holds the lock): it is marked stopped first."""
    two, why = P.staging_read(main, cfg, git)
    if why:
        return 'error', why
    if two is None or not kconfig.staging_on(two):
        return 'off', None
    lock, why = lock_for(main)
    if lock is None:
        return 'error', why
    try:
        lock.acquire()
    except TreeBusy as e:
        return 'busy', 'a promotion runs (%s)' % describe(e.record)
    except TreeLockError as e:
        return 'error', str(e)
    try:
        found = R.attempts(folder(cfg, main))
        for a in R.unannounced(found):
            if R.hunting(a):        # the lock is free, so the run that hunted is gone
                a.rec['culprit'] = dict(a.rec['culprit'], verdict=CU.STOPPED)
                a.write()
            announce(a)
            a.rec['announced'] = True
            a.write()
        left = R.unfinished(found)
        if left is not None:
            return 'start', '%s left at %s' % (left.sha[:10], left.phase)
        staging = kconfig.work_base(two)
        got, why = C.untested(main, two, staging, git)
        if why:
            return 'error', why
        count, oldest = got
        landings, hours = C.thresholds(two)
        if not C.due(count, oldest, clock(), landings, hours):
            return 'idle', '%d untested' % count
        tip = _sha(main, git, cfg.remote, staging)
        held = R.held(found, tip) if tip else None
        if held is not None:
            return 'idle', '%s held (%s)' % (tip[:10], held.phase)
        return 'start', '%d untested at %s' % (count, (tip or '?')[:10])
    except R.RecordError as e:
        return 'error', str(e)
    finally:
        lock.release()


def status_lines(main, cfg, last=3):
    """The latest `last` attempts' lines, newest first, read from disk (no fetch, no lock)."""
    found = R.attempts(folder(cfg, main))
    if not found:
        return ['promote   no promotion attempt recorded yet']
    return [R.line(a) for a in reversed(found[-last:])]
