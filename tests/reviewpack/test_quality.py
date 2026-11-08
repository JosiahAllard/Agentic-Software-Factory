"""review_pack's quality signals (kitlib/reviewpack/quality.py): sizes and net, a new file over the
size line, growth past 500 lines, a deleted test with no "covered by". Printed only."""
import os

from kitlib import reviewpack
from kitlib.reviewpack import quality
from tests.reviewpack.repo import PackCase


def body(n, blanks=0):
    """n non-blank lines, with `blanks` blank lines among them (never counted)."""
    return ''.join('line %d\n' % i + ('\n' if i < blanks else '') for i in range(n))


class QualitySignals(PackCase):
    def feature(self):
        self.git('checkout', '-q', '-b', 'feature')

    def write_bytes(self, rel, data):
        path = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)

    def pack(self):
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        return out

    def test_split_shows_before_after_and_net(self):
        # a 2,164-line file becoming 9 files of 4,000 lines in all, with a deleted file, a binary
        # file and blank lines (not counted) beside it
        self.write('src/Big.java', body(2164, 100))
        self.write('src/Old.java', body(30))
        self.write_bytes('src/blob.bin', b'\0\1\2')
        self.commit_all('big')
        self.feature()
        self.write('src/Big.java', body(400))
        for i in range(8):
            self.write('src/Part%d.java' % i, body(450, 50))
        self.write('src/tests/Part.java', body(900))     # a test file counts nowhere
        self.git('rm', '-q', 'src/Old.java')
        self.write_bytes('src/blob.bin', b'\0\3\4\5')
        self.commit_all('split')
        out = self.pack()
        self.assertIn('size     10 non-test file(s) | lines 2194 -> 4000 | net +1806', out)
        self.assertIn('2164 -> 400 src/Big.java', out)
        self.assertIn('deleted (was 30) src/Old.java', out)
        self.assertNotIn('blob.bin', out.partition('\nsize ')[2])
        self.assertNotIn('Part.java 900', out)
        self.assertNotIn('SIGNAL', out)

    def test_growth_needs_both_over_50_percent_and_past_500_lines(self):
        self.write('a.py', body(204))
        self.write('small.py', body(100))
        self.write('big.py', body(600))
        self.commit_all('base files')
        self.feature()
        self.write('a.py', body(614))         # +201%, past 500: flagged
        self.write('small.py', body(400))     # +300%, ends under 500
        self.write('big.py', body(700))       # ends past 500, +17%
        self.commit_all('grow')
        out = self.pack()
        self.assertIn('SIGNAL grew over 50% and past 500 lines: a.py 204 -> 614', out)
        self.assertEqual(out.count('SIGNAL'), 1, out)

    def test_new_file_over_the_size_line_is_flagged(self):
        self.feature()
        self.write('big.py', body(1501))
        self.write('ok.py', body(1500))
        self.commit_all('new files')
        out = self.pack()
        self.assertIn('SIGNAL new file over 1500 non-test lines: big.py (1501)', out)
        self.assertNotIn('ok.py (', out)

    def test_deleted_test_is_flagged_only_without_covered_by_in_its_own_commit(self):
        self.write('tests/test_a.py', 'def test_one():\n    pass\n\ndef test_two():\n    pass\n')
        self.write('tests/test_b.py', 'def test_three():\n    pass\n')
        self.write('tests/test_c.py', 'def test_four():\n    pass\n')
        self.commit_all('tests')
        self.feature()
        self.write('tests/test_a.py', 'def test_two():\n    pass\n')     # test_one removed
        self.commit_all('drop one\n\ncovered by test_two')
        self.git('rm', '-q', 'tests/test_b.py')
        self.commit_all('drop file b')
        self.write('tests/test_c.py', 'def test_five():\n    pass\n')     # test_four removed
        self.commit_all('drop four')
        out = self.pack()
        self.assertIn('tests/test_b.py', out)
        self.assertIn('tests/test_c.py::test_four', out)
        self.assertNotIn('test_one', out)

    def test_a_moved_test_is_not_a_deletion(self):
        self.write('tests/test_a.py', 'def test_one():\n    pass\n')
        self.commit_all('test')
        self.feature()
        self.git('rm', '-q', 'tests/test_a.py')
        self.write('tests/test_moved.py', 'def test_one():\n    pass\n')
        self.commit_all('move')
        self.assertNotIn('SIGNAL', self.pack())

    def test_test_methods_by_language(self):
        for path, lines, want in (
                ('t.py', ['def test_a():', '    def helper():', 'async def test_b(x):'],
                 {'test_a', 'test_b'}),
                ('T.java', ['@Test', 'public void foo() {', 'void helper() {'], {'foo'}),
                ('T.cs', ['[Fact]', 'public async Task Bar()', 'void helper('], {'Bar'}),
                ('t.test.js', ['it("does x", () => {', "test('y', f)"], {'does x', 'y'})):
            self.assertEqual(quality.test_names(path, lines), want, path)

    def test_a_failed_log_read_prints_not_checked_and_keeps_the_size_lines(self):
        self.write('tests/test_a.py', 'def test_one():\n    pass\n')
        self.write('a.py', body(3))
        self.commit_all('base')
        self.feature()
        self.write('tests/test_a.py', 'x = 1\n')
        self.write('a.py', body(5))
        self.commit_all('change')

        def read(*args):
            if args[0] == 'log':
                raise RuntimeError('log broke\nmore')
            return reviewpack.git(self.root, *args)
        mb = self.git('merge-base', 'main', 'feature')
        lines = quality.section(read, self.root, mb, 'feature')
        self.assertTrue(lines[0].startswith('size '), lines)
        self.assertEqual(lines[-1], 'quality  NOT CHECKED (deleted tests): log broke')
