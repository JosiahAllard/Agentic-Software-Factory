"""The gate's steps run in a machine-lock slot (kitlib.lock.run.book), and the two gates built on
that: the branch gate (`gate.py`) and the merged-tree gate a landing runs (kitlib.gate.land).

Every step runs in the slot's job (its launcher): pinned to the run's cores, measured, and ended
as a tree when the run is cancelled. A step's output goes to `<log dir>/<step>.log`; the step is
judged from that log (kitlib.gate.steps.judge). No Python the gate runs writes bytecode into the
tree: a step's child process (a cmd, a requires probe, a check's run()) gets
PYTHONDONTWRITEBYTECODE=1 added to the slot's environment (child_env), and a check itself runs
under kitlib.config.policy.no_bytecode.

A check step calls the repo hook module's GATE_CHECKS[<check>](ctx). ctx is a dict:
  root      the checkout gated          base     the base commit (hash)
  merged    True on a landing's merged-tree gate
  changed   the changed paths (repo-relative, posix)
  options   `.kit.json` "hook".options  name     the step name
  log       the step's log, a text file open for append
  run       run(argv, cwd=None) -> exit code: argv in the slot's job, output into the log
It returns 0 or True to pass; any other int or False fails it; raising, or calling sys.exit with
any code, fails it with the traceback in the log.

A gate where no selected step PASSed, WARNed, FAILed or was REUSED (all skipped for a missing
`requires`) is REFUSED (nothing_ran). The branch gate writes the gate-OK record
(kitlib.records.gate_ok) for HEAD only when every selected step passed, warned, was reused or
was skipped, no --only was given, the base is the default base, the work tree was clean (no
tracked change, no untracked file) when the gate started and when it ended, HEAD did not
move, and the repository's stash stack was empty when the gate started (else REFUSED, before
--plan; kitlib.gate.stash) and when it ended: the record then names exactly the commit whose tree
was gated, and its config_rev is that commit. A REUSED step did not run in this gate: a pass of
its key is in the gate cache
(kitlib.gate.reuse), stored by an earlier gate of the same inputs. A rerun with every step
reused therefore writes a gate-OK record for a commit on which no step ran; its record says
`reused` for each step. A `--full` gate that FAILED on those same terms (no --only, the default
base, a clean tree at start and end, HEAD unmoved, the stash empty) writes a red run instead
(kitlib.gate.rerun.record_red), which `gate.py --rerun` reruns (docs/gate-rerun-checkout.md "Targeted rerun").
"""
import datetime
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.config import policy
from kitlib.config.types import ConfigError
from kitlib.gate import budget
from kitlib.gate import detect
from kitlib.gate import gatecap
from kitlib.gate import gitstatus
from kitlib.gate import hookrev
from kitlib.gate import result
from kitlib.gate import stash
from kitlib.gate import steps as S
from kitlib.git import cmd, refs, repo
from kitlib.lock import run as lockrun
from kitlib.lock.store import EXIT_USAGE, LockError, Store
from kitlib.proc import process
from kitlib.platform.windows import winwatch
from kitlib.proc.job import JobError
from kitlib.records import gate_ok, place

NO_STEP = ('REFUSED: no gate step selected: the "gate".steps of .kit.json cover none of the %d '
           'changed path(s), or each step they select is dropped by another\'s skip_if_selected, '
           'so nothing would run; no gate-OK record is written. Give a step "always": true or '
           'globs that cover these paths')
# --full (and a landing's merged gate whose diff changes a gate config file) selects every step but
# the manual ones, whatever changed: none left means every step is manual, or each is dropped by
# another selected step's skip_if_selected.
NO_STEP_FULL = ('REFUSED: no gate step selected: every step in the "gate".steps of .kit.json is '
                'manual, or is dropped by another selected step\'s skip_if_selected, so nothing '
                'would run; no gate-OK record is written. Give the gate a step that is not manual '
                'and that no other step\'s skip_if_selected names')
NO_CHECKS = ('GATE OK (auto-detected: no stack found, so NO checks ran; give the gate steps with a '
             '"gate" section in .kit.json or tools/gate.json)')
PROBE_TIMEOUT = 60      # seconds a `requires` probe argv or a reuse.py toolchain probe may take
CANCELLED_WAITING = 'was cancelled by stop while it waited'     # kitlib.lock.run's LockError text
# A console window the gate's run opened (window_results): False, it warns; True, it fails the
# gate as a `windows` result. The one switch.
WINDOWS_FAIL = False
RESULT = {'PASS': 'pass', 'WARN': 'warn', 'SKIP': 'skip', 'REUSED': 'reused'}
# A branch gate that would only repeat a failed gate of its tree (_unchanged): its no_record.
UNCHANGED_NO_RECORD = 'the tree failed this gate unchanged'


def _quiet(_line):
    pass


def kit_identity():
    """{'commit': the kit checkout's HEAD ('unknown' when git cannot say), 'python': the
    interpreter running the gate, 'clean': True when the kit checkout has no uncommitted or
    untracked file (None when git cannot say)}, as a gate-OK record holds it. Run from a pinned
    export of pushed main (kitlib.gate.pin), the export's commit, clean: an export holds exactly
    that commit's tracked files. The gate cache (kitlib.gate.reuse) keys steps on reuse.kit_identity instead:
    the kit's file hashes, never its commit."""
    from kitlib import pluginsource
    marker = pluginsource.read_marker(S.KIT_ROOT)
    if marker is not None and refs.is_sha(marker.get('sha')):
        return {'commit': marker['sha'],
                'python': os.path.normcase(os.path.abspath(sys.executable)), 'clean': True}
    out, _ = cmd.run_text(['rev-parse', 'HEAD'], S.KIT_ROOT)
    status, _ = cmd.run_text(['--no-optional-locks', 'status', '--porcelain'], S.KIT_ROOT)
    return {'commit': out if refs.is_sha(out) else 'unknown',
            'python': os.path.normcase(os.path.abspath(sys.executable)),
            'clean': None if status is None else not status}


def say_note(text):
    """The default `note` of place.log_base's makers here: one line on stderr."""
    print('gate logs: %s' % text, file=sys.stderr, flush=True)


def new_log_dir(cfg, root, name, branch, base_dir=None, note=say_note):
    """A fresh folder for one gate run's logs: `<base>/<branch>/<time>-<pid>`. base: base_dir, else
    place.log_base; when the default log_base (no base_dir, no "landing".log_dir) cannot be made,
    place.fallback_log_base, and note(<why>)."""
    if base_dir:
        return _new_dir(os.path.join(base_dir, place.log_folder(branch)))
    return make_log_dir(cfg, root, name, place.log_folder(branch), note=note, fresh=True)


def make_log_dir(cfg, root, name, *parts, note=say_note, fresh=False):
    """`<log_base>/<parts...>`, made if missing (fresh: a new `<time>-<pid>` folder in it). When
    the default log_base cannot be made, the same under place.fallback_log_base, and note(<why>); a
    "landing".log_dir that cannot be made raises OSError, as does a fallback that cannot."""
    make = _new_dir if fresh else _made
    path = os.path.join(place.log_base(cfg, root, name, note), *parts)
    try:
        return make(path)
    except OSError as e:
        if cfg.section('landing').get('log_dir'):
            raise
        got = make(os.path.join(place.fallback_log_base(name), *parts))
        note('%s: %s could not be made (%s)' % (got, path, e))
        return got


def _made(path):
    os.makedirs(path, exist_ok=True)
    return path


