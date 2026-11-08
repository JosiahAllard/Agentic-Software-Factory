"""The gate queue (docs/gate-records.md "The gate queue"): a branch gate whose tip is already in main
runs nothing, a second gate of a queued branch, tip and options is refused naming the queued run,
and a gate at a new tip stops the session's own queued gate of the branch at the older tip. A
held run is never dropped or replaced. Each queued run here is a scratch-store record
(StoreCase.live_record) with a queue entry, whose ancestors hold a made-up agent host; it is
taken out of the store before the gate under test books (its wrapper leaving), so the real
booking never waits behind it."""
import json
import os
import unittest
from unittest import mock

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.gate import gatecap, result, runner
from kitlib.git import refs
from kitlib.lock import api as lockapi
from kitlib.lock import queue as lockqueue
from kitlib.lock import run as lockrun
from tests.gate import fixtures

HOST = [912345, 77]
OTHER_HOST = [912346, 78]
AGENT = {'role': 'agent', 'host': HOST, 'ancestors': [HOST], 'session': 'S1', 'name': 't'}


class QueueCase(fixtures.GateRepoCase):
    def setUp(self):
        fixtures.GateRepoCase.setUp(self)
        self.wt, self.tip = self.branch('feature', {'a.py': 'x = 1\n'})
        self.br = refs.current_branch(self.wt)[0]
        self.name = runner.repo_name(kconfig.load(self.wt), self.wt)
        self.tree = runner.tested_tree(self.wt)
        self.assertTrue(self.br and self.tree)
        self.n = 0
        self.queued = []            # ids taken out of the store before the gate books
        self.stop_seen = {}         # id: whether its stop request was there when the gate booked
        for target, value in ((gatecap, 'actor'), (gatecap, 'configured')):
            p = mock.patch.object(target, value, return_value=(
                AGENT if value == 'actor' else (None, None)))
            p.start()
            self.addCleanup(p.stop)
        inner = lockrun.book

        def book(*a, **kw):
            for rid in self.queued:
                self.stop_seen[rid] = os.path.exists(self.store.path('active', rid + '.stop'))
                path = self.store.path('active', rid + '.json')
                if os.path.exists(path):
                    os.remove(path)
                lockqueue.leave(self.store, rid)
            return inner(*a, **kw)
        p = mock.patch.object(lockrun, 'book', book)
        p.start()
        self.addCleanup(p.stop)

    def queued_run(self, tip=None, host=HOST, state='waiting', tree=None, record=True, **fields):
        """A live branch gate run of this repo and branch at `tip` (default the branch's), its
        queue entry in `state`; its id. `record`: the branch's result record says `running` at
        `tip`, as the queued gate's own branch_gate wrote it before it booked."""
        if record:
            path, line = result.Run(self.wt, 'gate', self.br, tip or self.tip).start()
            self.assertIsNotNone(path, line)
        self.n += 1
        rid = 'r-20261104T120000Z-%06x' % self.n
        fields.setdefault('note', gatecap.queue_note(tip or self.tip))
        rec = self.live_record(rid, ancestors=[host], repo=self.name, gate=True, branch=self.br,
                               tree=tree or self.tree, state=state,
                               session='S1' if host == HOST else 'S2', **fields)
        with self.store.lock('queue'):
            q = lockqueue.load(self.store)
            lockqueue.join(q, rec, {'cpu': 1}, state=state)
            lockqueue.save(self.store, q)
        self.queued.append(rid)
        return rid

    def gate(self, **kw):
        lines = []
        out = runner.branch_gate(self.wt, book_kw=self.book_kw, log_dir=self.logs,
                                 say=lines.append, **kw)
        return out, lines

    def main_takes_tip(self):
        self.repo.git('update-ref', 'refs/remotes/origin/main', self.tip, cwd=self.wt)

    def ended(self):
        folder = self.store.path('ended')
        out = []
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            with open(os.path.join(folder, name), encoding='utf-8') as f:
                out.append(json.load(f))
        return out

    def stop_asked(self, rid):
        return self.stop_seen.get(rid, os.path.exists(self.store.path('active', rid + '.stop')))


