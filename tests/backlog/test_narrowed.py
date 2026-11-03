"""The `narrowed:` record line and commit.py's narrowed-record check (kitlib/backlog/narrowed.py;
docs/commit.md "A narrowed record"): its shape, and which committed paths it refuses."""
import unittest
from unittest import mock

from kitlib.backlog import narrowed as nmod
from kitlib.backlog import record as rmod
from tests.backlog.helpers import messages, record_text

RULES = rmod.Rules()
BRANCH = 'kit/c'
OTHER = 'kit/l'


def record(rid, **over):
    return rmod.parse('backlog/%s.md' % rid, record_text(id=rid, **over), stem=rid)


def pulled(rid, by, scope, **over):
    return record(rid, state='pulled', pulled_at='2026-11-03', pulled_by=by, scope=scope, **over)


def pair(c_scope=('kit/a/**',), l_state='pulled', c_narrowed='kit/** L-ONE kit/l'):
    """C pulled on kit/c, narrowed off `kit/**` for L-ONE; L-ONE pulled on kit/l holding kit/b."""
    c = pulled('C-ONE', BRANCH, list(c_scope), narrowed=c_narrowed)
    if l_state == 'pulled':
        other = pulled('L-ONE', OTHER, ['kit/b/**'], narrowed='kit/** C-ONE kit/c')
    else:
        other = record('L-ONE', state=l_state, scope='kit/b/**')
    return [c, other]


class TheLine(unittest.TestCase):

    def test_a_well_formed_line_passes_check(self):
        self.assertEqual(messages(narrowed='kitlib/backlog/** KIT-OTHER kit/other'), [])
        self.assertEqual(messages(narrowed=['kitlib/** KIT-A kit/a', 'docs/x.md KIT-B kit/b']), [])

    def test_a_member_with_a_space_splits_at_the_last_two_spaces(self):
        self.assertEqual(rmod.split_narrowed('My Docs/** KIT-A kit/a'),
                         ('My Docs/**', 'KIT-A', 'kit/a'))
        self.assertEqual(messages(narrowed='My Docs/** KIT-A kit/a'), [])

    def test_a_malformed_line_fails_check(self):
        for bad in ('kitlib/**', 'kitlib/** KIT-A', 'kitlib/*.py KIT-A kit/a',
                    'kitlib/** kit-a kit/a', 'none KIT-A kit/a', '/abs/** KIT-A kit/a',
                    'kitlib/** KIT-A -kit/a'):
            self.assertTrue(messages(narrowed=bad), bad)
            self.assertIsNotNone(rmod._narrowed(bad, RULES), bad)

    def test_narrowed_returns_the_lines_and_the_errors(self):
        rec = record('C-ONE', narrowed=['kit/** L-ONE kit/l', 'kit/**'])
        lines, errors = rmod.narrowed(rec, RULES)
        self.assertEqual(lines, [('kit/**', 'L-ONE', 'kit/l')])
        self.assertEqual(len(errors), 1)


