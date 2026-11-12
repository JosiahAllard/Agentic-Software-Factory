"""kitlib.worktree.new and the worktree removal checks with a staging branch (`.kit.json`
"staging".branch, docs/gate-landing.md "Staging branch"): a new branch starts at the pushed
<remote>/<staging> (a landing never writes a local staging branch, so none is compared), and the
removal tools hold the main branch as well as the work base."""
import os
import unittest

from kitlib.worktree import new, remove_checks
from tests.worktree.test_new_worktree import WorktreeCase

STAGING = 'staging'


class StagingStart(WorktreeCase):
    CONFIG = {'staging': {'branch': STAGING}}

    def setUp(self):
        WorktreeCase.setUp(self)
        scratch = os.path.join(self.base_dir, 'seed')
        self.git('worktree', 'add', '-q', '--detach', scratch, self.BASE)
        with open(os.path.join(scratch, 's.txt'), 'w', encoding='utf-8', newline='\n') as h:
            h.write('landed on staging\n')
        self.run_git(scratch, 'add', 's.txt')
        self.run_git(scratch, 'commit', '-q', '-m', 'on staging')
        self.staged = self.run_git(scratch, 'rev-parse', 'HEAD')[1]
        self.run_git(scratch, 'push', '-q', 'origin', '%s:refs/heads/%s' % (self.staged, STAGING))
        self.git('worktree', 'remove', scratch)

    def test_a_new_branch_starts_at_the_pushed_staging_branch(self):
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git('rev-parse', 'fix/fresh')[1], self.staged)
        self.assertIn('branch    fix/fresh from pushed origin/%s @ %s' % (STAGING, self.staged[:7]),
                      out)
        self.assertNotEqual(self.git('rev-parse', '--verify', '-q', 'refs/heads/%s' % STAGING)[0], 0)
        self.assertEqual(self.git('rev-parse', '--abbrev-ref', 'HEAD')[1], self.BASE)

    def test_a_stale_local_staging_branch_is_not_the_start(self):
        self.git('branch', STAGING, self.BASE)          # behind the pushed one, never updated
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git('rev-parse', 'fix/fresh')[1], self.staged)

    def test_an_unpushed_staging_branch_is_refused(self):
        self.git('push', '-q', 'origin', '--delete', STAGING)
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, 1, out)
        self.assertIn('there is no origin/%s after the fetch' % STAGING, out)
        self.assertFalse(new.branch_exists(self.repo, 'fix/fresh'))

    def test_another_base_named_keeps_the_local_comparison(self):
        rc, out = self.tool('fix/fresh', '--base', self.BASE)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git('rev-parse', 'fix/fresh')[1], self.git('rev-parse', self.BASE)[1])
        self.assertIn('branch    fix/fresh from %s @ ' % self.BASE, out)

    def test_an_existing_branch_resumes(self):
        self.git('branch', 'fix/old', self.BASE)
        rc, out = self.tool('fix/old', '--existing')
        self.assertEqual(rc, 0, out)
        self.assertIn('resumed (1 commit(s) of pushed origin/%s not in it)' % STAGING, out)


class RemovalBase(unittest.TestCase):
    def test_the_main_branch_and_the_work_base_are_both_held(self):
        s = {'base': STAGING, 'main': 'main'}
        for name in (STAGING, 'Staging', 'main', 'MAIN'):
            self.assertTrue(remove_checks.is_base(name, s), name)
        self.assertFalse(remove_checks.is_base('kit/x', s))
        self.assertTrue(remove_checks.is_base('main', {'base': 'main'}))    # no 'main' key


if __name__ == '__main__':
    unittest.main()
