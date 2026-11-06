"""Folding queued submissions (docs/gate.md "Folding queued submissions"): folding and
"landing".gate_once are on by default (an owner ruling), so a repository whose .kit.json sets no
landing key folds up to the default batch_max (5) and runs its merged gate --full once;
"batch_max": 1 and "gate_once": false opt out, landing each submission alone on its own merged
gate as before the default changed; and an exception or Ctrl+C before the merge gives every
folded submission back to land at its own turn (docs/gate.md "Dropped units"). Fixtures:
tests/gate/test_land_fold.py."""
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import land, result
from tests.gate.test_land_fold import FoldCase

ONCE = 'GATE ONCE: '


class Default(FoldCase):
    KIT = {}                                # no "landing" key: the defaults fold and gate once

    def test_no_landing_keys_fold_up_to_five_and_gate_once(self):
        a = self.queued(['one'])
        b = self.queued(['two', 'three'])
        c = self.queued(['four'])
        d = self.queued(['five'])
        e = self.queued(['six'])                # 1 + 2 + 1 + 1 is the default batch_max (5)
        before = {'kit/six': self.record('kit/six')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        five = ['kit/one', 'kit/two', 'kit/three', 'kit/four', 'kit/five']
        for q, batch in ((b, five[:3]), (c, five[:4]), (d, five)):
            self.assertIn('FOLDED %s (run %s) into this landing: the batch is %s' % (
                '+'.join(q.branches), q.run_id, '+'.join(batch)), said)
        self.assertEqual(out.lines[-1].split(' on main')[0], 'LANDED %s' % ', '.join(five))
        self.assert_untaken(e, before)          # no room left: it waits for its own turn
        self.assertEqual(len([s for s in said if s.startswith(ONCE)]), 1, said)
        one = self.record('kit/one')
        self.assertEqual((one['batch'], one['gate_once'], one['escalated']), (five, True, True))
        self.assertEqual(self.landing(e).run().code, G.OK)     # alone, at its own turn
        self.assertEqual(self.record('kit/six')['batch'], ['kit/six'])


class OptOut(FoldCase):
    KIT = {'landing': {'batch_max': 1, 'gate_once': False}}

    def test_the_opt_out_folds_nothing_and_does_not_gate_once(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse([s for s in said if 'FOLDED' in s or 'folding' in s], said)
        self.assertFalse([s for s in said if s.startswith(ONCE)], said)
        self.assert_untaken(b, before)
        one = self.record('kit/one')
        self.assertEqual((one['batch'], one['gate_once'], one['escalated']),
                         (['kit/one'], False, False))
        self.assertEqual(self.landing(b).run().code, G.OK)     # alone, at its own turn
        self.assertEqual(self.record('kit/two')['batch'], ['kit/two'])


class GateOnceOptOutAlone(FoldCase):
    """Each opt-out stands alone: "gate_once": false with batch_max unset still folds."""
    KIT = {'landing': {'gate_once': False}}

    def test_gate_once_false_alone_still_folds_without_gate_once(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('FOLDED kit/two (run %s) into this landing: the batch is kit/one+kit/two'
                      % b.run_id, said)
        self.assertFalse([s for s in said if s.startswith(ONCE)], said)
        self.assertEqual(self.record('kit/one')['gate_once'], False)


class SetTwo(FoldCase):
    KIT = {'landing': {'batch_max': 2}}

    def test_a_set_batch_max_folds_up_to_it(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        c = self.queued(['three'])
        before = {'kit/three': self.record('kit/three')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('FOLDED kit/two (run %s) into this landing: the batch is kit/one+kit/two'
                      % b.run_id, said)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one', 'kit/two'])
        self.assert_untaken(c, before)


class Interrupted(FoldCase):
    """An exception or Ctrl+C after the fold and before the merge (here from prior_records,
    which _locked calls between fold_queued and the merge): the landing raises it again, its own
    records end `error`, and each folded submission is given back as it was, to land alone at
    its own turn."""

    def interrupted(self, exc):
        a = self.queued(['one'])
        b = self.queued(['two'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        with mock.patch.object(land.Lander, 'prior_records', side_effect=exc):
            with self.assertRaises(exc):
                self.landing(a, said).run()
        self.assertIn('FOLDED kit/two (run %s) into this landing: the batch is kit/one+kit/two'
                      % b.run_id, said)
        self.assert_untaken(b, before)
        self.assertIs(self.record('kit/two').get('fold_dropped'), False)
        one = self.record('kit/one')
        self.assertEqual((one['verdict'], one['batch']), (result.ERROR, ['kit/one']))
        out_b = self.landing(b).run()
        self.assertEqual(out_b.code, G.OK, out_b.lines)
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['run_id'], two['folded_into'], two['batch']),
                         ('pass', b.run_id, None, ['kit/two']))

    def test_an_exception_before_the_merge_gives_the_folds_back(self):
        self.interrupted(RuntimeError)

    def test_ctrl_c_before_the_merge_gives_the_folds_back(self):
        self.interrupted(KeyboardInterrupt)

    def test_a_give_back_that_raises_is_named_and_never_replaces_the_exception(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        said = []
        with mock.patch.object(land.Lander, 'prior_records', side_effect=RuntimeError('bug')),                 mock.patch('kitlib.gate.submit.give_back', side_effect=OSError('disk')):
            with self.assertRaisesRegex(RuntimeError, 'bug'):
                self.landing(a, said).run()
        self.assertIn('WARNING: folded submissions not given back (OSError: disk): kit/two '
                      "(run %s); their records end with this landing's outcome, so submit each "
                      'again' % b.run_id, said)


if __name__ == '__main__':
    unittest.main()
