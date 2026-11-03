"""`complement` (kitlib/backlog/scope.py): the sibling members that cover a scope member minus the
paths removed from it, over the tracked tree, or None when it cannot be done."""
import unittest

from kitlib.backlog import scope as smod

from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog.helpers import StoreCase

TREE = ['kit/a.py', 'kit/b.py', 'kit/sub/c.py', 'kit/sub/d.py', 'kit/deep/e/f.py',
        'kit/deep/e/g.py', 'kit/deep/h.py', 'docs/x.md', 'docs/y.md', 'top.py']


def covered(members, path):
    return any(smod.members_collide(m, path) for m in members)


class Complement(unittest.TestCase):

    def check(self, member, removed, tree=TREE):
        out = smod.complement(member, set(removed), tree)
        self.assertIsNotNone(out)
        for m in out:
            for r in removed:
                self.assertFalse(smod.members_collide(m, r), (m, r))
        expect = set(p for p in tree if smod.members_collide(member, p)) - set(removed)
        self.assertEqual(set(p for p in tree if covered(out, p)), expect)
        return out

    def test_a_folder_member(self):
        out = self.check('kit', ['kit/a.py'])
        self.assertEqual(out, ['kit/b.py', 'kit/deep/**', 'kit/sub/**'])

    def test_a_glob_member(self):
        out = self.check('kit/**', ['kit/sub/c.py'])
        self.assertEqual(out, ['kit/a.py', 'kit/b.py', 'kit/deep/**', 'kit/sub/d.py'])

    def test_nested_removal_descends_level_by_level(self):
        out = self.check('kit/**', ['kit/deep/e/f.py'])
        self.assertEqual(out, ['kit/a.py', 'kit/b.py', 'kit/deep/e/g.py', 'kit/deep/h.py',
                               'kit/sub/**'])

    def test_several_removed_paths(self):
        self.check('kit/**', ['kit/a.py', 'kit/deep/e/g.py'])

    def test_over_the_cap_is_none(self):
        tree = ['big/f%02d.py' % i for i in range(40)]
        self.assertIsNone(smod.complement('big/**', {'big/f00.py'}, tree))
        self.assertIsNotNone(smod.complement('big/**', {'big/f00.py'}, tree[:31]))
        self.assertIsNone(smod.complement('big/**', {'big/f00.py'}, tree[:32]))

    def test_a_file_member_holding_a_removed_path_is_none(self):
        self.assertIsNone(smod.complement('docs/x.md', {'docs/x.md'}, TREE))
        self.assertIsNone(smod.complement('kit/a.py', {'kit/a.py'}, TREE))

    def test_a_file_member_without_a_removed_path_is_unchanged(self):
        self.assertEqual(smod.complement('docs/x.md', {'kit/a.py'}, TREE), ['docs/x.md'])

    def test_nothing_removed_leaves_the_member(self):
        self.assertEqual(smod.complement('kit/**', set(), TREE), ['kit/**'])

    def test_a_removed_path_outside_the_member_leaves_it(self):
        self.assertEqual(smod.complement('kit/**', {'docs/x.md'}, TREE), ['kit/**'])
        self.assertEqual(smod.complement('kit/sub', {'kit/a.py'}, TREE), ['kit/sub'])

    def test_none_is_unchanged(self):
        self.assertEqual(smod.complement('none', {'kit/a.py'}, TREE), ['none'])

    def test_removing_every_file_leaves_nothing(self):
        self.assertEqual(smod.complement('docs/**', {'docs/x.md', 'docs/y.md'}, TREE), [])


class AddBroadScope(StoreCase):
    """`add` warns, never refuses, on a scope broader than the change needs."""

    def add(self, summary, scopes, body=None):
        return self.verb('add', 'NEW', summary, scopes, found_on='none',
                         why_not_folded='it needs the owner to choose', body=body)

    def warnings(self, change):
        return [l for l in change.lines if l.startswith('WARNING:')]

    def test_a_top_level_glob_warns_and_still_adds(self):
        change = self.add('tidy the module', ['kitlib/**'])
        self.assertEqual(len(self.warnings(change)), 1, change.lines)
        self.assertIn('kitlib/**', self.warnings(change)[0])
        self.assertIsNotNone(self.get('NEW'))

    def test_a_broad_scope_with_paths_named_in_the_summary_suggests_them(self):
        change = self.add('fix kitlib/backlog/plan.py warning', ['kitlib/backlog/**'])
        w = self.warnings(change)
        self.assertEqual(len(w), 1, change.lines)
        self.assertIn('kitlib/backlog/plan.py', w[0])
        self.assertIsNotNone(self.get('NEW'))

    def test_paths_named_in_the_body_count(self):
        change = self.add('fix the warning', ['kitlib/backlog/**'],
                          body='The defect is in kitlib/backlog/scope.py.\n')
        self.assertIn('kitlib/backlog/scope.py', self.warnings(change)[0])

    def test_a_narrow_scope_does_not_warn(self):
        change = self.add('fix kitlib/backlog/plan.py', ['kitlib/backlog/plan.py'])
        self.assertEqual(self.warnings(change), [])

    def test_a_broad_nested_scope_naming_no_path_does_not_warn(self):
        change = self.add('tidy the backlog', ['kitlib/backlog/**'])
        self.assertEqual(self.warnings(change), [])

    def test_a_path_outside_the_broad_scope_does_not_warn(self):
        change = self.add('fix docs/backlog.md', ['kitlib/backlog/**'])
        self.assertEqual(self.warnings(change), [])


if __name__ == '__main__':
    unittest.main()
