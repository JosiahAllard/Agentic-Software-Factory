"""commit.py refuses a path a narrowing pull gave to another live record (docs/commit.md "A
narrowed record"; kitlib/backlog/narrowed.py), end to end in a linked worktree whose `.kit.json`
names a folder store in it, so the check reads that store."""
import os
import subprocess
import sys
import unittest
from unittest import mock

from kitlib import commit as C
from tests.backlog.helpers import record_text
from tests.commit import test_commit
from tests.commit import test_commit_approval as tca
from tests.commit.test_commit import AGENT, OWNER, LinkedCase

setUpModule = test_commit.setUpModule
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STORE = 'tasks'


class NarrowedCase(LinkedCase):

    def setUp(self):
        super().setUp()
        self.configure(owner={'email': OWNER, 'agent_emails': [AGENT]},
                       backlog={'store': STORE})

    def record(self, rid, **over):
        self.put('%s/%s.md' % (STORE, rid), record_text(id=rid, **over))

    def move(self, old, new):
        """A rename as git records it with renames off: `old` deleted, `new` added."""
        self.git('rm', '-q', '--', old)
        self.put(new, 'x\n')

    def narrowed_pair(self, other_state='pulled'):
        """C-ONE pulled on this worktree's branch, kept kit/a/** of kit/**; L-ONE took kit/b/**."""
        branch = self.git('branch', '--show-current')
        self.record('C-ONE', state='pulled', pulled_at='2026-11-03', pulled_by=branch,
                    scope='kit/a/**', narrowed='kit/** L-ONE kit/l')
        if other_state == 'pulled':
            self.record('L-ONE', state='pulled', pulled_at='2026-11-03', pulled_by='kit/l',
                        scope='kit/b/**', narrowed='kit/** C-ONE %s' % branch)
        else:
            self.record('L-ONE', state=other_state, scope='kit/b/**')


class TheRefusal(NarrowedCase):

    def test_a_path_given_to_the_other_record_is_refused_and_nothing_committed(self):
        self.narrowed_pair()
        self.put('kit/b/x.py', 'x\n')
        head = self.head()
        code, out = self.commit('touch theirs', 'kit/b/x.py')
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: needs kit/b/x.py, held by L-ONE on kit/l (narrowed for C-ONE). '
                      'Stop and report "needs kit/b/x.py, held by L-ONE". Instead: backlog.py '
                      'block C-ONE --on item:L-ONE --reason "needs kit/b/x.py"', out)
        self.assertIn('Nothing was committed.', out)
        self.assertEqual(self.head(), head)

    def test_a_rename_across_refuses_the_whole_commit(self):
        self.put('kit/a/x.py', 'x\n')
        self.git('add', '--', 'kit/a/x.py')
        self.git('commit', '-q', '-m', 'mine', '--', 'kit/a/x.py')
        self.narrowed_pair()
        self.move('kit/a/x.py', 'kit/b/x.py')
        head = self.head()
        code, out = self.commit('move it', 'kit/a/x.py', 'kit/b/x.py')
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: needs kit/b/x.py (rename of kit/a/x.py), held by L-ONE on kit/l',
                      out)
        self.assertEqual(self.head(), head)

    def test_a_rename_out_of_the_given_part_is_refused_on_its_old_side(self):
        self.put('kit/b/x.py', 'x\n')
        self.git('add', '--', 'kit/b/x.py')
        self.git('commit', '-q', '-m', 'before the narrowing', '--', 'kit/b/x.py')
        self.narrowed_pair()
        self.move('kit/b/x.py', 'kit/a/x.py')
        code, out = self.commit('move it', 'kit/a/x.py', 'kit/b/x.py')
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: needs kit/b/x.py (renamed to kit/a/x.py), held by L-ONE on kit/l',
                      out)
        self.assertNotIn('needs kit/a/x.py', out)

    def test_a_path_in_the_split_member_that_neither_record_holds_is_refused(self):
        self.narrowed_pair()
        self.put('kit/c/new.py', 'x\n')
        code, out = self.commit('new', 'kit/c/new.py')
        self.assertEqual(code, 1, out)
        self.assertIn('needs kit/c/new.py, held by L-ONE', out)


