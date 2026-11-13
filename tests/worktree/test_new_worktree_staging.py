"""kitlib.worktree.new and the worktree removal checks with a staging branch (`.kit.json`
"staging".branch, docs/gate-landing.md "Staging branch"): a new branch starts at the pushed
<remote>/<staging> (a landing never writes a local staging branch, so none is compared), and the
removal tools hold the main branch as well as the work base."""
import json
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


class FetchedStagingSetting(WorktreeCase):
    """The staging setting is main's as just fetched (kitlib.config.overlay_fetched_staging),
    never the main checkout's working tree, which lags until the owner fast-forwards it."""

    def on_origin(self, branch, rel, text, start=None):
        """A commit writing `rel` on top of `start` (default origin/<branch>), pushed to `branch`
        from a scratch worktree; the main checkout is not moved."""
        scratch = os.path.join(self.base_dir, 'seed-%s' % branch)
        self.git('fetch', '-q', 'origin')
        self.git('worktree', 'add', '-q', '--detach', scratch, start or 'origin/%s' % branch)
        with open(os.path.join(scratch, rel), 'w', encoding='utf-8', newline='\n') as h:
            h.write(text)
        self.run_git(scratch, 'add', rel)
        self.run_git(scratch, 'commit', '-q', '-m', 'on %s' % branch)
        sha = self.run_git(scratch, 'rev-parse', 'HEAD')[1]
        self.run_git(scratch, 'push', '-q', 'origin', '%s:refs/heads/%s' % (sha, branch))
        self.git('worktree', 'remove', '--force', scratch)
        return sha

    def test_staging_turned_on_upstream_is_read_before_the_checkout_has_it(self):
        main = self.on_origin(self.BASE, '.kit.json', json.dumps({'staging': {'branch': STAGING}}))
        staged = self.on_origin(STAGING, 's.txt', 'landed on staging\n', start=main)
        self.assertFalse(os.path.exists(os.path.join(self.repo, '.kit.json')))   # checkout lags
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git('rev-parse', 'fix/fresh')[1], staged)
        self.assertIn('from pushed origin/%s @ %s' % (STAGING, staged[:7]), out)

    def test_staging_turned_off_upstream_is_read_before_the_checkout_drops_it(self):
        self.configure({'staging': {'branch': STAGING}})
        self.on_origin(STAGING, 's.txt', 'landed on staging\n', start='origin/%s' % self.BASE)
        main = self.on_origin(self.BASE, '.kit.json', '{}\n')
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.git('rev-parse', 'fix/fresh')[1], main)

    def test_a_malformed_staging_setting_on_main_refuses_and_creates_nothing(self):
        self.on_origin(self.BASE, '.kit.json', json.dumps({'staging': {'branch': self.BASE}}))
        rc, out = self.tool('fix/fresh')
        self.assertEqual(rc, new.CONFIG, out)
        self.assertIn('new_worktree: refused: kit configuration:', out)
        self.assertIn('The staging setting is read from refs/remotes/origin/%s' % self.BASE, out)
        self.assertIn('fix "staging" in .kit.json on %s, then fetch' % self.BASE, out)
        self.assertIn('nothing was created', out)
        self.assertFalse(new.branch_exists(self.repo, 'fix/fresh'))
        self.assertEqual(self.worktree_paths(), [])


class RemovalBase(unittest.TestCase):
    def test_the_main_branch_and_the_work_base_are_both_held(self):
        s = {'base': STAGING, 'main': 'main'}
        for name in (STAGING, 'Staging', 'main', 'MAIN'):
            self.assertTrue(remove_checks.is_base(name, s), name)
        self.assertFalse(remove_checks.is_base('kit/x', s))
        self.assertTrue(remove_checks.is_base('main', {'base': 'main'}))    # no 'main' key


if __name__ == '__main__':
    unittest.main()
