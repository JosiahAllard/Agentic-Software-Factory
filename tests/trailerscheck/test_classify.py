"""kitlib.trailerscheck.trailers' pure parts, no git: classify() and its owner check, label(),
unproven_claim() and parse_log()."""
import unittest

from kitlib.config.owner import Rules
from kitlib.trailerscheck import trailers
from tests.reviewpack.repo import AGENT

OWNER = 'owner@example.invalid'


def commit(coauthors=(), approved=(), authored=(), author=AGENT, h='a' * 40):
    return trailers.BranchCommit(h, False, 'subject', list(coauthors), list(approved),
                                 list(authored), author)


class TheTrailersRule(unittest.TestCase):
    RULES = Rules(owner_email=OWNER)

    def test_an_agent_trailer_passes_and_a_bare_name_does_not(self):
        self.assertEqual(trailers.classify(
            commit(['Claude Opus 5 <noreply@anthropic.com>']), self.RULES), trailers.TRAILERED)
        self.assertEqual(trailers.classify(commit(['Claude Opus 5']), self.RULES), trailers.MISSING)

    def test_lander_and_missing(self):
        self.assertEqual(trailers.classify(commit(), self.RULES, exempt={'a' * 40}),
                         trailers.LANDER)
        self.assertEqual(trailers.classify(commit(), self.RULES), trailers.MISSING)
        self.assertEqual(trailers.label(commit(), trailers.MISSING), 'missing')


class OwnerClaimsNeedTheOwnersAuthorship(unittest.TestCase):
    """An Owner-Authored or Owner-Approved trailer excuses a commit only when the owner authored
    it. Without the is_owner() check in classify() every case below but the first reads OWNER."""

    def test_the_owners_own_marker_commit_is_owner_authored(self):
        c = commit(authored=['yes'], author=OWNER)
        v = trailers.classify(c, Rules(owner_email=OWNER))
        self.assertEqual(v, trailers.OWNER)
        self.assertEqual(trailers.label(c, v), 'owner-authored')
        self.assertIsNone(trailers.unproven_claim(c, Rules(owner_email=OWNER)))

    def test_the_owners_approved_commit_reads_owner_approved(self):
        c = commit(approved=['f.txt'], author=OWNER.upper())
        v = trailers.classify(c, Rules(owner_email=OWNER))
        self.assertEqual(trailers.label(c, v), 'owner-approved')

    def test_an_agents_marker_is_an_unproven_claim_and_missing(self):
        c = commit(authored=['yes'])
        v = trailers.classify(c, Rules(owner_email=OWNER))
        self.assertEqual(v, trailers.MISSING)
        self.assertEqual(trailers.label(c, v), 'missing')
        self.assertEqual(trailers.unproven_claim(c, Rules(owner_email=OWNER)),
                         'authored by %s, not the owner' % AGENT)

    def test_an_agents_owner_approved_is_an_unproven_claim_too(self):
        c = commit(approved=['f.txt'])
        self.assertEqual(trailers.classify(c, Rules(owner_email=OWNER)), trailers.MISSING)
        self.assertIsNotNone(trailers.unproven_claim(c, Rules(owner_email=OWNER)))

    def test_with_no_owner_configured_nothing_is_the_owners(self):
        c = commit(authored=['yes'], author=OWNER)
        self.assertEqual(trailers.classify(c, Rules()), trailers.MISSING)
        self.assertIn('not configured', trailers.unproven_claim(c, Rules()))

    def test_an_owner_email_also_listed_as_an_agents_proves_nothing(self):
        rules = Rules(owner_email=OWNER, agent_emails=[OWNER])
        c = commit(authored=['yes'], author=OWNER)
        self.assertEqual(trailers.classify(c, rules), trailers.MISSING)
        self.assertIn('agent_emails', trailers.unproven_claim(c, rules))

    def test_a_marker_value_other_than_yes_claims_nothing(self):
        c = commit(authored=['no'], author=OWNER)
        self.assertFalse(trailers.claims_owner(c))
        self.assertEqual(trailers.classify(c, Rules(owner_email=OWNER)), trailers.MISSING)
        self.assertIsNone(trailers.unproven_claim(c, Rules(owner_email=OWNER)))


class ParseLog(unittest.TestCase):
    def test_a_record_splits_into_its_fields(self):
        rec = '\x1f'.join(('h' * 40, 'p1 p2', 'A@x', 'C <noreply@anthropic.com>\n', '',
                           'yes\n', 'hub:A-2\n', 'subject \x1f with a separator')) + '\x1e\n'
        [c] = trailers.parse_log(rec)
        self.assertEqual(c, trailers.BranchCommit('h' * 40, True, 'subject \x1f with a separator',
                                                  ['C <noreply@anthropic.com>'], [], ['yes'],
                                                  'A@x', approval=['hub:A-2']))

    def test_an_owner_approval_is_not_an_owner_claim(self):
        c = trailers.BranchCommit('h', False, 's', [], [], [], OWNER, approval=['hub:A-2'])
        self.assertFalse(trailers.claims_owner(c))
        self.assertEqual(trailers.classify(c, Rules(owner_email=OWNER)), trailers.MISSING)


if __name__ == '__main__':
    unittest.main()
