"""Folding queued submissions (docs/gate.md "Folding queued submissions"): folding is off unless
the repository's .kit.json sets "landing".batch_max itself (an owner ruling: off by default), and
an exception or Ctrl+C before the merge gives every folded submission back to land at its own
turn (docs/gate.md "Dropped units"). Fixtures: tests/gate/test_land_fold.py."""
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import land, result
from tests.gate.test_land_fold import FoldCase


class Unset(FoldCase):
    KIT = {}                                # no "landing".batch_max: the default folds nothing

    def test_an_unset_batch_max_folds_nothing(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse([s for s in said if 'FOLDED' in s or 'folding' in s], said)
        self.assert_untaken(b, before)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one'])
        self.assertEqual(self.landing(b).run().code, G.OK)     # alone, at its own turn
        self.assertEqual(self.record('kit/two')['batch'], ['kit/two'])


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
