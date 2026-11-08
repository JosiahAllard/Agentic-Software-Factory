"""Tests for quality_scorecard.py: the pure parts, and one made-up landing in a throwaway repository.

    python -m unittest test_quality_scorecard -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
for p in (ROOT, os.path.join(ROOT, 'optional', 'usage')):
    if p not in sys.path:
        sys.path.insert(0, p)
import quality_scorecard as Q  # noqa: E402
from tests.realgit import real_git  # noqa: E402

LINES = ['size     2 non-test file(s) | lines 10 -> 1600 | net +1590',
         '    new 1600   a/big.py',
         '  SIGNAL new file over 1500 non-test lines: a/big.py (1600)',
         '  SIGNAL grew over 50% and past 500 lines: b/c.py 400 -> 700',
         '  SIGNAL deleted test, no "covered by" in its commit message: t/test_x.py::test_y']


class Pure(unittest.TestCase):
    def test_parse_reads_size_and_every_signal(self):
        rec = Q.parse(LINES + ['quality  NOT CHECKED (deleted tests): boom'])
        self.assertEqual(rec['size'], {'files': 2, 'before': 10, 'after': 1600, 'net': 1590})
        self.assertEqual((rec['new'], rec['grew'], rec['deleted_tests']),
                         (['a/big.py'], ['b/c.py'], ['t/test_x.py::test_y']))
        self.assertEqual(len(rec['not_checked']), 1)

    def test_sync_merges_are_not_landings(self):
        for s in ('Merge 4454a41df7 into api/w4-move', "Merge branch 'a' into b", 'Merge 4454a41df7 (batch tip)'):
            self.assertTrue(Q.SYNC.search(s), s)
        for s in ('Merge api/w4-interact 860953041a (x)', 'Merge kit/reviewpack-quality'):
            self.assertFalse(Q.SYNC.search(s), s)
        self.assertEqual(Q.branch_name('Merge api/w4-interact 860953041a (x)'), 'api/w4-interact')

    def test_compare_table_has_a_delta_per_cohort(self):
        def rec(net, new):
            return {'repo': 'r', 'cohort': 'c', 'size': {'net': net}, 'new': ['f'] * new, 'grew': [],
                    'deleted_tests': [], 'not_checked': []}
        text = Q.table({'a': [rec(10, 1), rec(30, 0)], 'b': [rec(5, 0)]})
        self.assertIn('1 of 2', text)
        self.assertIn('0 of 1', text)
        self.assertRegex(text, r'r c\s+20\s+5\s+-15')


def run(repo, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    subprocess.run(['git', '-C', repo, '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                    '-c', 'commit.gpgsign=false'] + list(args), check=True, capture_output=True, env=env)


@real_git('the script starts git as its own process, as a tool run does')
class Landing(unittest.TestCase):
    def test_a_merged_branch_is_measured_and_a_missing_one_is_not_checked(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, 'init', '-q', '-b', 'main')
            open(os.path.join(d, 'a.py'), 'w').write('x = 1\n')
            run(d, 'add', '.')
            run(d, 'commit', '-qm', 'base')
            run(d, 'checkout', '-qb', 'feat')
            open(os.path.join(d, 'a.py'), 'w').write('x = 1\ny = 2\nz = 3\n')
            run(d, 'commit', '-qam', 'grow')
            run(d, 'checkout', '-q', 'main')
            open(os.path.join(d, 'b.py'), 'w').write('b = 1\n')
            run(d, 'add', '.')
            run(d, 'commit', '-qm', 'other')
            run(d, 'merge', '-q', '--no-ff', '-m', 'Merge feat', 'feat')
            got = Q.landings(d, 'main', '2000-01-01')
            self.assertEqual([s for _, _, s in got], ['Merge feat'])
            rec = Q.measure(d, 'main', 'feat', 'w', got[0][0])
            self.assertEqual((rec['landed'], rec['size']['net']), (True, 2))
            gone = Q.measure(d, 'main', 'nope', 'spot')
            self.assertFalse(gone['landed'])
            self.assertTrue(gone['not_checked'])


if __name__ == '__main__':
    unittest.main()