def _new_dir(parent):
    stem = os.path.join(parent, '%s-%d' % (
        datetime.datetime.now().strftime('%Y%m%d-%H%M%S'), os.getpid()))
    path, n = stem, 0
    while os.path.exists(path):
        n += 1
        path = '%s-%d' % (stem, n)
    os.makedirs(path)
    return path


def _names(out):
    return [p for p in cmd.text(out).split('\0') if p]


def changed_since(root, base):
    """`(sorted changed paths, None)`: tracked paths differing between `base` and the work tree,
    plus untracked files git does not ignore; or `(None, why)`."""
    out, why = cmd.run(['diff', '--no-renames', '--name-only', '-z', base, '--'], root)
    if out is None:
        return None, why
    paths = set(_names(out))
    out, why = cmd.run(['ls-files', '--others', '--exclude-standard', '-z'], root)
    if out is None:
        return None, why
    return sorted(paths | set(_names(out))), None


def changed_between(root, a, b):
    out, why = cmd.run(['diff', '--no-renames', '--name-only', '-z', a, b, '--'], root)
    if out is None:
        return None, why
    return sorted(set(_names(out))), None


def default_base(cfg, root):
    """`(ref, note)`: kitlib.git.refs.default_base (`<remote>/<main>` when it resolves, else
    `<main>`; `note` says when the local main differs), or `(None, why)` when neither resolves."""
    ref, note = refs.default_base(root, cfg.remote, cfg.main_branch)
    if refs.resolve(ref, root)[0]:
        return ref, note
    return None, 'neither %s/%s nor %s resolves: pass --base' % (cfg.remote, cfg.main_branch,
                                                                  cfg.main_branch)


# ---------------------------------------------------------------- running steps

def child_env(launcher):
    """The environment a step's child process gets: the slot's (launcher.env; os.environ for a
    launcher without one) with PYTHONDONTWRITEBYTECODE=1 added. A child Python does not inherit
    sys.dont_write_bytecode, and a `__pycache__` it writes beside the repo's scripts is an
    untracked file in the gated tree, which costs the gate-OK record (policy.no_bytecode)."""
    base = getattr(launcher, 'env', None)
    env = dict(os.environ if base is None else base)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def unmet(step, launcher):
    """The requirements of `step` that are missing: a command name not on PATH, or a probe argv
    that does not exit 0 within PROBE_TIMEOUT (run in the slot's job)."""
    missing = []
    for r in step.requires:
        if isinstance(r, str):
            if not shutil.which(r):
                missing.append(r)
            continue
        ok = False
        try:
            p = launcher.start(list(r), cwd=step.cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=child_env(launcher))
            ctime = process.proc_ctime(p.pid)
            try:
                ok = p.wait(PROBE_TIMEOUT) == 0
            except subprocess.TimeoutExpired:
                if ctime is not None:       # the probe and everything it started
                    process.kill_tree(p.pid, ctime)
                p.kill()
                p.wait()
        except (OSError, JobError, LockError):
            ok = False
        if not ok:
            missing.append(' '.join(r))
    return missing


def _run_cmd(step, launcher, log_path, give_back=False):
    """give_back: the gate's last step may give back cpu slots it no longer needs while it runs
    (kitlib.lock.giveback); no step runs after it that could need them."""
    more = {'give_back': True} if give_back else {}
    with open(log_path, 'wb') as log:
        try:
            p = launcher.start(step.cmd, cwd=step.cwd, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, env=child_env(launcher), **more)
        except (OSError, JobError, LockError) as e:
            log.write(('gate: could not start %s: %s\n' % (step.cmd, e)).encode('utf-8'))
            return None
        return launcher.wait(p)


def _run_check(step, launcher, log_path, ctx, checks):
    fn = (checks or {}).get(step.check)
    with open(log_path, 'a', encoding='utf-8') as log:
        if fn is None:
            log.write('gate: the hook module provides no check %r\n' % step.check)
            return None

        def run(argv, cwd=None):
            log.flush()
            p = launcher.start(list(argv), cwd=cwd or ctx['root'], stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, env=child_env(launcher))
            return launcher.wait(p)
        try:
            with policy.no_bytecode():   # a lazy sibling import writes no __pycache__ in the tree
                got = fn(dict(ctx, name=step.name, log=log, run=run))
        except KeyboardInterrupt:
            raise
        except SystemExit as e:     # a check never ends the gate, whatever code it exits with
            log.write('gate: check %s exited: SystemExit, code %r; a check returns, it does not '
                      'exit\n%s' % (step.check, e.code, traceback.format_exc()))
            return 1
        except BaseException:
            log.write('gate: check %s raised:\n%s' % (step.check, traceback.format_exc()))
            return 1
        if got is True:
            return 0
        if got is False:
            return 1
        if type(got) is int:
            return got
        log.write('gate: check %s returned %r; a check returns an int or a bool\n' % (step.check, got))
        return 1