class Hits(unittest.TestCase):

    def hits(self, paths, records=None, branch=BRANCH):
        return nmod.hits(records if records is not None else pair(), branch, paths, RULES)

    def test_a_path_given_to_the_live_other_record_is_refused_with_the_fixed_text(self):
        refusal, warnings = self.hits(['kit/b/x.py'])
        self.assertEqual(refusal, [
            'REFUSED: needs kit/b/x.py, held by L-ONE on kit/l (narrowed for C-ONE). Stop and '
            'report "needs kit/b/x.py, held by L-ONE". Instead: backlog.py block C-ONE --on '
            'item:L-ONE --reason "needs kit/b/x.py"'])
        self.assertEqual(warnings, [])

    def test_both_sides_of_a_rename_across_are_each_refused(self):
        refusal, _ = self.hits(['kit/a/x.py', 'kit/b/x.py', 'kit/b/y.py'])
        self.assertEqual([l.split(',')[0] for l in refusal],
                         ['REFUSED: needs kit/b/x.py', 'REFUSED: needs kit/b/y.py'])

    def test_a_path_in_the_split_member_that_neither_record_holds_is_refused(self):
        refusal, _ = self.hits(['kit/c/new.py'])
        self.assertEqual(len(refusal), 1)
        self.assertIn('needs kit/c/new.py, held by L-ONE', refusal[0])

    def test_the_record_s_own_scope_and_paths_outside_every_original_member_pass(self):
        self.assertEqual(self.hits(['kit/a/x.py', 'KIT/A/y.py', 'tools/x.py', 'docs/a.md']),
                         ([], []))

    def test_the_other_record_no_longer_pulled_lets_the_path_through(self):
        for state in ('closed', 'open'):
            self.assertEqual(self.hits(['kit/b/x.py'], pair(l_state=state)), ([], []), state)
        self.assertEqual(self.hits(['kit/b/x.py'], pair()[:1]), ([], []))

    def test_only_the_committing_branch_s_records_count(self):
        self.assertEqual(self.hits(['kit/b/x.py'], branch='kit/other'), ([], []))
        self.assertEqual(self.hits(['kit/b/x.py'], branch=''), ([], []))
        # The other side, committing on kit/l, is refused on the part C took.
        refusal, _ = self.hits(['kit/a/x.py'], branch=OTHER)
        self.assertEqual(len(refusal), 1)
        self.assertIn('held by C-ONE on kit/c (narrowed for L-ONE)', refusal[0])

    def test_a_record_with_no_narrowed_line_is_not_checked(self):
        records = [pulled('C-ONE', BRANCH, ['kit/a/**']), pair()[1]]
        self.assertEqual(self.hits(['kit/b/x.py'], records), ([], []))

    def test_a_malformed_line_on_the_branch_s_record_refuses_every_commit(self):
        refusal, _ = self.hits(['tools/x.py'], pair(c_narrowed='kit/** L-ONE'))
        self.assertEqual(len(refusal), 1)
        self.assertTrue(refusal[0].startswith('REFUSED: C-ONE carries a narrowed: line'),
                        refusal[0])

    def test_a_refused_rename_side_names_the_other(self):
        renames = {'kit/b/x.py': ('kit/a/x.py', nmod.RENAMED_FROM),
                   'kit/a/x.py': ('kit/b/x.py', nmod.RENAMED_TO)}
        refusal, _ = nmod.hits(pair(), BRANCH, ['kit/a/x.py', 'kit/b/x.py'], RULES,
                               renames=renames)
        self.assertEqual(refusal, [
            'REFUSED: needs kit/b/x.py (rename of kit/a/x.py), held by L-ONE on kit/l (narrowed '
            'for C-ONE). Stop and report "needs kit/b/x.py, held by L-ONE". Instead: backlog.py '
            'block C-ONE --on item:L-ONE --reason "needs kit/b/x.py"'])
        refusal, _ = nmod.hits(pair(), BRANCH, ['kit/b/x.py'], RULES,
                               renames={'kit/b/x.py': ('kit/a/x.py', nmod.RENAMED_TO)})
        self.assertIn('needs kit/b/x.py (renamed to kit/a/x.py), held by L-ONE', refusal[0])

    def test_paths_compare_as_the_landing_scope_check_compares_them(self):
        # One copy: the landing check's comparisons are scope's own.
        from kitlib.gate import land
        self.assertIs(land._covers, nmod.scmod.covers)
        self.assertIs(land._in_repo, nmod.scmod.in_repo)
        # A path the grammar cannot decide is covered by no member: not refused here (the
        # landing check lists it as undecidable).
        self.assertEqual(self.hits(['kit/a/../b/x.py']), ([], []))

    def test_an_unreadable_record_is_a_warning_not_a_refusal(self):
        bad = rmod.unreadable('backlog/X-ONE.md', 'X-ONE', 'not utf-8')
        refusal, warnings = self.hits(['tools/x.py'], pair() + [bad])
        self.assertEqual(refusal, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn('X-ONE', warnings[0])


class TheAreaPrefix(unittest.TestCase):
    """The PM data store (shared by repositories) writes a member `<area>/<path>`; the check reads
    it as the landing check does (scope.in_repo)."""

    def records(self):
        c = pulled('C-ONE', BRANCH, ['game/src/a/**'], area='game',
                   narrowed='game/src/** L-ONE kit/l')
        other = pulled('L-ONE', OTHER, ['game/src/b/**'], area='game')
        return [c, other]

    def test_an_area_prefixed_member_covers_the_repository_path(self):
        tops = {'src', 'docs'}
        refusal, _ = nmod.hits(self.records(), BRANCH, ['src/b/x.py'], RULES, True, tops)
        self.assertEqual(len(refusal), 1)
        self.assertIn('needs src/b/x.py, held by L-ONE', refusal[0])
        self.assertEqual(nmod.hits(self.records(), BRANCH, ['src/a/x.py', 'tools/x.py'], RULES,
                                   True, tops), ([], []))

    def test_an_area_that_names_a_top_level_entry_is_a_path(self):
        tops = {'game', 'src'}
        self.assertEqual(nmod.hits(self.records(), BRANCH, ['src/b/x.py'], RULES, True, tops),
                         ([], []))
        refusal, _ = nmod.hits(self.records(), BRANCH, ['game/src/b/x.py'], RULES, True, tops)
        self.assertEqual(len(refusal), 1)

    def test_another_store_keeps_its_members_as_written(self):
        self.assertEqual(nmod.hits(self.records(), BRANCH, ['src/b/x.py'], RULES), ([], []))


class TheStoreLookup(unittest.TestCase):
    """read takes the store backlog.py opens (store.resolve_store); a PM data worktree it cannot
    find or read is a WARNING, never a silent fall back to the worktree's own store."""

    def test_a_pm_data_worktree_that_cannot_be_read_warns(self):
        why = 'no worktree has the PM data branch pmdata checked out'
        with mock.patch.object(nmod.gitrepo, 'toplevel', return_value=('/repo', None)), \
                mock.patch.object(nmod.smod, 'resolve_store',
                                  return_value=('/repo', None, RULES, None, why)), \
                mock.patch.object(nmod.smod, 'open_backend',
                                  side_effect=AssertionError('read the fallback')):
            refusal, warnings = nmod.check('/repo', ['kit/b/x.py'])
        self.assertEqual(refusal, [])
        self.assertEqual(warnings, [nmod.UNREAD % ('Failure: the PM data worktree\'s store cannot '
                                                   'be opened: %s' % why)])

    def test_worktree_store_says_why_it_kept_the_own_store(self):
        cfg = mock.Mock()
        cfg.written.return_value = None
        smod = nmod.smod
        with mock.patch.object(smod, 'worktree_branch', return_value='kit/x'):
            with mock.patch.object(smod, 'pm_data_live_store',
                                   return_value=(None, None, 'cannot find it')):
                self.assertEqual(smod.worktree_store('/wt', cfg), (None, 'cannot find it'))
                self.assertIsNone(smod.worktree_live_store('/wt', cfg))
            with mock.patch.object(smod, 'pm_data_live_store',
                                   return_value=('/pm', None, 'bad json')):
                live, why = smod.worktree_store('/wt', cfg)
                self.assertIsNone(live)
                self.assertIn('bad json', why)
                self.assertIn('/pm', why)
            with mock.patch.object(smod, 'pm_data_live_store',
                                   return_value=('/pm', 'branch', None)):
                self.assertEqual(smod.worktree_store('/wt', cfg), (('/pm', 'kit/x'), None))
            with mock.patch.object(smod, 'pm_data_live_store', return_value=(None, None, None)):
                self.assertEqual(smod.worktree_store('/wt', cfg), (None, None))

    def test_the_live_store_is_read_as_shared(self):
        records = TheAreaPrefix().records()
        snap = mock.Mock(records=records)
        backend = mock.Mock()
        backend.load.return_value = snap
        with mock.patch.object(nmod.gitrepo, 'toplevel', return_value=('/repo', None)), \
                mock.patch.object(nmod.smod, 'resolve_store',
                                  return_value=('/pm', mock.Mock(), RULES, BRANCH, None)), \
                mock.patch.object(nmod.smod, 'open_backend', return_value=backend), \
                mock.patch.object(nmod, 'top_entries', return_value={'src'}):
            refusal, warnings = nmod.check('/repo', ['src/b/x.py'])
        self.assertEqual(warnings, [])
        self.assertEqual(len(refusal), 1, refusal)


class FailOpen(unittest.TestCase):

    def test_a_store_that_cannot_be_read_is_a_warning(self):
        with mock.patch.object(nmod, 'read', side_effect=OSError('store down')):
            refusal, warnings = nmod.check('.', ['kit/b/x.py'])
        self.assertEqual(refusal, [])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith('WARNING: the narrowed-record check could not '
                                               'read the backlog store (OSError: store down)'),
                        warnings[0])

    def test_no_paths_reads_nothing(self):
        with mock.patch.object(nmod, 'read', side_effect=AssertionError('read')):
            self.assertEqual(nmod.check('.', []), ([], []))


