"""Design before source (docs/guard-rules.md `design-first`): in a linked worktree whose branch has no design
approval recorded (verdict.py design), a source write, to a new file or an existing one, is refused; a test or a doc
write is not, and neither is the coordinator's own session."""
import json
import os
import unittest

from kitlib.guard import context
from kitlib.guard.rules_edit import DESIGN_FILE
from kitlib.landing import verdicts

from . import world as W
from .claudehost import decide
from .world import event

RULE = 'design-first'
INSTEAD = 'report your design to your coordinator and wait for approval'


class DesignFirst(unittest.TestCase):

    def setUp(self):
        self.w = W.World()
        self.addCleanup(self.w.cleanup)
        self.git_dir = os.path.join(self.w.main, '.git', 'worktrees', 'wt')
        self.approval = os.path.join(self.git_dir, DESIGN_FILE)
        self.w.approve_wt = False               # the world's worktree comes approved
        os.makedirs(self.git_dir)
        os.makedirs(os.path.join(self.w.wt, 'pkg'))
        with open(os.path.join(self.w.wt, 'pkg', 'old.py'), 'w', encoding='utf-8') as f:
            f.write('x = 1\n')

    def write(self, *parts, env=None, sub=True):
        ev = event('Write', {'file_path': os.path.join(self.w.wt, *parts), 'content': 'x'}, self.w.wt, sub)
        return decide(ev, self.w.ctx(environ=env or {}))

    def refused(self, d):
        self.assertIsNotNone(d)
        self.assertEqual((d.verdict, d.rule, d.instead), ('deny', RULE, INSTEAD), d.reason)

    def passed(self, d):
        self.assertIsNone(d, d and d.reason)

    def test_a_source_write_waits_for_the_approval_new_file_or_existing(self):
        for parts in (('pkg', 'new.py'), ('pkg', 'old.py')):
            self.refused(self.write(*parts))
        W.World.approve(self.git_dir, 'kit/wt')
        for parts in (('pkg', 'new.py'), ('pkg', 'old.py')):
            self.passed(self.write(*parts))

    def test_a_test_or_a_doc_is_written_before_it(self):
        for parts in (('tests', 'test_new.py'), ('pkg', 'test_old.py'), ('src', 'test', 'X.java'),
                      ('README.md',), ('docs', 'guide.txt')):
            self.passed(self.write(*parts))

    def test_python_c_counts_as_a_write_of_the_folder_it_runs_in(self):
        d = decide(W.bash('python -c "print(1)"', self.w.wt, sub=True), self.w.ctx())
        self.refused(d)

    def test_the_coordinator_session_is_not_held(self):
        self.passed(self.write('pkg', 'new.py', env={context.ROLE_ENV: 'coordinator'}, sub=False))
        self.refused(self.write('pkg', 'new.py', env={context.ROLE_ENV: 'topic'}, sub=False))

    def test_an_approval_of_another_branch_a_detached_head_or_a_bad_file_is_none(self):
        W.World.approve(self.git_dir, 'kit/other')
        self.refused(self.write('pkg', 'new.py'))
        W.World.approve(self.git_dir, 'kit/wt')
        self.w.git.current_branch = lambda cwd: ('', None)
        for text in ('{"branch": "kit/wt"}', '{"branch": ""}'):      # an empty branch approves no detached HEAD
            with open(self.approval, 'w', encoding='utf-8') as f:
                f.write(text)
            self.refused(self.write('pkg', 'new.py'))
        del self.w.git.current_branch
        for text in ('{', '[]', '{"branch": 1}', '"kit/wt"', '[' * 100000):
            with open(self.approval, 'w', encoding='utf-8') as f:
                f.write(text)
            self.refused(self.write('pkg', 'new.py'))

    def test_a_doc_path_that_links_out_of_the_worktree_is_source(self):
        os.makedirs(os.path.join(self.w.wt, 'docs'))
        try:
            import _winapi
            _winapi.CreateJunction(self.w.temp, os.path.join(self.w.wt, 'docs', 'ext'))
        except (ImportError, AttributeError, OSError):
            try:
                os.symlink(self.w.temp, os.path.join(self.w.wt, 'docs', 'ext'), target_is_directory=True)
            except (OSError, NotImplementedError) as e:
                self.skipTest('no junction or symlink: %s' % e)
        self.refused(self.write('docs', 'ext', 'x.md'))

    def test_the_guard_reads_the_file_verdicts_writes(self):
        self.assertEqual(DESIGN_FILE, verdicts.DESIGN_FILE)
        with open(self.approval, 'w', encoding='utf-8') as f:
            json.dump({'branch': 'kit/wt'}, f)
        self.passed(self.write('pkg', 'new.py'))


if __name__ == '__main__':
    unittest.main()
