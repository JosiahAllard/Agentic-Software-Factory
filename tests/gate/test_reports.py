"""kitlib.gate.reports: the JUnit XML readers red-first and the targeted rerun share, a red gradle
or dotnet step's failing test ids (each tied to the module or project folder its report is in),
and the judgement of those ids once the step ran again whole. Whatever does not add up is
uncovered: only failing tests, counted the same in the console and the reports, are covered."""
import os
import pathlib
import shutil
import tempfile
import time
import unittest

from kitlib.gate import reports as R

FOO = 'pkg.FooTest'
BAD = FOO + '.the flaky one'


def suite(cls, *cases):
    """A JUnit XML testsuite of `cases`, each `(name, status)`: ok, failure, error or skipped."""
    body = ''.join('<testcase classname="%s" name="%s">%s</testcase>' % (
        cls, n, '' if st == 'ok' else '<%s message="m" type="T">x</%s>' % (st, st))
        for n, st in cases)
    bad = sum(st == 'failure' for _, st in cases), sum(st == 'error' for _, st in cases)
    return '<testsuite name="%s" tests="%d" failures="%d" errors="%d">%s</testsuite>' % (
        cls, len(cases), bad[0], bad[1], body)


def gradle_log(*tasks, extra=()):
    """A red gradle console of failed test tasks, each `(task, report folder, tests, failed)`."""
    lines = ['gate_tiers: --- gradle-build: gradlew.bat build --offline']
    for task, _, n, m in tasks:
        lines += ['', '> Task %s' % task, '', 'FooTest > the flaky one FAILED',
                  '    org.opentest4j.AssertionFailedError at FooTest.java:7', '',
                  '> Task :other:assemble', '', '> Task %s' % task, '',
                  '%d tests completed, %d failed' % (n, m), '', '> Task %s FAILED' % task]
    lines += ['', 'FAILURE: Build completed with %d failures.' % len(tasks)]
    for i, (task, module, _, _) in enumerate(tasks):
        url = pathlib.Path(module, 'build', 'reports', 'tests', 'test', 'index.html').as_uri()
        lines += ['', '%d: Task failed with an exception.' % (i + 1), '-----------',
                  '* What went wrong:', "Execution failed for task '%s'." % task,
                  '> There were failing tests. See the report at: ' + url, '', '* Try:',
                  '> Run with --scan to get full insights.']
    lines += list(extra) + ['', 'BUILD FAILED in 9s', 'gate_tiers gradle: FAILED - gradle-build: exit 1']
    return '\n'.join(lines).splitlines()


DOTNET_LOG = r'''gate_tiers: --- dotnet-test: python tools\dotnet_each.py test
gate: dotnet test --nologo --no-build {top}\shared\P.Tests\P.Tests.csproj
Test run for {top}\shared\P.Tests\bin\Debug\net10.0\P.Tests.dll (.NETCoreApp,Version=v10.0)
A total of 1 test files matched the specified pattern.

Passed!  - Failed:     0, Passed:  1071, Skipped:     0, Total:  1071, Duration: 529 ms - P.Tests.dll (net10.0)
gate: dotnet test --nologo --no-build {top}\node\N.Tests\N.Tests.csproj
Test run for {top}\node\N.Tests\bin\Debug\net10.0\N.Tests.dll (.NETCoreApp,Version=v10.0)
A total of 1 test files matched the specified pattern.
[xUnit.net 00:00:19.71]     N.Tests.RoutingTests.ADuplicateSpawn_KeepsFrames [FAIL]
  Failed N.Tests.RoutingTests.ADuplicateSpawn_KeepsFrames [5 s]
  Error Message:
   Assert.Contains() Failure: Filter not matched in collection
  Stack Trace:
     at N.Tests.RoutingTests.ADuplicateSpawn_KeepsFrames() in {top}\node\N.Tests\RoutingTests.cs:line 80
--- End of stack trace from previous location ---

Failed!  - Failed:     1, Passed:   543, Skipped:     2, Total:   546, Duration: 2 m 31 s - N.Tests.dll (net10.0)
gate: dotnet test failed for {top}\node\N.Tests\N.Tests.csproj (exit 1)
gate_tiers: --- dotnet-test exited 1
gate_tiers dotnet: FAILED - dotnet-test: exit 1
'''


