"""RED only on an assertion failure (kitlib.redfirst.verdict, docs/redfirst.md "What counts as
red"): a named test that fails at the base on a crash, a setup error or anything else that is no
assertion is INCONCLUSIVE, never RED, so a proof cannot pass on a test that never reached its
assertion (a game repo's 11-03 proof: every test died on a NullReferenceException, not its check).

Each runner kind: python (unittest's FAIL and ERROR blocks), gradle and a command runner's result
(JUnit XML: <error>, or a <failure> whose type is no assertion), dotnet (the .trx message), and a
command runner in exit-code mode (INCONCLUSIVE unless the runner names an `assertion` pattern a
log line matches). A runner's `assertion` regex names more failures that count as assertions.
"""
import os
import tempfile
import time
import unittest

from kitlib.redfirst import verdict as V

from .test_run import Case

INCONCLUSIVE = 'INCONCLUSIVE'
SEP = '=' * 70
DASH = '-' * 70


def block(kind, name, *tail):
    """One unittest -v failure block: the header, a frame and the exception lines."""
    return '\n'.join([SEP, '%s: %s (test_x.T.%s)' % (kind, name, name), DASH,
                      'Traceback (most recent call last):',
                      '  File "test_x.py", line 9, in %s' % name, '    the line', '    ^^^^^^']
                     + list(tail) + [''])


def unittest_log(*blocks, ran=1):
    fails = sum(b.startswith(SEP + '\nFAIL') for b in blocks)
    errs = len(blocks) - fails
    return ('test_x ... \n\n' + '\n'.join(blocks) + '%s\nRan %d tests in 0.01s\n\nFAILED (failures=%d, '
            'errors=%d)\n' % (DASH, ran, fails, errs))


ASSERT_FAIL = block('FAIL', 'test_a', 'AssertionError: 1 != 2')
NONE_CRASH = block('ERROR', 'test_b', "AttributeError: 'NoneType' object has no attribute 'x'")
TYPE_CRASH = block('ERROR', 'test_c', "TypeError: unsupported operand type(s) for +: 'NoneType'")
MISSING_NAME = block('ERROR', 'test_d', "AttributeError: module 'calc' has no attribute 'mul'")
MISSING_IMPORT = block('ERROR', 'test_e', "ImportError: cannot import name 'mul' from 'calc'")
CHAINED = block('ERROR', 'test_f', "AttributeError: module 'calc' has no attribute 'mul'", '',
                'During handling of the above exception, another exception occurred:', '',
                'Traceback (most recent call last):', '  File "test_x.py", line 12, in test_f',
                '    raise ValueError()', 'ValueError: two', 'lines')
SETUP_CRASH = '\n'.join([SEP, 'ERROR: setUpClass (test_x.T)', DASH,
                         'Traceback (most recent call last):', '  File "test_x.py", line 4',
                         '    x', 'OSError: [Errno 2] No such file or directory', ''])


class Python(unittest.TestCase):
    def test_an_assertion_failure_is_red(self):
        v, reason = V.python_verdict(1, unittest_log(ASSERT_FAIL))
        self.assertEqual(v, 'RED', reason)

    def test_a_crash_is_inconclusive_and_names_the_test_and_its_exception(self):
        for crash in (NONE_CRASH, TYPE_CRASH, SETUP_CRASH):
            with self.subTest(crash=crash.splitlines()[1]):
                v, reason = V.python_verdict(1, unittest_log(crash))
                self.assertEqual(v, INCONCLUSIVE, reason)
                self.assertIn(crash.splitlines()[1].split(': ', 1)[1], reason)
                self.assertIn(crash.rstrip().splitlines()[-1], reason)

    def test_one_crash_beside_an_assertion_failure_is_inconclusive(self):
        v, reason = V.python_verdict(1, unittest_log(ASSERT_FAIL, NONE_CRASH, ran=2))
        self.assertEqual(v, INCONCLUSIVE, reason)
        self.assertIn('test_b', reason)

    def test_a_name_the_base_lacks_is_red(self):
        """A module attribute or an import the base lacks is what the test file failing to import
        is (RED there already): the test names code the change adds."""
        for missing in (MISSING_NAME, MISSING_IMPORT):
            with self.subTest(missing=missing.splitlines()[1]):
                v, reason = V.python_verdict(1, unittest_log(missing))
                self.assertEqual(v, 'RED', reason)
                self.assertIn(missing.rstrip().splitlines()[-1], reason)

    def test_the_last_exception_of_a_chain_decides(self):
        v, reason = V.python_verdict(1, unittest_log(CHAINED))
        self.assertEqual(v, INCONCLUSIVE, reason)
        self.assertIn('ValueError: two', reason)

    def test_a_failure_with_no_failed_test_is_inconclusive(self):
        log = 'test_a ... unexpected success\n\n%s\nRan 1 test in 0s\n\nFAILED (unexpected successes=1)\n' % DASH
        self.assertEqual(V.python_verdict(1, log)[0], INCONCLUSIVE)

    def test_the_runner_s_assertion_pattern_names_more_assertions(self):
        v, reason = V.python_verdict(1, unittest_log(TYPE_CRASH), r'^TypeError: unsupported')
        self.assertEqual(v, 'RED', reason)
        self.assertEqual(V.python_verdict(1, unittest_log(NONE_CRASH), r'^TypeError')[0],
                         INCONCLUSIVE)