def _read(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return ''


def run_steps(launcher, chosen, gate, ctx, log_dir, say=None, checks=None, halted=None):
    """[{name, status, reasons, seconds, log}] for `chosen` run in order with `launcher`. status:
    PASS, WARN, SKIP (a requirement missing), FAIL, or NOT RUN (after a failure of a step that
    does not `continue`). `halted`, asked before each step: once true no other step starts, not
    even a `continue` one, and the results so far are returned (a stop: gated, kitlib.gate.rerun)."""
    say = say or _quiet
    results, stopped = [], None
    for step in chosen:
        if halted is not None and halted():
            break
        log_path = os.path.join(log_dir, step.name + '.log')
        if stopped:
            results.append({'name': step.name, 'status': 'NOT RUN', 'seconds': None, 'log': None,
                            'reasons': ['%s failed first' % stopped]})
            continue
        t0 = time.monotonic()
        missing = unmet(step, launcher)
        if missing:
            status = 'FAIL' if step.requires_strict else 'SKIP'
            reasons = ['requires %s, which %s missing' % (', '.join(missing),
                                                          'is' if len(missing) == 1 else 'are')]
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write('gate: %s\n' % reasons[0])
        else:
            if step.check:
                rc = _run_check(step, launcher, log_path, ctx, checks)
            else:
                rc = _run_cmd(step, launcher, log_path, give_back=step is chosen[-1])
            if rc is None:
                status, reasons = 'FAIL', ['did not start (see its log)']
            else:
                status, reasons = S.judge(step, _read(log_path), rc, gate)
        secs = round(time.monotonic() - t0, 1)
        say('%-5s %s (%.1f s)%s' % (status, step.name, secs,
                                    ': ' + '; '.join(reasons[:3]) if reasons else ''))
        results.append({'name': step.name, 'status': status, 'reasons': reasons, 'seconds': secs,
                        'log': log_path})
        if status == 'FAIL' and not step.cont:
            stopped = step.name
    return results


def verdict(results, no_checks=False):
    """`(ok, the verdict line)`: `GATE OK ...` or `GATE FAILED: ...`; NO_CHECKS for an
    auto-detected gate that found no stack (`no_checks`)."""
    if no_checks and not results:
        return True, NO_CHECKS
    failed = [r for r in results if r['status'] == 'FAIL']
    if failed:
        first = failed[0]
        return False, 'GATE FAILED: %s (%s: %s)' % (', '.join(r['name'] for r in failed),
                                                    first['name'], first['reasons'][0])
    notes = []
    for status, word in (('WARN', 'warned'), ('SKIP', 'skipped'), ('REUSED', 'reused')):
        names = [r['name'] for r in results if r['status'] == status]
        if names:
            notes.append('%s: %s' % (word, ', '.join(names)))
    return True, 'GATE OK' + (' (%s)' % '; '.join(notes) if notes else '')


def nothing_ran(results):
    """The REFUSED line when no step PASSed, WARNed, FAILed or was REUSED, i.e. every selected
    step was skipped for a missing `requires`; else None. A REUSED step counts though it did
    not run here: a pass of its key (its inputs, argv, cwd, program, gate config and kit files)
    is in the gate cache (kitlib.gate.reuse). So a gate whose every step is reused passes, and a
    branch rerun of that kind writes a gate-OK record for a commit on which no step ran."""
    if any(r['status'] in ('PASS', 'WARN', 'FAIL', 'REUSED') for r in results):
        return None
    skipped = ['%s: %s' % (r['name'], '; '.join(r['reasons']) or 'skipped') for r in results]
    return ('REFUSED: no gate step ran: every selected step was skipped (%s); no gate-OK record is '
            'written. Install what they require, or give the gate a step that runs here'
            % '; '.join(skipped))


def record_steps(results):
    return [{'name': r['name'], 'result': RESULT[r['status']], 'seconds': r['seconds']}
            for r in results]


# ---------------------------------------------------------------- gate reuse (kitlib.gate.reuse)

def _reuse_log(logs, lines, say, mode):
    path = os.path.join(logs, 'reuse.log')
    try:
        with open(path, mode, encoding='utf-8') as f:
            f.write(''.join(line + '\n' for line in lines))
    except OSError as e:
        say('WARNING: %s could not be written: %s' % (path, e))


def _say_plan(plan, chosen, logs, say):
    """Say a reuse.Plan's lines and keep them in `<logs>/reuse.log`: {reused step: why}."""
    if plan is None:
        return {}
    reused = plan.reused
    lines = plan.lines + ['%-5s %s: %s' % ('REUSED', s.name, reused[s.name])
                          for s in chosen if s.name in reused]
    for line in lines:
        say(line)
    if lines:                       # no lines: no step asks for reuse, nothing to keep
        _reuse_log(logs, lines, say, 'w')
    return reused


def _to_run(chosen, reused):
    run = [s for s in chosen if s.name not in reused]
    if reused and all(s.name == 'setup' for s in run):
        run = []                    # setup prepares for steps, and every step is reused
    return run


def _with_reused(results, chosen, reused):
    """`results` with a REUSED result for each reused step, in the order of `chosen`, then any
    result that is no step's (window_results' `windows`)."""
    if not reused:
        return results
    by_name = {r['name']: r for r in results}
    steps = {s.name for s in chosen}
    return [by_name[s.name] if s.name in by_name else dict(
        {'name': s.name, 'status': 'REUSED', 'seconds': None, 'log': None},
        reasons=[reused[s.name]]) for s in chosen if s.name in by_name or s.name in reused] + [
        r for r in results if r['name'] not in steps]


def _keep(plan, results, logs, say):
    """Store the results a reuse.Plan asks for (its keep never raises); say its lines and add
    them to `<logs>/reuse.log`. A failure here never changes the gate's verdict."""
    if plan is None or plan.keep is None:
        return
    try:
        lines = plan.keep(results)
    except Exception as e:          # noqa: BLE001 - storing never fails the gate
        lines = ['gate: reuse: NOT STORED: %s: %s' % (type(e).__name__, e)]
    for line in lines:
        say(line)
    if lines:
        _reuse_log(logs, lines, say, 'a')


def plan_lines(chosen, why, all_steps, base_text, changed):
    lines = ['base %s; %d changed path(s)' % (base_text, len(changed))]
    lines += ['RUN   %s (%s)' % (s.name, why[s.name]) for s in chosen]
    picked = {s.name for s in chosen}
    lines += ['skip  %s' % s.name for s in all_steps if s.name not in picked]
    return lines


def load_checks(cfg, steps, root=None, rev=None):
    """`(checks, options, None)`: the hook module's checks when a step of `steps` has a check,
    else `({}, {}, None)`; loaded from the work tree (policy.load_gate_checks), or from commit
    `rev` of the repository at `root` when given (hookrev.load_gate_checks_at; a landing's
    --expect-main). `(None, None, why)` when the module cannot be loaded: a ConfigError, or a
    SystemExit from the repo's module-level code. The one load-failure predicate of gated, the
    targeted rerun (kitlib.gate.rerun) and the daily full run's bisect probe."""
    if not any(s.check for s in steps):
        return {}, {}, None
    try:
        if rev:
            checks, options = hookrev.load_gate_checks_at(cfg, root, rev)
        else:
            checks, options = policy.load_gate_checks(cfg)
    except ConfigError as e:
        return None, None, str(e)
    except SystemExit as e:         # the repo's module-level code; nothing has run yet
        return None, None, ('the hook module exited while it was loaded (SystemExit, code %r); '
                            'nothing ran' % (e.code,))
    return checks, options, None


def gated(cfg, root, base, changed, chosen, kind, name, slug, log_dir, say, book_kw, merged=False,
          checks_rev=None, gate=None, slot=None, seen=None, capped=False, budget_branch=None,
          unchanged=None, ran=None):
    """Runs `chosen` in a slot of `kind`: `(results, None)`, or `(None, Outcome)` when the hook
    module or the booking refuses. The hook module's checks come from the work tree, or from
    commit `checks_rev` when given (a landing: --expect-main). `chosen` is never empty. The slot
    is booked as a gate run, so the build daemons the gate leaves that serve nothing outside its
    job are stopped at its end (kitlib.lock.site, machine_lock.daemons; kitlib.proc.daemons).
    `gate`: the gate the steps are judged by (detect.effective); default the configured one.
    `slot`: a Slot the caller already holds (a landing's, kitlib.gate.land): the steps run in it,
    nothing is booked and the slot is not released here (its holder releases it); once the steps
    finish, their build daemons are stopped (Slot.stop_daemons, as a gate's release does) and
    the rest of their tree is ended (Slot.end_tree), and an exception or Ctrl+C ends the tree
    first, so nothing the gate started runs on into the push or an undo.
    After the steps it says the run's console-window report (window_results), in a held `slot`
    too (its monitor stops there, before its daemons and tree are ended); `seen`, a dict when
    given, gets `windows`: the windows as dicts, None when nothing was watched.
    The lock run records the tree it tests (tested_tree; book's `tree`, Slot.note_tree for a held
    slot): a branch's HEAD tree, a landing's merge tree, the daily full run's commit's tree.
    It records the gate's mode too, `book_kw`'s gate_mode (book's; Slot.note_tree for a held slot).
    `capped`: a branch gate's booking first waits for the session gate cap (kitlib.gate.gatecap,
    in the store it books in), which counts the session's gates and takes this gate's turn in one
    step, so a burst books no more than the cap; once the booking holds, gatecap.gave_way names
    the run in its turn and, as a backstop for a gate the count missed, gives the slot back and
    books again while the session's other gates are at the cap; a landing and the daily full run
    leave it False and never wait. A capped gate whose tip is already in the main branch
    (merged_tip) is REFUSED before it books, and again once its booking holds, before
    any step (released `not started`); before it books it also stops the session's own queued
    gates of the branch at an older tip (gatecap.replace_stale; docs/gate-records.md "The gate queue").
    `budget_branch`: with `capped`, the branch whose gate budget (kitlib.gate.budget) is asked
    first, before the cap is waited for or anything is booked: `(None, REFUSED Outcome)` when it
    is spent. None: not asked (a detached HEAD). The run is booked with it as book's `branch`,
    which its history line keeps, so the budget counts the run by its branch, not its slug.
    `unchanged`: with `capped`, unchanged(tree) gives the REFUSED lines of a gate that would only
    repeat a failed gate of the same tree (_unchanged), or None; asked after merged_tip before it
    books, and again at its turn (released `not started`), as merged_tip is.
    `ran`: ran(results) once the booked steps ran and no stop came, before the slot is released,
    so a gate queued behind this one finds what ran writes (a red run's red-tree mark) at its turn.
    A stop of the booked run (kitlib.gate.stop, machine_lock.py stop), whether it comes while the
    run waits, in a step (Slot.wait ends that step's tree) or after the last one, gives
    `(None, stopped_outcome)`: no other step starts and the run is released `cancelled`. A held
    `slot` is the landing's, which keeps its own way of ending."""
    checks, options, why = load_checks(cfg, chosen, root, checks_rev)
    if checks is None:
        return None, G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    ctx ={'root': root, 'base': base, 'merged': merged, 'changed': list(changed),
           'options': options}
    tree = tested_tree(root)
    if slot is not None:
        say('the gate runs in the slot the landing holds (run %s)' % slot.id)
        slot.note_tree(tree, (book_kw or {}).get('gate_mode'))
        try:
            results = run_steps(slot.launcher, chosen, gate or cfg.section('gate'), ctx, log_dir,
                                say, checks)
        except BaseException:
            try:
                slot.end_tree()
            except BaseException:   # noqa: BLE001 - the holder's release ends the tree again
                pass
            raise
        # The steps are done: their window report first, as a booked gate says it before its
        # release; then what they left is stopped, as a gate's own release does (stop_daemons),
        # and the rest of their tree is ended (end_tree), not left running in the slot through
        # the push until the holder releases it. The slot stays held.
        results += window_results(slot, say, seen)
        slot.stop_daemons()
        slot.end_tree()
        return results, None
    if capped:
        merged = merged_tip(cfg, root, budget_branch, tree)
        if merged:
            return None, merged_refusal(budget_branch, merged, base, 'nothing queued')
        repeat = unchanged(tree) if unchanged is not None else None
        if repeat:
            return None, unchanged_outcome(repeat, 'nothing queued')
        store = (book_kw or {}).get('store') or Store()
        over = budget.refusal(root, store, name, budget_branch, say)
        if over:
            return None, G.Outcome(G.REFUSED, over, record=None,
                                   no_record='the branch gate budget')
        gatecap.replace_stale(store, name, budget_branch, refs.head(root)[0], say)
    mine = {'branch': budget_branch} if capped and budget_branch else {}
    while True:
        if capped:
            gatecap.wait_turn(store, name, say)
        try:
            slot = lockrun.book(kind, name, slug,
                                **dict(book_kw or {}, gate=True, tree=tree, **mine))
        except LockError as e:
            return None, book_refusal(e, log_dir, base)
        with slot:
            # Its turn: a tip the main branch took while it queued needs no gate. A run asked to
            # stop is left to its stop (no step starts).
            if capped and not slot.stop_asked():
                merged = merged_tip(cfg, root, budget_branch, tested_tree(root))
                if merged:
                    slot.release(1, budget.NOT_STARTED)
                    return None, merged_refusal(
                        budget_branch, merged, base,
                        'the queued gate (run %s) was dropped at its turn; nothing ran' % slot.id)
                # A gate of the same tree that ran ahead of it while it queued, and failed.
                repeat = unchanged(tested_tree(root)) if unchanged is not None else None
                if repeat:
                    slot.release(1, budget.NOT_STARTED)
                    return None, unchanged_outcome(
                        repeat, 'the queued gate (run %s) was dropped at its turn; nothing ran'
                        % slot.id)
            # Its turn now names this run (gatecap.gave_way). A backstop for a gate the count
            # missed: with the session's other gates at the cap, it gives the slot back and books
            # again, unless a stop came while it gave the run back.
            if capped and gatecap.gave_way(store, name, slot, say):
                if gatecap.stopped(slot):
                    return None, stopped_outcome('gate run %s was stopped' % slot.id, log_dir,
                                                 base)
                continue
            say(slot.held_text())
            results = run_steps(slot.launcher, chosen, gate or cfg.section('gate'), ctx, log_dir,
                                say, checks, halted=slot.stop_asked)
            results += window_results(slot, say, seen)
            if slot.stop_asked():
                slot.release(1, 'cancelled')
                return None, stopped_outcome('gate run %s was stopped' % slot.id, log_dir, base)
            if ran is not None:
                ran(results)
            slot.release(0 if verdict(results)[0] and not nothing_ran(results) else 1, 'done')
        return results, None


def tested_tree(root):
    """The git tree hash of HEAD in `root` when its work tree is clean (no uncommitted or
    untracked path), else None: a gate of uncommitted files tests no commit's tree. None too
    when git cannot answer; the tree is a note on the lock run, never a reason to refuse."""
    entries, _ = gitstatus.status(root)
    if entries != []:
        return None
    tree, _ = refs.tree_of('HEAD', root)
    return tree or None


def merged_tip(cfg, root, branch, tree):
    """`(HEAD, main ref)` when a branch gate of `root` is not needed: its HEAD is already in the
    main branch (default_base, `<remote>/<main>` else `<main>`). Only on git's exact yes
    (refs.is_ancestor) for a clean work tree (`tree`, tested_tree, not None) on a branch other
    than the main branch; None otherwise: a detached HEAD, uncommitted files (they are what is
    gated), a no, or any git failure (the gate runs)."""
    if not branch or tree is None or branch == cfg.main_branch:
        return None
    try:
        head, _ = refs.head(root)
        ref, _ = default_base(cfg, root)
        if not head or not ref:
            return None
        yes, _ = refs.is_ancestor(head, ref, root)
    except Exception:               # noqa: BLE001 - a check that cannot answer never drops a gate
        return None
    return (head, ref) if yes is True else None


def unchanged_outcome(lines, then):
    """The REFUSED outcome of a gate _unchanged found a repeat: its REFUSED line ends with
    `then` (what was done), its Instead line follows. No gate-OK record."""
    return G.Outcome(G.REFUSED, ['%s; %s' % (lines[0], then)] + list(lines[1:]), record=None,
                     no_record=UNCHANGED_NO_RECORD)


def merged_refusal(branch, merged, base, then):
    """The REFUSED outcome of a gate merged_tip found not needed; `then` says what was done. Its
    Instead line names the way to gate that tip on purpose: a detached HEAD, which merged_tip
    never refuses."""
    head, ref = merged
    return G.Outcome(G.REFUSED, [
        'REFUSED: %s at %s is already in %s, so it needs no gate; %s' % (
            branch, head[:10], ref, then),
        'Instead: to gate that tip on purpose, gate a detached-HEAD worktree of it: '
        'git worktree add --detach <path> %s' % head], base=base, record=None,
        no_record='the tip is already in %s' % ref)


def window_results(slot, say, seen=None):
    """Stops the lock run's window monitor (Slot.watch_stop, kitlib.platform.windows.winwatch)
    and says its report: `windows opened: N` and a line per window. Returns [] (a window warns),
    or with WINDOWS_FAIL a FAIL result named `windows` when the run opened one. Fills
    seen['windows'] when `seen` is a dict. Each line is said through _say_safe: it runs inside
    the held slot, where a console that cannot print a window's title (cp1252, U+2733) would
    end the run `error` though every step passed."""
    windows = slot.watch_stop()
    for line in slot.window_lines(fails=WINDOWS_FAIL):
        _say_safe(say, line)
    if seen is not None:
        seen['windows'] = None if windows is None else [w.as_dict() for w in windows]
    n = len(winwatch.counted(windows))
    if not (WINDOWS_FAIL and n):
        return []
    return [{'name': 'windows', 'status': 'FAIL', 'seconds': None, 'log': None,
             'reasons': ['%d console window(s) opened' % n]}]


def ascii_line(line):
    """`line` as text escaped to ASCII (backslashreplace): any console can print it."""
    return str(line).encode('ascii', 'backslashreplace').decode('ascii')


def _say_safe(say, line):
    """say(ascii_line(line)); a stream that cannot be written (closed, broken: OSError, or an
    encoding error) drops the line rather than raising. Any other exception from `say` is
    raised: a report line is never worth ending the run over, a bug in `say` still shows."""
    try:
        say(ascii_line(line))
    except (UnicodeError, OSError):
        pass


def lock_refusal(e):
    """The Outcome of a machine-lock LockError: its USAGE code is worded `USAGE:` (refusal-log P10;
    an unknown "gate".kind, say), any other code is the lock refusing the gate."""
    if e.code == EXIT_USAGE:
        msg = re.sub(r'^usage:\s*', '', e.message or '')
        return G.Outcome(G.USAGE, ['USAGE: the machine lock: %s (check .kit.json "gate".kind)' % msg])
    return G.Outcome(e.code, ['NOT RUN: the machine lock refused the gate: %s' % e.message])


def stopped_outcome(what, log_dir=None, base=None):
    """A gate a stop ended: `STOPPED: <what>; ...`, exit G.STOPPED, no gate-OK record: nothing was
    judged, so its result record's verdict is `stopped`, neither a pass nor a fail."""
    lines = ['STOPPED: %s; no verdict and no gate-OK record' % what]
    if log_dir:
        lines.append('logs: %s' % log_dir)
    return G.Outcome(G.STOPPED, lines, base=base, log_dir=log_dir, record=None,
                     no_record='the gate was stopped')


def book_refusal(e, log_dir=None, base=None):
    """The Outcome of a LockError from a gate's own booking: lock_refusal's, but a stop of the run
    while it waited is STOPPED. The lock's code for that is 1, the same as for a command it could
    not start, so its words tell them apart (CANCELLED_WAITING). A landing's booking on main
    (Lander.book_slot) stays with lock_refusal; a load node's land gate books here, as a plain gate
    (kitlib.gate.stop)."""
    if CANCELLED_WAITING in (e.message or ''):
        return stopped_outcome('the gate was stopped while it waited for the machine lock',
                               log_dir, base)
    return lock_refusal(e)


# ---------------------------------------------------------------- the branch gate

def branch_gate(root, base=None, full=False, only=None, plan=False, slug=None, log_dir=None,
                say=None, book_kw=None, again=False):
    """_branch_gate() between the two result records of the branch at HEAD (kitlib.gate.result):
    `running` before it, the outcome's after it, or `error` when an exception, SystemExit or
    Ctrl+C escapes it (raised again, unchanged); the `error` record names the run's log folder
    once it was made, else none. None with `plan` or on a detached HEAD. The record lines are
    said as progress, never added to the outcome's lines. A gate that only repeats one already
    queued (queued_refusal) is REFUSED before either record: the queued run's records stay its
    own."""
    say = say or _quiet
    if not plan:
        twin = queued_refusal(root, base, full, only, book_kw)
        if twin is not None:
            return twin
    run = None if plan else _result_run(root)
    if run is not None:
        say(run.start()[1])
    known = {}                      # _gate_checkout: 'log_dir', once the run's folder is made
    try:
        out = _branch_gate(root, base=base, full=full, only=only, plan=plan, slug=slug,
                           log_dir=log_dir, say=say, book_kw=book_kw, known=known, again=again)
    except BaseException as e:
        if run is not None:
            try:
                say(run.error(e, log=known.get('log_dir'))[1])
            except Exception:       # noqa: BLE001 - never replace the exception being raised
                pass
        raise
    if run is not None:
        path, line = run.finish(out)
        say(line)
        out.facts['result_record'] = path
    return out


def queued_refusal(root, base, full, only, book_kw):
    """REFUSED, naming the run, when a gate of the checkout `root` is in already waits in the
    machine lock's queue for the same branch, tip, options and clean tree
    (gatecap.queued_twin): this one would gate the same thing after it. None otherwise, and
    whenever the checkout, its config, git or the lock store cannot be read (the gate runs).
    A detached HEAD or uncommitted files are never refused. The lock store is read only when the
    branch's result record says a gate at this tip is `running` (a queued gate wrote it before it
    booked): a gate with none reads no more of the store here than it did before."""
    try:
        top, _ = repo.toplevel(root)
        if not top:
            return None
        branch, _ = refs.current_branch(top)
        head, _ = refs.head(top)
        if not branch or not head:
            return None
        rec, _ = result.read(top, branch, 'gate')
        if not (rec and rec.get('verdict') == result.RUNNING
                and result.at_tip(rec, branch, 'gate', head)):
            return None
        note = gatecap.queue_note(head, full, only, base)
        store = (book_kw or {}).get('store') or Store()
        runs = [r for r in store.live_runs() if r.get('note') == note
                and r.get('branch') == branch]
        if not runs:
            return None
        cfg = kconfig.load(top)
        rid = gatecap.queued_twin(store, place.repo_name(cfg, top), branch, note,
                                  tested_tree(top), runs=runs)
    except Exception:               # noqa: BLE001 - a check that cannot answer never refuses
        return None
    if not rid:
        return None
    return G.Outcome(G.REFUSED, [
        'REFUSED: a gate of %s at %s with the same options is already queued: run %s; '
        'nothing queued' % (branch, head[:10], rid),
        'Instead: wait for its result: gate.py wait-result %s --tip %s -C %s' % (branch, head, top)],
        record=None, no_record='the same gate is already queued (run %s)' % rid, queued=rid)


def _result_run(root):
    """The result.Run of the checkout `root` is in (branch and HEAD now), or None."""
    try:
        top, _ = repo.toplevel(root)
        if not top:
            return None
        head, _ = refs.head(top)
        branch, _ = refs.current_branch(top)
        return result.Run.maybe(top, 'gate', branch, head)
    except Exception:               # noqa: BLE001 - no record, and the gate still runs
        return None


def _branch_gate(root, base=None, full=False, only=None, plan=False, slug=None, log_dir=None,
                 say=None, book_kw=None, known=None, again=False):
    """Gate the checkout `root` is in against `base` (default `<remote>/<main>`, else `<main>`; the
    merge base with HEAD is what is compared). Outcome codes: OK, FAILED, USAGE (an --only name
    no step has, a --base that does not resolve), REFUSED, or a machine lock code. Facts:
    verdict, results, record (the record's path or None), no_record (why none was written),
    head, base, log_dir, changed, windows (window_results: the console windows its run saw, as
    dicts; None when no step ran), source (kitlib.gate.detect: `gate.json` or `auto`, once the
    config is read). The last line, once the config is read, is the source line (detect.label).
    An auto-detected gate that found no stack runs nothing and passes, its verdict line saying
    so (NO_CHECKS). `known`, a dict: its 'log_dir' is set as soon as the run's log folder is
    made, so a caller still has it when an exception ends the run. A gate that would only repeat
    a failed gate of its clean tree is REFUSED before it books (_unchanged; docs/gate-records.md "An
    unchanged red tree"), unless `again` (gate.py --again)."""
    say = say or _quiet
    top, why = repo.toplevel(root)
    if not top:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    try:
        cfg = kconfig.load(top)
    except ConfigError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])
    seen = [detect.effective(cfg, top)]
    out = _gate_checkout(top, cfg, seen, base=base, full=full, only=only, plan=plan, slug=slug,
                         log_dir=log_dir, say=say, book_kw=book_kw, known=known, again=again)
    out.facts['source'] = seen[0].source
    out.lines.append(detect.label(seen[0]))
    return out