class Case(unittest.TestCase):
    def setUp(self):
        self.top = tempfile.mkdtemp(prefix='kit-reports-')
        self.addCleanup(shutil.rmtree, self.top, ignore_errors=True)
        self.since = time.time() - 60

    def put(self, rel, text, age=0):
        p = os.path.join(self.top, *rel.split('/'))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(text)
        if age:
            t = time.time() - age
            os.utime(p, (t, t))
        return p

    def dotnet(self, text):
        """A dotnet log of this tree, its paths in this platform's separator."""
        return text.replace('{top}', self.top).replace('\\', os.sep)

    def module(self, rel='app/api'):
        return os.path.join(self.top, *rel.split('/'))


class JUnit(Case):
    """The JUnit readers red-first reads its gradle and command runners' verdicts from."""

    def test_junit_counts_this_run_only(self):
        self.put('TEST-a.xml', '<testsuite tests="2" failures="1" errors="0"/>')
        self.put('TEST-old.xml', '<testsuite tests="9" failures="9" errors="0"/>', age=3600)
        self.assertEqual(R.junit_counts(self.top, time.time() - 60)[:2], (2, 1))

    def test_junit_unparseable_is_none(self):
        self.put('TEST-a.xml', '<testsuite tests="2"')
        self.assertIsNone(R.junit_counts(self.top))

    def test_junit_file_counts(self):
        wrapped = self.put('a.xml', '<testsuites><testsuite tests="2" failures="1" errors="0"/>'
                                    '<testsuite tests="3" failures="0" errors="1"/></testsuites>')
        flat = self.put('b.xml', '<testsuite tests="4" failures="0" errors="0"/>')
        self.assertEqual(R.junit_file_counts(wrapped), (5, 2, []))
        self.assertEqual(R.junit_file_counts(flat), (4, 0, []))
        self.assertIsNone(R.junit_file_counts(flat, since=os.path.getmtime(flat) + 60))
        self.assertIsNone(R.junit_file_counts(os.path.join(self.top, 'none.xml')))

    def test_junit_cases(self):
        p = self.put('c.xml', suite(FOO, ('a', 'ok'), ('b', 'failure'), ('c', 'error'),
                                    ('d', 'skipped')))
        self.assertEqual(R.junit_cases(p), [(FOO + '.a', FOO, 'ok'), (FOO + '.b', FOO, 'failure'),
                                            (FOO + '.c', FOO, 'error'),
                                            (FOO + '.d', FOO, 'skipped')])
        self.assertIsNone(R.junit_cases(self.put('d.xml', '<testsuite')))


