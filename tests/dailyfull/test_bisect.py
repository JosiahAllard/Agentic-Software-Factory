"""kitlib.dailyfull.bisect and kitlib.dailyfull.file: after a red daily run, the first-parent
commit that turned it red, and the backlog record filed for it through the real store (a ref
store in the scratch repository, pushed to its bare remote). The record is always priced at
priority 2 by an agent."""
import contextlib
import os
import tempfile
import unittest
from unittest import mock

from kitlib import dailyfull as D
from kitlib.backlog import opener
from kitlib.config import policy
from kitlib.config.types import ConfigError
from kitlib.dailyfull import bisect as B
from kitlib.dailyfull import file as filing
from kitlib.gate import hookrev, rerun, runner
from kitlib.lock import run as lockrun
from kitlib.lock import run_end
from kitlib.lock.store import LockError
from tests import gitfixture
from tests.gate import fixtures

from .test_dailyfull import Case


def bisect_kit(prefix='KIT'):
    conf = {'max_probes': 5, 'session': 'kit'}
    if prefix:
        conf['prefix'] = prefix
    return {'daily_full': {'enabled': True, 'kind': 'engine-test', 'bisect': conf},
            'backlog': {'store': 'ref'}}


class Search(unittest.TestCase):
    """search() with a fake probe: commits from index `first_red` on are red."""

    def probe(self, first_red, seen):
        def run(sha):
            seen.append(sha)
            return (B.RED, ['unit'], None) if int(sha) >= first_red else (B.GREEN, [], None)
        return run

    def commits(self, n):
        return ['%040d' % i for i in range(n)]

    def test_the_first_red_commit_is_found_in_about_log2_n_probes(self):
        for n in (1, 2, 5, 8, 13):
            for first in range(n):
                seen = []
                got = B.search(self.commits(n), self.probe(first, seen), 10)
                self.assertEqual(got['culprit'], self.commits(n)[first], (n, first))
                self.assertEqual(got['verdict'], 'culprit %s' % self.commits(n)[first][:10])
                self.assertEqual(seen[0], self.commits(n)[-1])       # the red run's commit first
                self.assertLessEqual(len(seen), 2 + (n - 1).bit_length(), (n, first))

    def test_the_red_commit_green_again_is_a_flake_after_one_probe(self):
        seen = []
        got = B.search(self.commits(6), self.probe(99, seen), 5)
        self.assertEqual(got['verdict'], B.FLAKE)
        self.assertIsNone(got['culprit'])
        self.assertEqual(len(seen), 1)

    def test_the_cap_names_the_range_left(self):
        seen = []
        got = B.search(self.commits(16), self.probe(5, seen), 2)
        self.assertEqual(len(seen), 2)
        self.assertIsNone(got['culprit'])
        self.assertIn('probe cap 2 reached', got['verdict'])
        self.assertEqual(got['bad'], self.commits(16)[7])

    def test_an_inconclusive_probe_stops_the_search(self):
        def probe(sha):
            return None, [], 'could not put it there'
        got = B.search(self.commits(4), probe, 5)
        self.assertEqual(got['verdict'], 'inconclusive: could not put it there')
        self.assertIsNone(got['culprit'])

    def test_an_infra_red_or_an_error_is_not_bisected(self):
        self.assertEqual(B.bisectable({'red': ['infra']}), [])
        self.assertEqual(B.bisectable({'red': ['unit'], 'error': 'x'}), [])
        self.assertEqual(B.bisectable({'red': ['unit', 'gate', 'windows']}), ['unit'])


class BisectCase(Case):
    KIT = bisect_kit()

    def setUp(self):
        Case.setUp(self)
        self.home = os.environ['USERPROFILE']        # Case's temp home

    def red_history(self):
        """A green run, then a, b, RED, c, d pushed one commit each, then a red run: (sha of the
        RED commit, the red result)."""
        self.run_daily('20261008-040000')
        shas = {}
        for name in ('a', 'b', 'RED', 'c', 'd'):
            self.push({name: 'x\n'})
            shas[name] = self.repo.git('rev-parse', 'HEAD')
        o = self.run_daily('20261009-040000')
        self.lines = o.lines
        return shas['RED'], self.result('20261009-040000')

    def record(self, rid):
        ctx = opener.open_store(self.main_dir, fetch=True, probes=opener.NO_PROBES)
        return ctx.store.load().get(rid)

    def daily_head(self):
        wt = os.path.join(self.main_dir, '.claude', 'worktrees', D.SLUG)
        return self.repo.git('rev-parse', 'HEAD', cwd=wt)