def zero_count_warnings(gate):
    """The lines of steps.zero_count_passes(gate), or one line saying the check could not run:
    a warning only, so nothing it raises stops or fails the gate. Each line is escaped to ASCII
    (backslashreplace), so a console that cannot encode a pattern (cp1252) cannot raise either."""
    try:
        lines = list(S.zero_count_passes(gate))
    except Exception as e:          # noqa: BLE001 - a warning never fails or stops the gate
        lines = ['the zero-count "pass" check could not run: %s: %s' % (type(e).__name__, e)]
    return [ascii_line(line) for line in lines]


def _gate_checkout(top, cfg, seen, base=None, full=False, only=None, plan=False, slug=None,
                   log_dir=None, say=None, book_kw=None, known=None, again=False):
    """_branch_gate() once the config is read. seen[0]: the gate it runs (detect.effective),
    first from the checkout alone; an auto-detected one is detected again once the merge base is
    known, with the steps the merge base owes, and set back in seen[0]."""
    eff = seen[0]
    gate = eff.gate
    asked_base = base                 # the queue note names the --base given, not the default
    if gate.get('worktree_guard') and repo.classify(top).kind == repo.MAIN:
        return G.Outcome(G.REFUSED, ['REFUSED: %s is a main checkout, and "gate".worktree_guard '
                                     'gates only linked worktrees' % top])
    why = stash.refusal(top)          # before --plan too: no gate command answers past an entry
    if why:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
    head, why = refs.head(top)
    if not head:
        return G.Outcome(G.REFUSED, ['REFUSED: no HEAD commit: %s' % why])
    branch, _ = refs.current_branch(top)
    default, why = default_base(cfg, top)
    if base is None:
        base = default
        if base is None:
            return G.Outcome(G.REFUSED, ['REFUSED: %s' % why])
        if why:
            say(why)                  # the local main differs from the one measured against
    base_sha, why = refs.resolve(base, top)
    if not base_sha:
        return G.Outcome(G.USAGE, ['USAGE: --base %s: %s' % (base, why)])
    partial = None
    if default is None or refs.resolve(default, top)[0] != base_sha:
        partial = '--base %s is not the default base (%s): the gate was partial' % (
            base, default or 'none resolves')
    mb, why = refs.merge_base(base_sha, head, top)
    if not mb:
        return G.Outcome(G.REFUSED, ['REFUSED: %s and HEAD have no merge base%s' % (
            base, ': %s' % why if why else '')])
    changed, why = changed_since(top, mb)
    if changed is None:
        return G.Outcome(G.REFUSED, ['REFUSED: git cannot list the changed paths: %s' % why])
    if eff.source == detect.AUTO:
        try:
            seen[0] = eff = detect.effective(cfg, top, base=mb)
        except detect.DetectError as e:
            return G.Outcome(G.REFUSED, ['REFUSED: the auto-detected gate: %s' % e])
        gate = eff.gate
        say(detect.label(eff))
    empty = detect.nothing_detected(eff)
    from kitlib.gate import reuse   # reuse imports this module
    docs = not (full or only or empty) and reuse.docs_only_change(cfg, gate, changed)
    if docs:
        say('a docs-only change: only the "docs_only" steps run (docs/gate-run.md "Docs-only changes")')
    try:
        all_steps = S.build(gate, top, mb, cfg.tools_dir, full=full)
        chosen, picked = S.select(gate, all_steps, changed, full=full, only=only,
                                  read=S.working_tree_reader(top), docs=docs)
    except S.UnknownStep as e:
        return G.Outcome(G.USAGE, ['USAGE: --only: %s' % e])
    except S.GateConfigError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])
    base_text = '%s (merge base with %s)' % (mb[:10], base)
    if plan:
        return G.Outcome(G.OK, plan_lines(chosen, picked, all_steps, base_text, changed),
                         chosen=[s.name for s in chosen])
    if not chosen and not empty:
        text = NO_STEP_FULL if full and not only else NO_STEP % len(changed)
        return G.Outcome(G.REFUSED, [text], record=None,
                         no_record='no gate step selected', head=head, base=mb, changed=changed)
    entries, why = gitstatus.status(top)
    if entries is None:
        return G.Outcome(G.REFUSED, ['REFUSED: git status failed: %s' % why])
    untracked = [p for xy, p in entries if xy == '??']
    if gate.get('warn_untracked') and untracked:
        say('WARNING: %d untracked file(s) are gated but not committed: %s' % (
            len(untracked), ', '.join(untracked[:10])))
    for line in zero_count_warnings(gate):
        say('WARNING: %s' % line)
    name = place.repo_name(cfg, top)
    started = gate_ok.utc()
    logs = new_log_dir(cfg, top, name, branch or head[:10], log_dir)
    if known is not None:
        known['log_dir'] = logs
    say('gate %s at %s, base %s: %s' % (branch or '(detached)', head[:10], base_text,
                                        ', '.join(s.name for s in chosen) or 'no step'))
    probes = {}         # the toolchains probed before the run (kitlib.gate.reuse)
    plan = None if empty else reuse.branch_plan(top, cfg, chosen, mb, changed, full=full,
                                                only=only, head=head, branch=branch, probes=probes)
    keys_kit = None     # the kit before the run: a record's input keys (kitlib.gate.reuse)
    keys_tools = None   # each step's toolchain before the run, the same way
    if not (only or partial or empty or docs or entries):
        try:
            keys_kit = reuse.kit_identity()
            keys_tools = reuse.toolchains(top, cfg, chosen, probes)
        except Exception as e:      # noqa: BLE001 - a key never fails the gate
            say(reuse.PREFIX + 'no input keys in the record: %s: %s' % (type(e).__name__, e))
    reused = _say_plan(plan, chosen, logs, say)
    run = _to_run(chosen, reused)
    results, watched = [], {}
    # a whole --full run: its gate_mode is full
    whole = full and not only and not partial and not empty
    # Its gate_mode, on the lock run and its history line: --only and a partial --base count as
    # select, with --full or of a docs-only change too. The red-tree marks key on it too.
    mode = 'full' if whole else 'docs' if docs and not partial else 'select'
    # a red whole run, --full or default-mode (not --only, a partial --base or docs-only), gets a
    # red-run record for a targeted rerun (_red_record)
    red_run = not (only or partial or empty) and mode in ('full', 'select')
    key = None          # the red-tree mark's key (rerun.mark_key), once it is asked for
    ended = []      # _unclean read once: the mark, the red run and the gate-OK record share it

    def unclean():
        if not ended:
            ended.append(_unclean(top, head, entries))
        return ended[0]

    marked = []

    def ran(got):
        # A red run's fail mark, written before the slot is released (gated's `ran`): a gate of
        # the same tree queued behind this one finds it at its turn. A pass is marked after the
        # release, as its gate-OK record reads _unclean: a child the release ends that writes to
        # the work tree before then still counts as the tree changing while the gate ran.
        got = _with_reused(got, chosen, reused)
        if key is not None and not nothing_ran(got) and not verdict(got, empty)[0]:
            marked.append(True)
            _mark(top, key, head, branch, got, logs, unclean(), say)
    if run:
        # capped and budget_branch: the session gate cap (kitlib.gate.gatecap) and the branch
        # gate budget (kitlib.gate.budget) are a branch gate's only; a landing and the daily full
        # run book through gated() without them, and a fully reused gate never gets here. Its
        # lock run's note names its tip and options (gatecap.queue_note): the gate queue's key.
        noted = dict({'note': gatecap.queue_note(head, full, only, asked_base), 'gate_mode': mode},
                     **(book_kw or {}))
        key = None if empty else _mark_key(cfg, mode, only, asked_base, say)
        unchanged = None
        if key is not None and not again:
            flags = ' '.join((['--full'] if full else []) +
                             (['--only'] + list(only) if only else []) +
                             (['--base', asked_base] if asked_base else []))

            def unchanged(tree):
                return _unchanged(top, key, tree, [s.name for s in run], branch, head, flags, say)
        results, refused = gated(cfg, top, mb, changed, run, gate['kind'], name,
                                 slug or place.slug_of(branch or head[:10]), logs, say, noted,
                                 gate=gate, seen=watched, capped=True,
                                 budget_branch=branch or None, unchanged=unchanged, ran=ran)
        if refused is not None:
            return refused
    results = _with_reused(results, chosen, reused)
    _keep(plan, results, logs, say)
    ok, line = verdict(results, empty)
    if key is not None and not marked and not nothing_ran(results):
        _mark(top, key, head, branch, results, logs, unclean(), say)
    lines = [line, 'logs: %s' % logs]
    facts = dict(verdict=line, results=results, record=None, no_record=None, head=head, base=mb,
                 log_dir=logs, changed=changed, windows=watched.get('windows'))
    if not ok:
        facts['no_record'] = 'the gate failed'
        if red_run:
            facts['rerun_record'] = _red_record(top, cfg, head, mb, started, results, chosen,
                                                logs, unclean(), say, whole)
        return G.Outcome(G.FAILED, lines, **facts)
    refused = None if empty else nothing_ran(results)
    if refused:
        facts['no_record'] = 'no gate step ran'
        return G.Outcome(G.REFUSED, [refused, 'logs: %s' % logs], **facts)
    if only:
        no = '--only gates part of the tree'
    elif partial:
        no = partial
    else:
        no = unclean()
    if no:
        facts['no_record'] = no
        lines.append('no gate-OK record: %s' % no)
        return G.Outcome(G.OK, lines, **facts)
    input_keys, why = reuse.record_keys(top, cfg, chosen, head, mb, results, keys_kit,
                                        keys_tools)
    if why:
        say(reuse.PREFIX + 'no input keys in the record: %s' % why)
    try:
        path, _rec = gate_ok.record_pass(top, head, gate_ok.gate_config(cfg), kit_identity(), name,
                                         started, record_steps(results), base=mb, config_rev=head,
                                         source=eff.source, full=bool(full),
                                         mode='docs' if docs else None, input_keys=input_keys)
    except gate_ok.NoRecordFolder as e:
        # Kept for load nodes set up without a dev_drive (docs/load-nodes.md): the .git copy.
        path = gate_ok.legacy_path(top, head)
        facts['record'] = path
        lines.append('warning: the gate-OK record could not be written to the record folder: %s'
                     % e)
        lines.append('gate-OK record (.git copy only): %s' % path)
        return G.Outcome(G.OK, lines, **facts)
    except gate_ok.RecordError as e:
        facts['no_record'] = str(e)
        lines.append('FAILED: the gate passed, and its gate-OK record could not be written: %s' % e)
        return G.Outcome(G.FAILED, lines, **facts)
    facts['record'] = path
    lines.append('gate-OK record: %s' % path)
    return G.Outcome(G.OK, lines, **facts)


