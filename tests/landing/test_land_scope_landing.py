"""How a landing acts on the scope check's answer (kitlib.landing.checks; docs/gate-landing.md
"Landing", step 4): a refusal before any merge with the store untouched, a NOTE before the LANDED
line, a WARNING after it while checks.SCOPE_REFUSES is False, a narrowed record's miss refused
whatever SCOPE_REFUSES says; each a whole landing. The rest of the Warns cases read the check
alone (test_land_scope.ScopeCase.check). Scratch repository and bare remote in temp only."""
import unittest
from unittest import mock

from kitlib.landing import lander as land
from tests.landing.test_land_scope import (NO_RECORD, OUTSIDE, WENT_AHEAD, PmDataCase,
                                           ScopeCase)
from tests.landing.test_land_store import pulled


class RefusesLanding(ScopeCase):
    def test_a_path_outside_the_scopes_is_refused_before_any_merge(self):
        before = self.seed(pulled('FOO', scope=['area/foo/**']), pulled('KEEP', by='kit/other'))
        _, tip = self.branch('feature', {'area/foo/x.py': 'x\n', 'b.py': 'b\n'})
        line = self.refused(self.land(tip))
        self.assertIn('kit/feature changes 1 path(s) %s (FOO): b.py; ' % OUTSIDE, line)
        self.assertIn('set checks the new members against every live pull\'s scope', line)
        self.assertIn('backlog.py" -C "<a worktree of this repository>" set FOO '
                      '--scope "area/foo/**" --scope "b.py"; or land them on their own branch',
                      line)
        self.assertEqual(self.remote_store(), before)        # nothing closed
        self.assertEqual(self.stems(), ['FOO', 'KEEP'])

    def test_paths_in_scope_tests_and_the_doc_index_land(self):
        self.seed(pulled('FOO', scope=['area/foo/**']))
        _, tip = self.branch('feature', {'area/foo/x.py': 'x\n', 'tests/test_x.py': 't\n',
                                         'docs/INDEX.md': 'i\n'})
        out = self.landed(self.land(tip))
        self.assertEqual(self.scope_lines(out), [])
        self.assertEqual(self.stems(), [])                   # FOO closed as before
        self.assertTrue(out.lines[-1].startswith('closed FOO '), out.lines)

    def test_a_branch_holding_no_record_lands_with_a_note_before_landed(self):
        self.seed(pulled('QUX', by='kit/other'))
        _, tip = self.branch('feature', {'a.py': 'x\n'})
        out = self.landed(self.land(tip))
        note = 'NOTE: kit/feature %s' % NO_RECORD
        self.assertEqual(self.scope_lines(out), [note])
        landed = [i for i, l in enumerate(out.lines) if l.startswith('LANDED ')]
        self.assertLess(out.lines.index(note), landed[0], out.lines)
        self.assertTrue(out.lines[-1].startswith('LANDED '), out.lines)


