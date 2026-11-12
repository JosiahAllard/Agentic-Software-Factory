"""kitlib.landing.checks's scope check (docs/gate-landing.md "Landing", step 4): every path a branch changes
must fall in the scope of a record it holds - pulled by it, or named by a `Closes:` trailer of its
own commits - besides tests/**, docs/INDEX.md and a folder store's own folder. With
checks.SCOPE_REFUSES a miss is REFUSED before any merge; without it the landing goes ahead with a
WARNING after the LANDED line. Scratch repository and bare remote in temp only.

The scope check's answers are read through ScopeCase.check (Lander.preconditions, then
Lander.scope_check: what Lander.locked_checks ends with), with no landing around it; a full
landing on real git costs a hundred-odd git starts. How a landing acts on an answer (REFUSED
before any merge, the NOTE before LANDED, the WARNING after it) is in test_land_scope_landing.py;
the store kinds and batches in test_land_scope_stores.py."""
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.backlog import record as rmod
from kitlib.backlog import scope as scmod
from kitlib.landing import lander as land
from kitlib.landing import checks
from tests.landing import test_land_store as tls
from tests.landing.test_land_store import BRANCH, StoreLandCase, pulled

TRAILERS = 'Co-Authored-By: Test Model <noreply@anthropic.com>'
OUTSIDE = 'outside the scopes of the records it holds'
NO_RECORD = 'holds no pulled record; its paths were not checked against a scope'
WENT_AHEAD = ('; the landing went ahead: the scope check only warns for now '
              '(kitlib.landing.checks SCOPE_REFUSES)')
AREA = 'tool-kit'           # an area that names no folder of the repository under test


class ScopeCase(StoreLandCase):
    REFUSES = True

    def setUp(self):
        StoreLandCase.setUp(self)
        patch = mock.patch.object(checks, 'SCOPE_REFUSES', self.REFUSES)
        patch.start()
        self.addCleanup(patch.stop)

    # ---- the scope check alone

    def check(self, tip, branch=BRANCH):
        """(scope_check's refusal or None, its NOTE lines, its WARNING lines) for `branch` at
        `tip` (lists for a batch), from a Lander past its preconditions: no booking, fetch,
        merge, gate, push or close."""
        lander = land.Lander(self.repo.root, branch, self.main, tip, git_env=self.env,
                             book_kw=self.book_kw, log_dir=self.logs)
        self.assertIsNone(lander.preconditions())
        return lander.scope_check(), lander.scope_notes, lander.scope_warnings

    def miss(self, tip, branch=BRANCH):
        """The refusal scope_check gives (no NOTE, no WARNING beside it)."""
        why, notes, warnings = self.check(tip, branch)
        self.assertIsNotNone(why)
        self.assertEqual((notes, warnings), ([], []), why)
        return why

    def clean(self, tip, branch=BRANCH):
        """scope_check passes `tip` with nothing to say."""
        self.assertEqual(self.check(tip, branch), (None, [], []))

    def warned(self, tip, text):
        """SCOPE_REFUSES False: no refusal, one WARNING holding `text`: the warning."""
        why, notes, warnings = self.check(tip)
        self.assertIsNone(why)
        self.assertEqual(notes, [])
        self.assertEqual(len(warnings), 1, warnings)
        warn = warnings[0]
        self.assertTrue(warn.startswith('WARNING: '), warn)
        self.assertIn(text, warn)
        self.assertTrue(warn.endswith(WENT_AHEAD), warn)
        return warn

    # ---- a whole landing (test_land_scope_landing.py, test_land_scope_restore.py)

    def land(self, tip, branch=BRANCH, expect_main=None):
        return self.lander(branch, expect_main or self.main, tip).run()

    def landed(self, out):
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        return out

    def refused(self, out):
        self.assert_untouched(out, G.REFUSED)
        self.assertEqual(len(out.lines), 1, out.lines)
        self.assertTrue(out.lines[0].startswith('REFUSED: '), out.lines)
        self.assertTrue(out.lines[0].endswith('; nothing was merged'), out.lines)
        return out.lines[0]

    def scope_lines(self, out):
        return [l for l in out.lines if OUTSIDE in l or NO_RECORD in l or 'scope check' in l]

    # ---- branches

    def commit_on(self, wt, message, files):
        for rel, text in files.items():
            self.repo.write(rel, text, root=wt)
        return self.repo.commit(message, *files, cwd=wt)

    def literal_branch(self, name, files):
        """self.branch, each path staged by its top component: `[x]` in a pathspec is a glob to
        git add, and the fake git takes no `:(literal)` magic."""
        wt = self.repo.worktree(name)
        for rel, text in files.items():
            self.repo.write(rel, text, root=wt)
        tops = sorted({p.split('/')[0] for p in files})
        return wt, self.repo.commit('work on %s' % name, *tops, cwd=wt)

    def on_main(self, files, message='main work'):
        for rel, text in files.items():
            self.repo.write(rel, text)
        self.main = self.repo.commit(message, *files)
        self.repo.git('push', '-q', 'origin', 'main')
        return self.main