def _unclean(top, head, entries):
    """Why a gate that started with git status `entries` names no one commit's tree now: the
    tree had uncommitted changes at the start, changed while the gate ran, HEAD moved, or a
    stash entry appeared (the stash was empty when the gate started: stash.refusal); '' or None
    when none of these. Read once per gate (_gate_checkout `unclean`): it reads the stash."""
    if entries:
        return 'the work tree had uncommitted changes when the gate started'
    after, _ = gitstatus.status(top)
    if after != []:
        return 'the work tree changed while the gate ran'
    head_now, _ = refs.head(top)
    if head_now != head:
        return 'HEAD moved while the gate ran (%s -> %s)' % (head[:10], (head_now or '?')[:10])
    return stash.appeared(top)


def _mark_key(cfg, mode, only, base, say):
    """rerun.mark_key, or None (said as a WARNING) when it cannot be made: no mark is read or
    written, and the gate runs."""
    from kitlib.gate import rerun   # rerun imports this module
    try:
        return rerun.mark_key(cfg, mode, only, base)
    except Exception as e:          # noqa: BLE001 - a mark never stops or fails the gate
        say('WARNING: no red-tree mark: %s: %s' % (type(e).__name__, e))
        return None


def _unchanged(top, key, tree, steps, branch, head, flags, say):
    """The REFUSED and Instead lines of a branch gate of `tree` that would only repeat a failed
    gate of it (rerun.unchanged_red), or None: none, an unclean tree (`tree` None), or a mark
    that cannot be read (said as a WARNING; the gate runs)."""
    from kitlib.gate import rerun   # rerun imports this module
    try:
        rec = rerun.unchanged_red(top, key, tree, steps)
    except rerun.NoMarkFolder:
        return None
    except rerun.RerunError as e:
        say('WARNING: the red-tree mark is not read, so the gate runs: %s' % e)
        return None
    if rec is None:
        return None
    rerun_way = ('; or, for this red --full, a targeted rerun: gate.py -C %s --rerun' % top
                 if rec.get('mode') == 'full' else '')
    return ['REFUSED: the tree of %s at %s (tree %s) failed its last %s gate with this gate '
            'config and kit at %s (%s; logs: %s)' % (
                branch or '(detached)', head[:10], tree[:10], rec.get('mode'), rec.get('ended'),
                ', '.join(rec.get('failed') or []) or 'failed', rec.get('log_dir')),
            'Instead: fix it and commit%s; or, to repeat it on purpose (docs/gate-records.md "An '
            'unchanged red tree"): gate.py -C %s%s --again' % (
                rerun_way, top, ' ' + flags if flags else '')]


