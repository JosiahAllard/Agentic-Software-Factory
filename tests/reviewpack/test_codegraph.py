"""review_pack's import-cycle signal (kitlib/reviewpack/codegraph.py, quality.cycle_lines)."""
import unittest

from kitlib.reviewpack import codegraph
from tests.reviewpack.repo import PackCase

PKG = 'src/main/java/org/r/camera/'

# w4-structure's shape: one class split into files that import each other's statics.
CAMERA = {
    PKG + 'Camera.java': 'package org.r.camera;\nimport static org.r.camera.CameraKeys.*;\n'
                         'final class Camera {}\n',
    PKG + 'CameraKeys.java': 'package org.r.camera;\nimport static org.r.camera.CameraSession.*;\n'
                             'import java.util.Map;\nfinal class CameraKeys {}\n',
    PKG + 'CameraSession.java': 'package org.r.camera;\n'
                                'import static org.r.camera.CameraKeys.KEY;\n'
                                'final class CameraSession {}\n',
}


class Cycles(unittest.TestCase):
    def cycles(self, tip, base=None, tree=()):
        base = base or {}
        return codegraph.new_cycles(codegraph.edges(base, list(tree)),
                                    codegraph.edges(tip, list(tree)))

    def test_cycles_by_language(self):
        cs = {'src/Keys.cs': 'namespace R.Keys;\nusing R.Session;\nclass Keys {}\n',
              'src/Session.cs': 'namespace R.Session {\n  using static R.Keys.Keys;\n'
                                '  class Session {}\n}\n',
              'src/Other.cs': '/* using R.Keys; */\nnamespace R.Other;\nclass Other {}\n'}
        py = {'pkg/__init__.py': '', 'pkg/a.py': 'from pkg import b\n',
              'pkg/b.py': 'from . import a\nimport os\n', 'pkg/c.py': 'import pkg.a\n'}
        for name, tip, want in (
                ('java', CAMERA, [[PKG + 'CameraKeys.java', PKG + 'CameraSession.java']]),
                ('csharp', cs, [['src/Keys.cs', 'src/Session.cs']]),
                ('python', py, [['pkg/a.py', 'pkg/b.py']])):
            self.assertEqual(self.cycles(tip, tree=tip), want, name)

    def test_a_type_named_in_a_comment_is_not_declared(self):
        cs = {'src/Keys.cs': 'namespace R.Keys;\nusing R.Session;\n// class Foo was here\n'
                             'class Keys { string s = "class Bar"; }\n',
              'src/Session.cs': 'namespace R.Session;\nusing static R.Keys.Foo;\n'
                                'using static R.Keys.Bar;\nclass Session {}\n'}
        self.assertEqual(self.cycles(cs), [])

    def test_a_cycle_already_at_the_merge_base_is_not_new(self):
        self.assertEqual(self.cycles(CAMERA, base=CAMERA), [])
        grown = dict(CAMERA)
        grown[PKG + 'Camera.java'] += 'import static org.r.camera.CameraSession.X;\n'
        grown[PKG + 'CameraSession.java'] += 'import org.r.camera.Camera;\n'
        self.assertEqual(self.cycles(grown, base=CAMERA),
                         [sorted(CAMERA)])


class CycleLine(PackCase):
    def test_pack_prints_a_new_python_cycle(self):
        self.write('pkg/__init__.py', '')
        self.write('pkg/a.py', 'import os\n')
        self.write('pkg/b.py', 'from pkg import a\n')
        self.commit_all('base')
        self.git('checkout', '-q', '-b', 'feature')
        self.write('pkg/a.py', 'from pkg import b\n')
        self.write('pkg/b.py', 'from pkg import a\nimport sys\n')     # both files changed
        self.commit_all('cycle')
        code, out = self.run_pack('feature', '--base', 'main')
        self.assertEqual(code, 0, out)
        self.assertIn('  SIGNAL import cycle among changed files (2): pkg/a.py, pkg/b.py', out)


if __name__ == '__main__':
    unittest.main()