class Culprit(BisectCase):
    def test_the_culprit_is_found_the_tree_put_back_and_priority_2_filed_without_a_ruling(self):
        culprit, r = self.red_history()
        found = r['bisect']
        self.assertEqual(found['culprit'], culprit, self.lines)
        self.assertLessEqual(len(found['probes']), 4)
        self.assertEqual(self.daily_head(), r['sha'])
        rid = filing.record_id('KIT', culprit)
        self.assertEqual(found['filed'], rid, self.lines)
        rec = self.record(rid)
        self.assertEqual(rec.get('priority'), '2')
        self.assertEqual(rec.get('priority_by'), 'agent')
        self.assertIsNone(rec.get('priority_ruling'))
        self.assertEqual(rec.all('scope'), ['RED'])
        self.assertNotIn('note', found)
        # the step's log is neither kit_tests' nor unittest's: each probe ran the whole step
        self.assertIn('mode', found)
        self.assertTrue(found['mode'].startswith(B.WHOLE), found['mode'])

    def test_a_second_red_run_files_nothing_new(self):
        culprit, _r = self.red_history()
        rid = filing.record_id('KIT', culprit)
        before = self.record(rid).text
        self.run_daily('20261010-040000')
        r = self.result('20261010-040000')
        self.assertEqual(r['bisect']['culprit'], culprit)
        rec = self.record(rid)
        self.assertEqual(rec.text, before)
        ids = [i for i in self.ids() if '-DAILY-RED-' in i]
        self.assertEqual(ids, [rid])

    def ids(self):
        ctx = opener.open_store(self.main_dir, fetch=True, probes=opener.NO_PROBES)
        return [r.stem for r in ctx.store.load().records]


class NoPrefix(BisectCase):
    KIT = bisect_kit(prefix=None)

    def test_without_a_prefix_the_culprit_is_named_and_nothing_is_filed(self):
        culprit, r = self.red_history()
        self.assertEqual(r['bisect']['culprit'], culprit)
        self.assertNotIn('filed', r['bisect'])


class Flake(BisectCase):
    """The step fails on its second run only: the red run's commit probes green again."""

    def setUp(self):
        marker_dir = tempfile.mkdtemp(prefix='kit-flake-')
        self.addCleanup(gitfixture.rmtree, marker_dir)
        count = os.path.join(marker_dir, 'count').replace('\\', '/')
        code = ('import os, sys; p = %r; n = int(open(p).read()) + 1 if os.path.exists(p) else 1; '
                'open(p, "w").write(str(n)); print("ok"); sys.exit(1 if n == 2 else 0)' % count)
        self.GATE = {'steps': [dict(fixtures.GATE['steps'][0], cmd=['{python}', '-c', code])]}
        BisectCase.setUp(self)

    def test_a_red_that_does_not_reproduce_is_a_flake_and_files_nothing(self):
        self.run_daily('20261008-040000')
        self.push({'a': 'x\n'})
        self.run_daily('20261009-040000')
        r = self.result('20261009-040000')
        self.assertEqual(r['verdict'], 'red')
        self.assertEqual(r['bisect']['verdict'], B.FLAKE)
        self.assertEqual(len(r['bisect']['probes']), 1)
        self.assertNotIn('filed', r['bisect'])
        self.assertEqual(self.daily_head(), r['sha'])


PROBE_MODULE = '''import os
import time
import unittest

COUNT = %(count)r
STATE = {}


def bump():
    p = COUNT + '.target'
    n = int(open(p).read()) + 1 if os.path.exists(p) else 1
    open(p, 'w').write(str(n))
    return n


class Other(unittest.TestCase):
    def test_count(self):
        STATE['other'] = True
        with open(COUNT, 'a') as f:
            f.write('x\\n')


class Target(unittest.TestCase):
    def test_red(self):
        if os.path.exists(COUNT + '.hang'):
            time.sleep(120)
        self.assertFalse(%(red)s)
'''
NEW_METHOD = '''
    def test_new(self):
        self.fail('new')
'''
NEW_CLASS = '''

class Added(unittest.TestCase):
    def test_new(self):
        self.fail('new')
'''


