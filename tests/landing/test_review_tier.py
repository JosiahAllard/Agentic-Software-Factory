"""The reviewer tier rule (kitlib.landing.verdicts.tier_why): a branch that is High risk or strict
lands only on a strong-tier review, whoever built it. tools/verdict.py add refuses a lighter
High-risk review at queue time; kitlib.landing.land refuses it at landing, a verdict queued before
the rule included (no grandfathering: review it again on a strong-tier model); both use the one
pure test. A Low or Medium branch off the strict paths lands on any reviewer model."""
import json
import os
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.landing import land
from kitlib.landing import verdicts as V
from kitlib.records import verdict_entry as VE
from tests.landing import test_land_strict, test_land_verdict, test_verdict_cli
from tests.gate.fixtures import GateRepoCase

BRANCH = test_land_strict.BRANCH
REVIEWER = test_land_strict.REVIEWER       # risk High, reviewer_model opus
WORKER = 'Kit Test / worker'
HIGH_REFUSAL = ('a High-risk branch lands only on a strong-tier review, whoever built it (its '
                'author is %s)' % WORKER)


def entry(**over):
    return dict(REVIEWER, branch='kit/a', tip='a' * 40, range='%s..%s' % ('0' * 10, 'a' * 10),
                author=WORKER, **over)


class TierWhy(unittest.TestCase):
    STRONG, WEAK = {'opus'}, {'haiku', 'sonnet', 'account-default'}

    def tiers(self):
        return self.STRONG, self.WEAK, None

    def test_a_high_risk_review_below_the_strong_tier_is_refused(self):
        for model in ('haiku', 'sonnet', 'claude-haiku-5-5', 'account-default', 'gpt-5'):
            with self.subTest(model=model):
                why = V.tier_why(entry(reviewer_model=model), False, self.tiers)
                self.assertIn('the reviewer ran on %s, not a strong-tier model (opus' % model, why)
                self.assertIn(HIGH_REFUSAL, why)

    def test_a_high_risk_review_on_the_strong_tier_passes(self):
        for model in ('opus', 'claude-opus-5-5', ' Opus '):
            with self.subTest(model=model):
                self.assertIsNone(V.tier_why(entry(reviewer_model=model), False, self.tiers))

    def test_low_or_medium_off_the_strict_paths_has_no_tier_rule_and_reads_no_tiers(self):
        def unread():
            raise AssertionError('the tiers are read only when the rule applies')
        for risk in ('Low', 'Medium'):
            with self.subTest(risk=risk):
                self.assertIsNone(V.tier_why(entry(risk=risk, reviewer_model='haiku'), False,
                                             unread))

    def test_a_strict_branch_keeps_its_own_message_whatever_its_risk(self):
        for risk in ('Low', 'Medium', 'High'):
            with self.subTest(risk=risk):
                why = V.tier_why(entry(risk=risk, reviewer_model='haiku'), True, self.tiers)
                self.assertIn('a branch that changes a strict path lands only on a strong-tier '
                              'review, whoever built it (its author is %s)' % WORKER, why)
                self.assertIsNone(V.tier_why(entry(risk=risk), True, self.tiers))

    def test_tiers_that_cannot_be_read_refuse(self):
        why = V.tier_why(entry(), False, lambda: (None, None, 'no hosts.json'))
        self.assertEqual(why, 'the strong-tier models cannot be read: no hosts.json')