if __name__ == '__main__':
    unittest.main()


class MergeLines(unittest.TestCase):
    """nmod.merge_lines: a record narrowed again keeps one line per (original member, other id)."""

    def test_a_new_line_replaces_the_old_one_for_the_same_member_and_other(self):
        self.assertEqual(nmod.merge_lines(['kit/** L-ONE kit/old', 'docs/** M-ONE kit/m'],
                                          [('kit/**', 'L-ONE', 'kit/l')]),
                         ['kit/** L-ONE kit/l', 'docs/** M-ONE kit/m'])

    def test_a_line_for_another_member_or_other_is_added(self):
        self.assertEqual(nmod.merge_lines(['kit/** L-ONE kit/l'],
                                          [('kit/**', 'M-ONE', 'kit/m'), ('src/**', 'L-ONE', 'kit/l')]),
                         ['kit/** L-ONE kit/l', 'kit/** M-ONE kit/m', 'src/** L-ONE kit/l'])

    def test_a_malformed_old_line_is_kept_and_duplicates_collapse(self):
        self.assertEqual(nmod.merge_lines(['kit/**'], [('a/**', 'L-ONE', 'kit/l'),
                                                       ('a/**', 'L-ONE', 'kit/l')]),
                         ['kit/**', 'a/** L-ONE kit/l'])


class RestoredScope(unittest.TestCase):
    """nmod.restored_scope: the original members back, plus every member they do not hold."""

    def test_the_originals_come_back_and_members_inside_them_go(self):
        self.assertEqual(nmod.restored_scope(['src/a/**', 'src/b.py', 'docs/x.md'], ['src/**']),
                         ['src/**', 'docs/x.md'])

    def test_none_is_dropped(self):
        self.assertEqual(nmod.restored_scope(['none'], ['src/**']), ['src/**'])
