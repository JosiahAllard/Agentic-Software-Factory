"""tools/kit_tests.py --select's base (docs/test-tools.md "kit_tests", docs/gate-run.md "Default
base"): origin/main when it resolves, a line when the local main differs, and an explicit --base
kept. The change list is mocked; the refs are a throwaway repository's (tests/gitfixture.py).
Also the git tier (KIT_FAKE_GIT) and the home fence's marker (KIT_HOME_FENCE) the units are given,
and --jobs 1's pid hold (docs/test-tools.md "The pid shield"), and the second pass on real git of
the test modules a change adds or changes, and the session's kit binding no test child gets."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from kitlib.host import registry as hostregistry
from tests import gitfixture
from tests.home import temp_home

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location('kit_tests_entry_for_base_tests',
                                               os.path.join(KIT, 'tools', 'kit_tests.py'))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


@contextlib.contextmanager
def no_change():
    """The change adds or changes no test module: --full's diff for the real-git pass is empty."""
    with mock.patch.object(cli.selftree, 'changed_paths', return_value=([], None)), \
            mock.patch.object(cli.kitrun, 'default_base', return_value=('main', None)):
        yield


class SelectBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = gitfixture.TempRepo(bare_remote=True)
        cls.repo.write('later.txt', 'later\n')
        cls.repo.commit('later', 'later.txt')
        cls.repo.git('push', '-q', 'origin', 'main')
        cls.repo.git('update-ref', 'refs/heads/main', cls.repo.initial)    # local main is stale

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()

    def run_select(self, *extra):
        bases, out = [], io.StringIO()

        def changed(base, top):
            bases.append(base)
            return [], None
        with mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'changed_paths', side_effect=changed), \
                mock.patch.object(cli.kitrun, 'plan', return_value=None):
            rc = cli.main(['-C', self.repo.root, '--select', '--plan'] + list(extra), out=out)
        return rc, bases, out.getvalue()

    def test_stale_local_main_selects_against_origin_main(self):
        rc, bases, out = self.run_select()
        self.assertEqual((rc, bases), (0, ['refs/remotes/origin/main']), out)
        self.assertIn('kit_tests: base: local main %s is 1 behind origin/main' %
                      self.repo.initial[:7], out)
        self.assertIn('--select against the merge-base with refs/remotes/origin/main', out)

    def test_an_explicit_base_is_kept(self):
        rc, bases, out = self.run_select('--base', 'main')
        self.assertEqual((rc, bases), (0, ['main']), out)
        self.assertNotIn('measuring against', out)

    def test_no_remote_selects_against_local_main(self):
        plain = gitfixture.TempRepo()
        self.addCleanup(plain.cleanup)
        self.assertEqual(cli.kitrun.default_base(plain.root), ('main', None))


