"""Folding queued submissions (docs/gate-landing.md "Folding queued submissions"): the landing of a
folded submission, its wait out of the landing queue while the landing it was folded into still
runs, its --wait-result, and `land.py --queue` naming the folds. Fixtures:
tests/landing/test_land_fold.py."""
import os
import threading
import time
import types
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import result
from kitlib.landing import lander as land
from kitlib.landing import submit
from kitlib.lock import queue as lockqueue
from kitlib.lock import run as lockrun
from kitlib.lock.store import LockError
from kitlib.records import place
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


class OutOfTheQueue(FoldCase):
    """A folded submission's own landing given its booking while the landing it was folded into
    still runs (that landing released its slot, then closes records, cleans up and finishes the
    records): it releases the slot and waits out of the landing queue (Lander.wait_folded), so it
    leaves no queue entry behind and the landing behind it gets the turn. Given back, it books
    again with what is left of its --wait; that running out first, it ends FOLDED."""

    def member(self, q, **kw):
        """The landing of `q` (`kw`: more of its Lander keywords), run in a thread that the
        anchor's booking (`book`, its book=) starts once the anchor holds its slot, and returns
        only once `q`'s landing waits in the landing queue behind it, so the anchor folds a
        submission whose landing is queued."""
        m = types.SimpleNamespace(said=[], results={}, waiting=threading.Event())
        m.slug = place.slug_of('+'.join(q.branches))
        prefix = ('waiting: %s is folded into the batch of the landing run '
                  % '+'.join(q.branches))

        def say(line):
            m.said.append(line)
            if line.startswith(prefix):
                m.waiting.set()

        def run():
            try:
                m.results['out'] = m.lander.run()
            except BaseException as e:     # noqa: BLE001 - the test reads it
                m.results['error'] = e
        m.lander = self.lander(q.branches[0], self.main, q.tips[0], submitted=q.sub,
                               wait_s=HANG_S, poll_s=0.05, say=say, **kw)
        m.thread = threading.Thread(target=run)
        m.entries = lambda: [e for e in lockqueue.load(self.store)['entries']
                             if e.get('slug') == m.slug]
        m.calls = lambda: [c for c in self.book_calls if c['slug'] == m.slug]

        def book(kind, repo, slug, **kw):
            slot = lockrun.book(kind, repo, slug, **kw)
            if not m.thread.is_alive() and not m.results:
                m.thread.start()
                self.addCleanup(m.thread.join, HANG_S)
                end = time.monotonic() + HANG_S
                while not [e for e in m.entries() if e.get('state') == 'waiting']:
                    if not m.thread.is_alive() or time.monotonic() > end:
                        raise AssertionError('the member never queued: %r %r'
                                             % (m.results, m.said))
                    time.sleep(0.02)
            return slot
        m.book = book
        return m

    def outcome(self, m):
        m.thread.join(HANG_S)
        self.assertFalse(m.thread.is_alive())
        self.assertNotIn('error', m.results, m.said)
        return m.results['out']

    def test_given_its_turn_while_the_anchor_finishes_it_waits_out_of_the_queue(self):
        """The window between the anchor's slot released and its records finished: the member
        booked once and holds no queue entry (it used to book again at the back, every poll);
        `land.py --queue` during the anchor's landing names the fold on the lock line and marks
        the member's entry."""
        a = self.queued(['one'])
        b = self.queued(['two'])
        m = self.member(b)
        anchor = self.landing(a, book=m.book)
        push, close, seen = anchor.push, anchor.close_pulled, {}

        def pushing(*args, **kw):
            seen['queue'] = land.queue(self.repo.root, store=self.store)
            return push(*args, **kw)

        def closing():
            seen['waited'] = m.waiting.wait(HANG_S)
            seen['entries'], seen['calls'] = m.entries(), len(m.calls())
            return close()
        anchor.push, anchor.close_pulled = pushing, closing
        out = anchor.run()
        self.assertEqual(out.code, G.OK, out.lines)
        got = self.outcome(m)
        self.assertTrue(seen['waited'], m.said)
        self.assertEqual((seen['entries'], seen['calls']), ([], 1))
        self.assertEqual((got.code, got.lines), (G.OK, [self.folded_line(b, a)]))
        self.assertEqual(len(m.calls()), 1)
        self.assertEqual([e for e in lockqueue.load(self.store)['entries']
                          if e.get('priority') == 'land'], [])
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['folded_into']), ('pass', a.run_id))
        listed = seen['queue']
        self.assertEqual(listed.code, G.OK, listed.lines)
        self.assertTrue(listed.lines[0].endswith('; folded into its run %s: kit/two (run %s)'
                                                 % (a.run_id, b.run_id)), listed.lines)
        mine = [l for l in listed.lines if '/%s (' % m.slug in l]
        self.assertEqual(len(mine), 1, listed.lines)
        self.assertTrue(mine[0].endswith('; folded into %s' % a.run_id), listed.lines)
        own = [l for l in listed.lines if '/%s (' % place.slug_of('kit/one') in l]
        self.assertEqual(len(own), 1, listed.lines)
        self.assertNotIn('folded into', own[0])

    def test_given_back_while_it_waits_it_books_again_and_lands_at_its_own_turn(self):
        """The anchor folds kit/two, then does not land (its own branch conflicts with main) and
        gives it back after its slot is released; the member, waiting out of the queue by then,
        books again with what is left of its --wait and lands alone."""
        a = self.queued(['one'], files={'one': {'README.md': 'one\n'}})
        self.repo.write('README.md', 'main\n')
        self.main = self.repo.commit('main edit', 'README.md')
        self.repo.git('push', '-q', 'origin', 'main')
        b = self.queued(['two'])
        m = self.member(b)
        write, main, seen = result.write, threading.current_thread(), {}

        def spy(root, rec, when=None):
            if (threading.current_thread() is main and 'waited' not in seen
                    and rec.get('branch') == 'kit/two' and rec['verdict'] == result.RUNNING
                    and rec.get('folded_into') is None):     # the give-back: once it waits
                seen['waited'] = m.waiting.wait(HANG_S)
                seen['entries'] = m.entries()
            return write(root, rec, when=when)
        said_a = []
        with mock.patch.object(result, 'write', spy):
            out_a = self.landing(a, said_a, book=m.book).run()
            got = self.outcome(m)
        self.assertEqual(out_a.code, G.FAILED, out_a.lines)
        self.assertIn('FOLDED kit/two (run %s) into this landing: the batch is kit/one+kit/two'
                      % b.run_id, said_a)
        self.assertTrue(seen['waited'], m.said)
        self.assertEqual(seen['entries'], [])
        self.assertEqual(got.code, G.OK, got.lines)
        self.assertEqual(got.lines[-1].split(' on main')[0], 'LANDED kit/two')
        self.assertFalse([s for s in m.said + got.lines if s.startswith('FOLDED')])
        calls = m.calls()
        self.assertEqual(len(calls), 2, calls)
        self.assertEqual(calls[0]['wait_s'], HANG_S)
        self.assertTrue(0 < calls[1]['wait_s'] < HANG_S, calls[1])
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['run_id'], two['folded_into'], two['batch']),
                         ('pass', b.run_id, None, ['kit/two']))
        self.assertEqual(self.record('kit/one')['verdict'], 'fail')

    def test_its_booking_not_given_while_the_fold_runs_ends_it_folded(self):
        """TheFoldedLanding's not-run case while the anchor still runs: the member's booking
        fails (LockError) once the anchor released its slot, before it finished the records.
        turn_check reads the fold still running (WAIT): FOLDED, not NOT RUN, nothing written, no
        queue entry."""
        def refused(kind, repo, slug, **kw):
            lockrun.book(kind, repo, slug, **kw).release(1, 'done')    # queued, then not given
            raise LockError(6, 'the queue is full')
        a = self.queued(['one'])
        b = self.queued(['two'])
        m = self.member(b, book=refused)
        anchor = self.landing(a, book=m.book)
        close, seen = anchor.close_pulled, {}

        def closing():
            m.thread.join(HANG_S)
            seen['ended'] = not m.thread.is_alive()
            seen['two'], seen['entries'] = self.record('kit/two'), m.entries()
            return close()
        anchor.close_pulled = closing
        out = anchor.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(seen['ended'], m.said)
        got = self.outcome(m)
        self.assertEqual((got.code, got.lines), (G.OK, [self.folded_line(b, a)]))
        self.assertEqual((seen['two']['verdict'], seen['two']['folded_into']),
                         (result.RUNNING, a.run_id))
        self.assertEqual(seen['entries'], [])
        self.assertEqual(len(m.calls()), 1)
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['folded_into']), ('pass', a.run_id))

    def test_its_wait_running_out_while_the_fold_runs_ends_it_folded(self):
        """Its --wait runs out (here: its deadline set to now) while it waits out of the queue
        and the anchor still runs: FOLDED, nothing written, no queue entry; the anchor then
        finishes its record."""
        a = self.queued(['one'])
        b = self.queued(['two'])
        m = self.member(b)
        anchor = self.landing(a, book=m.book)
        close, seen = anchor.close_pulled, {}

        def closing():
            seen['waited'] = m.waiting.wait(HANG_S)
            m.lander.deadline = time.monotonic()
            m.thread.join(HANG_S)
            seen['ended'] = not m.thread.is_alive()
            seen['two'], seen['entries'] = self.record('kit/two'), m.entries()
            return close()
        anchor.close_pulled = closing
        out = anchor.run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(seen['waited'] and seen['ended'], m.said)
        got = self.outcome(m)
        self.assertEqual((got.code, got.lines), (G.OK, [self.folded_line(b, a)]))
        self.assertEqual((seen['two']['verdict'], seen['two']['folded_into']),
                         (result.RUNNING, a.run_id))
        self.assertEqual(seen['entries'], [])
        self.assertEqual(len(m.calls()), 1)
        two = self.record('kit/two')
        self.assertEqual((two['verdict'], two['folded_into']), ('pass', a.run_id))


if __name__ == '__main__':
    unittest.main()
