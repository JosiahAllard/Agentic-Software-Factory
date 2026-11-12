"""The scope check (kitlib.landing.checks; docs/gate-landing.md "Landing", step 4) over each kind of
store and over a batch: a folder store the branch carries, the PM data branch's store and its
`<area>/` members, a local store's members as written, and a batch's branches each on its own
paths. Read through test_land_scope.ScopeCase.check, with no landing around it. Scratch repository
and bare remote in temp only."""
import os
import unittest
from unittest import mock

from kitlib.landing import lander as land
from tests.landing import test_land
from tests.landing.test_land_scope import AREA, OUTSIDE, PmDataCase, ScopeCase, delete_on_branch
from tests.landing.test_land_store import BRANCH, pulled


class Batches(ScopeCase):
    def test_a_batch_names_the_branch_outside_its_scopes(self):
        self.seed(pulled('FOO', scope=['a/**']), pulled('BAR', by='kit/other', scope=['c/**']))
        _, tip_a = self.branch('feature', {'a/x.py': 'x\n'})
        _, tip_b = self.branch('other', {'b/y.py': 'y\n'})
        line = self.miss([tip_a, tip_b], branch=[BRANCH, 'kit/other'])
        self.assertIn('kit/other changes 1 path(s) %s (BAR): b/y.py;' % OUTSIDE, line)
        self.assertNotIn('kit/feature changes', line)

    def test_a_stacked_branch_is_checked_on_its_own_paths(self):
        # kit/other starts at a1 of kit/feature and lands after it: a/x.py is kit/feature's.
        self.seed(pulled('FOO', scope=['a/**']), pulled('BAR', by='kit/other', scope=['c/**']))
        wt = self.repo.worktree('feature')
        a1 = self.commit_on(wt, 'a1', {'a/x.py': 'x\n'})
        tip_a = self.commit_on(wt, 'a2', {'a/y.py': 'y\n'})
        other = os.path.join(self.repo.root, '.claude', 'worktrees', 'other')
        self.repo.git('worktree', 'add', '-q', other, '-b', 'kit/other', a1)
        tip_b = self.commit_on(other, 'b', {'c/z.py': 'z\n'})
        self.clean([tip_a, tip_b], branch=[BRANCH, 'kit/other'])


