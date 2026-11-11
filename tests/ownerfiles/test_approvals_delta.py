"""kitlib.ownerfiles.approvals: an approval is a delta. delta_result is what the approved change of one
file (base blob B0 to approved blob A) gives on a parent P: A where P is B0 or already A, the clean
three-way merge (`git merge-file -p P B0 A`) where P moved on, and a refusal on a conflict or any
doubt. spent is the commit on main that carries the approval's Owner-Approval trailer: once its
change is on main the approval is used up. The fast paths and spent run on the fake git; the
merges on real git (the fake has no merge-file)."""
import os
import unittest

from kitlib.ownerfiles import approvals as ap
from tests.gitfixture import TempRepo
from tests.realgit import real_git

LINES = ''.join('line %d\n' % n for n in range(1, 13))       # twelve lines, one per number


def edit(text, n, new):
    """`text` with its line `n` (1-based) replaced by `new`."""
    rows = text.splitlines(keepends=True)
    rows[n - 1] = new + '\n'
    return ''.join(rows)


class DeltaCase(unittest.TestCase):

    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.root = self.repo.root
        self.n = 0

    def blob(self, text):
        """The blob id of `text`, written into the object database."""
        self.n += 1
        path = self.repo.write(os.path.join('scratch', 'b%d.txt' % self.n), text)
        oid = self.repo.git('hash-object', '-w', path)
        self.assertEqual(oid, ap.blob_id(text))
        return oid

    def result(self, base, parent, approved, content=None):
        """delta_result for blob ids, the approved content given as its UTF-8 bytes."""
        if content is None and approved is not None:
            raise AssertionError('give the approved content')
        data = None if content is None else content.encode('utf-8')
        return ap.delta_result(self.root, base, parent, approved, data)


class FastPaths(DeltaCase):
    """No merge: the parent is the base (today's rule), or holds the approved content already."""

    def test_a_parent_at_the_base_gives_the_approved_blob(self):
        b0, new = self.blob(LINES), edit(LINES, 2, 'two')
        self.assertEqual(self.result(b0, b0, ap.blob_id(new), new), (ap.blob_id(new), None))

    def test_a_parent_that_already_holds_the_approved_content_gives_it(self):
        b0, new = self.blob(LINES), edit(LINES, 2, 'two')
        a = self.blob(new)
        self.assertEqual(self.result(b0, a, a, new), (a, None))

    def test_a_parent_without_the_file_refuses_a_modification(self):
        b0, new = self.blob(LINES), edit(LINES, 2, 'two')
        got, why = self.result(b0, None, ap.blob_id(new), new)
        self.assertIsNone(got)
        self.assertIn('not at the parent', why)

    def test_a_new_file_needs_no_file_at_the_parent_or_the_approved_one(self):
        new = 'fresh\n'
        a = self.blob(new)
        self.assertEqual(self.result(None, None, a, new), (a, None))
        self.assertEqual(self.result(None, a, a, new), (a, None))
        got, why = self.result(None, self.blob('other\n'), a, new)
        self.assertIsNone(got)
        self.assertIn('already at the parent', why)

    def test_a_delete_needs_the_base_or_no_file_at_the_parent(self):
        b0 = self.blob(LINES)
        self.assertEqual(self.result(b0, b0, None), (None, None))
        self.assertEqual(self.result(b0, None, None), (None, None))
        got, why = self.result(b0, self.blob(edit(LINES, 2, 'two')), None)
        self.assertIsNone(got)
        self.assertIn('changed since the approved base', why)

    def test_approved_content_that_is_not_its_blob_is_refused(self):
        b0, new = self.blob(LINES), edit(LINES, 2, 'two')
        got, why = self.result(b0, b0, ap.blob_id(new), new + 'more\n')
        self.assertIsNone(got)
        self.assertIn('not its approved blob', why)
        got, why = ap.delta_result(self.root, b0, b0, ap.blob_id(new), None)
        self.assertIsNone(got)
        self.assertIn('not its approved blob', why)

    def test_a_parent_blob_git_cannot_read_is_refused(self):
        b0, new = self.blob(LINES), edit(LINES, 2, 'two')
        got, why = self.result(b0, '1' * 40, ap.blob_id(new), new)
        self.assertIsNone(got)
        self.assertTrue(why)