def _mark(top, key, head, branch, results, logs, unclean, say):
    """The red-tree mark of a branch gate that ran (rerun.mark), on the gate-OK record's own
    terms (`unclean`, _unclean's answer): a gate of no one commit's tree writes none, nor does
    one whose every FAIL is the machine's (rerun.machine_failure; said). Nothing here changes the
    gate's verdict; a mark that cannot be written is one WARNING."""
    from kitlib.gate import rerun   # rerun imports this module
    if unclean:
        return
    tree, _ = refs.tree_of('HEAD', top)
    if not tree:
        return
    try:
        path = rerun.mark(top, key, tree, head, branch or None, results, logs)
    except rerun.NoMarkFolder:
        return
    except rerun.RerunError as e:
        say('WARNING: no red-tree mark: %s' % e)
        return
    if path is None:
        say('no red-tree mark: every FAIL is the machine\'s (a step that did not start, a tool it '
            'requires missing, or kit_tests units that timed out)')


def _red_record(top, cfg, head, mb, started, results, chosen, logs, no, say, full):
    """The red run of a failed whole gate, --full (`full`) or default-mode
    (kitlib.gate.rerun.record_red), written on the gate-OK record's own terms (`no`, _unclean's answer): the tree clean when the gate started and when it
    ended, HEAD unmoved and the stash empty. Its path, or None (said). Nothing here changes the
    gate's verdict."""
    from kitlib.gate import rerun   # rerun imports this module
    if no:
        say('no red-run record for a targeted rerun: %s' % no)
        return None
    try:
        path = rerun.record_red(top, cfg, head, mb, started, results, chosen, logs, full)
    except rerun.RerunError as e:
        say('WARNING: no red-run record for a targeted rerun: %s' % e)
        return None
    say('red-run record: %s; a targeted rerun of the failing tests: gate.py --rerun '
        '(docs/gate-rerun-checkout.md "Targeted rerun")' % path)
    return path


