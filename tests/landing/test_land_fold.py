"""kitlib.landing.land and kitlib.landing.submit: folding queued submissions (docs/gate-landing.md "Folding
queued submissions"). A submitted landing at its turn folds the submissions queued behind it into
its batch, under "landing".batch_max; a folded submission's own landing ends saying FOLDED and
writes nothing; a folded batch drops a unit that does not merge, and a red merged gate gives
back every unit and gates the landing's own branches alone. Every take and give-back is judged
under the record's lock. The fixtures live here; the cases are split over test_land_fold*.py and
test_land_folded.py so each module stays well inside the test runner's time limit on real git."""
import os
import unittest
from unittest import mock

from kitlib import gate as G
from kitlib.gate import result
from kitlib.landing import land, submit
from kitlib.records import gate_ok, verdict_entry
from tests.gate import fixtures
from tests.landing.test_land import HANG_S
from tests.landing.test_land_submit import LAND_PY, Detach, SubmitCase
from tests.gate.test_result import ledger_lines


class Queued(object):
    def __init__(self, branches, tips, sub, out):
        self.branches, self.tips, self.sub, self.out = branches, tips, sub, out
        self.run_id = sub['runs'][0].run_id
        self.output = sub['output']


class FoldCase(SubmitCase):
    KIT = {'landing': {'batch_max': 3}}     # an explicit cap under the default (5) wins

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
                       verdict_entry.queue_dir(self.common())):
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
        """An owner ruling: a High-risk branch may fold; only a strict branch and a gate
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
        b = self.queued(['two', 'three'])       # 1 + 2 is FoldCase's batch_max (3)
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


if __name__ == '__main__':
    unittest.main()
