"""A red-first test run that hangs is killed, its whole process tree with it, and reported TIMEOUT
(kitlib.redfirst.run, RUN_TIMEOUT, run_timeout and default_run; a run hung
25 h on 2026-10-31). The limit is set short here, through RUN_TIMEOUT or the repo's own `.kit.json`
"tool_tests".unit_timeout at the base; the fake test sleeps well past it and starts a child of
its own that sleeps longer still, so a run that is not stopped ends GREEN much later, and a kill of
the command alone leaves the child alive.
"""
import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from kitlib.config import schema
from kitlib.content.oncommit import RENDER_TOOL
from kitlib.proc import process
from kitlib.redfirst import run as R
from kitlib.redfirst import verdict as V
from tests.redfirst.test_run import CONFIG, Case, tool

LIMIT = 6           # seconds: room for python to start and write its pids under load
SLEEP = 30          # the fake test's own sleep; its child's is longer
HANG = ('import os, subprocess, sys, time, unittest\n'
        'PIDS = %r\n\n\n'
        'class T(unittest.TestCase):\n'
        '    def test_hangs(self):\n'
        '        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(%d)"],\n'
        '                                 cwd=os.path.dirname(PIDS))\n'
        '        with open(PIDS, "w") as f:\n'
        '            f.write("%%d %%d" %% (os.getpid(), child.pid))\n'
        '        time.sleep(%d)\n')


class HangingTest(Case):
    """In the kit the limit is RUN_TIMEOUT: the run here takes the kit's place (is_kit)."""
    FROM = "the kit: kit_tests' UNIT_TIMEOUT"

    def hang(self, limit):
        """Commit the fake test and run redfirst on it; `(rc, its lines, seconds it took, pids)`.
        `limit`: RUN_TIMEOUT for a run in the kit, or None for a repo that is not the kit."""
        pids = os.path.join(self.fx.base, 'pids.txt')
        self.commit({'tests/test_hang.py': HANG % (pids, SLEEP + 15, SLEEP)})
        out = io.StringIO()
        began = time.monotonic()
        kit = mock.patch.object(R, 'is_kit', create=True, return_value=bool(limit))
        patch = (mock.patch.object(R, 'RUN_TIMEOUT', limit, create=True) if limit
                 else mock.patch.dict({}))
        with kit, patch, redirect_stdout(out):
            rc = tool.main(['-C', self.wt, '--log-dir', self.logs, '--tests', 'tests/test_hang.py'],
                           cleanup=self.here)
        return rc, out.getvalue(), time.monotonic() - began, pids

    def test_a_hanging_run_is_killed_with_its_tree_and_reported_timeout(self):
        rc, text, took, pids = self.hang(LIMIT)
        self.check(rc, text, took, pids)

    def check(self, rc, text, took, pids):
        self.assertEqual(rc, V.EXIT_UNDECIDED, text)
        self.assertIn('timeout   %d s a test run (%s' % (LIMIT, self.FROM), text)
        self.assertRegex(text, r'(?m)^TIMEOUT tests/test_hang\.py  still running after %d s; '
                         r'killed it and every process it started' % LIMIT)
        self.assertIn(R.TIMED_OUT, text)
        self.assertNotRegex(text, r'(?m)^(RED |GREEN |REDFIRST OK)')
        self.assertLess(took, SLEEP, text)
        with open(os.path.join(self.logs, 'summary.txt'), encoding='utf-8') as fh:
            summary = fh.read()
        self.assertIn('TIMEOUT tests/test_hang.py', summary)
        self.assertIn(R.TIMED_OUT, summary)
        with open(pids, encoding='utf-8') as fh:
            leaf, child = (int(p) for p in fh.read().split())
        self.assertFalse(process.alive(leaf, None), 'the hung test process still runs')
        self.assertFalse(process.alive(child, None), 'the process the hung test started still runs')
        self.assertEqual(self.throwaways(), [], text)



class ConfiguredLimit(HangingTest):
    """A repo whose `.kit.json` at the base sets "tool_tests".unit_timeout (pytests' per-unit
    limit, schema default 3600) has its runs timed by that."""
    KIT_JSON = dict(CONFIG, tool_tests={'unit_timeout': LIMIT})
    FROM = '.kit.json "tool_tests".unit_timeout at the base'

    def test_a_hanging_run_is_killed_with_its_tree_and_reported_timeout(self):
        rc, text, took, pids = self.hang(None)
        self.check(rc, text, took, pids)


class Limit(Case):
    def test_a_repo_that_sets_none_gets_the_schema_default(self):
        want = schema.defaults('tool_tests')['unit_timeout']
        self.assertEqual(R.run_timeout(self.wt, self.base)[0], want)
        self.assertNotEqual(want, R.RUN_TIMEOUT)

    def test_a_kit_checkout_gets_run_timeout(self):
        os.makedirs(os.path.join(self.wt, 'content', 'roles'))
        tool_path = os.path.join(self.wt, *RENDER_TOOL.split('/'))
        os.makedirs(os.path.dirname(tool_path), exist_ok=True)
        with open(tool_path, 'w', encoding='utf-8') as fh:
            fh.write('')
        self.assertEqual(R.run_timeout(self.wt, self.base),
                         (R.RUN_TIMEOUT, "the kit: kit_tests' UNIT_TIMEOUT"))

    def test_a_value_the_schema_refuses_is_a_usage_refusal(self):
        for bad in (0, 'ten', True):
            with self.subTest(bad=bad):
                head = self.commit({'.kit.json': json.dumps(dict(CONFIG, tool_tests={
                    'unit_timeout': bad})) + '\n'})
                with self.assertRaises(V.Usage) as e:
                    R.run_timeout(self.wt, head)
                self.assertIn('unit_timeout', str(e.exception))

    def test_the_kit_ignores_a_configured_value(self):
        head = self.commit({'.kit.json': json.dumps(dict(CONFIG, tool_tests={
            'unit_timeout': 7})) + '\n'})
        with mock.patch.object(R, 'is_kit', return_value=True, create=True):
            self.assertEqual(R.run_timeout(self.wt, head)[0], R.RUN_TIMEOUT)


if __name__ == '__main__':
    unittest.main()
