"""review_pack's static-state and caller signals (kitlib/reviewpack/codesignals.py,
quality.state_lines and quality.caller_lines)."""
import unittest
from unittest import mock

from kitlib import reviewpack
from kitlib.reviewpack import codesignals, quality
from tests.realgit import real_git
from tests.reviewpack.repo import PackCase, failing_call

# w4-interact's shape: a class split in two, its statics moving with it and one new mutable one.
BIG_BASE = ('final class Interact {\n'
            '    private static Map<String, Integer> CACHE = new HashMap<>();\n'
            '    private static int lastTick = 0;\n'
            '}\n')
PART_TIP = ('final class InteractPart {\n'
            '    private static Map<String, Integer> CACHE = new HashMap<>();\n'   # moved
            '    private static int lastTick = 7;\n'      # moved, initializer changed
            '    static int retries;\n'                   # new
            '    static final int MAX = 3;\n'
            '    public static void run(int x = 1) {\n'
            '    // static int commented = 1;\n'
            '}\n')
CS_TIP = ('class Session {\n'
          '    public static readonly int Limit = 1;\n'
          '    const int Size = 2;\n'
          '    public static int Count { get; set; }\n'
          '    internal static List<int> seen = new List<int>();\n'
          '    public static Func<int> F => () => 1;\n'
          '}\n')


class StaticState(unittest.TestCase):
    def test_new_static_non_final_fields_in_java_and_csharp(self):
        base = {'src/Interact.java': BIG_BASE}
        tip = {'src/Interact.java': 'final class Interact {}\n', 'src/InteractPart.java': PART_TIP,
               'src/Session.cs': CS_TIP}
        self.assertEqual(quality.state_lines(base, tip), [
            '  SIGNAL new static non-final field: src/InteractPart.java:4 static int retries',
            '  SIGNAL new static non-final field: src/Session.cs:5 '
            'internal static List<int> seen'])

    def test_new_global_rebinds_by_function(self):
        base = {'w.py': 'n = 0\ndef tick():\n    global n\n    n += 1\n'}
        tip = {'w.py': 'n = 0\ndef tick():\n    global n\n    n += 1\n',
               'w2.py': 'def reset():\n    global n, m\n    n = 0\n'
                        'def outer():\n    def inner():\n        global k\n'}   # once, for inner
        self.assertEqual(quality.state_lines(base, tip), [
            '  SIGNAL new global rebind: w2.py:2 n', '  SIGNAL new global rebind: w2.py:2 m',
            '  SIGNAL new global rebind: w2.py:6 k'])

    def test_rows_past_40_end_in_a_more_line(self):
        tip = {'S.java': ''.join('static int f%d;\n' % i for i in range(41))}
        lines = quality.state_lines({}, tip)
        self.assertEqual((len(lines), lines[-1]), (41, '  ... 1 more static fields'))


class Callers(unittest.TestCase):
    def test_callers_sorts_one_caller_and_test_only(self):
        java = ('public final class Worlds {\n'
                '    public int hop(int w) {\n'
                '    @Override\n    public String toString() {\n'
                '    public static void main(String[] a) {\n'
                '    void quiet() {\n'
                '    public int hop(String s) {\n')     # an overload: still one row for hop
        syms = codesignals.public_symbols('src/Worlds.java', java)
        syms += codesignals.public_symbols('w.py', 'def load():\n    pass\n'
                                                   'class Box:\n    def open(self): pass\n'
                                                   '    def _hide(self): pass\n'
                                                   'def _private(): pass\n')
        self.assertEqual([f.key for f in syms], ['Worlds', 'hop', 'hop', 'load', 'Box', 'open'])
        hits = [('src/Worlds.java', 1, 'public final class Worlds {'),        # its declaration
                ('src/Worlds.java', 2, '    public int hop(int w) {'),
                ('src/Map.java', 9, 'new Worlds().hop(1); hopper();'),         # one caller each
                ('src/test/java/WorldsTest.java', 3, 'w.hop(2);'),
                ('tests/test_w.py', 4, 'load(); load()'),                       # tests only
                ('a.py', 1, 'Box().open(); Box()'), ('b.py', 2, 'b = Box')]   # two lines
        got = [(f.key, kind, what) for f, kind, what in codesignals.callers(syms, hits,
                                                                              quality.is_test)]
        self.assertEqual(got, [('Worlds', 'one', ('src/Map.java', 9)),
                               ('hop', 'one', ('src/Map.java', 9)),
                               ('load', 'tests', 1),
                               ('open', 'one', ('a.py', 1))])


class CallerReadFails(PackCase):
    def test_a_failed_grep_prints_not_checked_callers(self):
        self.git('checkout', '-q', '-b', 'feature')
        self.write('w.py', 'def load():\n    pass\n')
        self.commit_all('public def')
        # the fake has no `git grep` (Unsupported is a BaseException), so the failure is injected
        self.enter(mock.patch.object(reviewpack.gitcmd, 'call', failing_call(('grep',))))
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('quality  NOT CHECKED (callers): `git grep` exit 128: '
                      'fatal: injected read failure', out)


@real_git('git grep')
class CallerLine(PackCase):
    def test_pack_prints_one_caller_and_test_only_symbols(self):
        # w4-worlds' shape: a new public method called once, another called only from a test
        self.write('src/main/java/r/Hop.java', 'package r;\nfinal class Hop {}\n')
        self.commit_all('base')
        self.git('checkout', '-q', '-b', 'feature')
        self.write('src/main/java/r/Worlds.java',
                   'package r;\nclass Worlds {\n    public int pick() { return 1; }\n'
                   '    public int peek() { return 2; }\n}\n')
        self.write('src/main/java/r/Hop.java',
                   'package r;\nfinal class Hop {\n    int go() { return new Worlds().pick(); }\n}\n')
        self.write('src/test/java/r/WorldsTest.java',
                   'package r;\nclass WorldsTest {\n    void t() { new Worlds().peek(); }\n}\n')
        self.commit_all('worlds')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('  SIGNAL new public symbol with one caller: src/main/java/r/Worlds.java:3 '
                      'pick (caller src/main/java/r/Hop.java:3)', out)
        self.assertIn('  SIGNAL new public symbol called only from tests: '
                      'src/main/java/r/Worlds.java:4 peek (1 test callers)', out)


if __name__ == '__main__':
    unittest.main()
