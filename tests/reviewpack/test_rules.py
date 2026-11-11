"""kitlib.reviewpack's pure parts, no git: the owner-approval rows, trailer parsing, the risk map
and the CLI's argument rules; the owner-only path rules themselves are tests/config/test_owner.py's,
the trailers rule tests/trailerscheck/test_classify.py's."""
import contextlib
import io
import unittest

from kitlib import reviewpack
from kitlib.config.owner import Rules
from tests.reviewpack.repo import AGENT, cli

NOT_OWNER = 'authored by %s, not the owner' % AGENT
BY_OWNER = None                 # not_owner for a commit the owner authored


def rows(*commits):
    """owner_approval_rows over (sha, paths, values[, not_owner]) records, default rules; the
    commit is an agent's unless not_owner is given."""
    return reviewpack.owner_approval_rows(
        [c if len(c) == 4 else c + (NOT_OWNER,) for c in commits], Rules())


def statuses(sha, paths, values):
    return [(status, subject) for _, status, subject, _ in rows((sha, paths, values))]


class TrailerNamesADifferentPath(unittest.TestCase):
    def test_the_claude_blanket_does_not_approve_claude_md(self):
        self.assertEqual(statuses('a1b2c3d', ['CLAUDE.md'], ['.claude/']),
                         [('MISSING', 'CLAUDE.md'), ('UNMATCHED', 'Owner-Approved .claude/')])

    def test_a_trailer_for_a_path_the_commit_does_not_touch_is_reported(self):
        self.assertEqual(statuses('a1b2c3d', ['docs/notes.md'], ['CLAUDE.md']),
                         [('UNMATCHED', 'Owner-Approved CLAUDE.md')])

    def test_the_details_name_both_sides(self):
        [row] = rows(('a1b2c3d', ['docs/notes.md'], ['CLAUDE.md']))
        self.assertIn('docs/notes.md', row[3])
        missing = next(r for r in rows(('a1b2c3d', ['CLAUDE.md'], ['.claude/'])) if r[1] == 'MISSING')
        self.assertIn('Owner-Approved .claude/', missing[3])


class HubApprovalRows(unittest.TestCase):
    def test_a_merge_with_a_hub_trailer_is_missing_and_lists_it(self):
        [row] = reviewpack.owner_approval_rows(
            [('a', ['CLAUDE.md'], [], NOT_OWNER, ['hub:A-001'], True)], Rules())
        self.assertEqual(row[1], 'MISSING')
        self.assertIn('Owner-Approval hub:A-001', row[3])
        self.assertNotIn('none', row[3])

    def test_the_same_trailer_on_a_plain_commit_is_ok(self):
        [row] = reviewpack.owner_approval_rows(
            [('a', ['CLAUDE.md'], [], NOT_OWNER, ['hub:A-001'], False)], Rules())
        self.assertEqual(row[1], 'ok')


class TheOwnersCommitIsJudgedAsTheLandingJudgesIt(unittest.TestCase):
    """kitlib.guard.ownercommits never counts a commit the owner authored, trailer or none; the
    table must not print MISSING for one (seen on owner commits that landed)."""

    def test_the_owners_commit_with_no_trailer_is_ok(self):
        [row] = rows(('a', ['CLAUDE.md'], [], BY_OWNER))
        self.assertEqual(row[1], 'ok', row)
        self.assertIn('authored by the owner', row[3])

    def test_an_agents_commit_with_no_trailer_is_missing(self):
        [row] = rows(('a', ['CLAUDE.md'], []))
        self.assertEqual(row[1], 'MISSING', row)
        self.assertIn(NOT_OWNER, row[3])

    def test_the_owners_trailer_is_still_shown(self):
        self.assertEqual(rows(('a', ['CLAUDE.md', '.claude/agent-profile.md'], ['CLAUDE.md'],
                               BY_OWNER)),
                         [('a', 'ok', 'CLAUDE.md', 'Owner-Approved CLAUDE.md'),
                          ('a', 'ok', '.claude/agent-profile.md',
                           'authored by the owner (the landing judges by author email)')])


