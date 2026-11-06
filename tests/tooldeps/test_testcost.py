"""kitlib.tooldeps.testcost: the per-module cost history a daily full run keeps and the read-only
report over it (docs/test-tools.md "kit_tests")."""
import _thread
import io
import os
import tempfile
import threading
import unittest

from kitlib.tooldeps import selfrun, testcost
from tests.gitfixture import rmtree
from tests.home import TempHomeCase
from tests.tooldeps.test_selfrun import fake_unit
from tools import kit_tests


def rec(date, **modules):
    return {'v': 1, 'date': date, 'label': 'full', 'jobs': 4, 'wall': 100.0, 'modules': {
        m: {'tests': t, 'wall': c, 'cpu': c, 'status': 'OK'} for m, (t, c) in modules.items()}}


class Rows(unittest.TestCase):
    def test_a_split_module_is_one_row_with_summed_numbers_and_the_worst_status(self):
        results = [(('tests.a.test_m.A', 1, 1), 'OK', 2, 10.04, ''),
                   (('tests.a.test_m.B', 1, 1), 'FAILED', 3, 20.0, ''),
                   (('tests.b.test_n', 1, 1), 'OK', 4, 1.0, '')]
        cpus = {'tests.a.test_m.A': 1.5, 'tests.a.test_m.B': 2.25, 'tests.b.test_n': 0.5}
        rows = testcost.module_rows(results, cpus, selfrun.module_of)
        self.assertEqual(rows['tests.a.test_m'],
                         {'tests': 5, 'wall': 30.0, 'cpu': 3.75, 'status': 'FAILED'})
        self.assertEqual(rows['tests.b.test_n']['cpu'], 0.5)

    def test_a_unit_with_no_cpu_makes_its_module_cpu_unknown(self):
        results = [(('tests.a.test_m.A', 1, 1), 'OK', 1, 1.0, ''),
                   (('tests.a.test_m.B', 1, 1), 'OK', 1, 1.0, '')]
        rows = testcost.module_rows(results, {'tests.a.test_m.A': 1.0}, selfrun.module_of)
        self.assertIsNone(rows['tests.a.test_m']['cpu'])