class WhatPasses(NarrowedCase):

    def test_the_record_s_own_part_commits(self):
        self.narrowed_pair()
        self.put('kit/a/x.py', 'x\n')
        code, out = self.commit('mine', 'kit/a/x.py')
        self.assertEqual(code, 0, out)
        self.assertNotIn('narrowed', out)

    def test_the_other_record_closed_lets_the_path_through(self):
        self.narrowed_pair(other_state='closed')
        self.put('kit/b/x.py', 'x\n')
        code, out = self.commit('theirs, landed', 'kit/b/x.py')
        self.assertEqual(code, 0, out)

    def test_a_store_that_cannot_be_read_warns_and_commits(self):
        self.narrowed_pair()
        self.put('kit/b/x.py', 'x\n')
        from kitlib.backlog import narrowed as nmod
        with mock.patch.object(nmod.smod, 'open_backend', side_effect=OSError('store down')):
            code, out = self.commit('store down', 'kit/b/x.py')
        self.assertEqual(code, 0, out)
        self.assertIn('WARNING: the narrowed-record check could not read the backlog store '
                      '(OSError: store down)', out)

    def test_a_malformed_narrowed_line_refuses(self):
        branch = self.git('branch', '--show-current')
        self.record('C-ONE', state='pulled', pulled_at='2026-11-03', pulled_by=branch,
                    scope='kit/a/**', narrowed='kit/** L-ONE')
        self.put('kit/a/x.py', 'x\n')
        code, out = self.commit('mine', 'kit/a/x.py')
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: C-ONE carries a narrowed: line', out)


class WhoCommits(NarrowedCase):
    """A narrowed-away path is a collision whoever commits it: the owner's own commit and an
    owner-approved one are checked like any other."""

    def test_an_owner_authored_commit_is_checked(self):
        self.as_author(OWNER)
        self.narrowed_pair()
        self.put('kit/b/x.py', 'x\n')
        head = self.head()
        code, out = self.commit('the owner touches theirs', 'kit/b/x.py', owner_authored=True)
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: needs kit/b/x.py, held by L-ONE on kit/l', out)
        self.assertEqual(self.head(), head)


class AnApprovedCommit(NarrowedCase, tca.ApprovalCase):

    def test_an_owner_approved_commit_is_checked_before_anything_is_written(self):
        self.narrowed_pair()
        self.approve({'CLAUDE.md': 'new rules\n', 'kit/b/x.py': 'x\n'})
        head = self.head()
        code, out = self.approved_commit('kit/b/x.py', 'CLAUDE.md')
        self.assertEqual(code, 1, out)
        self.assertIn('REFUSED: needs kit/b/x.py, held by L-ONE on kit/l', out)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.read('CLAUDE.md'), 'rules\n')
        self.assertFalse(os.path.exists(os.path.join(self.wt, 'kit', 'b', 'x.py')))
        self.assert_not_consumed()


class AMerge(NarrowedCase, test_commit.MergeCase):

    def setUp(self):
        super().setUp()
        self.configure(owner={'email': OWNER, 'agent_emails': [AGENT]},
                       commit={'merge_fold': True}, backlog={'store': STORE})

    def test_a_path_the_merge_carries_in_is_not_checked(self):
        # main brings kit/b/m.txt, inside the part given to L-ONE: main's, so it is not refused.
        self.narrowed_pair()
        tip, base = self.start_merge(extra=('kit/b/m.txt',))
        code, out = self.commit('merge main', 'work.txt')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.git('log', '-1', '--format=%P').split(), [tip, base])
        self.assertIn('kit/b/m.txt', out)
        self.assertNotIn('needs kit/b/m.txt', out)

    def test_a_named_path_in_a_merge_is_checked(self):
        self.narrowed_pair()
        self.start_merge(extra=('kit/b/m.txt',))
        self.put('kit/b/mine.txt', 'x\n')
        code, out = self.commit('merge main', 'work.txt', 'kit/b/mine.txt')
        self.assertEqual(code, 1, out)
        self.assertIn('needs kit/b/mine.txt, held by L-ONE', out)
        self.assertNotIn('needs kit/b/m.txt', out)


class TheImportStaysLazy(unittest.TestCase):

    def test_importing_kitlib_commit_does_not_import_kitlib_backlog(self):
        code = ('import sys; import kitlib.commit; '
                'print(sorted(m for m in sys.modules if m.split(".")[:2] == ["kitlib", "backlog"]))')
        p = subprocess.run([sys.executable, '-c', code], cwd=KIT, capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), '[]')
        self.assertTrue(hasattr(C, 'commit'))


if __name__ == '__main__':
    unittest.main()