class PmDataCase(ScopeCase):
    """The PM data branch's folder store (test_land_store.PmDataClose's set-up)."""
    pm_worktree = tls.PmDataClose.pm_worktree
    pm_root = tls.PmDataClose.pm_root

    def setUp(self):
        tls.PmDataClose.setUp(self)
        patch = mock.patch.object(checks, 'SCOPE_REFUSES', self.REFUSES)
        patch.start()
        self.addCleanup(patch.stop)

    def seeded(self, *records):
        wt = self.pm_worktree(*records)
        self.repo.commit('seed the PM data store', 'kit/backlog', '.kit.json', cwd=wt)
        return wt


def delete_on_branch(case, name, paths):
    """The tip of branch kit/<name> after one commit deleting `paths`."""
    wt = case.repo.worktree(name)
    for p in paths:
        case.repo.git('rm', '-q', p, cwd=wt)
    case.repo.git('commit', '-q', '-m', 'delete', cwd=wt)
    return case.repo.head(wt)


class Refuses(ScopeCase):
    def test_members_compare_whole_components(self):
        self.seed(pulled('FOO', scope=['src/Foo.py']))
        _, tip = self.branch('feature', {'src/Foobar.py': 'x\n'})
        self.assertIn('(FOO): src/Foobar.py;', self.miss(tip))

    def test_members_compare_casefolded(self):
        self.seed(pulled('FOO', scope=['SRC/foo/**']))
        _, tip = self.branch('feature', {'src/FOO/x.py': 'x\n'})
        self.clean(tip)

    def test_the_scopes_of_every_record_held_are_one_allowance(self):
        self.seed(pulled('FOO', scope=['a/**']), pulled('BAR', scope=['b/**']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n', 'b/y.py': 'y\n'})
        self.clean(tip)

    def test_the_paths_outside_name_every_record_held(self):
        self.seed(pulled('FOO', scope=['a/**']), pulled('BAR', scope=['b/**']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n', 'c/z.py': 'z\n'})
        line = self.miss(tip)
        self.assertIn('changes 1 path(s) %s (BAR, FOO): c/z.py;' % OUTSIDE, line)
        self.assertIn('set BAR --scope "b/**" --scope "c/z.py";', line)

    def test_none_covers_nothing(self):
        self.seed(pulled('FOO', scope=['none']))
        _, tip = self.branch('feature', {'a.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('(FOO): a.py;', line)
        self.assertIn('set FOO --scope "a.py";', line)       # `none` is not kept beside a path

    def test_more_paths_than_shown_name_the_diff_that_lists_them(self):
        self.seed(pulled('FOO', scope=['a/**']))
        files = {'z/f%02d.py' % i: 'x\n' for i in range(checks.SCOPE_SHOWN + 2)}
        _, tip = self.branch('feature', files)
        line = self.miss(tip)
        self.assertIn('changes %d path(s)' % (checks.SCOPE_SHOWN + 2), line)
        self.assertIn('z/f%02d.py, and 2 more (git diff --name-only --no-renames %s %s);'
                      % (checks.SCOPE_SHOWN - 1, self.main, tip), line)
        self.assertNotIn('z/f%02d.py' % checks.SCOPE_SHOWN, line)
        self.assertIn('set FOO --scope "a/**" --scope "<each path, or a folder/** that covers '
                      'them>";', line)

    def test_a_record_main_pulled_and_named_by_a_trailer_counts(self):
        self.seed(pulled('D2', by='main', scope=['d2/**']))
        wt = self.repo.worktree('feature')
        tip = self.commit_on(wt, 'fix\n\nCloses: D2\n%s\n' % TRAILERS, {'d2/x.py': 'x\n'})
        self.clean(tip)

    def closed_early(self):
        """EARLY, pulled from main and closed already in the store; KEEP stays."""
        self.seed(pulled('EARLY', by='main', scope=['e/**']), pulled('KEEP', by='kit/other'))
        backend = self.backend()
        backend.publish(backend.load(rmod.Rules()), {'EARLY': None}, 'closes EARLY: done\n')
        self.assertEqual(self.stems(), ['KEEP'])

    def test_a_record_closed_early_and_named_by_a_trailer_counts(self):
        self.closed_early()
        wt = self.repo.worktree('feature')
        tip = self.commit_on(wt, 'fix\n\nCloses: EARLY\n%s\n' % TRAILERS, {'e/x.py': 'x\n'})
        self.clean(tip)

    def test_a_record_closed_early_and_not_named_holds_nothing(self):
        self.closed_early()
        _, tip = self.branch('feature', {'e/x.py': 'x\n'})
        self.assertEqual(self.check(tip), (None, ['NOTE: kit/feature %s' % NO_RECORD], []))

    def test_main_merged_into_the_branch_brings_no_path_of_its_own(self):
        self.seed(pulled('FOO', scope=['a/**']))
        wt, _ = self.branch('feature', {'a/x.py': 'x\n'})
        self.on_main({'b.py': 'b\n'})
        self.repo.git('merge', '-q', '--no-edit', 'main', cwd=wt)
        self.clean(self.repo.head(wt))

    def test_a_rename_out_of_scope_names_both_paths(self):
        self.on_main({'b/x.py': 'x\n', 'a/keep.py': 'k\n'})     # the fake git mv needs a/
        self.seed(pulled('FOO', scope=['a/**']))
        wt = self.repo.worktree('feature')
        self.repo.git('mv', 'b/x.py', 'a/x.py', cwd=wt)
        self.repo.git('commit', '-q', '-m', 'move', cwd=wt)
        self.assertIn('(FOO): b/x.py;', self.miss(self.repo.head(wt)))

    def test_a_comma_or_wildcard_path_is_covered_by_its_folder(self):
        # NTFS forbids `*?` in a file name; `[]{}` and the comma reach the same code
        self.seed(pulled('FOO', scope=['a/**', 'B']))
        _, tip = self.literal_branch('feature', {'a/x,y.py': 'x\n', 'a/[x]{y}.py': 'y\n',
                                                 'b/q,[r].py': 'q\n'})
        self.clean(tip)

    def test_a_comma_path_in_tests_is_always_allowed(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n', 'tests/q,r.py': 'q\n'})
        self.clean(tip)

    def test_a_path_a_scope_cannot_name_is_outside_and_left_out_of_the_command(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.literal_branch('feature', {'b/x,y.py': 'x\n', 'b/[z].py': 'z\n',
                                                 'c.py': 'c\n'})
        line = self.miss(tip)
        self.assertIn('(FOO): b/[z].py (no scope member can name it), b/x,y.py (no scope member '
                      'can name it), c.py;', line)
        self.assertNotIn('undecidable', line)
        self.assertIn('set FOO --scope "a/**" --scope "c.py"; or land them on their own branch',
                      line)

    def test_an_undecidable_path_is_outside_and_left_out_of_the_command(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'None': 'n\n'})        # NTFS allows the name
        line = self.miss(tip)
        self.assertIn('(FOO): None (undecidable: a path that reads as the', line)
        self.assertNotIn('--scope "None"', line)
        self.assertTrue(line.endswith('; land them on their own branch'), line)

    def test_only_paths_no_member_can_name_say_land_them_on_their_own_branch(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'x,y.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('(FOO): x,y.py (no scope member can name it); land them on their own '
                      'branch', line)
        self.assertNotIn('--scope', line)

    def test_no_store_says_nothing(self):
        _, tip = self.branch('feature', {'a.py': 'x\n'})
        self.clean(tip)

    def test_an_unreadable_store_refuses(self):
        self.seed(pulled('FOO', scope=['a/**']), {'BAD': 'not a header line\n'})
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('cannot read the backlog store to check kit/feature\'s paths against the '
                      'scopes of the records it holds', line)
        self.assertIn('BAD', line)

    def test_a_check_that_stops_refuses(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        with mock.patch.object(land.Lander, '_scope_of', side_effect=RuntimeError('boom')):
            line = self.miss(tip)
        self.assertIn('the scope check of kit/feature stopped (RuntimeError: boom)', line)


class Default(unittest.TestCase):
    def test_the_scope_check_warns_for_now(self):
        self.assertIs(checks.SCOPE_REFUSES, False)
        self.assertEqual(checks.SCOPE_ALWAYS, ('tests/**', 'docs/INDEX.md'))


class LiteralPaths(unittest.TestCase):
    """A changed path is literal: only the scope member is read in the scope grammar
    (checks._covers), while kitlib.backlog.scope keeps reading a comma or a wildcard in a member as
    a parse error."""

    def test_a_folder_member_covers_a_comma_or_wildcard_path(self):
        for path in ('a/x,y.py', 'a/[x].py', 'a/{x}.py', 'a/*.py', 'a/x?.py', 'a/b,c/d.py'):
            self.assertTrue(checks._covers(['a/**'], path), path)
            self.assertTrue(checks._covers(['A'], path), path)      # components, casefolded
            self.assertFalse(checks._covers(['b/**'], path), path)
            self.assertIsNone(checks._path_error(path), path)
            self.assertTrue(checks._unnamed(path), path)

    def test_a_member_holding_a_wildcard_or_comma_still_covers_nothing(self):
        self.assertFalse(checks._covers(['a/[x].py'], 'a/[x].py'))
        self.assertFalse(checks._covers(['a/x,y.py'], 'a/x,y.py'))
        self.assertFalse(checks._covers(['none'], 'none'))

    def test_a_path_a_member_can_name_compares_as_before(self):
        self.assertTrue(checks._covers(['src/foo.py'], 'SRC/Foo.py'))
        self.assertFalse(checks._covers(['src/Foo.py'], 'src/Foobar.py'))
        self.assertFalse(checks._unnamed('src/foo.py'))

    def test_an_undecidable_path_stays_undecidable_and_covered_by_nothing(self):
        for path in ('a\\b.py', 'c:x', 'a//b', '../a', ' a', 'None', 'a/b\nc'):
            self.assertIsNotNone(checks._path_error(path), path)
            self.assertFalse(checks._covers(['a/**', 'tests/**', 'c:x'], path), path)

    def test_the_backlog_scope_grammar_is_unchanged(self):
        self.assertIn('comma', scmod.path_shape_error('a/x,y.py'))
        self.assertIsNotNone(scmod.scope_member_error('a/x,y.py'))
        self.assertIsNotNone(scmod.scope_member_error('a/[x].py'))
        for path in ('a/x,y.py', 'a/[x].py'):
            with self.assertRaises(scmod.UndecidableScope):
                scmod.members_collide('a/**', path)


if __name__ == '__main__':
    unittest.main()