class ProbeCase(BisectCase):
    """The red step is a unittest run of probe_test (PROBE_MODULE, Target's red when RED is
    true): `Other` counts every run of the whole step; a narrowed probe books the gate's own kind
    (its default, "test"), a whole-step probe the daily run's ("engine-test")."""
    GATE = {'steps': [{'name': 'unit', 'always': True,
                       'cmd': ['{python}', '-m', 'unittest', '-v', 'probe_test']}]}
    RED = 'os.path.exists("RED")'

    def setUp(self):
        BisectCase.setUp(self)
        marker_dir = tempfile.mkdtemp(prefix='kit-probe-')
        self.addCleanup(gitfixture.rmtree, marker_dir)
        self.count = os.path.join(marker_dir, 'count').replace('\\', '/')
        self.push({'probe_test.py': self.module(self.RED)})

    def module(self, red, extra=''):
        return PROBE_MODULE % {'count': self.count, 'red': red} + extra

    def whole_runs(self):
        with open(self.count, encoding='utf-8') as f:
            return len(f.read().split())

    def probe_kinds(self):
        return [c['kind'] for c in self.book_calls][2:]     # after the green and the red run

    def history(self, culprit_files):
        """A green run, then a, b, the culprit's files, c, d, then a red run: (culprit, result)."""
        self.run_daily('20261008-040000')
        self.push({'a': 'x\n'})
        self.push({'b': 'x\n'})
        self.push(culprit_files)
        culprit = self.repo.git('rev-parse', 'HEAD')
        self.push({'c': 'x\n'})
        self.push({'d': 'x\n'})
        o = self.run_daily('20261009-040000')
        self.lines = o.lines
        return culprit, self.result('20261009-040000')


class TestsOnly(ProbeCase):
    """Each probe runs S's failing classes only: no probe runs the whole step."""

    def test_probes_run_only_the_failing_class_and_find_the_culprit(self):
        culprit, r = self.red_history()
        found = r['bisect']
        self.assertEqual(found['culprit'], culprit, self.lines)
        self.assertEqual(found.get('mode'), 'tests: 1 class(es)')
        self.assertEqual(self.whole_runs(), 2)          # the two daily runs, no probe
        self.assertEqual(self.probe_kinds(), ['test'] * len(found['probes']))
        self.assertEqual(self.daily_head(), r['sha'])

    def test_a_failing_method_the_culprit_adds_counts_green_before_it(self):
        self.push({'probe_test.py': self.module('False')})
        culprit, r = self.history({'probe_test.py': self.module('False', NEW_METHOD)})
        self.assertEqual(r['bisect']['culprit'], culprit, self.lines)
        self.assertEqual(r['bisect'].get('mode'), 'tests: 1 class(es)')
        self.assertEqual(self.whole_runs(), 2)

    def test_a_failing_class_the_culprit_adds_counts_green_before_it(self):
        self.push({'probe_test.py': self.module('False')})
        culprit, r = self.history({'probe_test.py': self.module('False', NEW_CLASS)})
        self.assertEqual(r['bisect']['culprit'], culprit, self.lines)
        self.assertEqual(r['bisect'].get('mode'), 'tests: 1 class(es)')
        self.assertEqual(self.whole_runs(), 2)


class TestsGreenAloneAtS(ProbeCase):
    """Target fails only after Other ran in the same process: alone it passes at S, so one
    whole-step probe runs at S, which is red, and the rest of the search runs whole steps."""
    RED = 'os.path.exists("RED") and STATE.get("other")'

    def test_a_red_the_whole_step_reproduces_is_bisected_in_whole_steps(self):
        culprit, r = self.red_history()
        found = r['bisect']
        self.assertEqual(found['culprit'], culprit, self.lines)
        kinds = self.probe_kinds()
        self.assertEqual(kinds[0], 'test')              # the failing tests alone at S
        self.assertEqual(kinds[1:], ['engine-test'] * len(found['probes']))
        # the two daily runs, then every probe (the narrowed one and the whole steps): gate_mode
        self.assertEqual([c.get('gate_mode') for c in self.book_calls],
                         ['daily'] * 2 + ['bisect'] * len(kinds))
        self.assertEqual(found.get('mode'), B.NARROWED_GREEN)
        self.assertEqual(self.whole_runs(), 2 + len(found['probes']))