class PidHoldOptIn(unittest.TestCase):
    """--pid-hold is the opt-in: without it no hold is made; with it the hold starts before the
    units' environment is copied (its beacon reaches them, pooled or --jobs 1), and its end line
    is printed (the pool's by selfrun.run, before the summary)."""

    def run_full(self, jobs, extra=()):
        order, seen, out = [], {}, io.StringIO()

        class Hold(object):
            def start(self):
                order.append('start')
                os.environ['KIT_PID_HOLD'] = 'beacon'
                return 'pid hold: on (warmed 3 s, published)'

            def end(self):
                order.append('end')
                return 'pid hold: held to the end of the run'

        def run(modules, root, jobs, **kw):
            order.append('units')
            seen['beacon'] = kw['environ'].get('KIT_PID_HOLD')
            seen['hold'] = kw['hold']
            return 0, 'kit_tests: OK'

        def call(cmd, cwd=None, env=None, **kw):
            order.append('units')
            seen['beacon'] = env.get('KIT_PID_HOLD')
            seen['more'] = kw
            return 0
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {}), \
                mock.patch.object(cli.unit_runner, 'PidHoldRun', Hold), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(None, 'no git')), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=['tests.a.test_m']), \
                no_change(), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run), \
                mock.patch.object(cli.subprocess, 'call', side_effect=call):
            os.environ.pop('KIT_PID_HOLD', None)
            rc = cli.main(['--full', '--jobs', str(jobs)] + list(extra), out=out)
        return rc, order, seen, out.getvalue()

    def test_no_hold_without_the_flag(self):
        for jobs in (1, 2):
            rc, order, seen, out = self.run_full(jobs)
            self.assertEqual((rc, order, seen.get('beacon')), (0, ['units'], None), out)
            self.assertNotIn('pid hold', out)
            if jobs == 2:
                self.assertIsNone(seen['hold'])

    def test_the_one_process_run_from_no_console_asks_for_no_window(self):
        """kit_tests starts its one process through kitlib.proc.spawn.call, which adds a keyword
        when kit_tests has no console (pythonw, a detached parent): the fakes here take it."""
        from kitlib.proc import job
        from kitlib.platform.windows import win32
        with mock.patch.object(job, 'has_console', return_value=False):
            rc, order, seen, out = self.run_full(1)
        self.assertEqual((rc, order), (0, ['units']), out)
        self.assertEqual(seen['more'], {'creationflags': win32.CREATE_NO_WINDOW})
        with mock.patch.object(job, 'has_console', return_value=True):
            rc, order, seen, out = self.run_full(1)
        self.assertEqual((rc, seen['more']), (0, {}), out)

    def test_one_process_holds_before_its_unittest_and_reports_after(self):
        rc, order, seen, out = self.run_full(1, ['--pid-hold'])
        self.assertEqual((rc, order, seen['beacon']), (0, ['start', 'units', 'end'], 'beacon'), out)
        lines = out.splitlines()
        self.assertIn('kit_tests: pid hold: on (warmed 3 s, published)', lines)
        self.assertEqual(lines[-2:], ['kit_tests: pid hold: held to the end of the run',
                                      lines[-1]])
        self.assertTrue(lines[-1].startswith('kit_tests: OK in '), lines)

    def test_the_pool_gets_the_started_hold_and_its_beacon(self):
        rc, order, seen, out = self.run_full(2, ['--pid-hold'])
        self.assertEqual((rc, order, seen['beacon']), (0, ['start', 'units'], 'beacon'), out)
        self.assertIsNotNone(seen['hold'])


class GitTierEnvironment(unittest.TestCase):
    """The units' environment carries KIT_FAKE_GIT (a pool worker imports tests/__init__.py once,
    so its default cannot reach each unit); kit_tests' own process keeps it unchanged."""

    def run_full(self, extra=(), outer=None):
        seen, out = {}, io.StringIO()

        def run(modules, root, jobs, **kw):
            seen['pooled'] = kw['environ'].get('KIT_FAKE_GIT')
            return 0, 'kit_tests: OK'

        def call(cmd, cwd=None, env=None, **kw):
            seen['one'] = env.get('KIT_FAKE_GIT')
            return 0
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(None, 'no git')), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=['tests.a.test_m']), \
                no_change(), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run), \
                mock.patch.object(cli.subprocess, 'call', side_effect=call):
            os.environ.pop('KIT_FAKE_GIT', None)
            if outer is not None:
                os.environ['KIT_FAKE_GIT'] = outer
            for jobs in ('2', '1'):
                self.assertEqual(cli.main(['--full', '--jobs', jobs] + list(extra), out=out), 0)
            seen['own'] = os.environ.get('KIT_FAKE_GIT')
        return seen

    def test_the_units_run_on_the_fake_by_default(self):
        self.assertEqual(self.run_full(), {'pooled': '1', 'one': '1', 'own': None})

    def test_real_git_gives_the_units_0(self):
        self.assertEqual(self.run_full(['--real-git']), {'pooled': '0', 'one': '0', 'own': None})

    def test_a_value_already_set_is_kept(self):
        self.assertEqual(self.run_full(outer='0'), {'pooled': '0', 'one': '0', 'own': '0'})


