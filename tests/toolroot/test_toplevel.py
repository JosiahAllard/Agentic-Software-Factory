"""The -C of stack_skill, maint_lint, ci_ratchet, render_content and agent_roster is resolved to
its checkout top (kitlib.git.repo.toplevel): outside any git work tree each exits 2 and writes
nothing; a subfolder acts on the top; maint_lint names the main checkout."""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from kitlib import content, stackskill
from tests import gitfixture

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'tools')
sys.path.insert(0, TOOLS)
import agent_roster      # noqa: E402
import ci_ratchet        # noqa: E402
import maint_lint        # noqa: E402
import render_content    # noqa: E402
import stack_skill       # noqa: E402


class Case(unittest.TestCase):

    def setUp(self):
        self.fx = gitfixture.TempRepo()
        self.addCleanup(self.fx.cleanup)
        self.top = os.path.realpath(self.fx.root)
        self.sub = os.path.join(self.top, 'docs', 'deep')
        os.makedirs(self.sub)
        self.outside = tempfile.mkdtemp(prefix='toolroot-')
        self.addCleanup(shutil.rmtree, self.outside, True)

    def run_main(self, fn, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fn(argv, **kw)
        except SystemExit as e:
            code = e.code
        return code, out.getvalue() + err.getvalue()

    def assertEmpty(self):
        self.assertEqual(os.listdir(self.outside), [])


class OutsideAGitTree(Case):

    def test_stack_skill(self):
        with mock.patch.object(stackskill, 'install') as inst:
            code, _ = self.run_main(stack_skill.main, ['install', '--stack', 'python', '-C', self.outside, '--apply'])
        self.assertEqual(code, 2)
        inst.assert_not_called()
        self.assertEmpty()

    def test_maint_lint(self):
        out = os.path.join(self.outside, 'page.html')
        code, _ = self.run_main(maint_lint.main, ['-C', self.outside, '--dry-run', '--out', out])
        self.assertEqual(code, 2)
        self.assertEmpty()

    def test_ci_ratchet(self):
        os.makedirs(os.path.join(self.outside, 'tests'))
        buf = io.StringIO()
        code = ci_ratchet.main(['-C', self.outside, '--baseline', os.path.join(self.outside, 'b.txt')], out=buf)
        self.assertEqual(code, 2)
        self.assertIn('-C', buf.getvalue())
        self.assertEqual(os.listdir(self.outside), ['tests'])

    def test_render_content(self):
        buf = io.StringIO()
        with mock.patch.object(content, 'write') as w:
            code = render_content.main(['-C', self.outside, '--write'], out=buf)
        self.assertEqual(code, 2)
        w.assert_not_called()
        self.assertEmpty()

    def test_agent_roster(self):
        buf = io.StringIO()
        with mock.patch.object(content, 'write') as w:
            code = agent_roster.main(['-C', self.outside, '--write'], out=buf)
        self.assertEqual(code, 2)
        w.assert_not_called()
        self.assertEmpty()


class FromASubfolder(Case):

    def test_stack_skill_writes_at_the_top(self):
        with mock.patch.object(stackskill, 'install', return_value=[]) as inst:
            code, _ = self.run_main(stack_skill.main, ['install', '--stack', 'python', '-C', self.sub, '--apply'])
        self.assertEqual(code, 0)
        self.assertEqual(os.path.realpath(inst.call_args[0][1]), self.top)

    def test_maint_lint_names_the_main_checkout(self):
        wt = self.fx.worktree('linked')
        seen = []

        def default_dir(name):
            seen.append(name)
            return None, 'stop here'
        for start in (self.sub, wt):
            with mock.patch.object(maint_lint.rmod, 'default_dir', default_dir):
                self.run_main(maint_lint.main, ['-C', start])
        self.assertEqual(seen, [os.path.basename(self.top)] * 2)

    def test_render_content_acts_on_the_top(self):
        with mock.patch.object(content, 'write', return_value=[]) as w:
            code = render_content.main(['-C', self.sub, '--write'], out=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(os.path.realpath(w.call_args[0][0]), self.top)

    def test_agent_roster_acts_on_the_top(self):
        with mock.patch.object(content, 'write', return_value=[]) as w:
            code = agent_roster.main(['-C', self.sub, '--write'], out=io.StringIO())
        self.assertEqual(code, 0)
        self.assertEqual(os.path.realpath(w.call_args[0][0]), self.top)

    def test_ci_ratchet_acts_on_the_top(self):
        os.makedirs(os.path.join(self.top, 'tests'))
        buf = io.StringIO()
        with mock.patch.object(ci_ratchet.ratchet, 'default_baseline', return_value=None):
            code = ci_ratchet.main(['-C', self.sub], out=buf)
        self.assertEqual(code, 2)
        self.assertNotIn('no tests folder', buf.getvalue())


if __name__ == '__main__':
    unittest.main()
