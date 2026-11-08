"""Tests for kitlib/reviewpack/scorecard.py: the pure parts, and one made-up landing in a throwaway
repository."""
import json
import os
import subprocess
import tempfile
import unittest

from kitlib.reviewpack import scorecard as Q
from tests.realgit import real_git

LINES = ['size     1 non-test file(s) | lines 10 -> 1600 | net +1590',
         '  SIGNAL new file over 1500 non-test lines: a.py (1600)',
         '  SIGNAL grew over 50% and past 500 lines: b.py 400 -> 700',
         '  SIGNAL deleted test, no "covered by" in its commit message: t.py::test_y']


def rec(date, net, new, cohort='window'):
    return {'repo': 'r', 'cohort': cohort, 'date': date, 'size': {'net': net}, 'new': ['f'] * new,
            'grew': [], 'deleted_tests': [], 'not_checked': []}


class Pure(unittest.TestCase):
    def test_parse_reads_size_and_every_signal(self):
        out = Q.parse(LINES + ['quality  NOT CHECKED (deleted tests): boom'])
        self.assertEqual(out['size'], {'files': 1, 'before': 10, 'after': 1600, 'net': 1590})
        self.assertEqual((out['new'], out['grew'], out['deleted_tests']),
                         (['a.py'], ['b.py'], ['t.py::test_y']))
        self.assertEqual(len(out['not_checked']), 1)

    def test_an_unrecognised_line_or_signal_is_not_checked(self):
        for l in ('something new', '  SIGNAL a signal kind from later'):
            self.assertEqual(len(Q.parse([l])['not_checked']), 1, l)

    def test_sync_merges_are_not_landings(self):
        for s in ('Merge 4454a41df7 into api/w4-move', "Merge branch 'a' into b", 'Merge 4454a41df7 (batch tip)',
                  'Merge master 4454a41df7 (x)'):
            self.assertTrue(Q.SYNC.search(s), s)
        for s in ('Merge api/w4-interact 860953041a (SPLIT, imports into runtime.catalog)',
                  'Merge kit/reviewpack-quality'):
            self.assertFalse(Q.SYNC.search(s), s)
        self.assertEqual(Q.branch_name('Merge api/w4-interact 860953041a (x)'), 'api/w4-interact')

    def test_two_windows_compare_in_one_row_with_rates_and_a_delta(self):
        a = [rec('2026-10-01', 10, 1), rec('2026-10-02', 30, 0)]      # 50% with the signal, median 20
        b = [rec('2026-11-01', 5, 0)]                                 # 0%, median 5
        text = Q.table({'a': a, 'b': b})
        self.assertRegex(text, r'r window\s+1 of 2 \(50\.0%\)\s+0 of 1 \(0\.0%\)\s+-50')
        self.assertRegex(text, r'r window\s+20\s+5\s+-15')

    def test_save_adds_to_a_file_and_replaces_the_same_repo_and_cohort(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'base.json')
            Q.save(path, [rec('1', 1, 0, 'spot')])
            Q.save(path, [rec('2', 2, 0)])
            Q.save(path, [rec('3', 3, 0)])
            self.assertEqual(sorted(r['date'] for r in json.load(open(path))), ['1', '3'])


def run(repo, *args):
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
    subprocess.run(['git', '-C', repo, '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                    '-c', 'commit.gpgsign=false'] + list(args), check=True, capture_output=True, env=env)


def write(repo, name, text):
    with open(os.path.join(repo, name), 'w') as f:
        f.write(text)


@real_git('the script starts git as its own process, as a tool run does')
class Landing(unittest.TestCase):
    def test_a_merged_branch_is_measured_and_a_missing_one_is_not_checked(self):
        with tempfile.TemporaryDirectory() as d:
            run(d, 'init', '-q', '-b', 'main')
            os.mkdir(os.path.join(d, 'tests'))
            write(d, 'a.py', 'x = 1\n')
            write(d, 'tests/test_a.py', 'def test_one():\n    pass\n')
            run(d, 'add', '.')
            run(d, 'commit', '-qm', 'base')
            run(d, 'checkout', '-qb', 'feat')
            write(d, 'a.py', 'x = 1\ny = 2\nz = 3\n')
            run(d, 'commit', '-qam', 'grow')
            run(d, 'rm', '-q', 'tests/test_a.py')
            run(d, 'commit', '-qm', 'drop the test')
            run(d, 'checkout', '-q', 'main')
            write(d, 'b.py', 'b = 1\n')
            run(d, 'add', '.')
            run(d, 'commit', '-qm', 'other')
            run(d, 'merge', '-q', '--no-ff', '-m', 'Merge feat (folded into core)', 'feat')
            got = Q.landings(d, 'main', '2000-01-01T00:00:00')
            self.assertEqual(len(got), 1)
            out = Q.measure(d, 'main', 'feat', 'window', got[0][0])
            self.assertEqual((out['landed'], out['repo'], out['size']['net']),
                             (True, os.path.basename(d), 2))
            self.assertEqual(out['deleted_tests'], ['tests/test_a.py'])
            gone = Q.measure(d, 'main', 'nope', 'spot')
            self.assertFalse(gone['landed'])
            self.assertTrue(gone['not_checked'])


if __name__ == '__main__':
    unittest.main()