def junit(*cases):
    """A JUnit XML suite of `cases`: (name, tag or None, type, message)."""
    body = ''
    for name, tag, typ, msg in cases:
        inner = '' if tag is None else '<%s type="%s" message="%s">trace</%s>' % (tag, typ, msg, tag)
        body += '<testcase name="%s" classname="pkg.FooTest">%s</testcase>' % (name, inner)
    fails = sum(1 for c in cases if c[1] == 'failure')
    errs = sum(1 for c in cases if c[1] == 'error')
    return ('<testsuite name="pkg.FooTest" tests="%d" failures="%d" errors="%d">%s</testsuite>'
            % (len(cases), fails, errs, body))


AFE = ('a', 'failure', 'org.opentest4j.AssertionFailedError', 'expected: &lt;1&gt; but was: &lt;2&gt;')
NPE = ('b', 'failure', 'java.lang.NullPointerException', 'Cannot invoke a() because b is null')
ERR = ('c', 'error', 'java.io.IOException', 'gone')
PASS = ('d', None, None, None)


class Gradle(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, self.dir, True)

    def verdict(self, *cases, assertion=None):
        task = os.path.join(self.dir, 'test')
        os.makedirs(task, exist_ok=True)
        with open(os.path.join(task, 'TEST-pkg.FooTest.xml'), 'w', encoding='utf-8') as f:
            f.write(junit(*cases))
        counts = V.junit_counts(self.dir, time.time() - 60, 'FooTest')
        return V.gradle_verdict(1, '', counts, [], assertion)

    def test_an_assertion_failure_is_red(self):
        self.assertEqual(self.verdict(AFE, PASS)[0], 'RED')
        for typ in ('java.lang.AssertionError', 'org.junit.ComparisonFailure',
                    'org.opentest4j.MultipleFailuresError'):
            with self.subTest(typ=typ):
                self.assertEqual(self.verdict(('a', 'failure', typ, 'x'))[0], 'RED')

    def test_an_exception_or_an_error_element_is_inconclusive(self):
        for cases in ((NPE,), (ERR,), (AFE, NPE)):
            with self.subTest(cases=cases):
                v, reason = self.verdict(*cases)
                self.assertEqual(v, INCONCLUSIVE, reason)
        self.assertIn('NullPointerException', self.verdict(AFE, NPE)[1])

    def test_a_counted_failure_the_file_does_not_name_is_inconclusive(self):
        task = os.path.join(self.dir, 'test')
        os.makedirs(task)
        with open(os.path.join(task, 'TEST-pkg.FooTest.xml'), 'w', encoding='utf-8') as f:
            f.write('<testsuite tests="1" failures="1" errors="0"/>')
        counts = V.junit_counts(self.dir, time.time() - 60, 'FooTest')
        self.assertEqual(V.gradle_verdict(1, '', counts, [])[0], INCONCLUSIVE)

    def test_the_runner_s_assertion_pattern_names_more_assertions(self):
        self.assertEqual(self.verdict(NPE, assertion=r'NullPointerException: Cannot invoke')[0], 'RED')


TRX = ('<?xml version="1.0" encoding="utf-8"?><TestRun xmlns="http://microsoft.com/schemas/'
       'VisualStudio/TeamTest/2010"><Results>%s</Results><ResultSummary outcome="Failed">'
       '<Counters total="%d" executed="%d" passed="%d" failed="%d" error="0" /></ResultSummary>'
       '</TestRun>')


def trx(*results):
    """A .trx of `results`: (test name, outcome, message or None)."""
    body = ''
    for name, outcome, msg in results:
        info = '' if msg is None else ('<Output><ErrorInfo><Message>%s</Message><StackTrace>   at x'
                                       '</StackTrace></ErrorInfo></Output>' % msg)
        body += '<UnitTestResult testName="%s" outcome="%s">%s</UnitTestResult>' % (name, outcome, info)
    failed = sum(1 for r in results if r[1] == 'Failed')
    return TRX % (body, len(results), len(results), len(results) - failed, failed)