class TipInMain(QueueCase):
    def test_a_queued_gate_whose_tip_main_took_is_dropped_at_its_turn(self):
        inner = lockrun.book

        def book(*a, **kw):             # main takes the tip while the gate waits in the queue
            self.main_takes_tip()
            return inner(*a, **kw)
        with mock.patch.object(lockrun, 'book', book):
            out, lines = self.gate()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('is already in refs/remotes/origin/main', out.lines[0])
        self.assertIn('was dropped at its turn; nothing ran', out.lines[0])
        self.assertEqual(len(self.book_calls), 1)
        self.assertIsNone(out.facts.get('results'))
        self.assertFalse(os.path.exists(os.path.join(self.logs, 'unit.log')))
        self.assertEqual([r.get('how') for r in self.ended()], [lockrun.NOT_STARTED])

    def test_a_gate_whose_tip_is_already_in_main_books_nothing(self):
        self.main_takes_tip()
        out, _ = self.gate()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('needs no gate; nothing queued', out.lines[0])
        self.assertIn('git worktree add --detach <path> %s' % self.tip, out.lines[1])
        self.assertEqual(self.book_calls, [])

    def test_a_detached_head_at_a_tip_in_main_is_gated(self):
        self.main_takes_tip()
        self.repo.git('checkout', '-q', '--detach', self.tip, cwd=self.wt)
        out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(len(self.book_calls), 1)

    def test_no_answer_from_git_never_drops_a_gate(self):
        self.main_takes_tip()
        with mock.patch.object(refs, 'is_ancestor', return_value=(None, 'git was ended')):
            out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)

    def test_uncommitted_files_are_gated_even_on_a_tip_in_main(self):
        self.main_takes_tip()
        self.repo.write('b.py', 'y = 2\n', root=self.wt)
        out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(len(self.book_calls), 1)


class Duplicate(QueueCase):
    def test_a_second_gate_of_the_same_branch_and_tip_is_refused_naming_the_queued_run(self):
        rid = self.queued_run()
        out, lines = self.gate()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('already queued: run %s; nothing queued' % rid, out.lines[0])
        self.assertIn('Instead: wait for its result: gate.py wait-result %s --tip %s'
                      % (self.br, self.tip), out.lines[1])
        self.assertEqual(self.book_calls, [])
        self.assertFalse([t for t in lines if t.startswith('result record:')], lines)
        self.assertFalse(self.stop_asked(rid))

    def test_other_options_a_held_twin_or_uncommitted_files_are_not_a_duplicate(self):
        cases = [dict(note=gatecap.queue_note(self.tip, full=True)), dict(state='held'),
                 dict(tree='0' * 40), dict(record=False)]
        for fields in cases:
            rid = self.queued_run(**fields)
            out, _ = self.gate()
            self.assertEqual(out.code, G.OK, (fields, out.lines))
            self.assertFalse(self.stop_asked(rid), fields)

    def test_a_queued_twin_already_asked_to_stop_is_no_twin(self):
        rid = self.queued_run()
        self.assertIs(lockapi.stop_waiting(self.store, rid, 'reap'), True)  # gate.py stop, a reap
        out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(len(self.book_calls), 1)

    def test_a_dirty_work_tree_is_never_refused(self):
        self.queued_run()
        self.repo.write('b.py', 'y = 2\n', root=self.wt)
        out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)


class Replace(QueueCase):
    def test_a_gate_at_a_new_tip_replaces_the_older_queued_one(self):
        rid = self.queued_run(tip=self.main)
        out, lines = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(self.stop_asked(rid))
        said = [t for t in lines if 'replaces queued run %s' % rid in t]
        self.assertEqual(len(said), 1, lines)
        self.assertIn('at %s (the branch is at %s now)' % (self.main[:10], self.tip[:10]), said[0])
        self.assertEqual(len(self.book_calls), 1)
        self.assertEqual(self.book_calls[0].get('note'), gatecap.queue_note(self.tip))

    def test_a_held_run_another_sessions_or_a_rerun_is_never_replaced(self):
        cases = [dict(state='held'), dict(host=OTHER_HOST), dict(note=None)]
        for fields in cases:
            rid = self.queued_run(tip=self.main, **fields)
            out, lines = self.gate()
            self.assertEqual(out.code, G.OK, (fields, out.lines))
            self.assertFalse(self.stop_asked(rid), fields)
            self.assertFalse([t for t in lines if 'replaces queued run' in t], fields)

    def test_an_agent_whose_host_cannot_be_read_replaces_nothing(self):
        rid = self.queued_run(tip=self.main)
        hostless = {'role': 'agent', 'host': None, 'ancestors': [[912347, 79]], 'session': 'S1'}
        with mock.patch.object(gatecap, 'actor', return_value=hostless):
            out, _ = self.gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse(self.stop_asked(rid))


class Note(unittest.TestCase):
    def test_the_note_names_the_tip_and_options_and_is_read_back(self):
        tip = 'a' * 40
        self.assertEqual(gatecap.queue_note(tip, full=True, only=['unit', 'lint'], base='x'),
                         'branch gate at %s --full --only unit,lint --base x' % tip)
        self.assertEqual(gatecap.note_tip(gatecap.queue_note(tip, only=['unit'])), tip)
        for other in (None, 'landing: kit/a', 'branch gate at abc', 'branch gate at %sZ' % tip):
            self.assertIsNone(gatecap.note_tip(other), other)
