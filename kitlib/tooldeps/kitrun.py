"""kit_tests' orchestration (tools/kit_tests.py, docs/test-tools.md "kit_tests"): from the parsed
options and the checkout to the run's exit status - the tier choice, the base and change list, the
selection and its smoke modules, the one pass or the fake-git pass then the real-git pass, and the
cpu slots given back between them.

`run(a, top, out)` takes the argparse namespace the tool parsed (its attributes only), the
checkout and the stream the run's lines go to; it writes them as it goes, because a run lasts
minutes and its lines are the progress. It returns the exit status (0 passed or nothing selected,
1 failed, 2 USAGE or a refusal, 130 Ctrl+C from the first pass's count). It prints nothing to the
user itself, reads no argv and never exits. A KeyboardInterrupt elsewhere propagates to the tool.
"""
import os
import sys
import time

from kitlib import config as kconfig
from kitlib.git import refs as gitrefs
from kitlib.git import repo as gitrepo
from kitlib.lock import giveback
from kitlib.proc import spawn
from kitlib.pytests import runner as unit_runner
from kitlib.tooldeps import selfrun, selftree, testcost, tiers
from kitlib.tooldeps.childenv import child_env

USAGE = 2
FULL = ['-m', 'unittest', 'discover', '-s', 'tests', '-t', '.']


def default_base(top):
    """`(rev, note)`: the shared default base (kitlib.git.refs.default_base) for the checkout's
    .kit.json "repo" remote and main branch; origin and main when .kit.json cannot be read."""
    try:
        disk = kconfig.load(top)
        remote, main = disk.remote, disk.main_branch
    except kconfig.ConfigError:
        remote, main = 'origin', 'main'
    return gitrefs.default_base(top, remote, main)


def plan(root, changed, cfg, say):
    """The test modules to run (FULL: all; None: nothing), after printing the selection."""
    tree = selftree.scan(root)
    picked, why, notes = selftree.selection(changed, tree, cfg)
    for f in notes:
        if selftree._matches(f, cfg['prose']):
            say('  (no test)  %s: prose no test names' % f)
        else:
            say('  (no test)  nothing reads %s: always-run set only' % f)
    if picked == selftree.ALL:
        say('kit_tests: %s' % why[0])
        return FULL
    if not picked:
        say('kit_tests: OK, nothing selected (%d changed path(s))' % len(changed))
        return None
    packages, singles = selftree.split_picked(picked)
    shown = {p: ('tests/' + p if p in packages else p.replace('.', '/')) for p in picked}
    width = max(len(v) for v in shown.values()) + 6
    for p in picked:
        say('  %s %s' % (shown[p].ljust(width), why[p][0]))
        for more in why[p][1:]:
            say('  %s %s' % (' ' * width, more))
    modules = selftree.test_modules(root, packages)
    modules += [m for m in singles if m not in modules]
    say('kit_tests: selected %d of %d test module(s) (%d of %d test package(s) whole), from %d '
        'changed path(s)' % (len(modules), len(selftree.all_test_modules(root)), len(packages),
                             len(tree.tests), len(changed)))
    return modules


def drainer(say):
    """The pool's give back (kitlib.lock.giveback.drainer), its lines said through `say`; None,
    and a line that says so, when the lock run offers none."""
    give = giveback.drainer(lambda line: say('kit_tests: ' + line))
    if give is None:
        say('kit_tests: give back: no offer (only a run of this kit\'s lock wrapper offers one); '
            'the run keeps its cpu slots to its end')
    return give


def units_env(fake):
    """The environment the units of a pass get: child_env() with KIT_FAKE_GIT `fake`. selfrun
    drops the give back's offer from what the units inherit."""
    env = child_env()
    env['KIT_FAKE_GIT'] = fake
    return env


