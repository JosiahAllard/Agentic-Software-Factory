"""Tests for kitlib/reviewpack/scorecard.py: the pure parts, and one made-up landing in a throwaway
repository."""
import json
import os
import subprocess
import tempfile
import unittest

from kitlib import reviewpack
from kitlib.reviewpack import quality, scorecard as Q
from tests.realgit import real_git
from tests.reviewpack.repo import PackCase

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

    def test_parse_counts_each_code_signal_kind(self):
        for line, key, want in (
                ('  SIGNAL import cycle among changed files (2): pkg/a.py, pkg/b.py',
                 'import_cycles', 'pkg/a.py, pkg/b.py'),
                ('  SIGNAL namespace cycle among changed files (2): src/Keys.cs, src/Session.cs',
                 'namespace_cycles', 'src/Keys.cs, src/Session.cs'),
                ('  SIGNAL new static non-final field: src/S.java:4 static int retries',
                 'static_fields', 'src/S.java:4'),
                ('  SIGNAL new global rebind: w2.py:2 n', 'global_rebinds', 'w2.py:2 n'),
                ('  SIGNAL new public symbol with one caller: src/W.java:3 pick (caller src/H.java:3)',
                 'one_caller', 'src/W.java:3 pick'),
                ('  SIGNAL new public symbol called only from tests: src/W.java:4 peek (1 test callers)',
                 'test_only_callers', 'src/W.java:4 peek')):
            out = Q.parse([line])
            self.assertEqual((out[key], out['not_checked']), ([want], []), line)

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
        # records saved before a signal was counted show no value for it, never a 0%
        self.assertRegex(text, r'new global rebind\n.*\n  r window\s+-\s+-\s+-\n')

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


@real_git('git grep: the callers signal')
class RealSection(PackCase):
    def test_every_line_section_prints_for_code_signals_is_recognised(self):
        self.write('pkg/__init__.py', '')
        self.write('pkg/a.py', 'import os\n')
        self.write('pkg/b.py', 'import sys\n')
        self.commit_all('base')
        self.git('checkout', '-q', '-b', 'feature')
        self.write('pkg/a.py', 'from pkg import b\n')
        self.write('pkg/b.py', 'from pkg import a\nn = 0\ndef reset():\n    global n\n    n = 0\n')
        self.write('tests/test_b.py', 'from pkg.b import reset\ndef test_r():\n    reset()\n')
        self.write('src/Keys.cs', 'namespace R.Keys;\nusing R.Session;\nclass Keys {}\n')
        self.write('src/Session.cs', 'namespace R.Session {\n  using static R.Keys.Keys;\n'
                                     '  class Session {}\n}\n')
        self.write('src/S.java', 'class S {\n    static int retries;\n'
                                 '    public int pick() { return 1; }\n}\n')
        self.write('src/H.java', 'final class H {\n    int go() { return new S().pick(); }\n}\n')
        self.commit_all('signals')
        mb = self.git('merge-base', 'main', 'feature')
        out = Q.parse(quality.section(lambda *a: reviewpack.git(self.root, *a), self.root, mb,
                                      'feature'))
        self.assertEqual(out['not_checked'], [])
        for _, key, _ in Q.SIGNALS[3:]:
            self.assertTrue(out[key], key)


if __name__ == '__main__':
    unittest.main()
