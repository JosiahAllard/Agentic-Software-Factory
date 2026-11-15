"""kitlib.proc.job end_and_wait: terminating a job only starts each process's end, so it holds each
first and returns once all have ended, GONE_WAIT seconds at most. The process that ends late is
tests/testutil.py slow_to_end's (a real one with much to tear down cannot be made on demand);
the job's own command is a real member, killed by the terminate."""
import subprocess
import sys
import time
import unittest
from unittest import mock

from kitlib.proc import job as jobs
from kitlib.proc import process
from tests import testutil


class EndAndWait(unittest.TestCase):
    def job(self):
        j = jobs.Job(kill_on_close=True)
        self.addCleanup(j.close)
        p = j.start([sys.executable, '-c', 'import time; time.sleep(60)'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
        self.addCleanup(p.wait)
        return j, p

    def test_it_returns_only_once_every_process_the_job_held_has_ended(self):
        j, p = self.job()
        slow = testutil.slow_to_end(self, j, 1.0)
        with mock.patch.object(jobs, 'GONE_WAIT', 60.0):    # room for a late timer under load
            jobs.end_and_wait(j)
        self.assertFalse(process.alive(slow.pid, None))     # Popen's handle keeps its pid
        self.assertFalse(process.alive(p.pid, None))

    def test_a_process_that_does_not_end_holds_it_up_gone_wait_at_most(self):
        # Moved from tests/tooldeps/test_pool.py, which patched pool.GONE_WAIT.
        j, _p = self.job()
        slow = testutil.slow_to_end(self, j, 60)
        with mock.patch.object(jobs, 'GONE_WAIT', 1.0):
            t = time.monotonic()
            jobs.end_and_wait(j)
            took = time.monotonic() - t
        self.assertLess(took, 4.0)          # the patched bound, not the module's 5 s
        self.assertTrue(process.alive(slow.pid, None))


if __name__ == '__main__':
    unittest.main()