# ---------------------------------------------------------------- the merged-tree gate

def merged_gate(root, cfg, base, full=False, slug='land', log_dir=None, say=None, book_kw=None,
                reuse=None, slot=None, tree=None, docs=False):
    """_merged_gate() with its source (kitlib.gate.detect) in facts['source'] and as the last
    line. With no "gate" section in `cfg` the gate is detected from the merged tree, plus the
    steps `base` owes (detect.effective). `slot`: the Slot the landing already holds; the steps
    run in it and no second slot is booked (gated). `tree`: the checkout holding the merge (a
    landing's landing tree, kitlib.gate.land); default `root`. `docs`: a docs-only merge may run
    only its docs steps (_merged_gate)."""
    try:
        eff = detect.effective(cfg, tree or root, base=base)
    except detect.DetectError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: the auto-detected gate: %s' % e])
    if eff.source == detect.AUTO:
        (say or _quiet)(detect.label(eff))
    out = _merged_gate(root, cfg, eff, base, full=full, slug=slug, log_dir=log_dir, say=say,
                       book_kw=book_kw, reuse=reuse, slot=slot, tree=tree, docs=docs)
    out.facts['source'] = eff.source
    out.lines.append(detect.label(eff))
    return out


def _merged_choice(cfg, eff, tree, base, full, docs):
    """What _merged_gate selects at HEAD of `tree` against `base`, running nothing: (head,
    changed, chosen, docs, empty), or the REFUSED Outcome it would end with."""
    empty = detect.nothing_detected(eff)
    head, why = refs.head(tree)
    if not head:
        return G.Outcome(G.REFUSED, ['REFUSED: no HEAD commit: %s' % why])
    changed, why = changed_between(tree, base, head)
    if changed is None:
        return G.Outcome(G.REFUSED, ['REFUSED: git cannot list the merged paths: %s' % why])
    from kitlib.gate import reuse as R     # reuse imports this module; `reuse` is the planner
    docs = bool(docs) and not (full or empty) and R.docs_only_change(cfg, eff.gate, changed)
    try:
        all_steps = S.build(eff.gate, tree, base, cfg.tools_dir, merged=True, full=full)
        chosen, _ = S.select(eff.gate, all_steps, changed, full=full, docs=docs,
                             read=S.working_tree_reader(tree))
    except S.GateConfigError as e:
        return G.Outcome(G.REFUSED, ['REFUSED: %s' % e])
    if not chosen and not empty:
        return G.Outcome(G.REFUSED, [NO_STEP_FULL if full else NO_STEP % len(changed)], head=head)
    return head, changed, chosen, docs, empty