class QueueTime(GateRepoCase):
    """tools/verdict.py add: a High-risk verdict on a review below the strong tier is REFUSED,
    exit 2, nothing queued; the same review of a Medium branch is queued."""
    # test_verdict_cli's harness, borrowed without its tests
    setUp = test_verdict_cli.VerdictCli.setUp
    run_tool = test_verdict_cli.VerdictCli.run_tool
    add_args = test_verdict_cli.VerdictCli.add_args

    def test_a_high_risk_haiku_review_is_refused_at_add(self):
        code, out, err = self.run_tool(*self.add_args(**{'--risk': 'High',
                                                          '--reviewer-model': 'haiku'}))
        self.assertEqual(code, 2, (out, err))
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith('REFUSED: the reviewer ran on haiku, not a strong-tier '
                                            'model'), out)
        self.assertIn(HIGH_REFUSAL + '; nothing was queued', lines[0])
        self.assertEqual(lines[-1], V.TIER_INSTEAD)
        self.assertNotIn('QUEUED', out)
        self.assertEqual(V.scan(self.repo.common_dir()), ([], []))

    def test_a_high_risk_review_on_the_strong_tier_is_queued(self):
        code, out, err = self.run_tool(*self.add_args(**{'--risk': 'High',
                                                          '--reviewer-model': 'claude-opus-5-5'}))
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip), err)

    def test_a_medium_haiku_review_of_a_worker_build_is_queued(self):
        code, out, err = self.run_tool(*self.add_args(**{'--risk': 'Medium',
                                                          '--reviewer-model': 'haiku'}))
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip), err)

    def test_tiers_that_cannot_be_read_at_add_are_not_done_not_refused(self):
        """hosts.json unreadable: a High add is NOT DONE (exit 1, a configuration message, nothing
        queued), not REFUSED with an Instead: line to dispatch a strong-tier analyst."""
        unread = mock.patch.object(land, 'tier_models', return_value=(None, None, 'no hosts.json'))
        with unread:
            code, out, err = self.run_tool(*self.add_args(**{'--risk': 'High'}))
        self.assertEqual(code, 1, (out, err))
        both = out + err
        self.assertIn('NOT DONE: the kit configuration cannot be read: hosts.json "tiers": '
                      'no hosts.json; nothing was queued', both)
        self.assertNotIn('REFUSED', both)
        self.assertNotIn(V.TIER_INSTEAD, both)
        self.assertEqual(V.scan(self.repo.common_dir()), ([], []))
        with unread:              # the rule does not apply below High: the tiers are not read
            code, out, err = self.run_tool(*self.add_args(**{'--risk': 'Medium'}))
        self.assertEqual((code, out.strip()), (0, 'QUEUED kit/a %s' % self.tip), err)


class Landing(test_land_verdict.VerdictCase):
    """kitlib.landing.land: a High-risk branch off the strict paths lands only on a strong-tier
    review, whoever built it; a strict branch is unchanged."""

    def queue_past_add(self, branch, tip, **over):
        """An entry file written past add(), as one queued before the rule was: add() would
        refuse it now."""
        e = dict(REVIEWER, branch=branch, tip=tip, range='%s..%s' % (self.main[:10], tip),
                 queued_utc='2026-10-30T00:00:00Z', state='queued', last_result=None)
        e.update(over)
        self.assertIsNone(VE.check(e))
        os.makedirs(VE.queue_dir(self.common()), exist_ok=True)
        with open(VE.entry_path(self.common(), branch), 'w', encoding='utf-8') as f:
            json.dump(e, f)

    def test_a_high_risk_haiku_verdict_queued_before_the_rule_is_refused_at_landing(self):
        _, tip = self.plain()
        self.queue_past_add(BRANCH, tip, reviewer_model='haiku', author=WORKER)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, '%s: the reviewer ran on haiku, not a strong-tier model' % BRANCH,
                            HIGH_REFUSAL)
        self.assertNotIn('strict path', out.lines[0])

    def test_a_high_risk_opus_verdict_lands_a_worker_build(self):
        _, tip = self.plain()
        self.queue(BRANCH, tip, reviewer_model='opus', author=WORKER)
        lander, _ = self.lands(BRANCH, tip)
        self.assertIsNone(lander.strict_review)

    def test_a_medium_haiku_verdict_lands_a_worker_build(self):
        _, tip = self.plain()
        self.queue(BRANCH, tip, risk='Medium', reviewer_model='haiku', author=WORKER)
        lander, _ = self.lands(BRANCH, tip)
        self.assertIsNone(lander.strict_review)

    def test_a_strict_low_risk_haiku_verdict_is_refused_as_before(self):
        _, tip = self.branch('feature', {test_land_strict.STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip, risk='Low', reviewer_model='haiku', author=WORKER)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, '%s changes strict path %s' % (BRANCH, test_land_strict.STRICT),
                            'the reviewer ran on haiku, not a strong-tier model',
                            'a branch that changes a strict path lands only on a strong-tier '
                            'review, whoever built it')
        self.assertEqual(out.code, G.REFUSED)


if __name__ == '__main__':
    unittest.main()
