"""tools/gate.py: arguments and exit codes only (the behaviour is kitlib.gate's, tested there).

None of these runs reaches a booking, so the machine's lock store is never touched: usage errors
stop in argparse or before the gate selects, --plan books nothing, and the live-table refusal
comes before the store is read."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from kitlib.config import policy
from kitlib.gate import budget, steps
from tests import gitfixture
from tests.gate import fixtures

GATE_PY = os.path.join(steps.KIT_TOOLS, 'gate.py')
SHA = 'a' * 40


def gate_py(*args, cwd=None):
    r = subprocess.run([sys.executable, GATE_PY] + list(args), cwd=cwd, capture_output=True,
                       text=True, encoding='utf-8', errors='replace', env=fixtures.test_env(),
                       timeout=120)
    return r.returncode, r.stdout, r.stderr


class UsageCase(unittest.TestCase):
    def assert_usage(self, *args, cwd=None):
        rc, out, err = gate_py(*args, cwd=cwd)
        self.assertEqual(rc, 2, (args, out, err))
        self.assertIn('USAGE:', out + err)
        self.assertNotIn('refused', (out + err).lower())     # refusal-log P10


class Entry(UsageCase):
    def test_bad_arguments_are_usage(self):
        self.assert_usage('--nosuch')
        self.assert_usage('--full', '--only', 'x')
        self.assert_usage('--out', 'x.json')
        self.assert_usage('--measure')
        self.assert_usage('--measure', '--full', '--out', 'x.json')
        self.assert_usage('--measure', '--out', 'x.json', '--days', '0')

    def test_measure_refuses_the_flags_it_does_not_use(self):
        """-C, --slug and --log-dir would name a checkout, a slug and a log folder --measure never
        reads: USAGE, nothing written, not silently ignored."""
        out = os.path.join(tempfile.mkdtemp(prefix='measure-'), 'p.json')
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        for extra in (['-C', 'x'], ['--slug', 's'], ['--log-dir', 'logs']):
            rc, said, err = gate_py('--measure', '--out', out, *extra)
            self.assertEqual(rc, 2, (extra, said, err))
            self.assertIn('USAGE: --measure takes only --out and --days', err)
            self.assertFalse(os.path.exists(out), extra)

    def test_land_needs_both_hashes(self):
        self.assert_usage('land')
        self.assert_usage('land', 'kit/x', '--expect-main', SHA)
        self.assert_usage('land', 'kit/x', '--expect-main', 'abc', '--expect-tip', SHA)

    def test_measure_never_writes_the_live_table(self):
        with open(policy.KINDS_FILE, 'rb') as f:
            before = f.read()
        rc, out, err = gate_py('--measure', '--out', policy.KINDS_FILE)
        self.assertEqual(rc, 4, (out, err))
        self.assertIn('REFUSED: ', err)         # a refusal is said on stderr
        self.assertNotIn('REFUSED', out)
        with open(policy.KINDS_FILE, 'rb') as f:
            self.assertEqual(f.read(), before)


class EntryInRepo(UsageCase):
    def setUp(self):
        self.repo = gitfixture.TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.repo.write('.kit.json', '{"gate": {"steps": [{"name": "unit", "always": true, '
                                     '"cmd": ["{python}", "-c", "print(1)"]}]}}\n')
        self.repo.commit('kit', '.kit.json')

    def test_unknown_only_step_is_usage(self):
        self.assert_usage('--only', 'nosuch', '--base', 'HEAD', cwd=self.repo.root)

    def test_plan_exits_zero_and_lists_the_steps(self):
        rc, out, err = gate_py('--plan', '--base', 'HEAD', '-C', self.repo.root)
        self.assertEqual(rc, 0, (out, err))
        self.assertIn('RUN   unit (always)', out)

    def test_allow_says_its_refusal_on_stderr(self):
        rc, out, err = gate_py('allow', 'kit/no-such', '-C', self.repo.root,
                               '--runs', str(budget.RUNS + 1), '--why', 'w')
        self.assertEqual(rc, 4, (out, err))
        self.assertIn('REFUSED: ', err)
        self.assertNotIn('REFUSED', out)

    def test_land_outside_a_main_checkout_is_refused(self):
        wt = self.repo.worktree('x')
        rc, out, _ = gate_py('land', 'kit/x', '--expect-main', SHA, '--expect-tip', SHA, '-C', wt)
        self.assertEqual(rc, 4, out)
        self.assertIn('REFUSED: ', out)


if __name__ == '__main__':
    unittest.main()