class ApprovalIsPerCommit(unittest.TestCase):
    def test_a_sibling_commits_trailer_does_not_approve_this_one(self):
        got = rows(('a1b2c3d', ['CLAUDE.md'], ['CLAUDE.md'], BY_OWNER),
                   ('e4f5a6b', ['CLAUDE.md'], []))
        self.assertEqual([(sha, status) for sha, status, _, _ in got],
                         [('a1b2c3d', 'ok'), ('e4f5a6b', 'MISSING')])
        self.assertIn('none', got[1][3])

    def test_a_blanket_trailer_shows_on_every_path_it_covers(self):
        got = rows(('a', ['.claude/a.md', '.claude/agents/w.md'], ['.claude/'], BY_OWNER))
        self.assertEqual([(r[1], r[2], r[3]) for r in got],
                         [('ok', '.claude/a.md', 'Owner-Approved .claude/'),
                          ('ok', '.claude/agents/w.md', 'Owner-Approved .claude/')])

    def test_a_trailer_on_a_commit_not_provably_the_owners_approves_nothing(self):
        [row] = rows(('a', ['CLAUDE.md'], ['CLAUDE.md'], 'authored by x, not the owner'))
        self.assertEqual(row[1], 'MISSING')
        self.assertIn('is on a commit authored by x, not the owner', row[3])

    def test_the_configured_trailer_key_names_the_rows(self):
        got = reviewpack.owner_approval_rows([('a', ['f.txt'], ['CLAUDE.md'], None)],
                                             Rules(trailer='Owner-OK'))
        self.assertEqual(got[0][2], 'Owner-OK CLAUDE.md')


class TrailerParsing(unittest.TestCase):
    def test_it_reads_the_values_in_order(self):
        msg = ('subject\n\nbody\n\nOwner-Approved: CLAUDE.md\nOwner-Approved: .claude/\n'
               'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n')
        self.assertEqual(reviewpack.trailers_of(msg), ['CLAUDE.md', '.claude/'])

    def test_a_windows_separator_names_the_same_path(self):
        self.assertEqual(reviewpack.trailers_of('s\n\nOwner-Approved: .claude\\agent-profile.md'),
                         ['.claude/agent-profile.md'])

    def test_a_body_sentence_that_quotes_the_phrase_is_not_a_trailer(self):
        msg = ('subject\n\nThe fix requires an Owner-Approved: CLAUDE.md trailer.\n\n'
               'Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n')
        self.assertEqual(reviewpack.trailers_of(msg), [])
        self.assertEqual(reviewpack.trailers_of(msg.rstrip('\n') + '\nOwner-Approved: CLAUDE.md'),
                         ['CLAUDE.md'])

    def test_a_longer_key_that_starts_with_the_name_is_not_the_trailer(self):
        self.assertEqual(reviewpack.trailers_of('s\n\nOwner-Approved-By: CLAUDE.md'), [])

    def test_a_configured_key_is_read(self):
        self.assertEqual(reviewpack.trailers_of('s\n\nOwner-OK: CLAUDE.md', 'Owner-OK'),
                         ['CLAUDE.md'])


class RiskMap(unittest.TestCase):
    def test_rules_from_lines_reads_levels_paths_and_folders(self):
        lines = ['# risk map\n', '- **High** network | net/, src/core/Wire.java\n',
                 '- Medium ui | ui\n', '- Low docs | docs/*.md, readme\n']
        self.assertEqual(reviewpack.rules_from_lines(lines, lambda tok: tok == 'ui'),
                         [('High', 'net'), ('High', 'src/core/Wire.java'), ('Medium', 'ui'),
                          ('Low', 'docs/*.md')])

    def test_level_is_the_longest_prefix_and_an_unlisted_doc_is_low(self):
        rules = [('High', 'net'), ('Low', 'net/docs')]
        self.assertEqual(reviewpack.level('net/docs/a.txt', rules), 'Low')
        self.assertEqual(reviewpack.level('net/x.py', rules), 'High')
        self.assertEqual(reviewpack.level('other/x.py', rules), 'unlisted (= High)')
        self.assertEqual(reviewpack.level('other/x.md', rules), 'Low')


class WarningsFirst(unittest.TestCase):
    def test_warnings_move_up_and_the_rest_keeps_its_order(self):
        self.assertEqual(reviewpack.warnings_first('a\nb\nx WARNING: w\nc'), 'x WARNING: w\na\nb\nc')
        self.assertEqual(reviewpack.warnings_first(''), '')


class Arguments(unittest.TestCase):
    def test_minus_c_defaults_to_here(self):
        a = cli.build_parser().parse_args(['feature'])
        self.assertEqual((a.root, a.branch, a.base, a.diff), ('.', 'feature', None, None))

    def usage(self, argv):
        with contextlib.redirect_stderr(io.StringIO()) as err, \
                self.assertRaises(SystemExit) as ctx:
            cli.main(argv)
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn('USAGE:', err.getvalue())
        return err.getvalue()

    def test_a_prefix_of_a_flag_is_not_taken_as_the_flag(self):
        self.usage(['feature', '--ba', 'main'])

    def test_no_branch_or_a_blank_base_is_usage(self):
        self.usage([])
        self.usage(['feature', '--base', ' '])
        self.assertIn('-C needs a directory', self.usage(['-C', '', 'feature']))


if __name__ == '__main__':
    unittest.main()