@real_git('git merge-file: the fake has none')
class Merges(DeltaCase):
    """A parent that moved on: the clean merge of the approved change onto it, or a refusal."""

    def test_a_change_far_from_the_approved_one_merges_cleanly(self):
        new = edit(LINES, 2, 'two')
        moved = edit(LINES, 11, 'eleven')
        both = edit(moved, 2, 'two')
        self.assertEqual(self.result(self.blob(LINES), self.blob(moved), ap.blob_id(new), new),
                         (ap.blob_id(both), None))

    def test_the_approved_blob_need_not_be_in_the_object_database(self):
        new = edit(LINES, 2, 'two')                 # never written: its bytes come from the doc
        moved = edit(LINES, 11, 'eleven')
        got, why = self.result(self.blob(LINES), self.blob(moved), ap.blob_id(new), new)
        self.assertEqual((got, why), (ap.blob_id(edit(moved, 2, 'two')), None))

    def test_delta_content_gives_the_merged_bytes_that_commit_py_writes(self):
        new = edit(LINES, 2, 'two')
        moved = edit(LINES, 11, 'eleven')
        b0, par = self.blob(LINES), self.blob(moved)
        self.assertEqual(ap.delta_content(self.root, b0, par, ap.blob_id(new), new.encode()),
                         (edit(moved, 2, 'two').encode(), None))
        self.assertEqual(ap.delta_content(self.root, b0, b0, ap.blob_id(new), new.encode()),
                         (new.encode(), None))
        self.assertEqual(ap.delta_content(self.root, b0, b0, None, None), (None, None))

    def test_a_change_to_the_same_line_is_a_conflict(self):
        new = edit(LINES, 2, 'two')
        got, why = self.result(self.blob(LINES), self.blob(edit(LINES, 2, 'TWO')),
                               ap.blob_id(new), new)
        self.assertIsNone(got)
        self.assertIn('does not merge cleanly', why)

    def test_a_delete_of_a_file_that_moved_on_is_refused_unmerged(self):
        got, why = self.result(self.blob(LINES), self.blob(edit(LINES, 11, 'eleven')), None)
        self.assertIsNone(got)
        self.assertIn('changed since the approved base', why)


class Spent(DeltaCase):
    """spent(checkout, doc_id, rev): the newest commit `rev` reaches that carries exactly
    `Owner-Approval: hub:<doc_id>`, or None."""

    def landed(self, message):
        self.repo.write('a.txt', message)
        self.repo.git('add', 'a.txt')
        self.repo.git('commit', '-q', '-m', message)
        return self.repo.git('rev-parse', 'HEAD')

    def test_an_approval_whose_commit_is_on_main_is_spent(self):
        sha = self.landed('edit\n\nOwner-Approval: hub:A-001\n')
        self.landed('later work\n')
        self.assertEqual(ap.spent(self.root, 'A-001', 'main'), (sha, None))

    def test_an_approval_not_on_main_is_not_spent(self):
        self.landed('edit\n\nOwner-Approval: hub:A-002\n')
        self.assertEqual(ap.spent(self.root, 'A-001', 'main'), (None, None))
        self.assertEqual(ap.spent(self.root, 'A-001', self.repo.initial), (None, None))

    def test_only_the_exact_value_of_the_trailer_counts(self):
        self.landed('edit\n\nOwner-Approval: hub:A-1000\n')
        self.landed('names hub:A-100 in passing\n\nOwner-Approval: hub:A-1001\n')
        self.landed('hub:A-100 in the subject alone\n')
        self.assertEqual(ap.spent(self.root, 'A-100', 'main'), (None, None))

    def test_a_rev_git_cannot_read_is_a_why(self):
        got, why = ap.spent(self.root, 'A-001', 'refs/remotes/origin/nothing')
        self.assertIsNone(got)
        self.assertTrue(why)


class Landed(DeltaCase):
    """landed(checkout, base, tip): {A-<n>: sha} of the commits `tip` reaches and `base` does not
    that carry exactly one `Owner-Approval: hub:A-<n>`: what a landing puts on main (its feed
    event, kitlib.gate.land.Lander.post_landing)."""

    landed = Spent.landed

    def test_the_approvals_a_range_lands_each_with_its_commit(self):
        before = self.landed('edit\n\nOwner-Approval: hub:A-001\n')
        one = self.landed('edit again\n\nOwner-Approval: hub:A-002\n')
        self.landed('later work\n')
        two = self.landed('more\n\nOwner-Approval: hub:A-003\n')
        self.assertEqual(ap.landed(self.root, before, 'main'), ({'A-002': one, 'A-003': two}, None))
        self.assertEqual(ap.landed(self.root, two, 'main'), ({}, None))

    def test_only_one_exact_hub_value_counts(self):
        base = self.landed('start\n')
        self.landed('names hub:A-100 in passing\n')
        self.landed('two\n\nOwner-Approval: hub:A-4\nOwner-Approval: hub:A-5\n')
        self.landed('not the hub\n\nOwner-Approval: A-6\n')
        self.landed('not an id\n\nOwner-Approval: hub:Q-7\n')
        self.assertEqual(ap.landed(self.root, base, 'main'), ({}, None))

    def test_the_newest_commit_of_one_approval_is_its_commit(self):
        base = self.landed('start\n')
        self.landed('first\n\nOwner-Approval: hub:A-001\n')
        newer = self.landed('rebuilt\n\nOwner-Approval: hub:A-001\n')
        self.assertEqual(ap.landed(self.root, base, 'main'), ({'A-001': newer}, None))

    def test_a_range_git_cannot_read_is_a_why(self):
        for base, tip in (('1' * 40, 'main'), ('main', 'refs/remotes/origin/nothing'),
                          ('-x', 'main'), ('main', None)):
            with self.subTest(base=base, tip=tip):
                got, why = ap.landed(self.root, base, tip)
                self.assertIsNone(got)
                self.assertTrue(why)


if __name__ == '__main__':
    unittest.main()
