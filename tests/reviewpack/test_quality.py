"""review_pack's quality signals (kitlib/reviewpack/quality.py): sizes and net, a new file over the
size line, growth past 500 lines, a deleted test with no "covered by". Printed only."""
from tests.reviewpack.repo import PackCase


def body(n):
    return ''.join('line %d\n' % i for i in range(n))


class QualitySignals(PackCase):
    def feature(self):
        self.git('checkout', '-q', '-b', 'feature')

    def pack(self):
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        return out

    def test_split_shows_before_after_and_net(self):
        # a 2,164-line file becoming 9 files of 4,019 lines in all
        self.write('src/Big.java', body(2164))
        self.commit_all('big')
        self.feature()
        self.write('src/Big.java', body(400))
        for i in range(8):
            self.write('src/Part%d.java' % i, body(450))
        self.write('src/tests/Part.java', body(900))     # a test file counts nowhere
        self.commit_all('split')
        out = self.pack()
        self.assertIn('size     9 non-test file(s) | lines 2164 -> 4000 | net +1836', out)
        self.assertIn('2164 -> 400 src/Big.java', out)
        self.assertNotIn('Part.java 900', out)
        self.assertNotIn('SIGNAL', out)

    def test_growth_past_500_is_flagged_and_smaller_growth_is_not(self):
        self.write('a.py', body(204))
        self.write('b.py', body(300))
        self.commit_all('base files')
        self.feature()
        self.write('a.py', body(614))      # +201%, past 500
        self.write('b.py', body(450))      # +50%, under 500
        self.commit_all('grow')
        out = self.pack()
        self.assertIn('SIGNAL grew over 50% and past 500 lines: a.py 204 -> 614', out)
        self.assertNotIn('b.py 300', out.split('SIGNAL', 1)[1])

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