class GradleRed(Case):
    """gradle_red: a failed test task's ids from the XML it wrote in this run, its counts the
    console's."""

    def xml(self, rel='app/api/build/test-results/test', cases=(('a', 'ok'), ('the flaky one',
                                                                     'failure'), ('c', 'ok')),
            age=0):
        return self.put(rel + '/TEST-%s.xml' % FOO, suite(FOO, *cases), age=age)

    def red(self, *tasks, extra=()):
        return R.gradle_red(gradle_log(*tasks, extra=extra), self.top, self.since)

    def test_a_failed_test_task_gives_its_ids_from_its_fresh_xml(self):
        self.xml()
        self.assertEqual(self.red((':app-api:test', self.module(), 3, 1)), (
            [{'id': BAD, 'cls': FOO, 'env': {}, 'runner': 'gradle', 'scope': 'app/api',
              'report': {'task': ':app-api:test'}}], []))

    def test_a_results_folder_named_after_the_task_with_a_suffix_counts(self):
        self.xml('app/api/build/test-results/test-worldmap')
        failures, uncovered = self.red((':app-api:test', self.module(), 3, 1))
        self.assertEqual(([f['id'] for f in failures], uncovered), ([BAD], []))

    def test_two_failed_tasks_are_both_covered(self):
        self.xml()
        self.put('lib/build/test-results/test/TEST-q.BarTest.xml',
                 suite('q.BarTest', ('x', 'error'), ('y', 'ok')))
        failures, uncovered = self.red((':app-api:test', self.module(), 3, 1),
                                       (':lib:test', self.module('lib'), 2, 1))
        self.assertEqual(uncovered, [])
        self.assertEqual([(f['id'], f['scope']) for f in failures],
                         [(BAD, 'app/api'), ('q.BarTest.x', 'lib')])

    def test_stale_xml_is_uncovered(self):
        self.xml(age=3600)
        failures, uncovered = self.red((':app-api:test', self.module(), 3, 1))
        self.assertEqual(failures, [])
        self.assertIn('holds 0 test(s), 0 failed; the log says 3, 1 failed', uncovered[0])

    def test_xml_that_does_not_match_the_console_count_is_uncovered(self):
        self.xml()
        _, uncovered = self.red((':app-api:test', self.module(), 3, 2))
        self.assertIn('the log says 3, 2 failed', uncovered[0])

    def test_a_failure_that_is_not_failing_tests_is_uncovered(self):
        self.xml()
        for wrong in (["Execution failed for task ':app-api:compileJava'.",
                       '> Compilation failed; see the compiler error output for details.'],
                      ["Execution failed for task ':app-api:test'.",
                       '> java.io.EOFException (no error message)'],
                      ['java.io.EOFException']):
            with self.subTest(wrong[-1]):
                extra = ['', 'x: Task failed with an exception.', '* What went wrong:'] + wrong
                failures, uncovered = self.red((':app-api:test', self.module(), 3, 1),
                                               extra=extra)
                self.assertEqual(failures, [])
                self.assertIn('gradle: a failure that is not failing tests: ' + ' '.join(wrong),
                              uncovered)

    def test_a_log_with_no_failed_task_is_uncovered(self):
        failures, uncovered = R.gradle_red(['> Task :a:test', 'BUILD FAILED in 1s'], self.top,
                                           self.since)
        self.assertEqual(failures, [])
        self.assertIn('no test task failed', uncovered[0])

    def test_a_report_outside_the_tree_is_uncovered(self):
        out = tempfile.mkdtemp(prefix='kit-reports-out-')
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        _, uncovered = self.red((':app-api:test', out, 3, 1))
        self.assertIn('is not a module folder in the tree', uncovered[0])

    def test_no_tree_or_start_reads_no_report(self):
        log = gradle_log((':app-api:test', self.module(), 3, 1))
        for top, since in ((None, self.since), (self.top, None)):
            self.assertEqual(R.gradle_red(log, top, since),
                             ([], ['gradle: its test reports are not read here']))


class GradleJudge(Case):
    FAILURE = {'id': BAD, 'cls': FOO, 'runner': 'gradle', 'scope': 'app/api',
               'report': {'task': ':app-api:test'}}

    def judge(self, status=None, age=0):
        if status:
            self.put('app/api/build/test-results/test/TEST-%s.xml' % FOO,
                     suite(FOO, ('a', 'ok'), ('the flaky one', status)), age=age)
        return R.judge([self.FAILURE], '', self.top, self.since)[BAD]

    def test_each_outcome(self):
        for status, age, want in (('ok', 0, 'ok'), ('failure', 0, 'failure'),
                                  ('skipped', 0, 'skipped'), ('ok', 3600, 'not run'),
                                  (None, 0, 'not run')):
            with self.subTest(status=status, age=age):
                self.assertEqual(self.judge(status, age), want)

    def test_an_unreadable_fresh_report_is_no_pass(self):
        self.put('app/api/build/test-results/test/TEST-%s.xml' % FOO, '<testsuite')
        self.assertEqual(self.judge(), 'report unreadable')


