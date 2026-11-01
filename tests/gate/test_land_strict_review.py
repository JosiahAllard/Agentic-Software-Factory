"""kitlib.gate.land and strict paths (docs/gate.md "Strict paths"), split from test_land_strict so
each module's real-git run stays well under the test pool's unit timeout: the reviewer's tier by
the author's type, the commit, records and landing entry points as strict paths, and a branch that
changes the gate's config landing alone. Fixtures: test_land_strict.StrictCase."""
import unittest

from kitlib import gate as G
from tests.gate import test_land, test_land_strict

StrictCase = test_land_strict.StrictCase
BRANCH = test_land_strict.BRANCH
STRICT = test_land_strict.STRICT
FULL_GATE = test_land_strict.LandsAloneFullFresh.GATE     # only --full selects the strict path


class GateConfigLandsAlone(StrictCase):
    """A branch that changes a gate config file (kitlib.gate.reuse.config_hits; here the hook
    module, as .kit.json is owner-only) is refused in a named batch, as a strict branch is, and
    lands alone."""
    KIT = dict(test_land.LandCase.KIT, hook={'module': 'kit_hooks'})

    def setUp(self):
        StrictCase.setUp(self)
        self.on_main({'tools/kit_hooks.py': 'GATE_CHECKS = {}\n'}, 'hook module')

    def test_a_batch_holding_a_branch_that_changes_the_gate_config_is_refused(self):
        _, config_tip = self.branch('config', {'tools/kit_hooks.py': 'GATE_CHECKS = {}  # x\n'})
        _, plain_tip = self.branch('feature', {'src/a.py': 'a\n'})
        self.queue('kit/config', config_tip)
        self.queue(BRANCH, plain_tip)
        _, out, _ = self.land_it([BRANCH, 'kit/config'], [plain_tip, config_tip])
        self.assert_refused(out, "kit/config changes the gate's config (tools/kit_hooks.py)",
                            'lands alone, never in a batch')

    def test_alone_it_lands(self):
        _, config_tip = self.branch('config', {'tools/kit_hooks.py': 'GATE_CHECKS = {}  # x\n'})
        self.queue('kit/config', config_tip)
        _, out, _ = self.land_it('kit/config', config_tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)


class ReviewerTierByAuthor(StrictCase):
    """A strict branch needs a strong-tier review whoever built it: a haiku review counts for
    none, a worker or weak-worker build included, at any risk (Medium here: add() refuses a light
    review of a High-risk branch, test_review_tier). A plain Medium branch built by worker lands
    on a haiku review (no tier rule off the strict paths below High)."""
    GATE = FULL_GATE

    def lands(self, model, author):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip, reviewer_model=model, author=author)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertTrue(lander.full and lander.fresh)
        self.assertIn('STRICT: %s changes strict path %s: reviewed by pm-kit:reviewer on %s '
                      '(agent-r1); the merged gate runs --full --fresh' % (BRANCH, STRICT, model),
                      said)

    def refused(self, model, author):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.queue(BRANCH, tip, risk='Medium', reviewer_model=model, author=author)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'the reviewer ran on %s, not a strong-tier model' % model,
                            'a branch that changes a strict path lands only on a strong-tier '
                            'review, whoever built it')

    def test_a_haiku_verdict_for_a_worker_build_is_refused(self):
        self.refused('haiku', 'Kit Test / worker')

    def test_a_haiku_model_id_for_a_weak_worker_build_is_refused(self):
        self.refused('claude-haiku-4-5', 'Kit Test / pm-kit:weak-worker')

    def test_a_haiku_verdict_lands_a_plain_worker_build(self):
        _, tip = self.branch('feature', {'src/a.py': 'a\n'})
        self.queue(BRANCH, tip, risk='Medium', reviewer_model='haiku', author='Kit Test / worker')
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertIsNone(lander.strict_review)
        self.assertEqual([l for l in said if l.startswith('STRICT')], [])

    def test_a_haiku_verdict_for_a_strong_worker_build_is_refused(self):
        self.refused('haiku', 'Kit Test / strong-worker')

    def test_a_haiku_verdict_for_a_senior_worker_build_is_refused(self):
        self.refused('haiku', 'Kit Test / senior-worker')

    def test_a_haiku_verdict_with_no_author_type_is_refused(self):
        self.refused('haiku', 'Kit Test')

    def test_an_opus_verdict_still_lands_for_a_worker_build(self):
        self.lands('opus', 'Kit Test / worker')

    def test_an_opus_verdict_still_lands_for_a_senior_worker_build(self):
        self.lands('opus', 'Kit Test / senior-worker')


class CommitRecordsLandGate(StrictCase):
    """The commit and records code and the landing and gate entry points are strict paths of the
    running kit's list (kitlib/commit/**, kitlib/records/**, tools/land.py, tools/gate.py): a
    branch changing one is refused on a haiku review of a worker build, which lands a plain
    branch (ReviewerTierByAuthor)."""
    GATE = FULL_GATE

    def test_each_is_a_strict_path(self):
        for n, path in enumerate(('kitlib/commit/x.py', 'kitlib/records/x.py', 'tools/land.py',
                                  'tools/gate.py')):
            with self.subTest(path=path):
                branch = 'kit/strict-%d' % n
                _, tip = self.branch('strict-%d' % n, {path: 'x = 1\n'})
                self.queue(branch, tip, risk='Medium', reviewer_model='haiku',
                           author='Kit Test / worker')
                _, out, _ = self.land_it(branch, tip)
                self.assert_refused(out, '%s changes strict path %s' % (branch, path),
                                    'the reviewer ran on haiku, not a strong-tier model')


if __name__ == '__main__':
    unittest.main()
