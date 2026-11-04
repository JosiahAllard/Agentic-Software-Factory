"""The command runner kind (kitlib.redfirst.verdict command_argv, command_verdict; run.lock_kind):
a test the repo's own command runs, an engine test say, with {selector} and the other
placeholders filled in. The selector reaches the program as one argument, a batch file's words
are refused as gradlew.bat's are, a run that names no test or writes no result is never RED, a
test that crashes is INCONCLUSIVE (exit 1 is RED only on a log line the runner's `assertion`
matches, or a result file's assertion failure), and the run books the machine lock as build
unless the runner names another kind.

The end-to-end cases run a fake engine (ENGINE, a python script outside the fixture repository)
that reads the test file and the base's src/calc.py in the throwaway tree, and book on the
scratch store of tests/redfirst/test_lock.py's LockCase.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

from kitlib.config import heavy_programs
from kitlib.redfirst import run as R
from kitlib.redfirst import verdict as V

from .test_lock import LockCase

ENGINE_RUNNER = {'glob': 'tools/engine_tests/*.cs', 'kind': 'command',
                 'command': ['godot.exe', '--headless', '--run-test', '{selector}', '--file', '{path}',
                             '--root', '{tree}', '--out', '{log_dir}/{n}-{stem}.xml']}

# The fake engine: argv is <args file> <tree> <test path> <selector> [<result file>]. It writes its
# argv to the args file, then runs the test named <selector> in the test file: a line
# `test <name> needs <function>` passes when the tree's src/calc.py defines that function, and
# fails an assertion when it does not; a line `test <name> crashes` dies on an exception. A
# result file it writes is dated a minute ahead, so only a delete keeps a later run from reading it.
ENGINE = r'''import json, os, re, sys, time
args_file, tree, path, selector = sys.argv[1:5]
result = sys.argv[5] if len(sys.argv) > 5 else None
with open(args_file, 'w', encoding='utf-8') as f:
    json.dump(sys.argv[2:], f)
with open(os.path.join(tree, *path.split('/')), encoding='utf-8') as f:
    tests = dict(re.findall(r'^test (\S+) (?:needs )?(\w+)$', f.read(), re.M))
if selector not in tests:
    print('engine: no test named %s' % selector)
    sys.exit(1)
crash = tests[selector] == 'crashes'
with open(os.path.join(tree, 'src', 'calc.py'), encoding='utf-8') as f:
    ok = not crash and re.search(r'^def %s\(' % tests[selector], f.read(), re.M) is not None
print('engine: %s %s' % (selector, 'passed' if ok else 'CRASHED: NullReferenceException: boom'
                         if crash else 'FAILED: expected 6, got None'))
if result and result != '-':
    case = ('' if ok else '<error type="NullReferenceException" message="boom"/>' if crash
            else '<failure type="AssertionError" message="expected 6, got None"/>')
    with open(result, 'w', encoding='utf-8') as f:
        f.write('<testsuites><testsuite name="%s" tests="1" failures="%d" errors="%d">'
                '<testcase name="%s">%s</testcase></testsuite></testsuites>'
                % (selector, 0 if ok or crash else 1, 1 if crash else 0, selector, case))
    os.utime(result, (time.time() + 60, time.time() + 60))   # fresh to any later run's clock
sys.exit(0 if ok else 1)
'''
TEST_FILE = 'test MulWorks needs mul\ntest AddWorks needs add\ntest MulCrashes crashes\n'


def sel(text, runner=ENGINE_RUNNER):
    return V.parse_selector(text, [runner])


class Argv(unittest.TestCase):
    def test_the_placeholders_are_filled_and_the_selector_is_one_word(self):
        s = sel('tools/engine_tests/PhaseTest.cs::Phase path & no strings')
        argv, cwd, env = V.command(s, 'TW', 'LOGS', 2)
        self.assertEqual(argv, ['godot.exe', '--headless', '--run-test', 'Phase path & no strings',
                                '--file', 'tools/engine_tests/PhaseTest.cs', '--root', 'TW',
                                '--out', 'LOGS/2-PhaseTest.xml'])
        self.assertEqual((cwd, env), ('TW', {}))

    def test_no_filter_selects_the_file_name(self):
        argv = V.command(sel('tools/engine_tests/PhaseTest.cs'), 'TW', 'LOGS', 1)[0]
        self.assertEqual(argv[3], 'PhaseTest')

    def test_a_program_with_a_slash_is_under_the_tree_and_args_are_appended(self):
        runner = dict(ENGINE_RUNNER, command=['tools/run_engine.exe', '{selector}'], args=['-v'])
        argv = V.command(sel('tools/engine_tests/A.cs', runner), 'TW', 'LOGS', 1)[0]
        self.assertEqual(argv, [os.path.join('TW', 'tools', 'run_engine.exe'), 'A', '-v'])

    def test_head_is_the_head_checkout_beside_tree(self):
        runner = dict(ENGINE_RUNNER, command=['{head}/tools/x', '-Tree', '{tree}'],
                      result='{head}/r-{n}.xml')
        s = sel('tools/engine_tests/A.cs', runner)
        argv = V.command(s, 'TW', 'LOGS', 1, head='/h/wt')[0]
        self.assertEqual(argv, ['/h/wt/tools/x', '-Tree', 'TW'])    # absolute: not put under TW
        self.assertEqual(V.result_path(s, 'TW', 'LOGS', 1, '/h/wt'),
                         os.path.normpath('/h/wt/r-1.xml'))

    def test_a_runner_with_no_command_is_a_usage_error(self):
        runner = {'glob': 'tools/engine_tests/*.cs', 'kind': 'command'}
        with self.assertRaisesRegex(V.Usage, 'has no command'):
            sel('tools/engine_tests/A.cs', runner)


class BatchFile(unittest.TestCase):
    """A .bat or .cmd program runs in cmd.exe, which reads its command line again."""

    def test_a_cmd_character_after_a_batch_program_refuses_before_anything_runs(self):
        for prog in ('tools/run_engine.bat', 'RUN.CMD'):
            runner = dict(ENGINE_RUNNER, command=[prog, '--test', '{selector}'])
            with self.subTest(prog=prog):
                with self.assertRaises(V.Usage) as cm:
                    sel('tools/engine_tests/A.cs::a&whoami', runner)
                self.assertIn("the word 'a&whoami'", str(cm.exception))
                self.assertIn('cmd.exe reads again', str(cm.exception))
                self.assertIn('nothing was run', str(cm.exception))

    def test_a_trailing_dot_or_space_and_cmd_itself_count_as_cmd_exe(self):
        for command in (['tools/run_engine.bat.', '{selector}'], ['RUN.CMD ', '{selector}'],
                        ['cmd', '/c', r'tools\run_test.bat', '{selector}'],
                        [r'C:\Windows\System32\cmd.exe', '/c', 'run_test.bat', '{selector}'],
                        ['CMD.EXE.', '/c', 'run_test.bat', '{selector}']):
            runner = dict(ENGINE_RUNNER, command=command)
            with self.subTest(command=command):
                with self.assertRaisesRegex(V.Usage, "the word 'a&whoami' .*cmd.exe reads again"):
                    sel('tools/engine_tests/A.cs::a&whoami', runner)

    def test_runs_in_cmd(self):
        for prog in ('x.bat', 'x.BAT. .', 'a/b/x.cmd', 'cmd', r'C:\W\cmd.exe','cmd.exe '):
            self.assertTrue(V.runs_in_cmd(prog), prog)
        for prog in ('godot.exe', 'cmdtool.exe', 'x.bat.exe', 'tools/cmd/godot', 'batch'):
            self.assertFalse(V.runs_in_cmd(prog), prog)

    def test_a_batch_program_with_plain_words_and_an_exe_with_any_word_are_taken(self):
        bat = dict(ENGINE_RUNNER, command=['tools/run_engine.bat', '{selector}'])
        self.assertEqual(V.command(sel('tools/engine_tests/A.cs::Phase', bat), 'TW', 'L', 1)[0][1],
                         'Phase')
        self.assertEqual(V.command(sel('tools/engine_tests/A.cs::a&b'), 'TW', 'L', 1)[0][3], 'a&b')


class Verdicts(unittest.TestCase):
    PLAIN = {'command': ['x']}
    ASSERTION = {'command': ['x'], 'assertion': r'^engine: \S+ FAILED'}
    NO_TEST = {'command': ['x'], 'no_test': r'^engine: no test named'}
    RESULT = {'command': ['x'], 'result': '{log_dir}/r.xml'}

    def test_with_no_result_file_only_exit_1_with_an_assertion_line_is_red(self):
        failed = 'engine: MulWorks FAILED: expected 6\n'
        for rc, runner, want in ((0, self.PLAIN, V.GREEN), (1, self.PLAIN, 'INCONCLUSIVE'),
                                 (1, self.ASSERTION, V.RED), (2, self.ASSERTION, V.ERROR),
                                 (127, self.PLAIN, V.ERROR), (-1, self.PLAIN, V.ERROR)):
            with self.subTest(rc=rc, runner=runner):
                self.assertEqual(V.command_verdict(rc, failed, runner, None)[0], want)

    def test_a_log_that_says_no_test_matched_is_none(self):
        v, reason = V.command_verdict(1, 'engine: no test named X\n', self.NO_TEST, None)
        self.assertEqual(v, V.NONE)
        self.assertIn('no_test', reason)

    def test_a_named_result_file_gives_the_verdict_and_its_absence_is_never_red(self):
        failed = [('failure', 'AssertionError', 'x', 'T.a')]
        for rc, counts, want in ((1, None, V.ERROR), (0, None, V.ERROR), (1, (0, 0, []), V.NONE),
                                 (1, (3, 1, failed), V.RED), (0, (3, 0, []), V.GREEN),
                                 (1, (3, 0, []), V.ERROR)):
            with self.subTest(rc=rc, counts=counts):
                self.assertEqual(V.command_verdict(rc, '', self.RESULT, counts)[0], want)

    def test_junit_file_counts(self):
        with tempfile.TemporaryDirectory() as d:
            wrapped, flat = os.path.join(d, 'a.xml'), os.path.join(d, 'b.xml')
            with open(wrapped, 'w') as f:
                f.write('<testsuites><testsuite tests="2" failures="1" errors="0"/>'
                        '<testsuite tests="3" failures="0" errors="1"/></testsuites>')
            with open(flat, 'w') as f:
                f.write('<testsuite tests="4" failures="0" errors="0"/>')
            self.assertEqual(V.junit_file_counts(wrapped), (5, 2, []))
            self.assertEqual(V.junit_file_counts(flat), (4, 0, []))
            self.assertIsNone(V.junit_file_counts(flat, since=os.path.getmtime(flat) + 60))
            self.assertIsNone(V.junit_file_counts(os.path.join(d, 'none.xml')))


class Kind(unittest.TestCase):
    def test_a_command_runner_books_build_unless_its_lock_names_another_kind(self):
        heavy = heavy_programs()
        kind, where = R.lock_kind(sel('tools/engine_tests/A.cs'), heavy)
        self.assertEqual(kind, 'build')
        self.assertIn('the command kind books build', where)
        named = dict(ENGINE_RUNNER, lock='engine-test')
        self.assertEqual(R.lock_kind(sel('tools/engine_tests/A.cs', named), heavy)[0], 'engine-test')


class EngineRun(LockCase):
    """tools/redfirst.py end to end with a fake engine as the runner's command."""
    RUNNER_EXTRA = {}
    RESULT_WORD = None              # a fifth engine argument: where it writes its result
    ENGINE_AT_HEAD = False          # the engine is a repo file committed with the test, not at base

    def setUp(self):
        scratch = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, scratch, True)
        self.engine = os.path.join(scratch, 'engine.py')
        with open(self.engine, 'w', encoding='utf-8') as f:
            f.write(ENGINE)
        self.args_file = os.path.join(scratch, 'args.json')
        engine = '{head}/tools/engine.py' if self.ENGINE_AT_HEAD else self.engine
        runner = dict({'glob': 'tools/engine_tests/*.cs', 'kind': 'command',
                       'command': [sys.executable, engine, self.args_file, '{tree}', '{path}',
                                   '{selector}'] + ([self.RESULT_WORD] if self.RESULT_WORD else [])},
                      **self.RUNNER_EXTRA)
        self.KIT_JSON = {'repo': {'name': 'fixture'}, 'redfirst': {'runners': [runner]}}
        LockCase.setUp(self)

    def engine_args(self):
        with open(self.args_file, encoding='utf-8') as f:
            return json.load(f)