def run_pass(args, top, cfg, jobs, fake, out, hold, give_back=None, record_times=True,
             counted=None, history=False):
    """One run of `args` (FULL or test modules) with the units' KIT_FAKE_GIT set to `fake`
    (units_env), writing to `out`; its exit status. `give_back`: the pool's (drainer, or
    tiers.keep_for_next of it), None for none. `counted`: selfrun.count_units' answer for `args`,
    already asked (the pool then asks no count child). `history`: the run also appends its
    per-module cost to the history file (testcost), which only the daily full run asks for."""
    def say(line):
        out.write(line + '\n')
        out.flush()
    env = units_env(fake)
    label = 'full' if args is FULL else 'selected'
    if jobs == 1:
        start = time.time()
        cmd = args if args is FULL else ['-m', 'unittest'] + args
        out.flush()
        rc = spawn.call([sys.executable] + cmd, cwd=top, env=env)
        if hold is not None:
            say('kit_tests: %s' % hold.end())
        say('kit_tests: %s in %.0fs (%s, one process)' % (
            'OK' if rc == 0 else 'FAILED, exit %d' % rc, time.time() - start, label))
        return 0 if rc == 0 else 1
    modules = selftree.all_test_modules(top) if args is FULL else args
    common, _why = gitrepo.common_dir(top)
    times = os.path.join(common, selfrun.TIMES_FILE) if common else None
    counts, classes = counted if counted is not None else (None, None)
    rc, _summary = selfrun.run(modules, top, jobs, serial=cfg['serial'], out=out, label=label,
                               times_path=times, hint=cfg.get('long', ()), environ=env,
                               give_back=give_back, hold=hold, record_times=record_times,
                               skip_fails=cfg.get('skip_fails', ()), counts=counts,
                               classes=classes,
                               history_path=testcost.history_path(common) if history else None)
    return rc


def cost_report(a, top, out):
    """Write the per-module cost report (testcost.report) from the history file in `top`'s git
    common dir; the exit status (0, or 2 when there is no git common dir). Runs nothing."""
    common, why = gitrepo.common_dir(top)
    if not common:
        out.write('kit_tests: refused: %s\n' % why)
        return USAGE
    for line in testcost.report(testcost.load(testcost.history_path(common)), a.since, a.top,
                                a.sort):
        out.write(line + '\n')
    out.flush()
    return 0


