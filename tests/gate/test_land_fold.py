"""kitlib.gate.land and kitlib.gate.submit: folding queued submissions (docs/gate.md "Folding
queued submissions"). A submitted landing at its turn folds the submissions queued behind it into
its batch, under "landing".batch_max; a folded submission's own landing ends saying FOLDED and
writes nothing; a folded batch drops a unit that does not merge, and a red merged gate gives
back every unit and gates the landing's own branches alone. Every take and give-back is judged
under the record's lock."""
import os
import threading
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import land, result, submit, verdicts
from kitlib.lock import run as lockrun
from kitlib.lock.store import LockError
from kitlib.records import gate_ok
from tests.gate import fixtures
from tests.gate.test_land import HANG_S
from tests.gate.test_land_strict import STRICT
from tests.gate.test_land_submit import LAND_PY, Detach, SubmitCase
from tests.gate.test_result import ledger_lines


class Queued(object):
    def __init__(self, branches, tips, sub, out):
        self.branches, self.tips, self.sub, self.out = branches, tips, sub, out
        self.run_id = sub['runs'][0].run_id
        self.output = sub['output']


class FoldCase(SubmitCase):
    def queued(self, names, files=None, extra=(), review=True, on=None):
        """Branches kit/<name> (each with `files[name]`, or a file of its own; `on`: a branch's
        worktree to build them in, stacked), submitted as one landing (`extra`: its flags) and
        restored as its detached landing does at its start: a Queued."""
        branches, tips = [], []
        for n in names:
            body = (files or {}).get(n) or {'%s.py' % n: '%s\n' % n}
            if on:
                self.repo.git('checkout', '-q', '-b', 'kit/%s' % n, cwd=on)
                for rel, text in body.items():
                    self.repo.write(rel, text, root=on)
                tip = self.repo.commit('work on %s' % n, *body, cwd=on)
            else:
                _, tip = self.branch(n, body)
            branches.append('kit/%s' % n)
            tips.append(tip)
        detach = Detach(pid=os.getpid())
        args = (['-C', self.repo.root] + sum((['--expect-tip', t] for t in tips), [])
                + list(extra) + branches)
        out = submit.submit(self.repo.root, LAND_PY, args, branches, tips, environ={},
                            detach_fn=detach, kit=r'C:\kit', claim_wait_s=0,
                            landing_of=lambda carrier: None, expect_main=self.main)
        self.assertEqual(out.code, G.OK, out.lines)
        sub = self.restored(out, detach, branches, tips, expect_main=self.main)
        self.gate_tips(*tips)
        if review:
            fixtures.queue_review(self.repo.root, branches, tips)
        return Queued(branches, tips, sub, out)

    def landing(self, q, said=None, **kw):
        kw.setdefault('wait_s', HANG_S)
        kw.setdefault('poll_s', 0.05)
        one = len(q.branches) == 1
        return self.lander(q.branches[0] if one else q.branches, self.main,
                           q.tips[0] if one else q.tips, submitted=q.sub,
                           say=(said if said is not None else []).append, **kw)

    def state(self, skip=()):
        """Every file of the result folder (records, payloads, outputs, the ledger) but `skip`,
        the gate-OK records, the verdict queue, the refs and the landing lock file."""
        got = {}
        skip = set(os.path.normcase(os.path.abspath(p)) for p in skip)
        for folder in (result.result_dir(self.repo.root), gate_ok.record_dir(self.repo.root),
                       verdicts.queue_dir(self.common())):
            for dirpath, _, names in os.walk(folder):
                for name in names:
                    path = os.path.join(dirpath, name)
                    if os.path.normcase(os.path.abspath(path)) in skip:
                        continue
                    with open(path, 'rb') as f:
                        got[path] = f.read()
        got['refs'] = self.repo.git('for-each-ref', '--format=%(refname) %(objectname)')
        got['lock'] = self.lock().read()
        return got

    def assert_untaken(self, q, rec_before):
        """The records of `q` are as they were: running, its own, folded into no landing."""
        for b in q.branches:
            rec = self.record(b)
            self.assertEqual((rec['verdict'], rec['run_id'], rec.get('folded_into'), rec['pid']),
                             (result.RUNNING, q.run_id, None, rec_before[b]['pid']), rec)

    def folded_line(self, q, into):
        return ('FOLDED: %s lands in the batch of the landing run %s; its land records say how '
                'it ended' % ('+'.join(q.branches), into.run_id))