XUNIT_ASSERT = ('T.A', 'Failed', 'Assert.Equal() Failure: Values differ&#xD;\nExpected: 1&#xD;\nActual:   2')
USER_MESSAGE = ('T.B', 'Failed', 'a LockedOut refusal committed nothing')
MSTEST_ASSERT = ('T.C', 'Failed', 'Assert.AreEqual failed. Expected:&lt;1&gt;. Actual:&lt;2&gt;.')
XUNIT_NRE = ('T.D', 'Failed', 'System.NullReferenceException : Object reference not set to an instance of an object.')
MSTEST_THREW = ('T.E', 'Failed', 'Test method T.E threw exception: &#xD;\nSystem.InvalidOperationException: x')
NUNIT_SETUP = ('T.F', 'Failed', 'OneTimeSetUp: System.IO.IOException : the file is gone')


class Dotnet(unittest.TestCase):
    def verdict(self, *results, assertion=None):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, '1-T.trx')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(trx(*results))
            return V.dotnet_verdict(1, '', V.trx_counts(p), [], assertion)

    def test_an_assertion_failure_is_red(self):
        for r in (XUNIT_ASSERT, USER_MESSAGE, MSTEST_ASSERT):
            with self.subTest(r=r[0]):
                self.assertEqual(self.verdict(r, ('T.P', 'Passed', None))[0], 'RED')

    def test_an_exception_is_inconclusive_and_named(self):
        for r in (XUNIT_NRE, MSTEST_THREW, NUNIT_SETUP):
            with self.subTest(r=r[0]):
                v, reason = self.verdict(XUNIT_ASSERT, r)
                self.assertEqual(v, INCONCLUSIVE, reason)
                self.assertIn(r[0], reason)

    def test_the_runner_s_assertion_pattern_names_more_assertions(self):
        self.assertEqual(self.verdict(XUNIT_NRE, assertion=r'NullReferenceException')[0], 'RED')

    def failures(self, *results):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, '1-T.trx')
            with open(p, 'w', encoding='utf-8') as f:
                f.write(trx(*results))
            return V.trx_counts(p)[2]

    def test_a_failed_result_with_no_message_is_inconclusive(self):
        failures = self.failures(('T.G', 'Failed', None), ('T.P', 'Passed', None))
        self.assertEqual(failures, [('failure', '', '', 'T.G')])
        # A real assertion always carries a message: one with none fails safe.
        v, reason = V.dotnet_verdict(1, '', (2, 1, failures), [])
        self.assertEqual(v, INCONCLUSIVE, reason)
        self.assertIn('T.G failed on a failure with no message', reason)

    def test_an_outcome_other_than_failed_is_an_error_never_an_assertion(self):
        for outcome in ('Error', 'Timeout', 'Aborted'):
            with self.subTest(outcome=outcome):
                # Even a message an assertion would write does not make it one.
                failures = self.failures(('T.H', outcome, 'Assert.Equal() Failure'))
                self.assertEqual(failures, [('error', '', 'Assert.Equal() Failure', 'T.H')])
                v, reason = V.dotnet_verdict(1, '', (1, 1, failures), [])
                self.assertEqual(v, INCONCLUSIVE, reason)
                self.assertIn('T.H', reason)
                self.assertEqual(self.failures(('T.I', outcome, None)),
                                 [('error', '', outcome, 'T.I')])


# Lines of a game repo's log (<gate log dir>/redfirst/<stamp>-<pid>/1-command-<stem>.log).
ENGINE_CRASH = '\n'.join([
    '[FAIL] PvpPredictionTests.APredictedReplayWalksThroughAnotherPlayer (1069 ms): '
    'NullReferenceException: Object reference not set to an instance of an object.',
    '--- 3 tests in 1.4 s',
    '[FAIL] PvpPredictionTests.APredictedReplayWalksThroughAnotherPlayer: NullReferenceException: '
    'Object reference not set to an instance of an object.',
    '   at Player.FirstHeldIndex() in Z:\\x\\scripts\\player\\Player.cs:line 347',
    'UNIT TESTS FAIL 1 of 3', ''])
ENGINE_ASSERT = '\n'.join([
    '[FAIL] PvpPredictionTests.APredictedReplayWalksThroughAnotherPlayer (12 ms): mask: expected '
    'True, got False', 'UNIT TESTS FAIL 1 of 3', ''])
