"""kitlib.staging.promote: one promotion run of a repository with a staging branch
(tools/promote.py; docs/modules/staging-design.md "Promotion").

run(main, cfg) tests the staging tip S against `<remote>/<main>` M with the merged-tree gate's
selection (kitlib.gate.runner.merged_gate on a detached tree of its own, `<worktrees_dir>/promote`,
escalated to every step in full when M..S changes the gate's config, as a landing is; each step's
promote_args appended after its merged_args, in this gate alone), and on a
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
  5. S already ended in a hold (record.HOLDS: red, refused, diverged, failed, error,
     standing-hold): never tested again; a new staging tip is a fresh attempt.
     M not an ancestor of S: an attempt that ends diverged.
  6. The gate, its slot booked now or never (BOOK: no_wait). A machine-lock refusal (busy,
     timeout, reserved, hold, no lock): a skip, nothing recorded (the next hub tick asks again).
     FAILED with every FAIL the machine's (kitlib.gate.rerun.machine_only: a step that did not
     start, a strict requirement unmet such as a busy resource's probe, kit_tests units that
     timed out): ends `machine`, tested again next time, until the streak of machine ends at S
     (record.machine_since) is older than MACHINE_WINDOW_S; then it ends red with the reasons in
     the attempt's `machine` (fix the machine or tool, not the tree; never hunted). FAILED with
     every FAIL a `"promote_hold"` step's of M's gate (steps.promote_holds: the repository's own
     standing hold, such as a daily hold): ends standing-hold, never inspected, hunted or
     announced; each new staging tip is gated again, which is how the hold is seen to clear. Any
     other FAILED: ends red, then kitlib.staging.culprit after_red (`hunter`), still under the
     promote lock: the attempt's `inspect` (no probe, no slot), handed to the coordinator to fix first;
     the culprit hunt and its revert branch only when the red repeats one a fix was marked
     landed for (culprit.repeat), its probes' slots waiting within culprit.HUNT_BUDGET_S. Any
     other refusal: ends error.
  7. OK: the promotion record of S on M (`write_record`,
     gate_ok.record_tested_pass(kind 'promotion')): the pre-push guard lets main move to S only
     with it. Not written: ends failed. Written: an attempt at tested, then advance.

The gate's machine-lock slot is booked and released inside merged_gate (slot=None: gated books
it and releases it before returning), so no slot is held while advance waits for the landing lock.

hunt(main, cfg) is the coordinator's request for the hunt (`tools/promote.py --hunt`), and
fixed(main, cfg, sha) its mark that the fix of a red landed (`--fixed <sha>`, the attempt's
`fix`). Both run under the promote lock, after the same fetch and staging read, on the latest
attempt that ended red with no later one done; neither runs a gate.

Outcome codes: OK (done, behind, machine, not due, a skip, a wait; a hunt run, a fix marked),
FAILED (red), USAGE (staging off), REFUSED (held, standing-hold, diverged, refused, failed,
error, a step before the gate that could not be made; nothing to hunt, a fix not marked), BUSY
(another run holds the promote lock). Output goes through `say`; nothing here prints.
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
from kitlib.gate import steps as S
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
CODES = {'done': G.OK, 'behind': G.OK, 'machine': G.OK, 'red': G.FAILED}  # else: REFUSED
MACHINE_WINDOW_S = 3600     # a tip whose every FAIL is the machine's is retried this long, then red
STANDING_HELD = ('promote: STANDING HOLD - %s ended standing-hold (%s) and is never gated again; '
                 "main moves on a new landing the hold no longer stops, the daily run's own "
                 "promotion, or the owner deleting that attempt's folder")


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
    book = dict(BOOK, **(book_kw or {}))
    return _locked(main, say, lambda: _held(main, cfg, say, force, book, git, gate, record,
                                            mover or A.Mover, clock, hunter))


def _locked(main, say, body):
    """`body()` under the promote lock: BUSY when another run holds it."""
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
        return body()
    finally:
        lock.release()


def _staging(main, cfg, git, say):
    """`(None, cfg2, staging, where)` after a fetch: the staging setting `<remote>/<main>` writes,
    its branch and the attempts' folder; `(the Outcome, None, None, None)` when it cannot be
    read or is off."""
    why = D.fetch(main, cfg.remote, git)
    if not why:
        two, why = P.staging_read(main, cfg, git)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why]), None, None, None
    if two is None or not kconfig.staging_on(two):
        return G.Outcome(G.USAGE, [OFF % (cfg.remote, P.main_of(cfg))]), None, None, None
    return None, two, kconfig.work_base(two), folder(cfg, main, say)


def _held(main, cfg, say, force, book, git, gate, record, mover, clock, hunter):
    stop, two, staging, where = _staging(main, cfg, git, say)
    if stop is not None:
        return stop
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
    if held is not None and held.phase == R.STANDING:
        return G.Outcome(G.REFUSED, [STANDING_HELD % (tip[:10], held.rec.get('why')),
                                     R.line(held)])
    if held is not None:
        return G.Outcome(G.REFUSED, ['promote: HELD - %s ended %s and is never tested again; a '
                                     'new landing on %s, or the owner, moves it on' % (
                                         tip[:10], held.phase, staging), R.line(held)])
    try:
        return _test(main, cfg, two, staging, tip, base, where, count, say, book, git, gate,
                     record, move, hunter, now)
    except R.RecordError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])


def _sha(main, git, remote, branch):
    rc, sha, _ = git(main, 'rev-parse', '--verify', '-q', 'refs/remotes/%s/%s' % (remote, branch))
    return sha if rc == 0 and sha else None


def _ended(where, tip, base, phase, why, log_dir=None):
    a = R.start(where, tip, base, phase, why=why, log_dir=log_dir)
    return G.Outcome(code_of(a), ['promote: %s - %s' % (phase.upper(), why), R.line(a)])


def _test(main, cfg, two, staging, tip, base, where, count, say, book, git, gate, record, move,
          hunter, now):
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
               book_kw=book, tree=wt, promote=True)
    log_dir = out.facts.get('log_dir')
    cause = D.SKIP_CAUSES.get(out.code)
    if cause is not None:
        return G.Outcome(G.OK, ['promote: SKIPPED (%s) - %s; nothing recorded, the next run asks '
                                'again' % (cause, '; '.join(out.lines))], skipped=cause)
    if out.code == G.FAILED:
        why = out.facts.get('verdict') or 'the gate failed'
        earlier = R.attempts(where)
        machine = rerun.machine_only(out.facts.get('results') or [])
        more = {}
        if machine:
            since = R.machine_since(earlier, tip)
            if since is None or now - since < MACHINE_WINDOW_S:
                return _ended(where, tip, base, 'machine', machine, log_dir)
            why, more = "%s (the machine's FAIL for over 1 h)" % machine, {'machine': machine}
        else:
            fails = [r['name'] for r in out.facts.get('results') or [] if r.get('status') == 'FAIL']
            if fails and set(fails) <= set(S.promote_holds(cfg_base.section('gate'))):
                return _ended(where, tip, base, R.STANDING, 'held by %s (%s)' % (
                    ', '.join(fails), why), log_dir)
        a = R.start(where, tip, base, 'red', why=why, log_dir=log_dir, **more)
        ctx = {'main': main, 'cfg': cfg, 'cfg_base': cfg_base, 'staging': staging, 'wt': wt,
               'tip': tip, 'base': base, 'book': book, 'git': git,
               'since': rerun.since_of(started), 'log_dir': log_dir, 'earlier': earlier}
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


def last_red(found):
    """`(attempt, None)`: the latest attempt of `found` (attempts()) that ended red, with no later
    one done; `(None, why)` when there is none."""
    end = next((a for a in reversed(found) if a.phase in ('red', 'done')), None)
    if end is None or not any(a.phase == 'red' for a in found):
        return None, 'no promotion attempt ended red'
    if end.phase == 'done':
        return None, '%s promoted after the last red, so that red is past' % end.sha[:10]
    return end, None


def hunt(main, cfg, say=_quiet, book_kw=None, git=D.git, hunter=CU.hunt_red):
    """The culprit hunt the coordinator asks for (`tools/promote.py --hunt`), after one failed fix,
    of last_red's attempt, under the promote lock: a G.Outcome. REFUSED (nothing to hunt) when no
    red is left, it is the machine's (its `machine`), it has no step a hunt can probe (no
    `inspect` red), or its hunt is marked running; the promotion's tree is put at its tip and
    `hunter(ctx, attempt, say)` (culprit.hunt_red) runs. A culprit found posts no promote-revert
    hub item: the attempt was announced as red already."""
    book = dict(BOOK, **(book_kw or {}))
    return _locked(main, say, lambda: _hunt_held(main, cfg, say, book, git, hunter))


def _hunt_held(main, cfg, say, book, git, hunter):
    stop, _two, staging, where = _staging(main, cfg, git, say)
    if stop is not None:
        return stop
    a, why = last_red(R.attempts(where))
    if a is None:
        return G.Outcome(G.REFUSED, ['promote: NOTHING TO HUNT - %s' % why])
    if a.rec.get('machine'):
        return G.Outcome(G.REFUSED, ["promote: NOTHING TO HUNT - the red is the machine's: %s; "
                                     'fix the machine or tool' % a.rec['machine'], R.line(a)])
    if not (a.rec.get('inspect') or {}).get('red'):
        return G.Outcome(G.REFUSED, ['promote: NOTHING TO HUNT - the red of %s has no step a hunt '
                                     'can probe (`gate`, `windows` and `infra` reds are never '
                                     'hunted)' % a.sha[:10], R.line(a)])
    if R.hunting(a):
        return G.Outcome(G.REFUSED, ['promote: NOTHING TO HUNT - the hunt of %s is marked running '
                                     '(pid %s), by a run that is gone since the promote lock '
                                     'was free; the hub job\'s next look marks it stopped, and '
                                     'then --hunt runs it again' % (
                                         a.sha[:10], a.rec['culprit'].get('pid')),
                                     R.line(a)])
    tip, base = a.sha, a.rec.get('base')
    wt = tree_of(cfg, main)
    _sha_at, why = D.prepare(main, wt, tip, cfg.remote, git=git, fetched=True)
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    try:
        cfg_base = load_at(main, base)
    except ConfigError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: the config at %s: %s' % ((base or '?')[:10], e)])
    say('promote: hunting the red of %s on main %s' % (tip[:10], base[:10]))
    ctx = {'main': main, 'cfg': cfg, 'cfg_base': cfg_base, 'staging': staging, 'wt': wt,
           'tip': tip, 'base': base, 'book': book, 'git': git, 'log_dir': a.rec.get('log_dir')}
    try:
        lines = hunter(ctx, a, say)
    except R.RecordError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])
    return G.Outcome(G.OK, lines + [R.line(a)])


def fixed(main, cfg, sha, say=_quiet, git=D.git):
    """The coordinator's mark that the fix of last_red's attempt landed on the staging branch as
    `sha` (`tools/promote.py --fixed <sha>`), under the promote lock: the attempt's `fix` {sha,
    at}, which lets a red on the same tests hunt by itself (culprit.repeat). REFUSED when no red
    is left, the red is the machine's (its `machine`: no tree fix to mark), `sha` is no commit, is not on `<remote>/<staging>`, or is already in the red tip."""
    return _locked(main, say, lambda: _fixed_held(main, cfg, sha, say, git))


