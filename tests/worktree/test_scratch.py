"""kitlib.worktree.scratch reachability (`reachable`, `_reaches`) and remove.kept_reflog: whether
a ref reaches every commit a tree's HEAD reflog holds is decided by one git call for all of them,
whatever the number of refs and commits, and each commit still gets its own right answer."""
import os
import unittest
from unittest import mock

from kitlib.worktree import remove, scratch
from tests import gitfixture

WALKS = ('merge-base', 'rev-list')


class ReachCase(unittest.TestCase):
    """A repo whose main holds c1..c3 above the base, `spare` branches parked at the base (none
    reaches c1..c3), and a commit `lone` no ref reaches."""

    def setUp(self):
        env = mock.patch.dict(os.environ, gitfixture.fixture_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.fx = gitfixture.TempRepo(branch='main', bare_remote=True)
        self.addCleanup(self.fx.cleanup)
        self.repo = os.path.realpath(self.fx.root)
        self.base = self.fx.head()
        self.chain = []
        for i in range(3):
            self.fx.write('c%d.txt' % i, '%d\n' % i)
            self.chain.append(self.fx.commit('c%d' % i, 'c%d.txt' % i))
        self.lone = self.fx.git('commit-tree', self.base + '^{tree}', '-p', self.base, '-m',
                                'lone', cwd=self.repo).strip()
        self.ctx, refused = remove.open_ctx(self.repo)
        self.assertIsNone(refused)

    made = 0

    def spare(self, n):
        # Named to sort before refs/heads/main: a per-ref walk asks each of them first.
        for i in range(self.made, n):
            self.fx.git('branch', 'a-%03d' % i, self.base, cwd=self.repo)
        self.made = max(self.made, n)

    def walks(self, call):
        """`(call's result, the number of reachability walks git ran)`."""
        real, seen = remove.git, []

        def counting(cwd, *args, **kw):
            if args[:1] and args[0] in WALKS:
                seen.append(args[0])
            return real(cwd, *args, **kw)

        with mock.patch.object(remove, 'git', counting):
            got = call()
        return got, len(seen)


class Batched(ReachCase):

    def test_the_walks_do_not_grow_with_refs_or_commits(self):
        head = self.chain[-1]               # main's tip: its name needs no walk
        counts = []
        for refs, logged in ((2, self.chain[:1]), (12, self.chain[:2]), (24, self.chain)):
            self.spare(refs)
            got, n = self.walks(lambda: scratch.reachable(self.ctx, 'p', head, logged + [head]))
            self.assertEqual(got, 'refs/heads/main', self.ctx.out)
            counts.append(n)
        self.assertEqual(counts, [1, 1, 1])

    def test_kept_reflog_with_the_tag_walks_once(self):
        self.spare(20)
        reflog = mock.patch.object(scratch, 'reflog', return_value=(self.chain + [self.lone], None))
        with reflog:
            got, n = self.walks(lambda: remove.kept_reflog(self.ctx, 'p', self.lone, True))
        self.assertEqual(got, self.chain + [self.lone], self.ctx.out)
        self.assertEqual(n, 1)


class Answers(ReachCase):

    def test_each_commit_gets_its_own_answer(self):
        self.spare(3)
        got, why = scratch._reaches(self.ctx, [self.chain[0], self.lone, self.chain[2]])
        self.assertIsNone(why)
        self.assertEqual(got, [self.lone])

    def test_a_reached_head_is_named_and_an_unreached_reflog_commit_refuses(self):
        head = self.chain[1]
        self.assertEqual(scratch.reachable(self.ctx, 'p', head, [self.chain[0], head]),
                         'refs/heads/main', self.ctx.out)
        self.assertIsNone(scratch.reachable(self.ctx, 'p', head, [self.lone, head]))
        self.assertIn('holds %s, which no ref reaches' % self.lone[:10], self.ctx.out[-1])

    def test_an_unreached_head_refuses(self):
        self.assertIsNone(scratch.reachable(self.ctx, 'p', self.lone, [self.lone]))
        self.assertIn('no ref reaches its detached HEAD %s' % self.lone[:10], self.ctx.out[-1])

    def test_a_commit_git_no_longer_has_is_unknown_never_unreached(self):
        gone = 'f' * len(self.base)
        got, why = scratch._reaches(self.ctx, [self.chain[0], gone])
        self.assertIsNone(got)
        self.assertIn('git rev-list exited', why)
        self.assertIsNone(scratch.reachable(self.ctx, 'p', self.chain[0], [gone, self.chain[0]]))
        self.assertIn('is unknown', self.ctx.out[-1])

    def test_the_tag_on_head_keeps_its_history_and_nothing_else(self):
        on_lone = self.fx.git('commit-tree', self.base + '^{tree}', '-p', self.lone, '-m', 'top',
                              cwd=self.repo).strip()
        with mock.patch.object(scratch, 'reflog', return_value=([self.lone, on_lone], None)):
            self.assertEqual(remove.kept_reflog(self.ctx, 'p', on_lone, True),
                             [self.lone, on_lone], self.ctx.out)
        other = self.fx.git('commit-tree', self.base + '^{tree}', '-p', self.base, '-m', 'other',
                            cwd=self.repo).strip()
        with mock.patch.object(scratch, 'reflog', return_value=([other, on_lone], None)):
            self.assertIsNone(remove.kept_reflog(self.ctx, 'p', on_lone, True))
        self.assertIn('holds %s, which no ref reaches' % other[:10], self.ctx.out[-1])


if __name__ == '__main__':
    unittest.main()
