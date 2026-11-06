"""kitlib.tooldeps.kitrun, kit_tests' orchestration, through the library (the options the tool parses
come from its parser), and tools/kit_tests.py's Ctrl+C: an interrupt anywhere in the run path is
exit 130, not a traceback."""
import contextlib
import importlib.util
import io
import os
import unittest
from unittest import mock

from kitlib.tooldeps import kitrun, selftree, tiers

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location('kit_tests_entry_for_kitrun_tests',
                                               os.path.join(KIT, 'tools', 'kit_tests.py'))
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

HERE = os.path.abspath('.')
MODULES = ['tests.a.test_m', 'tests.b.test_n']


def options(*argv):
    return cli.build_parser().parse_args(list(argv))


@contextlib.contextmanager
def fixed_checkout(**patches):
    """A checkout whose table and test modules are fixed and whose git is not asked."""
    with mock.patch.dict(os.environ, {'KIT_FAKE_GIT': '1'}), \
            mock.patch.object(selftree, 'config', return_value={'serial': []}), \
            mock.patch.object(selftree, 'all_test_modules', return_value=MODULES), \
            mock.patch.object(kitrun.gitrepo, 'common_dir', return_value=(None, 'no git')), \
            mock.patch.object(kitrun.giveback, 'drainer', return_value=None):
        yield