class TestsFlakeAtS(ProbeCase):
    """Target fails on its second run only (the red daily run): green alone at S and green in
    the whole-step probe at S, so the red is a flake."""
    RED = 'bump() == 2'

    def test_green_alone_and_green_whole_at_s_is_a_flake(self):
        self.run_daily('20261008-040000')
        self.push({'a': 'x\n'})
        self.run_daily('20261009-040000')
        r = self.result('20261009-040000')
        self.assertEqual(r['verdict'], 'red')
        found = r['bisect']
        self.assertEqual(found['verdict'], B.FLAKE)
        self.assertEqual(len(found['probes']), 1)
        self.assertEqual(self.probe_kinds(), ['test', 'engine-test'])
        self.assertEqual(found.get('mode'), B.NARROWED_GREEN)
        self.assertEqual(self.whole_runs(), 3)          # two daily runs and the probe at S
        self.assertNotIn('filed', found)
        self.assertEqual(self.daily_head(), r['sha'])


class ProbeCannotFinish(ProbeCase):
    """A narrowed probe at S that cannot finish stops the bisect there as inconclusive. Each case
    acts only while the bisect runs (B.bisect), never in the daily runs themselves; the gate has a
    setup step that fails while the flag file is there."""

    def setUp(self):
        flags = tempfile.mkdtemp(prefix='kit-probe-flags-')
        self.addCleanup(gitfixture.rmtree, flags)
        self.flag = os.path.join(flags, 'setup-fail')
        self.GATE = dict(ProbeCase.GATE, setup=[
            '{python}', '-c',
            'import os, sys; print("setup"); sys.exit(os.path.exists(%r))' % self.flag])
        ProbeCase.setUp(self)

    @contextlib.contextmanager
    def flag_file(self, path):
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x\n')
        try:
            yield
        finally:
            os.remove(path)

    def refused(self):
        real = lockrun.book

        def book(kind, *args, **kw):
            if kind == 'test':                          # the narrowed probe's, not the runs'
                raise LockError(6, 'busy: the test refuses the probe')
            return real(kind, *args, **kw)
        return mock.patch.object(lockrun, 'book', book)

    def stopped(self):
        real = rerun.run_class

        def run_class(launcher, *args, **kw):
            for d in self.store.live_runs():
                if d.get('kind') == 'test':
                    run_end.request_stop(self.store, d['id'], 'test')
            return real(launcher, *args, **kw)
        return mock.patch.object(rerun, 'run_class', run_class)

    @contextlib.contextmanager
    def hangs(self):
        with mock.patch.object(rerun, 'class_timeout', lambda top, cfg: 2), \
                self.flag_file(self.count + '.hang'):
            yield

    CASES = {
        'setup': (lambda self: self.flag_file(self.flag), 'setup failed at '),
        'refused': (refused, 'the machine lock refused a probe: '),
        'stopped': (stopped, 'probe 1 at '),
        'timeout': (hangs, 'probe_test.Target (still running after 2 s)'),
    }

    def check(self, case):
        act, why = self.CASES[case]
        real = B.bisect

        def bisect(*args, **kw):
            with act(self):
                return real(*args, **kw)
        with mock.patch.object(B, 'bisect', bisect):
            _, r = self.red_history()
        found = r['bisect']
        self.assertTrue(found['verdict'].startswith('inconclusive: '), (found, self.lines))
        self.assertIn(why, found['verdict'])
        self.assertEqual([p['verdict'] for p in found['probes']], ['inconclusive'])
        self.assertEqual(found['probes'][0]['sha'], r['sha'])
        self.assertEqual(self.whole_runs(), 2)          # no whole-step probe
        self.assertEqual(self.daily_head(), r['sha'])
        return found

    def test_a_failed_setup(self):
        self.check('setup')

    def test_a_refused_booking(self):
        self.check('refused')

    def test_a_stop(self):
        self.assertIn('was stopped', self.check('stopped')['verdict'])

    def test_a_class_past_its_time_limit_is_ended(self):
        self.check('timeout')


class LoadChecks(unittest.TestCase):
    """runner.load_checks, the gate's, the targeted rerun's and the probe's: a hook module that
    cannot load refuses with why, a SystemExit included; with a commit it loads from that
    commit (hookrev), as a landing's gate does."""

    def test_each_outcome(self):
        none, check = mock.Mock(check=None), mock.Mock(check='a_check')
        cases = [
            ('no step has a check', none, None, None, ({}, {}, None)),
            ('loaded', check, None, ({'c': 1}, {'o': 2}), ({'c': 1}, {'o': 2}, None)),
            ('loaded at a commit', check, 'abc', ({'c': 1}, {}), ({'c': 1}, {}, None)),
            ('config error', check, None, ConfigError('bad hook'), (None, None, 'bad hook')),
            ('module exits', check, 'abc', SystemExit(3), (None, None, (
                'the hook module exited while it was loaded (SystemExit, code 3); nothing ran'))),
        ]
        for what, step, rev, load, want in cases:
            with self.subTest(what):
                fake = (mock.Mock(side_effect=load) if isinstance(load, BaseException)
                        else mock.Mock(return_value=load))
                there = 'load_gate_checks_at' if rev else 'load_gate_checks'
                with mock.patch.object(hookrev if rev else policy, there, fake):
                    self.assertEqual(runner.load_checks(object(), [step], 'root', rev), want)
                self.assertEqual(fake.called, step is check)