class HomeFenceEnvironment(unittest.TestCase):
    """The units' environment carries the home fence's marker (docs/test-tools.md "Home fence"):
    KIT_HOME_FENCE, the home kit_tests itself resolves, for the reason KIT_FAKE_GIT is there (a
    pool worker replaces its environment for each unit); a marker already set is kept, and
    kit_tests' own process stays unmarked. The home here is a temp folder."""
    MARK = 'KIT_HOME_FENCE'

    def run_main(self, argv_sets, outer=None):
        home = temp_home(self)
        seen, out = {}, io.StringIO()

        def run(modules, root, jobs, **kw):
            seen['pooled'] = kw['environ'].get(self.MARK)
            return 0, 'kit_tests: OK'

        def call(cmd, cwd=None, env=None, **kw):
            seen['one'] = env.get(self.MARK)
            return 0

        def collect(top, modules, environ=None):
            seen['list'] = environ.get(self.MARK)
            return []
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(None, 'no git')), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=['tests.a.test_m']), \
                no_change(), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run), \
                mock.patch.object(cli.selflist, 'collect', side_effect=collect), \
                mock.patch.object(cli.selflist, 'render', return_value=[]), \
                mock.patch.object(cli.subprocess, 'call', side_effect=call):
            os.environ.pop(self.MARK, None)
            if outer is not None:
                os.environ[self.MARK] = outer
            for argv in argv_sets:
                self.assertEqual(cli.main(argv, out=out), 0)
            seen['own'] = os.environ.get(self.MARK)
        return home, seen

    RUNS = (['--full', '--jobs', '2'], ['--full', '--jobs', '1'], ['--list'])

    def test_the_units_get_the_home_kit_tests_resolves(self):
        home, seen = self.run_main(self.RUNS)
        self.assertEqual(seen.pop('own'), None)
        self.assertEqual(sorted(seen), ['list', 'one', 'pooled'])
        for who, marker in seen.items():
            self.assertEqual(os.path.normcase(marker or ''), os.path.normcase(home), who)

    def test_an_inherited_marker_is_kept(self):
        # a guard: green before the marker too (the units' environment is a copy)
        outer = os.path.join(tempfile.gettempdir(), 'kit-outer-home')      # only a name
        home, seen = self.run_main(self.RUNS, outer=outer)
        self.assertEqual(seen, {'pooled': outer, 'one': outer, 'list': outer, 'own': outer})

    def test_both_tiers_mark_each_pass(self):
        home, seen, out = temp_home(self), [], io.StringIO()

        def run(modules, root, jobs, **kw):
            seen.append(kw['environ'].get(self.MARK))
            kw['out'].write('kit_tests: OK in 1s (full)\n')
            return 0, ''
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {'KIT_FAKE_GIT': '1'}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(here, None)), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=MODULES), \
                no_change(), \
                mock.patch.object(cli.giveback, 'drainer', return_value=lambda n: None), \
                mock.patch.object(cli.selfrun, 'count_units', return_value=({}, {})), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run):
            os.environ.pop(self.MARK, None)
            rc = cli.main(['--full', '--jobs', '2', '--both-tiers'], out=out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual([os.path.normcase(m or '') for m in seen], [os.path.normcase(home)] * 2)


MODULES = ['tests.a.test_m', 'tests.git.test_g', 'tests.landing.test_land_x', 'tests.gate.test_other']


class KitSessionEnvironment(unittest.TestCase):
    """No test child gets the session's kit binding (kit_tests KIT_SESSION_ENV): PM_KIT_ROOT,
    KIT_LIVE_ROOT, KIT_PINNED, PM_KIT_ROLE, PM_KIT_HOST, PM_KIT_SESSION_NAME,
    PM_KIT_CODEX_STATE, PM_KIT_CODEX_EXE and every host's session_env variable (hosts.json) are
    dropped from the pooled units', the --jobs 1 process's, the real-git count child's and --list's
    loader's environment, while another variable passes and kit_tests' own process keeps them all."""
    HOST_SESSION = {n: 'host-session' for n in hostregistry.union('session_env')}
    SESSION = dict({'PM_KIT_ROOT': r'C:\session\kit',
                    'KIT_LIVE_ROOT': r'C:\session\main', 'KIT_PINNED': 'live',
                    'PM_KIT_ROLE': 'worker', 'PM_KIT_HOST': 'claude',
                    'PM_KIT_SESSION_NAME': 'coord-x', 'PM_KIT_CODEX_STATE': r'C:\codex\state',
                    'PM_KIT_CODEX_EXE': r'C:\codex\codex.exe'}, **HOST_SESSION)
    OTHER = 'KIT_TESTS_UNRELATED_MARK'

    def test_the_session_kit_binding_reaches_no_test_child(self):
        self.assertGreaterEqual(len(self.HOST_SESSION), 2)     # hosts.json registers every host
        temp_home(self)
        seen, out = {}, io.StringIO()

        def keep(who, env):
            seen.setdefault(who, []).append(dict(env))

        def run(modules, root, jobs, **kw):
            keep('pooled', kw['environ'])
            kw['out'].write('kit_tests: OK in 1s (full)\n')
            return 0, ''

        def count(top, modules, environ=None):
            keep('count', environ)
            return {}, {}

        def call(cmd, cwd=None, env=None, **kw):
            keep('one', env)
            return 0

        def collect(top, modules, environ=None):
            keep('list', environ)
            return []
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, dict(self.SESSION, **{self.OTHER: 'kept'})), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(None, 'no git')), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=MODULES), \
                no_change(), \
                mock.patch.object(cli.giveback, 'drainer', return_value=lambda n: None), \
                mock.patch.object(cli.selfrun, 'count_units', side_effect=count), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run), \
                mock.patch.object(cli.selflist, 'collect', side_effect=collect), \
                mock.patch.object(cli.selflist, 'render', return_value=[]), \
                mock.patch.object(cli.subprocess, 'call', side_effect=call):
            for argv in (['--full', '--jobs', '2'], ['--full', '--jobs', '1'], ['--list'],
                         ['--full', '--jobs', '2', '--both-tiers']):
                self.assertEqual(cli.main(argv, out=out), 0, out.getvalue())
            own = {k: os.environ.get(k) for k in self.SESSION}
        self.assertEqual(own, self.SESSION)
        self.assertEqual(sorted(seen), ['count', 'list', 'one', 'pooled'])
        self.assertEqual(len(seen['pooled']), 3)          # --jobs 2, then both tiers' two passes
        for who, envs in seen.items():
            for env in envs:
                self.assertEqual([k for k in self.SESSION if k in env], [], who)
                self.assertEqual(env.get(self.OTHER), 'kept', who)


