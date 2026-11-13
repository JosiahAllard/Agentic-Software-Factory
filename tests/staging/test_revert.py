"""kitlib.staging.revert: the revert of one landing as a commit object, and its local branch, in a
scratch repository (the fake git: tests/__init__.py)."""
import unittest

from kitlib.git import refs
from kitlib.staging import revert as V
from tests.gitfixture import TempRepo


class Case(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.root = self.repo.root

    def put(self, message, **files):
        for rel, text in files.items():
            self.repo.write(rel, text)
        return self.repo.commit(message, *files)

    def landing(self, name, **files):
        """A landing as the landing merges one: a --no-ff merge of kit/<name>, its sha."""
        self.repo.git('switch', '-q', '-c', 'kit/' + name)
        self.put('work on %s' % name, **files)
        self.repo.git('switch', '-q', 'main')
        self.repo.git('merge', '-q', '--no-ff', '-m', 'Merge kit/%s' % name, 'kit/' + name)
        return self.repo.head()

    def show(self, rev, path):
        return self.repo.git('show', '%s:%s' % (rev, path), check=False)

    def files(self, rev):
        return set(self.repo.git('ls-tree', '--name-only', rev).split())


class Make(Case):
    def test_a_landing_is_undone_on_the_tip_and_the_later_work_kept(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        tip = self.put('later', **{'c.txt': 'c\n'})
        made, why = V.make(self.root, merge, tip)
        self.assertIsNone(why)
        self.assertEqual((made.status, made.paths), (V.MADE, []))
        self.assertNotIn('b.txt', self.files(made.commit))
        self.assertIn('c.txt', self.files(made.commit))
        self.assertEqual(self.repo.git('rev-parse', made.commit + '^1'), tip)
        self.assertEqual(self.repo.git('rev-list', '--parents', '-1', made.commit).split(),
                         [made.commit, tip])        # one parent: the tip
        self.assertEqual(self.repo.head(), tip)          # no ref moved, no tree changed
        self.assertEqual(self.repo.git('status', '--porcelain'), '')

    def test_a_straight_commit_is_reverted_alike(self):
        sha = self.put('straight', **{'b.txt': 'b\n'})
        tip = self.put('later', **{'c.txt': 'c\n'})
        made, _ = V.make(self.root, sha, tip)
        self.assertEqual(made.status, V.MADE)
        self.assertEqual(self.files(made.commit), self.files(tip) - {'b.txt'})

    def test_the_message_names_the_landing_and_ends_with_its_reverts_landing_line(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        made, _ = V.make(self.root, merge, merge)
        body = self.repo.git('log', '-1', '--format=%B', made.commit)
        self.assertTrue(body.startswith('Revert landing %s: Merge kit/feat' % merge[:10]), body)
        self.assertEqual(body.splitlines()[-1], 'Reverts-landing: %s' % merge)
        from kitlib.backlog import at_landing
        self.assertEqual(V.REVERTS_KEY, at_landing.REVERTS_KEY)

    def test_author_and_committer_are_the_kit_s_never_the_machine_s(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        made, _ = V.make(self.root, merge, merge)
        who = self.repo.git('log', '-1', '--format=%an|%ae|%cn|%ce', made.commit)
        self.assertEqual(who, '|'.join(V.AUTHOR + V.AUTHOR))
        self.assertNotEqual(self.repo.git('log', '-1', '--format=%an', merge), V.AUTHOR[0])

    def test_a_revert_that_conflicts_is_a_conflict_with_its_paths_and_no_commit(self):
        self.put('before', **{'a.txt': 'one\n'})
        merge =self.landing('feat', **{'a.txt': 'landed\n'})
        tip = self.put('rewrites it', **{'a.txt': 'rewritten\n'})
        made, why = V.make(self.root, merge, tip)
        self.assertIsNone(why)
        self.assertEqual((made.status, made.commit, made.paths), (V.CONFLICT, None, ['a.txt']))

    def test_a_change_already_undone_on_the_tip_is_empty(self):
        merge = self.landing('feat', **{'a.txt': 'landed\n'})
        self.repo.git('rm', '-q', 'a.txt')
        self.repo.git('commit', '-q', '-m', 'undone by hand')
        tip = self.repo.head()
        made, why = V.make(self.root, merge, tip)
        self.assertEqual((made.status, made.commit, why), (V.EMPTY, None, None))

    def test_a_rev_that_names_nothing_is_why(self):
        made, why = V.make(self.root, 'f' * 40, self.repo.head())
        self.assertIsNone(made)
        self.assertIn('could not revert', why)


class Prepare(Case):
    def test_the_local_branch_is_made_at_the_revert_and_nothing_is_pushed(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        tip = self.put('later', **{'c.txt': 'c\n'})
        got, why = V.prepare(self.root, merge, tip)
        self.assertIsNone(why)
        self.assertEqual(got.branch, 'revert/%s' % merge[:10])
        self.assertEqual(refs.resolve('refs/heads/' + got.branch, self.root)[0], got.commit)
        self.assertEqual(self.repo.git('rev-parse', got.commit + '^1'), tip)
        self.assertIsNone(got.note)
        self.assertEqual(self.repo.head(), tip)

    def test_a_made_revert_is_used_and_a_second_prepare_keeps_the_branch(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        made, _ = V.make(self.root, merge, merge)
        first, _ = V.prepare(self.root, merge, merge, made=made)
        self.assertEqual(first.commit, made.commit)
        again, why = V.prepare(self.root, merge, merge, made=made)
        self.assertEqual((again.commit, again.note, why), (made.commit, 'already there', None))

    def test_a_branch_at_another_commit_is_left_alone_and_the_note_says_so(self):
        merge = self.landing('feat', **{'b.txt': 'b\n'})
        self.repo.git('branch', V.branch_of(merge), merge)
        got, why = V.prepare(self.root, merge, merge)
        self.assertIsNone(why)
        self.assertEqual(got.commit, merge)
        self.assertIn('left as it is', got.note)
        self.assertEqual(refs.resolve('refs/heads/' + got.branch, self.root)[0], merge)

    def test_a_conflict_or_an_empty_revert_makes_no_branch(self):
        self.put('before', **{'a.txt': 'one\n'})
        merge =self.landing('feat', **{'a.txt': 'landed\n'})
        tip = self.put('rewrites it', **{'a.txt': 'rewritten\n'})
        got, why = V.prepare(self.root, merge, tip)
        self.assertIsNone(got)
        self.assertIn('conflicts in a.txt', why)
        self.assertTrue(why.endswith('by hand on a branch from %s: git revert -m 1 %s' % (
            tip[:10], merge)), why)
        got, why = V.prepare(self.root, merge, tip, made=V.Made(V.EMPTY, None, []))
        self.assertIsNone(got)
        self.assertIn('nothing to revert', why)
        self.assertEqual(refs.resolve('refs/heads/' + V.branch_of(merge), self.root,
                                      known=True), ('', None))

    def test_a_conflict_of_a_commit_pushed_straight_gives_the_revert_with_no_mainline(self):
        self.put('before', **{'a.txt': 'one\n'})
        straight = self.put('straight', **{'a.txt': 'landed\n'})
        tip = self.put('rewrites it', **{'a.txt': 'rewritten\n'})
        got, why = V.prepare(self.root, straight, tip)
        self.assertIsNone(got)
        self.assertTrue(why.endswith('by hand on a branch from %s: git revert %s' % (
            tip[:10], straight)), why)
        self.assertNotIn('-m 1', why)


if __name__ == '__main__':
    unittest.main()