# What a game repo's runner prints for an assertion: `[FAIL] <name> (<n> ms): <message>`, where a
# crash prints the exception's type first (tools/engine_tests/EngineTestRunner.cs, RunOne).
ENGINE_ASSERTION = r'^\[FAIL\] \S+ \(\d+ ms\): (?!\w+Exception: )'


class CommandExitCode(unittest.TestCase):
    PLAIN = {'command': ['x']}
    NAMED = {'command': ['x'], 'assertion': ENGINE_ASSERTION}

    def test_exit_1_with_no_assertion_pattern_is_inconclusive(self):
        """The game repo's proof: exit 1 from tests that crashed. With no pattern no log tells a
        crash from an assertion, so exit 1 is never RED on its own."""
        for log in (ENGINE_CRASH, ENGINE_ASSERT, ''):
            with self.subTest(log=log[:30]):
                v, reason = V.command_verdict(1, log, self.PLAIN, None)
                self.assertEqual(v, INCONCLUSIVE, reason)
                self.assertIn('assertion', reason)

    def test_exit_1_is_red_only_when_a_log_line_matches_the_assertion_pattern(self):
        self.assertEqual(V.command_verdict(1, ENGINE_ASSERT, self.NAMED, None)[0], 'RED')
        v, reason = V.command_verdict(1, ENGINE_CRASH, self.NAMED, None)
        self.assertEqual(v, INCONCLUSIVE, reason)

    def test_exit_0_and_other_codes_keep_their_verdicts(self):
        self.assertEqual(V.command_verdict(0, '', self.NAMED, None)[0], V.GREEN)
        self.assertEqual(V.command_verdict(2, ENGINE_ASSERT, self.NAMED, None)[0], V.ERROR)


class CommandResultFile(unittest.TestCase):
    RESULT = {'command': ['x'], 'result': '{log_dir}/r.xml'}

    def verdict(self, *cases, runner=None):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'r.xml')
            with open(p, 'w', encoding='utf-8') as f:
                f.write('<testsuites>%s</testsuites>' % junit(*cases))
            return V.command_verdict(1, '', runner or self.RESULT, V.junit_file_counts(p))

    def test_an_assertion_failure_is_red(self):
        self.assertEqual(self.verdict(AFE, PASS)[0], 'RED')

    def test_an_exception_or_an_error_element_is_inconclusive(self):
        for cases in ((NPE,), (ERR,), (AFE, ERR)):
            with self.subTest(cases=cases):
                self.assertEqual(self.verdict(*cases)[0], INCONCLUSIVE)

    def test_the_runner_s_assertion_pattern_names_more_assertions(self):
        runner = dict(self.RESULT, assertion=r'IOException: gone')
        self.assertEqual(self.verdict(ERR, runner=runner)[0], 'RED')


class ExitCode(unittest.TestCase):
    def test_an_inconclusive_verdict_is_undecided_never_ok(self):
        self.assertEqual(V.exit_code(['RED', INCONCLUSIVE], True), V.EXIT_UNDECIDED)
        self.assertEqual(V.exit_code([INCONCLUSIVE], True), V.EXIT_UNDECIDED)


CRASH_TEST = ('import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n'
              '    def test_mul(self):\n        self.assertEqual(calc.add(None, 3), 6)\n')
ASSERT_TEST = ('import unittest\nimport calc\n\n\nclass T(unittest.TestCase):\n'
               '    def test_mul(self):\n        self.assertEqual(calc.add(2, 3), 6)\n')


class EndToEnd(Case):
    """tools/redfirst.py on a real unittest run at the base."""

    def test_a_test_that_crashes_on_the_base_is_inconclusive_not_ok(self):
        self.commit({'tests/test_crash.py': CRASH_TEST})
        rc, out = self.run_tool('--tests', 'tests/test_crash.py')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertRegex(out, r"(?m)^INCONCLUSIVE tests/test_crash.py  .*test_mul.*TypeError: "
                              r"unsupported operand")
        self.assertIn('REDFIRST INCONCLUSIVE', out)
        self.assertNotIn('REDFIRST OK', out)
        self.assertEqual(self.throwaways(), [])

    def test_a_real_assertion_failure_on_the_base_is_still_ok(self):
        self.commit({'tests/test_assert.py': ASSERT_TEST})
        rc, out = self.run_tool('--tests', 'tests/test_assert.py')
        self.assertEqual(rc, V.EXIT_RED, out)
        self.assertRegex(out, r'(?m)^RED    tests/test_assert.py  ')
        self.assertIn('REDFIRST OK', out)


if __name__ == '__main__':
    unittest.main()
