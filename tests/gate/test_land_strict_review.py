"""kitlib.gate.land and strict paths (docs/gate-landing-policy.md "Strict paths"), split from test_land_strict so
each module's real-git run stays well under the test pool's unit timeout: the reviewer's tier by
the author's type, the commit, records and landing entry points as strict paths, a branch that
changes the gate's config landing alone, and a strict tip's scoped record with and without
"landing".gate_once. Fixtures: test_land_strict.StrictCase."""
import unittest

import kitlib.config as kconfig
from kitlib import gate as G
from kitlib.gate import land, runner
from kitlib.records import gate_ok
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


class ScopedCase(StrictCase):
    """Tips given a default-mode ("select") gate-OK record instead of the --full one lander()
    would add (GATE_TIPS off)."""
    GATE = FULL_GATE
    GATE_TIPS = False

    def scoped(self, tip):
        rec = gate_ok.build(self.repo.root, tip, {}, {'version': 'test'}, 'test', gate_ok.utc(),
                            [{'name': 'unit', 'result': 'pass'}], base=self.main)
        gate_ok.write(self.repo.root, rec)
        self.__dict__.setdefault('fixture_records', set()).add(tip + '.json')
        self.assertEqual(gate_ok.mode_of(rec), 'select')

    def full_needed(self, tip):
        changed, why = runner.changed_between(self.repo.root, self.main, tip)
        self.assertIsNotNone(changed, why)
        return land.full_needed(self.repo.root, kconfig.load(self.repo.root), self.main, changed,
                                False)


class GateOnceStrict(ScopedCase):
    """A base that sets "landing".gate_once (docs/gate-run.md "Gate once at landing"): the landing's
    merged gate is a branch's one --full run, so a strict tip lands on a scoped record, alone,
    under --full --fresh, and the plain gate's FULL NEEDED line no longer asks it for --full; a
    gate config change still needs a --full record, strict or not."""
    KIT = dict(test_land.LandCase.KIT, landing={'gate_once': True}, hook={'module': 'kit_hooks'})

    def setUp(self):
        ScopedCase.setUp(self)
        self.on_main({'tools/kit_hooks.py': 'GATE_CHECKS = {}\n'}, 'hook module')

    def test_a_strict_branch_with_a_scoped_record_lands_under_full_fresh(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.scoped(tip)
        self.assertEqual(self.full_needed(tip), (None, None))
        self.queue(BRANCH, tip)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.remote_main(), out.merge)
        self.assertTrue(lander.full and lander.fresh)
        self.assertTrue(any(l.startswith('STRICT: %s changes strict path %s' % (BRANCH, STRICT))
                            and l.endswith('the merged gate runs --full --fresh') for l in said),
                        said)
        merged, why = gate_ok.verify(self.repo.root, out.merge)
        self.assertIsNone(why)
        self.assertEqual((gate_ok.mode_of(merged), [s['name'] for s in merged['steps']]),
                         ('full', ['unit']))

    def test_a_plain_branch_with_a_scoped_record_lands_under_full(self):
        _, tip = self.branch('feature', {'src/a.py': 'a\n'})
        self.scoped(tip)
        self.assertEqual(self.full_needed(tip), (None, None))
        self.queue(BRANCH, tip)
        lander, out, said = self.land_it(BRANCH, tip)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(lander.full and lander.escalated)
        self.assertFalse(lander.fresh)
        self.assertEqual([l for l in said if l.startswith('STRICT')], [])

    def test_a_strict_branch_that_changes_the_gate_config_still_needs_full(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n',
                                         'tools/kit_hooks.py': 'GATE_CHECKS = {}  # x\n'})
        self.scoped(tip)
        config = "the tip changes the gate's config (tools/kit_hooks.py), which only a --full "
        reason, why = self.full_needed(tip)
        self.assertIsNone(why)
        self.assertIn(config, reason or '')
        self.queue(BRANCH, tip)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'has no full-gate OK record', config)


class NoGateOnceStrict(ScopedCase):
    """A base with "landing".gate_once off keeps its rule: under "landing".shared_paths a strict
    tip's scoped record is refused (its merged gate is the only whole run otherwise, and the
    landing still needs the branch's --full), and the FULL NEEDED line says so."""
    KIT = dict(test_land.LandCase.KIT, landing={'shared_paths': ['api/*'], 'gate_once': False})

    def test_a_strict_branch_with_a_scoped_record_is_refused(self):
        _, tip = self.branch('feature', {STRICT: 'x = 1\n'})
        self.scoped(tip)
        strict = 'the tip changes strict path %s, which only a --full record lands' % STRICT
        reason, why = self.full_needed(tip)
        self.assertIsNone(why)
        self.assertIn(strict, reason or '')
        self.queue(BRANCH, tip)
        _, out, _ = self.land_it(BRANCH, tip)
        self.assert_refused(out, 'has no full-gate OK record', strict,
                            '"landing".shared_paths')


if __name__ == '__main__':
    unittest.main()
