"""Folding queued submissions (docs/gate-landing.md "Folding queued submissions"): the landing of a
folded submission, and its --wait-result. Fixtures: tests/landing/test_land_fold.py."""
import os
import unittest

from kitlib import gate as G
from kitlib.landing import submit
from kitlib.lock import run as lockrun
from kitlib.lock.store import LockError
from tests.landing.test_land import HANG_S
from tests.landing.test_land_fold import FoldCase


class TheFoldedLanding(FoldCase):
    """The landing of a folded submission ends at once saying FOLDED, before any write: no
    record, no ledger line, no landing lock, no lock booking when it is folded before it books;
    on the booking given, the booking refused (NOT RUN) or the landing lock held (BUSY)
    alike."""

    def check(self, how):
        a = self.queued(['one'])
        b = self.queued(['two'])
        fold_b = self.landing(b, wait_s=0 if how == 'busy' else HANG_S)
        anchor = self.landing(a)
        seen = {}

        def book(kind, repo, slug, **kw):
            seen.setdefault('calls', 0)
            seen['calls'] += 1
            out = anchor.run()
            self.assertEqual(out.code, G.OK, out.lines)
            if how == 'busy':
                self.lock().take(['kit/someone'], pid=os.getpid())
            seen['state'] = self.state(skip=[b.output])
            if how == 'not-run':
                raise LockError(6, 'the queue is full')
            return lockrun.book(kind, repo, slug, **kw)
        if how == 'before':
            out = anchor.run()
            self.assertEqual(out.code, G.OK, out.lines)
            seen['state'] = self.state(skip=[b.output])
            calls = len(self.book_calls)
            got = fold_b.run()
            self.assertEqual(len(self.book_calls), calls)       # it never booked
        else:
            fold_b.book_fn = book
            got = fold_b.run()
            self.assertEqual(seen['calls'], 1)
        self.assertEqual((got.code, got.lines), (G.OK, [self.folded_line(b, a)]))
        self.assertEqual(self.state(skip=[b.output]), seen['state'])
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['run_id'], two['folded_into']),
                         ('pass', b.run_id, a.run_id))

    def test_folded_before_its_booking(self):
        self.check('before')

    def test_folded_while_it_waited_for_its_booking(self):
        self.check('given')

    def test_folded_and_its_booking_refused(self):
        self.check('not-run')

    def test_folded_and_the_landing_lock_held(self):
        self.check('busy')

    def test_its_wait_result_says_folded_with_the_anchors_outcome(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        out = self.landing(a).run()
        self.assertEqual(out.code, G.OK, out.lines)
        with open(a.output, 'w', encoding='utf-8') as f:     # tools/land.py prints it there
            f.write('result record: x\n' + '\n'.join(out.lines) + '\n')
        # Not folded, kit/two's record would stay running and follow() would wait for ever.
        self.assertEqual(self.record('kit/two').get('folded_into'), a.run_id)
        got = submit.follow(self.repo.root, b.branches, b.tips, b.output, exit_wait_s=0)
        self.assertEqual(got.code, G.OK, got.lines)
        self.assertIn('FOLDED: the landing of kit/two was folded into the batch of the landing '
                      'run %s; its outcome, from that landing\'s output %s:' % (a.run_id, a.output),
                      got.lines)
        self.assertIn(out.lines[-1], got.lines)


if __name__ == '__main__':
    unittest.main()