class Folds(FoldCase):
    def test_queued_submissions_fold_into_one_batch_and_land_together(self):
        a = self.queued(['one'])
        b = self.queued(['two'])
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('FOLDED kit/two (run %s) into this landing: the batch is kit/one+kit/two'
                      % b.run_id, said)
        self.assertEqual(out.lines[-1].split(' on main')[0], 'LANDED kit/one, kit/two')
        self.assertEqual(self.remote_main(), out.merge)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^2'), b.tips[0])
        one, two = self.record('kit/one'), self.record('kit/two')
        self.assertEqual((one['verdict'], one['run_id'], one['folded_into'], one['batch']),
                         ('pass', a.run_id, None, ['kit/one', 'kit/two']))
        self.assertEqual((two['verdict'], two['run_id'], two['folded_into'], two['batch']),
                         ('pass', b.run_id, a.run_id, ['kit/one', 'kit/two']))
        self.assertEqual(len(self.book_calls), 1)      # one booking, one merged gate
        lines = ledger_lines(self.repo.root)            # the ledger says the batch's size
        self.assertEqual([(l['branch'], l['run_id'], l['batch_size'], l['folded_into'])
                          for l in lines],
                         [('kit/one', a.run_id, 2, None), ('kit/two', b.run_id, 2, a.run_id)])

    def test_high_risk_verdicts_fold(self):
        """Owner ruling (hub Q-1652): a High-risk branch may fold; only a strict branch and a gate
        config branch land alone. Both verdicts here are High."""
        a = self.queued(['one'], review=False)
        b = self.queued(['two'], review=False)
        for q in (a, b):
            self.queue(q.branches[0], q.tips[0], risk='High')
        out = self.landing(a).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.record('kit/two')['folded_into'], a.run_id)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one', 'kit/two'])

    def test_batch_max_caps_the_fold_and_the_rest_waits_its_turn(self):
        a = self.queued(['one'])
        b = self.queued(['two', 'three'])       # 1 + 2 is the default batch_max (3)
        c = self.queued(['four'])
        before = {'kit/four': self.record('kit/four')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('FOLDED kit/two+kit/three (run %s) into this landing: the batch is '
                      'kit/one+kit/two+kit/three' % b.run_id, said)
        self.assert_untaken(c, before)
        self.assertEqual(self.landing(c).run().code, G.OK)     # alone, at its own turn
        self.assertEqual(self.record('kit/four')['batch'], ['kit/four'])

    def test_a_unit_that_does_not_fit_is_left_and_a_smaller_one_after_it_folds(self):
        a = self.queued(['one', 'two'])
        b = self.queued(['three', 'four'])      # 2 + 2 > 3
        c = self.queued(['five'])
        before = {x: self.record(x) for x in b.branches}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('not folding kit/three+kit/four (run %s): its 2 branch(es) are more than '
                      'the 1 "landing".batch_max leaves room for' % b.run_id, said)
        self.assertIn('FOLDED kit/five (run %s) into this landing: the batch is '
                      'kit/one+kit/two+kit/five' % c.run_id, said)
        self.assert_untaken(b, before)

    def left_out(self, why, **case):
        """A submission queued behind the landing that the batch checks refuse: said with `why`,
        left as it was, and the landing lands alone."""
        a = self.queued(['one'])
        b = self.queued(['two'], **case)
        before = {'kit/two': self.record('kit/two')}
        said = []
        out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        lines = [s for s in said if s.startswith('not folding kit/two (run %s): ' % b.run_id)]
        self.assertEqual(len(lines), 1, said)
        self.assertIn(why, lines[0])
        self.assert_untaken(b, before)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one'])

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

    def test_a_batch_that_does_not_land_as_one_gives_every_unit_back(self):
        """Each unit passes the checks with the batch before it; the whole batch's do not (here
        its scope check): every unit is given back, as it was, and the landing lands alone."""
        a = self.queued(['one'])
        b = self.queued(['two'])
        c = self.queued(['three'])
        before = {x: self.record(x) for x in ('kit/two', 'kit/three')}
        scope = land.Lander.scope_check

        def narrow(lander):
            return ('the batch is too wide' if len(lander.branches) > 1 else scope(lander))
        said = []
        with mock.patch.object(land.Lander, 'scope_check', narrow):
            out = self.landing(a, said).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('not folding: the batch kit/one+kit/two+kit/three does not land as one '
                      '(the batch is too wide); each folded submission is given back to land at '
                      'its own turn', said)
        for q in (b, c):
            self.assert_untaken(q, before)
            self.assertIs(self.record(q.branches[0]).get('fold_dropped'), False)
        self.assertEqual(self.record('kit/one')['batch'], ['kit/one'])
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^2'), a.tips[0])


class ConfigUnit(FoldCase):
    """A unit that changes a gate config file (here the hook module; .kit.json is owner-only)
    is never folded: it lands alone."""
    KIT = {'hook': {'module': 'kit_hooks'}}

    def setUp(self):
        FoldCase.setUp(self)
        self.repo.write('tools/kit_hooks.py', 'GATE_CHECKS = {}\n')
        self.main = self.repo.commit('hook module', 'tools/kit_hooks.py')
        self.repo.git('push', '-q', 'origin', 'main')

    def test_a_unit_that_changes_the_gate_config_is_left(self):
        Folds.left_out(self, "kit/two changes the gate's config (tools/kit_hooks.py): a branch "
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