class Warns(ScopeCase):
    """SCOPE_REFUSES False (the default for now): the landing goes ahead, and the WARNING follows
    the LANDED line."""
    REFUSES = False

    def test_a_path_outside_lands_with_a_warning_after_landed(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'b.py': 'x\n'})
        out = self.landed(self.land(tip))
        self.assertEqual(self.main_now(), out.merge)
        landed = [i for i, l in enumerate(out.lines) if l.startswith('LANDED ')]
        self.assertEqual(len(landed), 1, out.lines)
        warn = out.lines[landed[0] + 1]
        self.assertTrue(warn.startswith('WARNING: '), out.lines)
        self.assertIn('kit/feature changes 1 path(s) %s (FOO): b.py;' % OUTSIDE, warn)
        self.assertIn('set FOO --scope "a/**" --scope "b.py"', warn)
        self.assertTrue(warn.endswith(WENT_AHEAD), warn)
        self.assertEqual(self.scope_lines(out), [warn])
        self.assertEqual(self.stems(), [])                   # FOO closed: the landing stands

    def test_an_unreadable_store_lands_with_a_warning(self):
        self.seed(pulled('FOO', scope=['a/**']), {'BAD': 'not a header line\n'})
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        self.warned(tip, 'cannot read the backlog store to check')

    def test_a_comma_path_its_folder_covers_lands_without_a_warning(self):
        self.seed(pulled('FOO', scope=['a/**']))
        _, tip = self.branch('feature', {'a/x,y.py': 'x\n'})
        self.clean(tip)

    def test_a_check_that_stops_lands_with_a_warning(self):
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        with mock.patch.object(land.Lander, '_scope_of', side_effect=RuntimeError('boom')):
            self.warned(tip, 'the scope check of kit/feature stopped (RuntimeError: boom)')

    # A branch holding a narrowed record (a `narrowed:` line; docs/gate-landing.md "Landing", step 4) is
    # refused on a miss even while SCOPE_REFUSES is False: the backstop for a plain git commit.

    def test_a_narrowed_record_s_miss_is_refused_naming_its_original_member(self):
        before = self.seed(pulled('FOO', scope=['src/a/**', 'notes.md'],
                                  narrowed='src/** BAR kit/bar'),
                           pulled('BAR', by='kit/bar', scope=['src/b/**'],
                                  narrowed='src/** FOO kit/feature'))
        _, tip = self.branch('feature', {'src/a/x.py': 'x\n', 'src/b/y.py': 'y\n'})
        line = self.refused(self.land(tip))
        self.assertIn('kit/feature changes 1 path(s) %s (FOO): src/b/y.py; ' % OUTSIDE, line)
        # The restore keeps notes.md and drops src/a/**, which the original src/** holds.
        self.assertIn('; FOO was narrowed off src/** for BAR: a path given to BAR waits until it '
                      'lands, then restore the original member with ', line)
        self.assertIn('set FOO --scope "src/**" --scope "notes.md" --unset narrowed; or widen',
                      line)
        self.assertNotIn('<original member>', line)
        self.assertEqual(self.remote_store(), before)        # nothing closed

    def test_every_narrowed_record_held_is_named_with_its_own_restore(self):
        self.seed(pulled('FOO', scope=['src/a/**'], narrowed='src/** BAR kit/bar'),
                  pulled('QUX', scope=['lib/a/**'],
                         narrowed=['lib/** ZED kit/zed', 'doc/** ZED kit/zed']))
        _, tip = self.branch('feature', {'other.py': 'x\n'})
        line = self.miss(tip)
        self.assertIn('FOO was narrowed off src/** for BAR: ', line)
        self.assertIn('set FOO --scope "src/**" --unset narrowed; QUX was narrowed off lib/**, '
                      'doc/** for ZED: ', line)
        self.assertIn('set QUX --scope "lib/**" --scope "doc/**" --unset narrowed; or widen', line)

    def test_the_restore_keeps_a_line_it_cannot_read_as_next_s_restore_does(self):
        self.seed(pulled('FOO', scope=['src/a/**'], narrowed=['src/** BAR kit/bar', 'broken']))
        _, tip = self.branch('feature', {'other.py': 'x\n'})
        self.assertIn('set FOO --scope "src/**" --narrowed "broken"; or widen', self.miss(tip))

    def test_a_narrowed_record_s_branch_in_scope_lands(self):
        self.seed(pulled('FOO', scope=['a/**'], narrowed='kit/** BAR kit/bar'))
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        self.clean(tip)

    def test_an_unread_store_still_only_warns(self):
        self.seed(pulled('FOO', scope=['a/**']), {'BAD': 'not a header line\n'})
        _, tip = self.branch('feature', {'b.py': 'x\n'})
        self.warned(tip, 'cannot read the backlog store to check')

    def test_a_narrowed_record_s_miss_is_refused_beside_an_unreadable_record(self):
        # The bad record may be one the branch holds, but the readable narrowed one still names
        # a part given to BAR: a plain git commit into it is refused, not only warned.
        self.seed(pulled('FOO', scope=['src/a/**'], narrowed='src/** BAR kit/bar'),
                  pulled('BAR', by='kit/bar', scope=['src/b/**']),
                  {'BAD': 'not a header line\n'})
        _, tip = self.branch('feature', {'src/a/x.py': 'x\n', 'src/b/y.py': 'y\n'})
        line = self.miss(tip)
        self.assertIn('kit/feature changes 1 path(s) %s (FOO): src/b/y.py; ' % OUTSIDE, line)
        self.assertIn('FOO was narrowed off src/** for BAR: ', line)
        self.assertIn('; or land them on their own branch; the store also held a record it could '
                      'not read, so a path in that record\'s scope may be counted outside: ', line)
        self.assertIn('1 record(s) unreadable (BAD)', line)

    def test_a_narrowed_record_beside_an_unreadable_one_lands_in_scope(self):
        self.seed(pulled('FOO', scope=['a/**'], narrowed='kit/** BAR kit/bar'),
                  {'BAD': 'not a header line\n'})
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        self.clean(tip)


class PmDataLanding(PmDataCase):
    def test_an_unread_store_is_not_named_when_the_records_read_cover_every_path(self):
        self.seed(pulled('FOO', scope=['a/**']))     # the configured store; no PM data worktree
        _, tip = self.branch('feature', {'a/x.py': 'x\n'})
        out = self.landed(self.land(tip))
        self.assertEqual(self.scope_lines(out), [])
        self.assertTrue([l for l in out.lines if 'PM data branch\'s backlog store was not read'
                         in l], out.lines)          # the PM data store was unread in this landing


if __name__ == '__main__':
    unittest.main()