def merged_steps(root, cfg, base, full=False, tree=None, docs=False):
    """What merged_gate() with these arguments would run, without running anything:
    `({'head', 'steps': [names, in order], 'docs': bool, 'source'}, None)`, or `(None, why)` when
    it would refuse. A landing whose merged gate runs on a load node checks the node's record
    against this (kitlib.loadnode.landgate)."""
    tree = tree or root
    try:
        eff = detect.effective(cfg, tree, base=base)
    except detect.DetectError as e:
        return None, 'the auto-detected gate: %s' % e
    picked = _merged_choice(cfg, eff, tree, base, full, docs)
    if isinstance(picked, G.Outcome):
        return None, '; '.join(picked.lines)
    head, _changed, chosen, docs, _empty = picked
    return {'head': head, 'steps': [s.name for s in chosen], 'docs': docs,
            'source': eff.source}, None


def _merged_gate(root, cfg, eff, base, full=False, slug='land', log_dir=None, say=None,
                 book_kw=None, reuse=None, slot=None, tree=None, docs=False):
    """Gate the merged tree at HEAD of `tree` (a landing's merge, in its landing tree; default
    `root`) against `base` (the main it was merged into). The steps run in `tree`, and their hook
    checks get it as ctx['root']; the repository's name and its log folder are `root`'s (the main
    checkout's), wherever the tree is. The steps are selected as a default-mode branch gate
    selects them (steps.select): each step the paths base..HEAD change pick, the smoke steps and
    setup, each running its cmd (ARCHITECTURE.md "What a landing runs"). With `full` (a landing
    whose diff changes a gate config file, kitlib.gate.land config_escalate) every non-manual step
    is selected, as gate.py --full selects them, and each runs its full_cmd where it has one. A
    step whose result needs an ignored cache the fresh landing tree lacks must be `always` or
    rebuild it in `setup`. Each step's merged_args are appended. Outcome codes:
    OK, FAILED, REFUSED (also when no step is selected: no step covers the change, every step is
    manual, or each is dropped by another's skip_if_selected) or a machine lock code; facts verdict,
    results, log_dir, head, windows (as the branch gate's), docs. Check steps run the hook module
    of commit `base`, as the config is read from it (kitlib.gate.hookrev), never the merged tree's.

    `docs` True and not `full`: when base..HEAD is a docs-only change under `cfg` (the base's;
    kitlib.gate.reuse.docs_only_change: its "gate".docs_only, never a config, hook or strict path)
    only the docs_only steps (and setup) run, and facts['docs'] is True (the landing records the
    merge with mode "docs"; docs/gate-run.md "Docs-only changes").

    `reuse(chosen) -> (reuse.Plan)` (kitlib.gate.reuse.planner): the steps it names are not run
    and count as REUSED; its lines, then the lines of its keep (the results it stores in the gate
    cache), are said and kept in `<log dir>/reuse.log`. `setup` runs only when some step runs,
    and no slot is booked when none does. An auto-detected gate that found no stack runs nothing
    and passes (NO_CHECKS). `slot`: a held Slot to run in instead of booking one (gated)."""
    say = say or _quiet
    tree = tree or root
    gate = eff.gate
    picked = _merged_choice(cfg, eff, tree, base, full, docs)
    if isinstance(picked, G.Outcome):
        return picked
    head, changed, chosen, docs, empty = picked
    name = place.repo_name(cfg, root)
    logs = new_log_dir(cfg, root, name, 'land-' + slug, log_dir)
    if docs:
        say('a docs-only merge: only the "docs_only" steps run (docs/gate-run.md "Docs-only changes")')
    say('merged-tree gate at %s against %s: %s' % (
        head[:10], base[:10], ', '.join(s.name for s in chosen) or 'no step'))
    plan = reuse(chosen) if reuse is not None and not empty else None
    reused = _say_plan(plan, chosen, logs, say)
    run = _to_run(chosen, reused)
    results, watched = [], {}
    if run:
        # Its gate_mode: the selected steps with cmd are "merged"; every step with full_cmd, "full".
        mode = 'docs' if docs else 'full' if full else 'merged'
        results, refused = gated(cfg, tree, base, changed, run, gate['kind'], name,
                                 place.slug_of(slug), logs, say, dict(book_kw or {}, gate_mode=mode), merged=True,
                                 checks_rev=base, gate=gate,
                                 slot=slot, seen=watched)
        if refused is not None:
            return refused
    results = _with_reused(results, chosen, reused)
    _keep(plan, results, logs, say)
    ok, line = verdict(results, empty)
    refused = nothing_ran(results) if ok and not empty else None
    if refused:
        return G.Outcome(G.REFUSED, [refused, 'logs: %s' % logs], verdict=line, results=results,
                         log_dir=logs, head=head, windows=watched.get('windows'), docs=docs)
    return G.Outcome(G.OK if ok else G.FAILED, [line, 'logs: %s' % logs], verdict=line,
                     results=results, log_dir=logs, head=head, windows=watched.get('windows'),
                     docs=docs)
