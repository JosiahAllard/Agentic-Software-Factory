"""kitlib.landing.push.sync_checkout: the main checkout's main fast-forwarded to a commit already
on the remote's main, as the staging promotion calls it (kitlib.staging.advance.sync_main). The
landing's own call (Push.sync_main, its WARNING text and its git environment) is covered by the
landing tests that land for real (tests/landing/test_already_landed.py and the like)."""
import os
import unittest

from kitlib.landing import push
from kitlib.staging import advance as A
from tests import gitfixture


class Cfg:
    remote, main_branch = 'origin', 'main'


class SyncCheckout(unittest.TestCase):
    def setUp(self):
        self.repo = gitfixture.TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.root = os.path.realpath(self.repo.root)
        self.start = self.repo.head()
        side = self.repo.worktree('side')
        self.repo.write('promoted.txt', 'x\n', root=side)
        self.target = self.repo.commit('promoted', 'promoted.txt', cwd=side)

    def test_the_promotions_sync_fast_forwards_the_main_checkout(self):
        self.assertEqual(A.sync_main(self.root, Cfg(), self.target), [])
        self.assertEqual(self.repo.head(), self.target)

    def test_a_checkout_off_main_is_not_moved_and_the_warning_names_the_move(self):
        self.repo.git('switch', '-q', '-c', 'elsewhere')
        lines = push.sync_checkout(self.root, Cfg(), 'main', self.target)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith(
            "WARNING: origin/main moved to %s, and the main checkout's main (%s) was not "
            "fast-forwarded to it: the main checkout is on elsewhere" % (
                self.target[:10], self.start[:10])), lines[0])
        self.assertTrue(lines[0].endswith('merge --ff-only origin/main'), lines[0])
        self.assertEqual(self.repo.git('rev-parse', 'refs/heads/main'), self.start)


if __name__ == '__main__':
    unittest.main()