class Orchestration(unittest.TestCase):
    def test_plan_says_the_selection_and_runs_nothing(self):
        out = io.StringIO()
        with fixed_checkout(), mock.patch.object(kitrun, 'plan', return_value=['tests.a.test_m']):
            rc = kitrun.run(options('--select', '--changed', 'x.py', '--plan'), HERE, out)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), '')

    def test_full_one_process_is_run_pass_on_the_fake_tier_and_its_verdict(self):
        out, seen = io.StringIO(), {}

        def run_pass(args, top, cfg, jobs, fake, tier_out, hold, **kw):
            seen.update(args=args, jobs=jobs, fake=fake, hold=hold)
            return 0
        with fixed_checkout(), \
                mock.patch.object(selftree, 'changed_paths', return_value=([], None)), \
                mock.patch.object(kitrun, 'default_base', return_value=('main', None)), \
                mock.patch.object(kitrun, 'run_pass', side_effect=run_pass):
            rc = kitrun.run(options('--full', '--jobs', '1'), HERE, out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual(seen, {'args': kitrun.FULL, 'jobs': 1, 'fake': '1', 'hold': None})
        self.assertIn('kit_tests: full suite (--full)', out.getvalue())
        self.assertIn('real-git pass: no added or changed test module', out.getvalue())

    def test_a_change_adding_a_test_module_gets_the_second_pass_on_real_git(self):
        out, passes = io.StringIO(), []

        def run_pass(args, top, cfg, jobs, fake, tier_out, hold, **kw):
            passes.append((fake, args))
            return 0
        with fixed_checkout(), \
                mock.patch.object(kitrun, 'plan', return_value=['tests.b.test_n']), \
                mock.patch.object(kitrun, 'run_pass', side_effect=run_pass):
            rc = kitrun.run(options('--select', '--changed', 'tests/b/test_n.py', '--jobs', '1'),
                            HERE, out)
        self.assertEqual(rc, 0, out.getvalue())
        self.assertEqual([p[0] for p in passes], ['1', '0'])
        self.assertEqual(passes[1][1], ['tests.b.test_n'])
        self.assertIn('both tiers green', out.getvalue())

    def test_only_the_fake_pass_of_full_both_tiers_keeps_the_cost_history(self):
        # The daily run's daily_cmd; the landing gate's --full, --select and the real-git pass
        # would write partial or duplicate days.
        added = ['tests.b.test_n']          # a test module the change adds: its own real-git pass
        for argv, again, want in (
                (('--full', '--both-tiers'), [], {'1': True, '0': False}),
                (('--full',), [], {'1': False}),
                (('--full',), added, {'1': False, '0': False}),
                (('--select', '--changed', 'x.py'), [], {'1': False}),
                (('--select', '--changed', 'x.py', '--both-tiers'), [], {'1': False, '0': False})):
            out, seen = io.StringIO(), {}

            def run_pass(args, top, cfg, jobs, fake, tier_out, hold, **kw):
                seen[fake] = kw.get('history', False)
                return 0
            with fixed_checkout(), \
                    mock.patch.object(selftree, 'changed_paths', return_value=([], None)), \
                    mock.patch.object(kitrun, 'default_base', return_value=('main', None)), \
                    mock.patch.object(kitrun, 'plan', return_value=['tests.a.test_m']), \
                    mock.patch.object(kitrun.tiers, 'real_git_modules',
                                      return_value=['tests.a.test_m']), \
                    mock.patch.object(kitrun.tiers, 'changed_test_modules', return_value=again), \
                    mock.patch.object(kitrun, 'run_pass', side_effect=run_pass):
                rc = kitrun.run(options(*(argv + ('--jobs', '1'))), HERE, out)
            self.assertEqual((rc, seen), (0, want), (argv, again, out.getvalue()))

    def test_select_and_full_get_the_same_jobs_from_the_lock_mask(self):
        # The scoped branch gate and --full size their pool the same way: by the cpu slots the
        # lock run holds. What differs is the kind the caller books (docs/test-tools.md).
        for argv in (('--select', '--changed', 'tests/b/test_n.py'), ('--full',)):
            out, seen = io.StringIO(), []

            def run_pass(args, top, cfg, jobs, fake, tier_out, hold, **kw):
                seen.append(jobs)
                return 0
            with fixed_checkout(), \
                    mock.patch.dict(os.environ, {kitrun.selfrun.CPUMASK_ENV: '0xff'}), \
                    mock.patch.object(selftree, 'changed_paths', return_value=([], None)), \
                    mock.patch.object(kitrun, 'default_base', return_value=('main', None)), \
                    mock.patch.object(kitrun, 'plan', return_value=['tests.a.test_m']), \
                    mock.patch.object(kitrun.tiers, 'changed_test_modules', return_value=[]), \
                    mock.patch.object(kitrun, 'run_pass', side_effect=run_pass):
                rc = kitrun.run(options(*argv), HERE, out)
            self.assertEqual((rc, seen), (0, [8]), out.getvalue())
            self.assertIn('kit_tests: jobs 8: the 8 logical processor(s) the lock run holds',
                          out.getvalue())

    def test_a_git_that_cannot_list_the_change_refuses_with_usage(self):
        out = io.StringIO()
        with fixed_checkout(), \
                mock.patch.object(selftree, 'changed_paths', return_value=(None, 'no git')), \
                mock.patch.object(kitrun, 'default_base', return_value=('main', None)):
            rc = kitrun.run(options('--select'), HERE, out)
        self.assertEqual(rc, kitrun.USAGE)
        self.assertIn('kit_tests: refused: no git - no test ran', out.getvalue())

    def test_an_interrupt_in_the_first_count_is_130_before_the_first_pass(self):
        out = io.StringIO()
        with fixed_checkout(), \
                mock.patch.object(kitrun, 'plan', return_value=['tests.b.test_n']), \
                mock.patch.object(kitrun.selfrun, 'count_units', side_effect=KeyboardInterrupt):
            rc = kitrun.run(options('--select', '--changed', 'tests/b/test_n.py', '--jobs', '2'),
                            HERE, out)
        self.assertEqual(rc, tiers.INTERRUPTED)
        self.assertIn('INTERRUPTED, exit 130 before the first pass', out.getvalue())


class CtrlC(unittest.TestCase):
    """The tool answers 130 for an interrupt anywhere in the run, not only in the count."""

    def main(self, argv, **patches):
        out = io.StringIO()
        with fixed_checkout(), \
                mock.patch.object(cli.gitrepo, 'toplevel', return_value=(HERE, None)), \
                mock.patch.object(selftree, 'changed_paths', return_value=([], None)), \
                mock.patch.object(kitrun, 'default_base', return_value=('main', None)):
            with contextlib.ExitStack() as stack:
                for obj, name, effect in patches.values():
                    stack.enter_context(mock.patch.object(obj, name, side_effect=effect))
                rc = cli.main(argv, out=out)
        return rc, out.getvalue()

    def test_interrupt_in_the_one_process_run(self):
        rc, out = self.main(['--full', '--jobs', '1'],
                            call=(cli.subprocess, 'call', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)

    def test_interrupt_in_the_pool_run(self):
        rc, out = self.main(['--full', '--jobs', '2'],
                            run=(cli.selfrun, 'run', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)

    def test_interrupt_in_the_selection(self):
        rc, out = self.main(['--select', '--changed', 'tools/x.py'],
                            plan=(kitrun, 'plan', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)
        self.assertIn('INTERRUPTED, exit 130', out)

    def test_interrupt_in_the_list_path(self):
        rc, out = self.main(['--list'], collect=(cli.selflist, 'collect', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)

    def test_interrupt_in_finding_the_checkout(self):
        rc, out = self.main(['--full'], top=(cli.gitrepo, 'toplevel', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)

    def test_interrupt_in_the_change_list(self):
        rc, out = self.main(['--select'],
                            changed=(selftree, 'changed_paths', KeyboardInterrupt))
        self.assertEqual(rc, 130, out)


if __name__ == '__main__':
    unittest.main()
