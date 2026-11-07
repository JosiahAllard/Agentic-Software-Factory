"""Folding queued submissions (docs/gate-landing.md "Folding queued submissions"): the submissions a
landing leaves out of its fold, and the landings that fold nothing. Fixtures:
tests/gate/test_land_fold.py."""
import os
import unittest

from kitlib import gate as G
from kitlib.gate import submit
from tests.gate import fixtures
from tests.gate.test_land_fold import FoldCase, Queued
from tests.gate.test_land_strict import STRICT
from tests.gate.test_land_submit import LAND_PY, Detach


class Left(FoldCase):
    def test_a_unit_with_other_flags_is_left(self):
        self.left_out('it was submitted with other landing flags than this landing',
                      extra=['--keep-branch'])

    def test_a_unit_with_no_verdict_is_left(self):
        self.left_out('kit/two', review=False)

    def test_a_strict_unit_is_left(self):
        self.left_out('lands alone, never in a batch', files={'two': {STRICT: 'x = 1\n'}})

    def test_a_strict_landing_folds_nothing(self):
        a = self.queued(['one'], files={'one': {STRICT: 'x = 1\n'}}, review=False)
        self.queue('kit/one', a.tips[0])        # a strong-tier review
        b = self.queued(['two'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse([s for s in said if 'FOLDED' in s or 'folding' in s], said)
        self.assert_untaken(b, before)

    def test_a_unit_inside_one_folded_before_it_is_left(self):
        """Each unit is checked with the batch as folded so far: kit/three (submitted first)
        folds, and kit/two, whose tip kit/three holds, is left."""
        a = self.queued(['one'])
        wt, _ = self.branch('two', {'two.py': '2\n'})
        two_tip = self.repo.git('rev-parse', 'refs/heads/kit/two')
        b = self.queued(['three'], on=wt)       # kit/three's tip holds kit/two's
        detach = Detach(pid=os.getpid())
        out2 = submit.submit(self.repo.root, LAND_PY, ['-C', self.repo.root, '--expect-tip',
                                                       two_tip, 'kit/two'],
                             ['kit/two'], [two_tip], environ={}, detach_fn=detach,
                             kit=r'C:\kit', claim_wait_s=0, landing_of=lambda c: None,
                             expect_main=self.main)
        self.assertEqual(out2.code, G.OK, out2.lines)
        self.gate_tips(two_tip)
        fixtures.queue_review(self.repo.root, ['kit/two'], [two_tip])
        c = Queued(['kit/two'], [two_tip], self.restored(out2, detach, ['kit/two'], [two_tip],
                                                         expect_main=self.main), out2)
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('FOLDED kit/three (run %s) into this landing: the batch is '
                      'kit/one+kit/three' % b.run_id, said)
        self.assertIn('not folding kit/two (run %s): kit/two (%s) is already in kit/three '
                      '(%s), in the same batch: land kit/three alone'
                      % (c.run_id, two_tip[:10], b.tips[0][:10]), said)
        self.assert_untaken(c, before)
        self.assertEqual(self.record('kit/three')['folded_into'], a.run_id)


class ConfigUnit(FoldCase):
    """A unit that changes a gate config file (here the hook module; .kit.json is owner-only)
    is never folded: it lands alone."""
    KIT = dict(FoldCase.KIT, hook={'module': 'kit_hooks'})

    def setUp(self):
        FoldCase.setUp(self)
        self.repo.write('tools/kit_hooks.py', 'GATE_CHECKS = {}\n')
        self.main = self.repo.commit('hook module', 'tools/kit_hooks.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def test_a_unit_that_changes_the_gate_config_is_left(self):
        self.left_out("kit/two changes the gate's config (tools/kit_hooks.py): a branch "
                             "that changes a gate config file lands alone, never in a batch",
                       files={'two': {'tools/kit_hooks.py': 'GATE_CHECKS = {}  # two\n'}})

    def test_a_landing_that_changes_the_gate_config_folds_nothing(self):
        a = self.queued(['one'], files={'one': {'tools/kit_hooks.py': 'GATE_CHECKS = {}  # 1\n'}})
        b = self.queued(['two'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse([s for s in said if 'FOLDED' in s or 'folding' in s], said)
        self.assert_untaken(b, before)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one'])


class StrictByDefault(FoldCase):
    """With no "landing" key (folding and gate_once on by default) a strict branch still lands
    alone: it is never folded, and a strict landing folds nothing."""
    KIT = {}
    test_a_strict_unit_is_left = Left.test_a_strict_unit_is_left
    test_a_strict_landing_folds_nothing = Left.test_a_strict_landing_folds_nothing


class ConfigUnitByDefault(ConfigUnit):
    """ConfigUnit with no "landing" key: a gate-config branch still lands alone."""
    KIT = {'hook': {'module': 'kit_hooks'}}


if __name__ == '__main__':
    unittest.main()