class FolderStore(ScopeCase):
    """A folder store ('main'): its records land with the branch, read at the tip; its folder is
    always allowed."""
    KIT = dict(test_land.LandCase.KIT, backlog={'store': 'main'})

    def test_a_record_the_branch_carries_counts_and_its_folder_is_allowed(self):
        rec = pulled('FOO', scope=['a/**'])['FOO']
        _, tip = self.branch('feature', {'docs/backlog/FOO.md': rec, 'a/x.py': 'x\n'})
        self.clean(tip)

    def test_a_path_outside_is_refused(self):
        rec = pulled('FOO', scope=['a/**'])['FOO']
        _, tip = self.branch('feature', {'docs/backlog/FOO.md': rec, 'b.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('(FOO): b.py;', line)
        self.assertIn('set FOO --scope "a/**" --scope "b.py";', line)

    def test_a_record_the_branch_deleted_counts_at_its_last_version(self):
        # KEEP makes the store a read one, so a FOO not counted would give the NOTE
        self.on_main({'docs/backlog/FOO.md': pulled('FOO', scope=['a/**'])['FOO'],
                      'docs/backlog/KEEP.md': pulled('KEEP', by='kit/other')['KEEP']})
        wt = self.repo.worktree('feature')
        self.commit_on(wt, 'work', {'a/x.py': 'x\n'})
        self.repo.git('rm', '-q', 'docs/backlog/FOO.md', cwd=wt)
        self.repo.git('commit', '-q', '-m', 'close FOO', cwd=wt)
        self.clean(self.repo.head(wt))


class PmDataStore(PmDataCase):
    def test_a_record_held_there_counts(self):
        self.seeded(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        self.clean(tip)

    def test_a_path_outside_names_the_pm_data_worktree_in_the_command(self):
        wt = self.seeded(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'b.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('(FOO): b.py;', line)
        self.assertIn('backlog.py" -C "%s" set FOO --scope "a/**" --scope "b.py";'
                      % self.pm_root(wt), line)

    def test_no_worktree_on_the_pm_data_branch_refuses(self):
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('cannot read the backlog store to check kit/feature\'s paths', line)
        self.assertIn('no worktree has the PM data branch pmdata checked out', line)

    # An unread store not named when the records read cover every path: a whole landing, in
    # test_land_scope_landing.PmDataLanding (the line it checks is the landing's, not the check's).


class AreaPrefix(PmDataCase):
    """The PM data branch's store, shared by repositories, writes a member `<area>/<path>`: when
    the repository has no top-level entry named as the record's area, at the merge base or the
    tip, the member is read without it."""
    REFUSES = False

    def test_a_file_member_under_the_area_lands_that_file_without_a_warning(self):
        self.seeded(pulled('FOO', area=AREA, scope=[AREA + '/a/x.py']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        self.clean(tip)

    def test_a_folder_member_under_the_area_covers_the_files_under_it(self):
        self.seeded(pulled('FOO', area=AREA, scope=[AREA + '/k/lock', AREA + '/m/**']))
        _, tip = self.branch('feature', {'k/lock/a.py': 'a\n', 'k/lock/sub/b.py': 'b\n',
                                         'm/c.py': 'c\n'})
        self.clean(tip)

    def test_a_path_outside_still_warns_and_is_added_once_in_the_stores_form(self):
        self.seeded(pulled('FOO', area=AREA, scope=[AREA + '/a/x.py']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n', 'b.py': 'b\n'})
        warn = self.warned(tip, 'kit/feature changes 1 path(s) %s (FOO): b.py;' % OUTSIDE)
        self.assertIn(' set FOO --scope "%s/a/x.py" --scope "%s/b.py";' % (AREA, AREA), warn)
        self.assertEqual(warn.count('--scope'), 2, warn)

    def test_an_area_the_repository_has_as_a_folder_is_a_path(self):
        self.seeded(pulled('FOO', area='a', scope=['a/x.py']))
        _, tip = self.branch('feature', {'a/y.py': 'y\n', 'x.py': 'x\n'})
        warn = self.warned(tip, 'changes 2 path(s) %s (FOO): a/y.py, x.py;' % OUTSIDE)
        self.assertIn(' set FOO --scope "a/x.py" --scope "a/y.py" --scope "x.py";', warn)

    def test_an_area_folder_the_branch_deletes_is_still_a_path(self):
        self.on_main({'legacy/x.py': 'x\n', 'legacy/y.py': 'y\n'})
        self.seeded(pulled('FOO', area='legacy', scope=['legacy/**']))
        self.clean(delete_on_branch(self, 'feature', ['legacy/x.py', 'legacy/y.py']))

    def test_top_level_entries_git_cannot_list_warn(self):
        self.seeded(pulled('FOO', area=AREA, scope=[AREA + '/a/x.py']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        real = land.cmd.run_text

        def run_text(args, *a, **kw):
            return (None, 'boom') if args[0] == 'ls-tree' else real(args, *a, **kw)
        with mock.patch.object(land.cmd, 'run_text', side_effect=run_text):
            warn = self.warned(tip, 'git cannot list the top-level entries of kit/feature at ')
        self.assertIn('to read its records\' scopes as paths of this repository: boom;', warn)


class LocalStoreArea(ScopeCase):
    """A record of any store but the PM data branch's keeps every member as written: its area is
    the first component of its first member (kitlib.backlog.scope.area_from_scope), not a
    prefix."""
    REFUSES = False

    def test_a_folder_member_the_branch_deletes_covers_the_deletions(self):
        self.on_main({'legacy/x.py': 'x\n', 'legacy/y.py': 'y\n'})
        self.seed(pulled('FOO', area='legacy', scope=['legacy/**']))
        self.clean(delete_on_branch(self, 'feature', ['legacy/x.py', 'legacy/y.py']))

    def test_a_member_keeps_its_first_component(self):
        self.seed(pulled('FOO', area='legacy', scope=['legacy/docs/**', 'docs/new.md']))
        _, tip = self.branch('feature', {'docs/new.md': 'n\n', 'docs/other.md': 'o\n'})
        warn = self.warned(tip, '(FOO): docs/other.md;')
        self.assertIn(' set FOO --scope "legacy/docs/**" --scope "docs/new.md" --scope '
                      '"docs/other.md";', warn)


if __name__ == '__main__':
    unittest.main()
