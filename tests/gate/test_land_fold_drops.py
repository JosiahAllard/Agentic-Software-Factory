"""Folding queued submissions (docs/gate-landing.md "Folding queued submissions"): a folded batch
drops a unit that does not merge, a red merged gate gives every unit back, and a folded
landing woken while the anchor gives it back lands at its own turn. Fixtures:
tests/gate/test_land_fold.py."""
import threading
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import result
from kitlib.lock import run as lockrun
from tests.gate.test_land import HANG_S
from tests.gate.test_land_fold import FoldCase


class Drops(FoldCase):
    def test_a_unit_that_does_not_merge_is_dropped_and_the_rest_lands(self):
        a = self.queued(['one'], files={'one': {'README.md': 'one\n'}})
        b = self.queued(['two'], files={'two': {'README.md': 'two\n'}})
        c = self.queued(['three'])
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue([s for s in said if s.startswith('dropping kit/two (run %s) from the '
                                                         'batch: ' % b.run_id)], said)
        self.assertEqual(out.lines[-1].split(' on main')[0], 'LANDED kit/one, kit/three')
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['run_id'], two['folded_into'], two['fold_dropped'],
                          two['pid']),
                         (result.RUNNING, b.run_id, None, True, before['kit/two']['pid']))
        self.assertEqual(self.record('kit/three')['folded_into'], a.run_id)
        # its own turn: it lands alone, so it conflicts with main as it is now
        out_b = self.landing(b).run()
        self.assertEqual(out_b.code, G.FAILED, out_b.lines)
        self.assertEqual(self.record('kit/two')['verdict'], 'fail')

    def test_a_red_merged_gate_drops_every_unit_and_gates_the_landing_alone(self):
        a = self.queued(['one'])
        b = self.queued(['two'], files={'two': {'RED': 'x\n'}})
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('the merged gate of the batch kit/one+kit/two failed: each folded '
                      'submission is given back to land alone, at its own turn; gating kit/one '
                      'alone', said)
        self.assertEqual(out.lines[-1].split(' on main')[0], 'LANDED kit/one')
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^2'), a.tips[0])
        self.assertEqual(len(self.book_calls), 1)      # the second gate ran in the same slot
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['folded_into'], two['fold_dropped']),
                         (result.RUNNING, None, True))
        # a dropped unit is never folded again, and lands alone, folding nothing itself
        a2 = self.queued(['four'])
        said2 = []
        self.assertEqual(self.landing(a2, said2).run().code, G.OK)
        self.assertFalse([s for s in said2 if 'kit/two' in s], said2)
        said_b = []
        out_b = self.landing(b, said_b).run()
        self.assertEqual(out_b.code, G.FAILED, out_b.lines)
        self.assertFalse([s for s in said_b if 'FOLDED' in s], said_b)
        self.assertEqual(self.record('kit/two')['verdict'], 'fail')


class Race(FoldCase):
    def test_woken_while_the_anchor_gives_it_back_it_lands_at_its_own_turn(self):
        """The anchor folds kit/two+kit/three, then does not land (its own branch conflicts with
        main); it gives them back, kit/three first and kit/two (the first record) last. The
        folded landing is given its booking between the two writes: it reads its first record
        still folded into a landing that runs, waits, and once given back lands at its own turn,
        never saying FOLDED."""
        a = self.queued(['one'], files={'one': {'README.md': 'one\n'}})
        self.repo.write('README.md', 'main\n')
        self.main = self.repo.commit('main edit', 'README.md')
        self.repo.git('push', '-q', 'origin', 'main')
        b = self.queued(['two', 'three'])
        booking, mid, waiting = threading.Event(), threading.Event(), threading.Event()
        said_b, results = [], {}

        def say_b(line):
            said_b.append(line)
            if line.startswith('waiting: kit/two+kit/three is folded into the batch of the '
                               'landing run %s' % a.run_id):
                waiting.set()

        def book_b(kind, repo, slug, **kw):
            if not booking.is_set():
                booking.set()
                self.assertTrue(mid.wait(HANG_S))
            return lockrun.book(kind, repo, slug, **kw)
        lb = self.lander(b.branches, self.main, b.tips, submitted=b.sub, wait_s=HANG_S,
                         poll_s=0.05, say=say_b, book=book_b)
        t = threading.Thread(target=lambda: results.__setitem__('b', lb.run()))
        t.start()
        self.addCleanup(t.join, HANG_S)
        self.assertTrue(booking.wait(HANG_S))      # past its first look, waiting to book
        write, main = result.write, threading.current_thread()

        def spy(root, rec, when=None):
            path = write(root, rec, when=when)
            if (threading.current_thread() is main and path is not None and not mid.is_set()
                    and rec.get('branch') == 'kit/three' and rec['verdict'] == result.RUNNING
                    and rec.get('folded_into') is None):
                mid.set()                           # kit/three given back, kit/two not yet
                self.assertTrue(waiting.wait(HANG_S))
                self.assertEqual(result.read(root, 'kit/two', 'land')[0]['folded_into'],
                                 a.run_id)
            return path
        said_a = []
        with mock.patch.object(result, 'write', spy):
            out_a = self.landing(a, said_a).run()
            t.join(HANG_S)
        self.assertFalse(t.is_alive())
        self.assertEqual(out_a.code, G.FAILED, out_a.lines)
        self.assertIn('FOLDED kit/two+kit/three (run %s) into this landing: the batch is '
                      'kit/one+kit/two+kit/three' % b.run_id, said_a)
        self.assertTrue(mid.is_set() and waiting.is_set())
        out_b = results['b']
        self.assertEqual(out_b.code, G.OK, out_b.lines)
        self.assertEqual(out_b.lines[-1].split(' on main')[0], 'LANDED kit/two, kit/three')
        self.assertFalse([s for s in said_b + out_b.lines if s.startswith('FOLDED')])
        for br in b.branches:
            rec = self.record(br)
            self.assertEqual((rec['verdict'], rec['run_id'], rec['folded_into'], rec['batch']),
                             ('pass', b.run_id, None, b.branches))
        self.assertEqual(self.record('kit/one')['verdict'], 'fail')


if __name__ == '__main__':
    unittest.main()