def _fixed_held(main, cfg, sha, say, git):
    stop, _two, staging, where = _staging(main, cfg, git, say)
    if stop is not None:
        return stop
    a, why = last_red(R.attempts(where))
    if a is None:
        return G.Outcome(G.REFUSED, ['promote: NO FIX MARKED - %s' % why])
    if a.rec.get('machine'):
        return G.Outcome(G.REFUSED, ["promote: NO FIX MARKED - the red of %s is the machine's: %s; "
                                     'fix the machine or tool: a new staging tip clears it, or '
                                     'deleting its folder once the machine is fixed' % (
                                         a.sha[:10], a.rec['machine']), R.line(a)])
    rc, full, err = git(main, 'rev-parse', '--verify', '-q', '%s^{commit}' % sha)
    if rc != 0 or not full:
        return G.Outcome(G.REFUSED, ['promote: NO FIX MARKED - %s names no commit' % sha])
    on = '%s/%s' % (cfg.remote, staging)
    if git(main, 'merge-base', '--is-ancestor', full, 'refs/remotes/%s' % on)[0] != 0:
        return G.Outcome(G.REFUSED, ['promote: NO FIX MARKED - %s is not on %s: mark the fix once '
                                     'it landed there' % (full[:10], on)])
    if git(main, 'merge-base', '--is-ancestor', full, a.sha)[0] == 0:
        return G.Outcome(G.REFUSED, ['promote: NO FIX MARKED - %s is already in the red tip %s, so '
                                     'it cannot be its fix' % (full[:10], a.sha[:10])])
    a.rec['fix'] = {'sha': full, 'at': R.now_text()}
    try:
        a.write()
    except R.RecordError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])
    return G.Outcome(G.OK, ['promote: FIX MARKED - %s fixes the red of %s; the same tests red '
                            'again hunt by themselves' % (full[:10], a.sha[:10]), R.line(a)])


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
    verdict but off and busy. Any attempt whose culprit hunt is still marked hunting (an announced
    red's `--hunt` included) is from a run that is gone (a live one holds the lock): it is marked
    stopped first."""
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
        for a in found:
            if R.hunting(a):        # the lock is free, so the run that hunted is gone
                a.rec['culprit'] = dict(a.rec['culprit'], verdict=CU.STOPPED)
                a.write()
        for a in R.unannounced(found):
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
    """The latest `last` attempts' lines, newest first, read from disk (no fetch, no lock); a red
    one's `inspect` and `fix` on a line of its own (record.more_line)."""
    found = R.attempts(folder(cfg, main))
    if not found:
        return ['promote   no promotion attempt recorded yet']
    out = []
    for a in reversed(found[-last:]):
        out.append(R.line(a))
        more = R.more_line(a)
        if more:
            out.append(more)
    return out