class BothTiers(unittest.TestCase):
    """--both-tiers: the pool runs twice, KIT_FAKE_GIT 1 then 0; both passes give cpu slots back
    (the fake pass down to what the real pass can use, tests/tooldeps/test_kit_tests_passes.py)
    and each pass plans and records by its own tier's unit times; exit 0 only when both pass."""

    def run_both(self, rcs, extra=()):
        calls, out = [], io.StringIO()

        def run(modules, root, jobs, **kw):
            calls.append((kw['environ'].get('KIT_FAKE_GIT'), kw['give_back'] is not None,
                          kw['times_path']))
            kw['out'].write('kit_tests: %s in 1s (full)\n' % ('OK' if rcs[len(calls) - 1] == 0
                                                              else 'FAILED, exit 1'))
            return rcs[len(calls) - 1], ''
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {'KIT_FAKE_GIT': '1'}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(here, None)), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=MODULES), \
                no_change(), \
                mock.patch.object(cli.giveback, 'drainer', return_value=lambda n: None), \
                mock.patch.object(cli.selfrun, 'count_units', return_value=({}, {})), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run):
            rc = cli.main(['--full', '--jobs', '2', '--both-tiers'] + list(extra), out=out)
        return rc, calls, out.getvalue()

    def test_fake_then_real_and_ok_only_when_both_pass(self):
        rc, calls, text = self.run_both([0, 0])
        self.assertEqual(rc, 0, text)
        # Each pass orders, splits and records by its own tier's file.
        here = os.path.abspath('.')
        self.assertEqual(calls, [('1', True, os.path.join(here, cli.selfrun.TIMES_FILE)),
                                 ('0', True, os.path.join(here, cli.selfrun.REAL_TIMES_FILE))])
        lines = text.splitlines()
        self.assertEqual(lines[-1], 'kit_tests: OK, both tiers green (fake git, real git)')
        self.assertEqual([ln for ln in lines if ln.startswith('kit_tests: OK')], lines[-1:])
        self.assertIn('kit_tests [fake git]: OK in 1s (full)', lines)
        self.assertIn('kit_tests [real git]: OK in 1s (full)', lines)

    def test_the_real_git_pass_splits_a_module_by_its_real_git_times(self):
        # A landing module fast on the fake git and slow on real git: the planner splits it by
        # class in the real-git pass only, and each pass writes back only its own tier's file.
        common = tempfile.mkdtemp(prefix='kit-times-')
        self.addCleanup(gitfixture.rmtree, common)
        fake_path = os.path.join(common, cli.selfrun.TIMES_FILE)
        real_path = os.path.join(common, cli.selfrun.REAL_TIMES_FILE)
        with open(fake_path, 'w', encoding='utf-8') as fh:     # as save_times writes it
            json.dump({m: 5.0 for m in MODULES}, fh, indent=0, sort_keys=True)
        with open(fake_path, 'rb') as fh:
            fake_bytes = fh.read()
        with open(real_path, 'w', encoding='utf-8') as fh:
            json.dump({'tests.git.test_g': 20.0, 'tests.landing.test_land_x': 240.0}, fh)
        started, out, real_run = [], io.StringIO(), cli.selfrun.run

        def unit(u, audit, timeout, root=None, cmd=None, env=None):
            fake = env['KIT_FAKE_GIT']
            started.append((fake, u[0]))
            return u, 'OK', 1, 5.0 if fake == '1' else 120.0, ''

        def run(modules, root, jobs, **kw):
            return real_run(modules, root, jobs, run_unit=unit, **kw)
        counted = ({m: 2 for m in MODULES}, {'tests.landing.test_land_x': ['A', 'B']})
        here = os.path.abspath('.')
        temp_home(self)         # selfrun.run's run folder comes from user.make_temp
        with mock.patch.dict(os.environ, {'KIT_FAKE_GIT': '1'}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(common, None)), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=MODULES), \
                no_change(), \
                mock.patch.object(cli.giveback, 'drainer', return_value=lambda n: None), \
                mock.patch.object(cli.selfrun, 'count_units', return_value=counted), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run):
            rc = cli.main(['--full', '--jobs', '2', '--both-tiers'], out=out)
        text = out.getvalue()
        self.assertEqual(rc, 0, text)
        self.assertEqual(sorted(u for f, u in started if f == '1'), sorted(MODULES), text)
        # Two pool threads start the units, so only the set is fixed, not the order.
        self.assertEqual(sorted(u for f, u in started if f == '0'),
                         ['tests.git.test_g', 'tests.landing.test_land_x.A',
                          'tests.landing.test_land_x.B'], text)
        self.assertIn('kit_tests [real git]: split by class: tests.landing.test_land_x '
                      '(2 classes)\n', text)
        self.assertNotIn('kit_tests [fake git]: split by class', text)
        self.assertEqual(cli.selfrun.load_times(real_path), {
            'tests.git.test_g': 120.0, 'tests.landing.test_land_x': 240.0,
            'tests.landing.test_land_x.A': 120.0, 'tests.landing.test_land_x.B': 120.0})
        with open(fake_path, 'rb') as fh:
            self.assertEqual(fh.read(), fake_bytes)

    def test_the_real_git_pass_runs_only_git_worktree_and_landing_modules(self):
        ran = []
        with mock.patch.object(cli.kitrun, 'run_pass', side_effect=lambda args, *a, **k: ran.append(args) or 0):
            rc, calls, text = self.run_both([0, 0])
        self.assertEqual(rc, 0, text)
        self.assertEqual(ran, [cli.FULL, ['tests.git.test_g', 'tests.landing.test_land_x']])

    def test_a_red_real_git_pass_fails_the_run_and_prints_no_ok(self):
        rc, calls, text = self.run_both([0, 1])
        self.assertEqual(rc, 1, text)
        self.assertEqual(len(calls), 2)
        self.assertNotRegex(text, r'(?m)^kit_tests: OK')
        self.assertIn('kit_tests: FAILED, exit 1: red tier(s): real git', text)

    def test_a_red_fake_pass_still_runs_the_real_pass(self):
        rc, calls, text = self.run_both([1, 0])
        self.assertEqual((rc, len(calls)), (1, 2), text)
        self.assertIn('red tier(s): fake git\n', text)

    def test_with_real_git_or_pid_hold_is_a_usage_error(self):
        for extra in (['--real-git'], ['--pid-hold']):
            with self.assertRaises(SystemExit) as cm, \
                    mock.patch('sys.stderr', io.StringIO()):
                cli.main(['--full', '--both-tiers'] + extra, out=io.StringIO())
            self.assertEqual(cm.exception.code, cli.USAGE, extra)