def run(a, top, out):
    """Run what the parsed options `a` ask for in the checkout `top`, its lines written to `out`;
    the exit status."""
    def say(line):
        out.write(line + '\n')
        out.flush()
    try:
        cfg = selftree.config(top)
    except selftree.ConfigError as e:
        if not (a.full or a.scrub):
            say('kit_tests: refused: %s - no test ran' % e)
            return USAGE
        # --full is the escape hatch: it needs no table, so a broken one cannot stop it; nor
        # --scrub, which selects nothing from it.
        say('kit_tests: WARNING: %s; %s runs %s, none serial'
            % (e, '--full' if a.full else '--scrub', 'every module' if a.full else 'the scrub'))
        cfg = {'serial': [], 'skip_fails': []}
    stale = selftree.stale_skip_fails(cfg.get('skip_fails', ()), selftree.all_test_modules(top))
    for m in stale:
        say('kit_tests: WARNING: skip_fails names %s, which is not a test module; its entry guards '
            'nothing (fix tests/selection.json)' % m)
    # The tier the units get. On the fake git, the test modules the change adds or changes also run
    # on real git, as a second pass (kitlib.tooldeps.tiers): a test that only ever ran on the fake
    # cannot reach main unseen by real git.
    tier = '0' if a.real_git else os.environ.get('KIT_FAKE_GIT', '1')
    if a.scrub:
        # The docs-only gate step: no test a docs change cannot reach, but every tracked file
        # still scanned. One pass, with the same isolation as any other run.
        changed = []
        args = selftree.test_modules(top, [selftree.SCRUB])
        if not args:
            say('kit_tests: refused: tests/%s holds no test module - no test ran' % selftree.SCRUB)
            return USAGE
        say('kit_tests: --scrub: tests/%s only, %d module(s)' % (selftree.SCRUB, len(args)))
    elif a.full:
        say('kit_tests: full suite (--full)')
        args = FULL
        changed = []
        if tier == '1':
            base = a.base
            if base is None:
                base, note = default_base(top)
                if note:
                    say('kit_tests: %s' % note)
            changed, why = selftree.changed_paths(base, top)
            if changed is None:
                say('kit_tests: refused: %s - no test ran; the real-git pass of the added or '
                    'changed test modules needs the change list: fix git, or run --real-git'
                    % why)
                return USAGE
            say('kit_tests: --full: the real-git pass\'s change list against the merge-base '
                'with %s: %d changed path(s)' % (base, len(changed)))
    else:
        changed = a.changed
        if changed is None:
            base = a.base
            if base is None:
                base, note = default_base(top)
                if note:
                    say('kit_tests: %s' % note)
            changed, why = selftree.changed_paths(base, top)
            if changed is None:
                say('kit_tests: refused: %s - no test ran; fix git, or run --full' % why)
                return USAGE
            say('kit_tests: --select against the merge-base with %s: %d changed path(s)'
                % (base, len(changed)))
        args = plan(top, changed, cfg, say)
        if a.smoke:
            if args is FULL:
                say('kit_tests: --smoke: the full suite holds every smoke module')
            else:
                try:
                    extra, why = selftree.smoke(changed, cfg, args,
                                                selftree.all_test_modules(top))
                except selftree.ConfigError as e:
                    say('kit_tests: refused: %s - no test ran' % e)
                    return USAGE
                say('kit_tests: --smoke: %s%s' % (why, ': ' + ', '.join(extra) if extra else ''))
                if extra:
                    args = (args or []) + extra
    again = []
    if tier == '1':
        again = tiers.changed_test_modules(changed, selftree.all_test_modules(top))
    if again:
        say('kit_tests: real-git pass: %d added or changed test module(s): %s'
            % (len(again), ', '.join(again)))
    if a.plan or args is None:
        return 0
    if tier == '1' and not again and not a.both_tiers and not a.scrub:
        # Said before the pass, and never a verdict: the run's verdict is the pass's own.
        say('kit_tests: real-git pass: no added or changed test module')
    hold = None
    if a.pid_hold:
        # Opt-in only (never in the gate): started before the units' environment is copied below,
        # so its published beacon reaches them.
        hold = unit_runner.PidHoldRun()
        say('kit_tests: %s' % hold.start())
    if a.jobs is None:
        jobs, why = selfrun.default_jobs()
        say('kit_tests: jobs %d: %s' % (jobs, why))
    else:
        jobs = a.jobs
    if a.both_tiers or again:
        # The fake pass, then the real-git pass (kitlib.tooldeps.tiers); only the fake pass
        # records unit times (the landing tier's). The real-git pass runs only some modules:
        # under --both-tiers tiers.REAL_GIT_MODULES of the selection and the added or changed test
        # modules, otherwise only those.
        if a.both_tiers:
            every = selftree.all_test_modules(top) if args is FULL else args
            real = tiers.real_git_modules(every, also=again)
            label = '--both-tiers'
        else:
            real = again
            label = '%s, then its added or changed test modules on real git' % (
                '--full' if a.full else '--select')
            if hold is not None:
                say('kit_tests: the pid hold covers the fake-git pass; the real-git pass runs '
                    'without the pid hold')
        # One drainer for both passes, its lines going to the pass that runs. A second one would
        # read the threads per slot afresh from a record the first pass already shrank, against
        # the cpu mask this process started with, and so keep too few slots for the real pass.
        stream = {'out': out}
        give = None
        if jobs != 1:
            give = drainer(lambda line: (stream['out'].write(line + '\n'), stream['out'].flush()))
        counted = None
        first_give = give
        if jobs != 1:
            # One count child for both passes, not one each: the fake pass's modules and the
            # real-git pass's, counted on the fake tier as the fake pass's own child counted them
            # (a module's tests and classes do not depend on the tier: the loader only loads it).
            # It gets no offer, as the pool's own count child gets none.
            fake_set = selftree.all_test_modules(top) if args is FULL else args
            seen = set(fake_set)
            both = list(fake_set) + [m for m in real if m not in seen]
            env1 = {k: v for k, v in units_env('1').items() if k != giveback.GIVE_ENV}
            try:
                counted = selfrun.count_units(top, both, environ=env1)
            except KeyboardInterrupt:
                say('kit_tests: INTERRUPTED, exit %d before the first pass' % tiers.INTERRUPTED)
                return tiers.INTERRUPTED
        if give is not None and real:
            # Giving back is one-way, so the fake pass keeps what the real pass can use at once.
            bound = tiers.unit_bound(real, counted[1])
            need = min(jobs, bound)
            first_give = tiers.keep_for_next(give, need)
            say('kit_tests: give back: the fake-git pass keeps %d thread(s) for the real-git pass '
                '(%d module(s), up to %d unit(s))' % (need, len(real), bound))

        def one_pass(fake, tier_out, last):
            stream['out'] = tier_out
            use = args if fake == '1' else real
            if not use:
                tier_out.write('kit_tests: no real-git module in the selection\n')
                return 0
            if fake == '1':
                return run_pass(use, top, cfg, jobs, fake, tier_out, hold, give_back=first_give,
                                counted=counted, history=a.full and a.both_tiers)
            # Counted before the fake pass, so the units it plans are the ones the fake pass kept
            # threads for.
            return run_pass(use, top, cfg, jobs, fake, tier_out, None, give_back=give,
                            record_times=False, counted=counted)
        return tiers.run_tiers(one_pass, out, label=label)
    # The units' environment carries the tier: a pool worker imports tests/__init__.py once, so
    # its default cannot reach each unit (kitlib/tooldeps/pool_worker.py). This process keeps
    # its own git real.
    if a.real_git:
        say('kit_tests: --real-git: every test on real git')
    return run_pass(args, top, cfg, jobs, tier, out, hold,
                    give_back=drainer(say) if jobs != 1 else None)