class Judge(unittest.TestCase):
    """judge() of one class run's log for S's failing ids, and present()."""
    ID = 'probe_test.Target.test_red'
    LOAD_FAILED = ('ERROR: Target (unittest.loader._FailedTest.Target)\n'
                   'AttributeError: module has no attribute Target\n'
                   'Ran 1 test in 0.001s\nFAILED (errors=1)\n')

    def judge(self, text, rc=0, defined=False):
        return B.judge(text, rc, [self.ID], lambda: defined)

    def test_each_outcome(self):
        cases = [
            ('the id fails', 'FAIL: test_red (probe_test.Target.test_red)\n'
             'Ran 1 test in 0.1s\nFAILED (failures=1)\n', 1, False, B.RED),
            ('the id errors (3.10 form)', 'ERROR: test_red (probe_test.Target)\n'
             'Ran 1 test in 0.1s\nFAILED (errors=1)\n', 1, False, B.RED),
            ('the id passes', 'test_red (probe_test.Target.test_red) ... ok\n'
             'Ran 1 test in 0.1s\nOK\n', 0, False, B.GREEN),
            ('another test fails', 'FAIL: test_x (probe_test.Target.test_x)\n'
             'Ran 2 tests in 0.1s\nFAILED (failures=1)\n', 1, False, B.GREEN),
            ('the method is absent', 'test_x (probe_test.Target.test_x) ... ok\n'
             'Ran 1 test in 0.1s\nOK\n', 0, False, B.GREEN),
            ('its class setUpClass errors', 'ERROR: setUpClass (probe_test.Target)\n'
             'Ran 0 tests in 0.001s\nFAILED (errors=1)\n', 1, True, B.RED),
            ('its module setUpModule errors', 'ERROR: setUpModule (probe_test)\n'
             'Ran 0 tests in 0.001s\nFAILED (errors=1)\n', 1, True, B.RED),
            ('another class setUpClass errors', 'ERROR: setUpClass (probe_test.Other)\n'
             'Ran 0 tests in 0.001s\nFAILED (errors=1)\n', 1, True, B.GREEN),
            ('its class tearDownClass errors after its tests passed',
             'test_red (probe_test.Target.test_red) ... ok\n'
             'ERROR: tearDownClass (probe_test.Target)\n'
             'Ran 1 test in 0.001s\nFAILED (errors=1)\n', 1, True, B.GREEN),
            ('its module tearDownModule errors after its tests passed',
             'test_red (probe_test.Target.test_red) ... ok\n'
             'ERROR: tearDownModule (probe_test)\n'
             'Ran 1 test in 0.001s\nFAILED (errors=1)\n', 1, True, B.GREEN),
            ('not loaded, class defined', self.LOAD_FAILED, 1, True, B.RED),
            ('not loaded, class absent', self.LOAD_FAILED, 1, False, B.GREEN),
            ('no Ran line', 'Traceback (most recent call last):\n', 1, False, None),
            ('did not start', '', None, False, None),
        ]
        for what, text, rc, defined, want in cases:
            with self.subTest(what):
                self.assertEqual(self.judge(text, rc, defined), want)

    def test_present_reads_the_tree(self):
        top = tempfile.mkdtemp(prefix='kit-present-')
        self.addCleanup(gitfixture.rmtree, top)
        with open(os.path.join(top, 'probe_test.py'), 'w', encoding='utf-8') as f:
            f.write('import unittest\n\n\nclass Target(unittest.TestCase):\n    pass\n')
        self.assertTrue(B.present(top, 'probe_test.Target'))
        self.assertTrue(B.present(top, 'probe_test'))
        self.assertFalse(B.present(top, 'probe_test.Added'))
        self.assertFalse(B.present(top, 'gone_test.Target'))


if __name__ == '__main__':
    unittest.main()