SELECTED = ['tests.a.test_m', 'tests.gate.test_other']
OK_BOTH = 'kit_tests: OK, both tiers green (fake git, real git)'
NONE_CHANGED = 'kit_tests: real-git pass: no added or changed test module'


class ChangedTestsOnRealGit(unittest.TestCase):
    """The default --select and --full (no flag; the gate's cmd and full_cmd): the run on the fake
    git, then a second pass on real git of the test modules the change adds or changes; none, one
    pass and a line that says so. --full refuses when git cannot list the change."""

    def run_main(self, argv, rcs=(0, 0), changed=([], None), outer='1'):
        calls, bases, out = [], [], io.StringIO()

        class Hold(object):
            def start(self):
                return 'pid hold: on'

            def end(self):
                return 'pid hold: held to the end of the run'

        def run(modules, root, jobs, **kw):
            calls.append((kw['environ'].get('KIT_FAKE_GIT'), list(modules),
                          kw['give_back'] is not None, kw['hold'] is not None))
            rc = rcs[len(calls) - 1]
            kw['out'].write('kit_tests: %s in 1s\n' % ('OK' if rc == 0 else 'FAILED, exit 1'))
            return rc, ''

        def paths(base, top):
            bases.append(base)
            return changed
        here = os.path.abspath('.')
        with mock.patch.dict(os.environ, {'KIT_FAKE_GIT': outer}), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(here, None)), \
                mock.patch.object(cli.gitrepo, 'common_dir', return_value=(here, None)), \
                mock.patch.object(cli.selftree, 'config', return_value={'serial': []}), \
                mock.patch.object(cli.selftree, 'all_test_modules', return_value=MODULES), \
                mock.patch.object(cli.selftree, 'changed_paths', side_effect=paths), \
                mock.patch.object(cli.kitrun, 'default_base', return_value=('main', None)), \
                mock.patch.object(cli.kitrun, 'plan', return_value=SELECTED), \
                mock.patch.object(cli.giveback, 'drainer', return_value=lambda n: None), \
                mock.patch.object(cli.selfrun, 'count_units', return_value=({}, {})), \
                mock.patch.object(cli.unit_runner, 'PidHoldRun', Hold), \
                mock.patch.object(cli.selfrun, 'run', side_effect=run):
            rc = cli.main(['--jobs', '2'] + list(argv), out=out)
        return rc, calls, bases, out.getvalue()

    def oks(self, text):
        return [ln for ln in text.splitlines() if ln.startswith('kit_tests: OK')]

    def test_the_default_select_runs_the_changed_test_modules_on_real_git_too(self):
        rc, calls, bases, text = self.run_main(
            ['--select', '--changed', 'tests/a/test_m.py', 'kitlib/x.py'])
        self.assertEqual(rc, 0, text)
        self.assertEqual(calls, [('1', SELECTED, True, False),
                                 ('0', ['tests.a.test_m'], True, False)], text)
        self.assertEqual(bases, [])                 # --changed: git is not asked
        self.assertIn('kit_tests: real-git pass: 1 added or changed test module(s): '
                      'tests.a.test_m\n', text)
        self.assertEqual(self.oks(text), [OK_BOTH], text)
        self.assertEqual(text.splitlines()[-1], OK_BOTH)

    def test_the_default_full_diffs_against_the_default_base(self):
        rc, calls, bases, text = self.run_main(
            ['--full'], changed=(['tests/gate/test_other.py', 'docs/x.md'], None))
        self.assertEqual((rc, bases), (0, ['main']), text)
        self.assertEqual(calls, [('1', MODULES, True, False),
                                 ('0', ['tests.gate.test_other'], True, False)], text)
        self.assertEqual(self.oks(text), [OK_BOTH], text)

    def test_no_changed_test_module_is_one_pass_and_a_line_that_is_no_verdict(self):
        for argv, changed in ((['--select', '--changed', 'kitlib/x.py'], ([], None)),
                              (['--full'], (['kitlib/x.py', 'tests/gate/fixtures.py'], None))):
            rc, calls, bases, text = self.run_main(argv, changed=changed)
            self.assertEqual(rc, 0, text)
            self.assertEqual(calls, [('1', SELECTED if '--select' in argv else MODULES,
                                      True, False)], text)
            self.assertIn(NONE_CHANGED + '\n', text)
            self.assertEqual(self.oks(text), ['kit_tests: OK in 1s'], text)

    def test_full_refuses_when_git_cannot_list_the_change(self):
        rc, calls, bases, text = self.run_main(['--full'], changed=(None, 'git: broken'))
        self.assertEqual((rc, calls), (cli.USAGE, []), text)
        self.assertRegex(text, r'(?m)^kit_tests: refused: .*git: broken.* - no test ran')
        self.assertEqual(self.oks(text), [], text)

    def test_a_red_real_git_pass_fails_the_run(self):
        rc, calls, bases, text = self.run_main(['--select', '--changed', 'tests/a/test_m.py'],
                                               rcs=(0, 1))
        self.assertEqual((rc, len(calls)), (1, 2), text)
        self.assertNotRegex(text, r'(?m)^kit_tests: OK')
        self.assertIn('red tier(s): real git\n', text)

    def test_real_git_already_runs_every_test_on_real_git(self):
        for argv in (['--full', '--real-git'],
                     ['--select', '--real-git', '--changed', 'tests/a/test_m.py']):
            rc, calls, bases, text = self.run_main(argv, changed=(['tests/a/test_m.py'], None))
            self.assertEqual((rc, bases, len(calls), calls[0][0]), (0, [], 1, '0'), text)
            self.assertNotIn('real-git pass', text)

    def test_an_outer_real_git_tier_is_one_pass(self):
        rc, calls, bases, text = self.run_main(['--full'], changed=(['tests/a/test_m.py'], None),
                                               outer='0')
        self.assertEqual((rc, bases, [c[0] for c in calls]), (0, [], ['0']), text)

    def test_both_tiers_real_pass_takes_the_changed_modules_too(self):
        rc, calls, bases, text = self.run_main(['--full', '--both-tiers'],
                                               changed=(['tests/a/test_m.py'], None))
        self.assertEqual(rc, 0, text)
        self.assertEqual([c[:2] for c in calls],
                         [('1', MODULES),
                          ('0', ['tests.a.test_m', 'tests.git.test_g', 'tests.landing.test_land_x'])])
        self.assertEqual(self.oks(text), [OK_BOTH], text)

    def test_plan_names_the_real_git_modules_and_runs_nothing(self):
        rc, calls, bases, text = self.run_main(
            ['--select', '--plan', '--changed', 'tests/a/test_m.py'])
        self.assertEqual((rc, calls), (0, []), text)
        self.assertIn('kit_tests: real-git pass: 1 added or changed test module(s): '
                      'tests.a.test_m\n', text)

    def test_the_pid_hold_holds_only_the_fake_pass(self):
        rc, calls, bases, text = self.run_main(
            ['--select', '--pid-hold', '--changed', 'tests/a/test_m.py'])
        self.assertEqual(rc, 0, text)
        self.assertEqual([c[3] for c in calls], [True, False], text)
        self.assertIn('without the pid hold', text)

    def test_a_module_changed_only_in_comments_and_docstrings_runs_on_the_fake_only(self):
        asked = []

        def quiet(top, base, modules):
            asked.append((base, list(modules)))
            return ['tests.gate.test_other']
        changed = (['tests/gate/test_other.py', 'tests/git/test_g.py'], None)
        for argv in (['--full'], ['--select']):
            del asked[:]
            with mock.patch.object(cli.kitrun, 'comment_only', side_effect=quiet):
                rc, calls, bases, text = self.run_main(argv, changed=changed)
            self.assertEqual(rc, 0, text)
            self.assertEqual(asked, [('main', ['tests.git.test_g', 'tests.gate.test_other'])])
            self.assertEqual(calls[1][:2], ('0', ['tests.git.test_g']), text)
            self.assertIn('kit_tests: real-git pass: 1 test module(s) changed only in comments or '
                          'docstrings, fake git only: tests.gate.test_other\n', text)
            self.assertIn('kit_tests: real-git pass: 1 added or changed test module(s): '
                          'tests.git.test_g\n', text)
        # every changed module quiet: one pass, and the line that says no real-git pass runs
        with mock.patch.object(cli.kitrun, 'comment_only', return_value=['tests.gate.test_other']):
            rc, calls, bases, text = self.run_main(
                ['--full'], changed=(['tests/gate/test_other.py'], None))
        self.assertEqual((rc, [c[0] for c in calls]), (0, ['1']), text)
        self.assertIn(NONE_CHANGED + '\n', text)

    def test_changed_paths_given_by_hand_have_no_base_and_all_count(self):
        with mock.patch.object(cli.kitrun, 'comment_only', side_effect=AssertionError) as quiet:
            rc, calls, bases, text = self.run_main(['--select', '--changed', 'tests/a/test_m.py'])
        self.assertEqual((rc, quiet.call_count), (0, 0), text)
        self.assertEqual(calls[1][:2], ('0', ['tests.a.test_m']), text)