class DotnetRed(Case):
    def log(self, text=None):
        return self.dotnet(text or DOTNET_LOG).splitlines()

    def test_each_failed_line_of_a_summary_is_an_id_in_its_project_folder(self):
        self.assertEqual(R.dotnet_red(self.log(), self.top), (
            [{'id': 'N.Tests.RoutingTests.ADuplicateSpawn_KeepsFrames',
              'cls': 'N.Tests.RoutingTests', 'env': {}, 'runner': 'dotnet',
              'scope': 'node/N.Tests',
              'report': {'dll': 'N.Tests.dll', 'fw': 'net10.0', 'total': 546, 'skipped': 2}}],
            []))

    def test_no_tree_checks_no_folder(self):
        failures, uncovered = R.dotnet_red(self.log(), None)
        self.assertEqual(([f['scope'] for f in failures], uncovered), ([None], []))

    def test_what_does_not_add_up_is_uncovered(self):
        cases = [
            ('a build error', DOTNET_LOG.replace(
                'A total of 1', 'N.cs(3,4): error CS0103: no x\nA total of 1'), 'error CS0103'),
            ('a crashed host', DOTNET_LOG + 'The active test run was aborted. Reason: Test host '
                                            'process crashed\n', 'aborted'),
            ('more lines than the summary', DOTNET_LOG.replace(
                '  Error Message:', '  Failed N.Tests.RoutingTests.Other [1 s]\n  Error Message:'),
             '2 Failed line(s) against 1'),
            ('a failed line with no summary', DOTNET_LOG + '  Failed N.Tests.X.Y [1 s]\n',
             'with no test summary'),
            ('no test ran', DOTNET_LOG.replace('Passed:  1071, Skipped:     0, Total:  1071',
                                               'Passed:     0, Skipped:     0, Total:     0'),
             'no test ran'),
            ('no summary at all', 'Test run for x.dll (.NETCoreApp)\n', 'no `dotnet test` summary'),
            ('a project outside the tree', DOTNET_LOG.replace(
                r'{top}\node\N.Tests\bin', r'{top}\..\N.Tests\bin'), 'no project folder'),
        ]
        for what, text, said in cases:
            with self.subTest(what):
                _, uncovered = R.dotnet_red(self.log(text), self.top)
                self.assertTrue(any(said in u for u in uncovered), uncovered)

    def test_the_class_of_a_test_name(self):
        self.assertEqual(R.dotnet_class('A.B.C.M(x: 1, y: "a.b")'), 'A.B.C')
        self.assertEqual(R.dotnet_class('A.B.M'), 'A.B')
        self.assertEqual(R.dotnet_class('M'), 'M')


class DotnetJudge(Case):
    ID = 'N.Tests.RoutingTests.ADuplicateSpawn_KeepsFrames'

    def judge(self, text):
        failures, _ = R.dotnet_red(self.dotnet(DOTNET_LOG).splitlines(), self.top)
        return R.judge(failures, self.dotnet(text), self.top, self.since)[self.ID]

    def test_each_outcome(self):
        green = DOTNET_LOG.replace('Failed!  - Failed:     1, Passed:   543',
                                   'Passed!  - Failed:     0, Passed:   544').replace(
            '  Failed ' + self.ID + ' [5 s]', '')
        cases = [
            ('the same run with no failure', green, 'ok'),
            ('it failed again', DOTNET_LOG, 'Failed'),
            ('another total', green.replace('Total:   546', 'Total:   545'), 'not run'),
            ('another skipped count', green.replace('Skipped:     2', 'Skipped:     3'),
             'not run'),
            ('another test failed', green.replace('Passed!  - Failed:     0',
                                                  'Failed!  - Failed:     1'), 'not run'),
            ('no summary of its dll', 'Build FAILED.\n', 'not run'),
        ]
        for what, text, want in cases:
            with self.subTest(what):
                self.assertEqual(self.judge(text), want)


class Detect(unittest.TestCase):
    def test_which_log_is_which(self):
        self.assertTrue(R.is_gradle(['> Task :a:test']))
        self.assertTrue(R.is_gradle(['BUILD FAILED in 3s']))
        self.assertFalse(R.is_gradle(['Build FAILED.']))
        self.assertTrue(R.is_dotnet(['Build FAILED.']))
        self.assertTrue(R.is_dotnet(DOTNET_LOG.splitlines()))
        self.assertFalse(R.is_dotnet(['lint: 3 problems']))


if __name__ == '__main__':
    unittest.main()
