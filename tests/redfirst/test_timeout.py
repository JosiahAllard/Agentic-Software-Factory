"""A red-first test run that hangs is killed, its whole process tree with it, and reported TIMEOUT
(kitlib.redfirst.run, RUN_TIMEOUT and default_run; KIT-REDFIRST-TIMEOUT: a run hung 25 h on
2026-10-31). The limit is set short here; the fake test sleeps well past it and starts a child of
its own that sleeps longer still, so a run that is not stopped ends GREEN much later, and a kill of
the command alone leaves the child alive.
"""
import io
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from kitlib.proc import process
from kitlib.redfirst import run as R
from kitlib.redfirst import verdict as V
from tests.redfirst.test_run import Case, tool

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
    def test_a_hanging_run_is_killed_with_its_tree_and_reported_timeout(self):
        pids = os.path.join(self.fx.base, 'pids.txt')
        self.commit({'tests/test_hang.py': HANG % (pids, SLEEP + 15, SLEEP)})
        out = io.StringIO()
        began = time.monotonic()
        with mock.patch.object(R, 'RUN_TIMEOUT', LIMIT, create=True), redirect_stdout(out):
            rc = tool.main(['-C', self.wt, '--log-dir', self.logs, '--tests', 'tests/test_hang.py'],
                           cleanup=self.here)
        took = time.monotonic() - began
        text = out.getvalue()
        self.assertEqual(rc, V.EXIT_UNDECIDED, text)
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


if __name__ == '__main__':
    unittest.main()