DOC_BASE = '"""Doc: docs/gate.md."""\nimport os\n\n\ndef test_x():\n    return os.sep  # why\n'


class CommentOnlyAgainstTheBase(unittest.TestCase):
    """kitrun.comment_only on a throwaway repository: the work-tree source of each changed test
    module against its source at the merge-base with the base (one cat-file batch). The merge-base
    is pinned: the branch commits a doc-only and a code edit (against HEAD both would look
    unchanged), and main moves past the fork point with a code edit to the doc-only module and the
    branch's code edit (against main the answer would flip)."""

    def setUp(self):
        self.repo = gitfixture.TempRepo()
        self.addCleanup(self.repo.cleanup)
        names = ('test_doc', 'test_code', 'test_both', 'test_broken', 'test_gone')
        for n in names:
            self.repo.write('tests/x/%s.py' % n, DOC_BASE)
        self.repo.commit('fork point', *['tests/x/%s.py' % n for n in names])
        doc_only = DOC_BASE.replace('gate.md', 'gate-run.md').replace('# why', '# why not')
        code = DOC_BASE.replace('os.sep', 'os.pathsep')
        self.repo.git('checkout', '-q', '-b', 'topic')
        self.repo.write('tests/x/test_doc.py', doc_only)
        self.repo.write('tests/x/test_code.py', code)
        self.repo.commit('topic: a doc-only and a code edit',
                         'tests/x/test_doc.py', 'tests/x/test_code.py')
        self.repo.git('checkout', '-q', 'main')
        self.repo.write('tests/x/test_doc.py', DOC_BASE.replace('os.sep', 'os.curdir'))
        self.repo.write('tests/x/test_code.py', code)
        self.repo.commit('main moves past the fork point',
                         'tests/x/test_doc.py', 'tests/x/test_code.py')
        self.repo.git('checkout', '-q', 'topic')
        # and the work tree's own changes on top of the branch
        self.repo.write('tests/x/test_both.py',
                        DOC_BASE.replace('gate.md', 'gate-run.md').replace('os.sep', 'os.pathsep'))
        self.repo.write('tests/x/test_broken.py', DOC_BASE + 'def (:\n')
        self.repo.write('tests/x/test_new.py', DOC_BASE)              # added: no base
        os.remove(os.path.join(self.repo.root, 'tests', 'x', 'test_gone.py'))

    def modules(self):
        return ['tests.x.test_%s' % n for n in ('both', 'broken', 'code', 'doc', 'gone', 'new')]

    def test_only_the_comment_and_docstring_edit_is_left_out(self):
        self.assertEqual(cli.kitrun.comment_only(self.repo.root, 'main', self.modules()),
                         ['tests.x.test_doc'])

    def test_a_base_git_cannot_resolve_leaves_none_out(self):
        self.assertEqual(cli.kitrun.comment_only(self.repo.root, 'no-such-base', self.modules()),
                         [])

    def test_no_readable_module_asks_git_nothing(self):
        with mock.patch.object(cli.kitrun.gitrefs, 'merge_base',
                               side_effect=AssertionError) as mb:
            self.assertEqual(cli.kitrun.comment_only(self.repo.root, 'main',
                                                     ['tests.x.test_gone', 'tests.y.test_z']), [])
        self.assertEqual(mb.call_count, 0)


if __name__ == '__main__':
    unittest.main()