class History(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kit-cost-')
        self.addCleanup(rmtree, self.dir)
        self.path = os.path.join(self.dir, testcost.HISTORY_FILE)

    def test_runs_append_and_read_back_oldest_first_and_bad_lines_are_skipped(self):
        rows = {'tests.a.test_m': {'tests': 2, 'wall': 1.0, 'cpu': 0.5, 'status': 'OK'}}
        self.assertTrue(testcost.record(self.path, rows, 9.0, 4, 'full', today='2026-11-05'))
        with open(self.path, 'a', encoding='utf-8') as fh:
            fh.write('{broken\n[1]\n')
        self.assertTrue(testcost.record(self.path, rows, 9.0, 4, 'full', today='2026-11-06'))
        got = testcost.load(self.path)
        self.assertEqual([r['date'] for r in got], ['2026-11-05', '2026-11-06'])
        self.assertEqual(got[0]['modules'], rows)
        self.assertEqual(testcost.load(os.path.join(self.dir, 'none')), [])
        self.assertFalse(testcost.record(None, rows, 1, 1, 'full'))
        self.assertFalse(testcost.record(self.dir, rows, 1, 1, 'full'))     # a folder: no write


class RunWrites(TempHomeCase):
    def setUp(self):
        super().setUp()         # TempHomeCase: selfrun.run's run folder comes from user.make_temp
        self.dir = tempfile.mkdtemp(prefix='kit-cost-')
        self.addCleanup(rmtree, self.dir)
        self.path = os.path.join(self.dir, testcost.HISTORY_FILE)

    def test_a_run_writes_the_history_only_when_asked_and_leaves_the_times_file_alone(self):
        times = os.path.join(self.dir, selfrun.TIMES_FILE)
        mods = ['tests.a.test_m', 'tests.b.test_n']
        for asked in (False, True):
            out = io.StringIO()
            rc, _ = selfrun.run(mods, tempfile.gettempdir(), 1, out=out,
                                run_unit=fake_unit([], threading.Lock(), pause=0),
                                counts={m: 3 for m in mods}, times_path=times,
                                history_path=self.path if asked else None)
            self.assertEqual(rc, 0, out.getvalue())
            self.assertEqual(os.path.exists(self.path), asked)
        got = testcost.load(self.path)
        self.assertEqual(len(got), 1)
        self.assertEqual(sorted(got[0]['modules']), mods)
        self.assertEqual(got[0]['modules']['tests.a.test_m']['tests'], 3)
        self.assertEqual(sorted(selfrun.load_times(times)), mods)       # scheduling data as before

    def test_an_interrupted_run_writes_no_history(self):
        # A Ctrl+C part-way would show the modules it never reached as gone and the rest as less.
        mods = ['tests.a.test_1', 'tests.a.test_2', 'tests.a.test_3']
        slow = fake_unit([], threading.Lock(), pause=0.5)

        def run_unit(unit, audit, timeout, root=None, cmd=None, env=None):
            if unit[0] == 'tests.a.test_1':
                threading.Timer(0.05, _thread.interrupt_main).start()
            return slow(unit, audit, timeout, root=root, cmd=cmd, env=env)
        out = io.StringIO()
        rc, _ = selfrun.run(mods, tempfile.gettempdir(), 1, out=out, run_unit=run_unit,
                            counts={m: 3 for m in mods}, history_path=self.path)
        self.assertEqual(rc, selfrun.INTERRUPTED, out.getvalue())
        self.assertFalse(os.path.exists(self.path))


class Report(unittest.TestCase):
    RECORDS = [rec('2026-10-23', **{'tests.a.test_x': (10, 100.0), 'tests.b.test_y': (5, 20.0),
                                    'tests.a.test_gone': (1, 1.0)}),
               rec('2026-11-05', **{'tests.a.test_x': (30, 300.0), 'tests.b.test_y': (5, 20.0),
                                    'tests.c.test_new': (2, 50.0)})]

    def text(self, **kw):
        return '\n'.join(testcost.report(self.RECORDS, **kw))

    def test_no_history_says_how_to_get_one(self):
        self.assertIn('no cost history yet', testcost.report([])[0])

    def test_modules_are_ranked_by_cpu_with_growth_from_the_baseline(self):
        text = self.text(since='2026-10-30')
        self.assertIn('growth since the 2026-10-23 run', text)       # latest on or before the date
        top = text.split('per package')[0]
        names = [w for w in top.split() if w.startswith('tests.')]
        self.assertEqual(names, ['tests.a.test_x', 'tests.c.test_new', 'tests.b.test_y'])
        self.assertRegex(top, r'300\.0\s+300\.0\s+30\s+10\.000\s+\+200%\s+tests\.a\.test_x')
        self.assertRegex(top, r'\s+new\s+tests\.c\.test_new')
        self.assertRegex(top, r'\+0%\s+tests\.b\.test_y')
        self.assertIn('1 module(s) in the 2026-10-23 run are gone', text)
        self.assertRegex(text, r'\+\d+%\s+tests/a')                  # the per-package table

    def test_a_date_before_every_run_measures_from_the_earliest_and_top_cuts_the_list(self):
        text = self.text(since='2020-01-01', top=1)
        self.assertIn('earliest on record', text)
        top = text.split('per package')[0]
        self.assertIn('tests.a.test_x', top)
        self.assertNotIn('tests.c.test_new', top)

    def test_one_run_on_record_has_no_growth_column_and_says_why(self):
        text = '\n'.join(testcost.report(self.RECORDS[:1]))
        self.assertNotIn('growth since', text)
        self.assertIn('no growth line: only one run is on record', text)

    def test_a_date_on_or_after_the_latest_run_says_why_there_is_no_growth(self):
        text = self.text(since='2026-11-06')
        self.assertNotIn('growth since', text)
        self.assertIn('no growth line: --since 2026-11-06 is on or after the latest run '
                      '(2026-11-05)', text)


class Cli(unittest.TestCase):
    def test_a_bad_date_or_a_report_option_without_the_report_is_a_usage_error(self):
        for argv in (['--cost-report', '--since', 'last week'], ['--select', '--top', '5'],
                     ['--cost-report', '--top', '0']):
            with self.assertRaises(SystemExit) as e:
                kit_tests.main(argv, io.StringIO())
            self.assertEqual(e.exception.code, 2, argv)

    def test_the_options_parse(self):
        a = kit_tests.build_parser().parse_args(['--cost-report', '--top', '5', '--sort', 'wall'])
        self.assertEqual((a.cost_report, a.top, a.sort, a.since), (True, 5, 'wall', None))


if __name__ == '__main__':
    unittest.main()