class EngineExitCode(EngineRun):
    RUNNER_EXTRA = {'no_test': r'^engine: no test named', 'assertion': r'^engine: \S+ FAILED'}

    def test_a_test_that_fails_on_the_base_is_red_and_books_build(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_RED, out)
        self.assertRegex(out, r'(?m)^RED    tools/engine_tests/CalcTest.cs::MulWorks  the command '
                              r'exited 1 and a log line matches the runner.s assertion')
        self.assertIn('engine: MulWorks FAILED', self.log('1-command-CalcTest.log'))
        self.assertEqual(self.booked, [('build', 'fixture', self.slug)])
        self.assertIn('lock      build holds cpu slot', out)
        self.assertEqual((self.records('active'), self.entries(), self.throwaways()), ([], [], []))

    def test_a_test_that_passes_on_the_base_is_not_red(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::AddWorks')
        self.assertEqual(rc, V.EXIT_GREEN, out)
        self.assertIn('REDFIRST NOT RED', out)

    def test_a_test_that_crashes_on_the_base_is_inconclusive_not_ok(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulCrashes')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertRegex(out, r'(?m)^INCONCLUSIVE tools/engine_tests/CalcTest.cs::MulCrashes  the '
                              r'command exited 1, but no log line matches')
        self.assertIn('REDFIRST INCONCLUSIVE', out)
        self.assertNotIn('REDFIRST OK', out)

    def test_a_selector_that_names_no_test_is_none_never_red(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::NoSuchTest')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertRegex(out, r'(?m)^NONE   tools/engine_tests/CalcTest.cs::NoSuchTest ')
        self.assertNotIn('REDFIRST OK', out)

    def test_the_selector_reaches_the_command_as_one_argument(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::Mul "Works" & more')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)       # no such test: NONE
        tree, path, selector = self.engine_args()[:3]
        self.assertEqual((path, selector), ('tools/engine_tests/CalcTest.cs', 'Mul "Works" & more'))
        self.assertTrue(os.path.basename(tree).startswith(R.PREFIX), tree)


class EngineAtHead(EngineRun):
    """The runner script is added with the test, so the base lacks it: {head} runs the head's copy
    against the base tree."""
    RUNNER_EXTRA = {'no_test': r'^engine: no test named', 'assertion': r'^engine: \S+ FAILED'}
    ENGINE_AT_HEAD = True

    def test_a_runner_only_at_head_proves_the_base_red_by_assertion(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE, 'tools/engine.py': ENGINE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_RED, out)
        self.assertRegex(out, r'(?m)^RED    tools/engine_tests/CalcTest.cs::MulWorks  the command '
                              r'exited 1 and a log line matches the runner.s assertion')
        self.assertIn('engine: MulWorks FAILED', self.log('1-command-CalcTest.log'))
        tree = self.engine_args()[0]
        self.assertTrue(os.path.basename(tree).startswith(R.PREFIX), tree)   # the base tree ran it


class EngineExitCodeNoPattern(EngineRun):
    """A command runner that names no `assertion`: exit 1 cannot tell a crash from an assertion."""

    def test_a_failing_test_is_inconclusive_and_the_reason_names_the_key(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertRegex(out, r'(?m)^INCONCLUSIVE tools/engine_tests/CalcTest.cs::MulWorks  .*'
                              r'names no assertion pattern')
        self.assertNotIn('REDFIRST OK', out)


class EngineResultFile(EngineRun):
    RUNNER_EXTRA = {'result': '{log_dir}/{n}-{stem}.xml'}
    RESULT_WORD = '{log_dir}/{n}-{stem}.xml'

    def test_the_result_file_gives_red(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_RED, out)
        self.assertRegex(out, r'(?m)^RED    tools/engine_tests/CalcTest.cs::MulWorks  1 of 1 '
                              r'test\(s\) failed ')
        self.assertTrue(os.path.isfile(os.path.join(self.logs, '1-CalcTest.xml')))

    def test_a_crash_in_the_result_file_is_inconclusive(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulCrashes')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertRegex(out, r'(?m)^INCONCLUSIVE tools/engine_tests/CalcTest.cs::MulCrashes  .*'
                              r'NullReferenceException')


class EngineNoResult(EngineRun):
    RUNNER_EXTRA = {'result': '{log_dir}/{n}-{stem}.xml'}
    RESULT_WORD = '-'                                   # the engine writes no result file

    def test_a_run_that_writes_no_result_is_never_red(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertIn('engine: MulWorks FAILED', self.log('1-command-CalcTest.log'))
        self.assertIn('wrote no readable result file in this run', out)
        self.assertNotIn('REDFIRST OK', out)


class EngineSharedResult(EngineRun):
    """Two selectors whose result path is the same file (no {n} in it)."""
    RUNNER_EXTRA = {'result': '{log_dir}/shared.xml'}
    RESULT_WORD = '{log_dir}/shared.xml'

    def test_an_earlier_selector_s_result_is_deleted_and_never_read(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks',
                                  'tools/engine_tests/CalcTest.cs::NoSuchTest')
        self.assertRegex(out, r'(?m)^RED    tools/engine_tests/CalcTest.cs::MulWorks  1 of 1 ')
        # The second writes no result: the first one's RED file, dated ahead, is gone.
        self.assertRegex(out, r'(?m)^ERROR  tools/engine_tests/CalcTest.cs::NoSuchTest  the command '
                              r'exited 1 and wrote no readable result file')
        self.assertEqual(rc, V.EXIT_UNDECIDED, out)
        self.assertFalse(os.path.exists(os.path.join(self.logs, 'shared.xml')))


class EngineTrackedResult(EngineRun):
    """A result path in the tree that is there before the run."""
    RUNNER_EXTRA = {'result': 'src/calc.py'}

    def test_a_result_the_base_tracks_is_refused_before_anything_is_made(self):
        self.commit({'tools/engine_tests/CalcTest.cs': TEST_FILE})
        rc, out = self.run_locked('--tests', 'tools/engine_tests/CalcTest.cs::MulWorks')
        self.assertEqual(rc, V.EXIT_USAGE, out)
        self.assertIn("the result 'src/calc.py' of the runner for 'tools/engine_tests/*.cs'", out)
        self.assertIn('in the tree at base %s' % self.base[:10], out)
        self.assertEqual((self.booked, self.throwaways()), ([], []))

    def test_a_result_that_is_a_checked_out_file_is_refused(self):
        runner = dict(self.KIT_JSON['redfirst']['runners'][0], result='{path}')
        s = V.parse_selector('tools/engine_tests/CalcTest.cs::MulWorks', [runner])
        with self.assertRaisesRegex(V.Usage, 'is a file the run checks out'):
            R.refuse_tracked_results(self.wt, self.base, [s], [s.path])
        R.refuse_tracked_results(self.wt, self.base,
                                 [V.parse_selector('tools/engine_tests/CalcTest.cs',
                                                   [dict(runner, result='out/{stem}.xml')])], [])


if __name__ == '__main__':
    unittest.main()
