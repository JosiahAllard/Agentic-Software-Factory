"""A `running` land record whose process is gone (docs/coord-state.md "The state note"): `show`
and `reported` finish it `error` as `gate.py wait-result` does, so the landing ends and can be
marked; a live one stays in flight."""
import os
import subprocess
import sys
import unittest

from kitlib.coordstate import log
from kitlib.gate import result
from tests.coordstate import test_cli
from tests.coordstate.helpers import SLUG

TIP = test_cli.TIP


def dead_pid():
    p = subprocess.Popen([sys.executable, '-c', 'pass'])
    p.wait()
    return p.pid


class DeadLand(test_cli.Cli):
    def running(self, pid, ctime=None):
        log.note('land_submit', self.env, branches=['kit/b'], tips=[TIP], output=None)
        result.write(self.repo.root, result.build('kit/b', TIP, None, 'land', None, verdict=result.RUNNING,
                                                  pid=pid, ctime=ctime))

    def test_show_finishes_a_dead_running_land_record(self):
        self.ok('start', '--slug', SLUG)
        self.running(dead_pid())
        out, _ = self.ok('show')
        self.assertIn('Landings to report to the PM (1)', out)
        self.assertIn('Landings in flight (0)', out)
        self.assertEqual(result.read(self.repo.root, 'kit/b', 'land')[0]['verdict'], result.ERROR)

    def test_reported_marks_a_landing_whose_process_died(self):
        self.ok('start', '--slug', SLUG)
        self.running(dead_pid())
        out, _ = self.ok('reported', 'kit/b')
        self.assertEqual(out.strip(), 'MARKED kit/b')

    def test_a_live_running_land_stays_in_flight(self):
        self.ok('start', '--slug', SLUG)
        self.running(os.getpid(), result.own_process()[1])
        out, _ = self.ok('show')
        self.assertIn('Landings in flight (1)', out)
        rc, _o, err = self.run_tool('reported', 'kit/b')
        self.assertEqual(rc, 1)
        self.assertIn('NOT MARKED', err)


if __name__ == '__main__':
    unittest.main()
