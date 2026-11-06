"""kitlib.gate.gatecap against a fake store (docs/gate.md "The session gate cap"): a gate at its
turn counts only the session's gate runs that have started, never those still queued in the
lock; and gates waiting at the cap book in the order they began waiting, one per freed slot.
No real lock, no real sleep: each waiting gate's sleep is a handshake with the test."""
import contextlib
import os
import queue
import shutil
import tempfile
import threading
import types
import unittest
from unittest import mock

from kitlib.gate import budget, gatecap
from kitlib.proc import process

REPO = 'Some-Repo'
HOST = [912345, 77]
AGENT = {'role': 'agent', 'host': HOST, 'ancestors': [HOST], 'session': 'S1', 'name': 't'}
STEP_S = 10         # the longest a handshake with a waiting gate's thread may take


class FakeStore:
    """live_runs from a list the test edits; the waiter and turn files in a temp folder; the
    store mutex a lock of this process."""

    def __init__(self, root):
        self.root = root
        self.runs = []
        self.settings = types.SimpleNamespace(queue_poll_s=1.0)
        self.n = 0
        self.mutex = threading.RLock()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    @contextlib.contextmanager
    def lock(self, _name):
        with self.mutex:
            yield

    def live_runs(self):
        return [dict(r) for r in self.runs]

    def add(self, state='held'):
        """A gate run booked by this process, as the gates the tests run here book."""
        self.n += 1
        rid = 'r-20261105T000000Z-%06x' % self.n
        self.runs.append({'id': rid, 'state': state, 'gate': True, 'repo': REPO,
                          'ancestors': [HOST], 'pid': os.getpid(),
                          'ctime': process.own_ctime()})
        return rid

    def drop(self, rid):
        self.runs[:] = [r for r in self.runs if r['id'] != rid]


class FakeSlot:
    def __init__(self, rid):
        self.id = rid
        self.released = None
        self.ended = None

    def stop_asked(self):
        return False

    def release(self, code, how):
        self.released = (code, how)
        self.ended = {'how': how}


class Case(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(prefix='gatecap-fifo-')
        self.addCleanup(shutil.rmtree, root, True)
        self.store = FakeStore(root)


class AtItsTurn(Case):
    """gave_way counts the session's other gate runs that hold their slots, not those queued."""

    def test_four_queued_siblings_do_not_hold_back_a_gate_at_its_turn(self):
        """Red before: the 4 queued runs counted, and the gate gave its slots back."""
        for _ in range(4):
            self.store.add(state='waiting')
        slot = FakeSlot(self.store.add())
        lines = []
        self.assertFalse(gatecap.gave_way(self.store, REPO, slot, lines.append, cap=4,
                                          who=AGENT, sleep=self.fail))
        self.assertIsNone(slot.released)
        self.assertEqual(lines, [])

    def test_four_started_siblings_still_make_it_give_back(self):
        for _ in range(3):
            self.store.add(state='waiting')
        held = [self.store.add() for _ in range(4)]
        slot = FakeSlot(self.store.add())
        lines, naps = [], []
        self.assertTrue(gatecap.gave_way(self.store, REPO, slot, lines.append, cap=4,
                                         who=AGENT, sleep=naps.append))
        self.assertEqual(slot.released, (1, budget.NOT_STARTED))
        self.assertIn('4 of 4 other gate runs of this session in %s have started' % REPO,
                      lines[0])
        for rid in held:
            self.assertIn(rid, lines[0])
        self.assertEqual(len(naps), 1)


class Waiter:
    """One gate in gatecap.wait_turn on its own thread. Its sleep says 'nap' and blocks until
    the test ticks it; its return says 'done'."""

    def __init__(self, store, name, cap=2):
        self.name, self.out, self.tick = name, queue.Queue(), threading.Semaphore(0)
        self.cap = cap
        self.quit = False
        self.thread = threading.Thread(target=self.run, args=(store,), daemon=True)

    def sleep(self, _s):
        self.out.put('nap')
        if not self.tick.acquire(timeout=STEP_S) or self.quit:
            raise SystemExit('the test let this waiting gate go')

    def run(self, store):
        try:
            gatecap.wait_turn(store, REPO, lambda t: None, cap=self.cap, who=AGENT,
                              sleep=self.sleep)
            self.out.put('done')
        except BaseException as e:      # noqa: BLE001 - reported to the test as what it did
            self.out.put('raised %s' % e)

    def next(self):
        """What the gate did since the last look: 'nap', 'done' or 'raised ...'."""
        try:
            return self.out.get(timeout=STEP_S)
        except queue.Empty:
            return 'nothing within %d s' % STEP_S

    def step(self):
        """Lets the gate count once; what it did then."""
        self.tick.release()
        return self.next()


class InTheOrderTheyWaited(Case):
    """Gates of one session waiting at a cap of 2 book one at a time, oldest first."""

    def start(self, name, cap=2):
        w = Waiter(self.store, name, cap)
        self.addCleanup(self.let_go, w)
        w.thread.start()
        self.assertEqual(w.next(), 'nap', name)     # it waits: its waiter file is written
        return w

    def let_go(self, w):
        w.quit = True
        w.tick.release()
        w.thread.join(STEP_S)

    def test_a_freed_slot_lets_the_earliest_waiter_book_and_the_others_wait(self):
        """Red before: the last to wait counted one run below the cap and booked first."""
        held = [self.store.add(), self.store.add()]
        a, b, c = self.start('a'), self.start('b'), self.start('c')
        self.store.drop(held[0])                  # one slot frees
        self.assertEqual(c.step(), 'nap')           # the latest counts first, and waits
        self.assertEqual(b.step(), 'nap')
        self.assertEqual(a.step(), 'done')          # the earliest books
        self.store.add(state='waiting')             # its booking, queued in the lock
        self.assertEqual(c.step(), 'nap')           # at the cap again: both still wait
        self.assertEqual(b.step(), 'nap')
        self.store.drop(held[1])                    # the next slot frees
        self.assertEqual(c.step(), 'nap')
        self.assertEqual(b.step(), 'done')
        self.assertEqual(len(os.listdir(self.store.path(gatecap.WAITERS))), 1)   # c's only

    def test_a_newcomer_waits_behind_gates_already_waiting(self):
        held = [self.store.add(), self.store.add()]
        a = self.start('a')
        self.store.drop(held[0])
        lines = []

        def stop(_s):
            raise SystemExit('it waits')
        with self.assertRaises(SystemExit):         # it waits: its first sleep ends it here
            gatecap.wait_turn(self.store, REPO, lines.append, cap=2, who=AGENT, sleep=stop)
        self.assertIn('1 gate(s) wait ahead of it', lines[0])
        self.assertEqual(len(os.listdir(self.store.path(gatecap.WAITERS))), 1)   # its own gone
        self.assertEqual(a.step(), 'done')

    def test_with_no_session_run_in_flight_a_newcomer_still_waits_behind_waiters(self):
        """Red before: with no live gate run of the repo, or none of the session's, the newcomer
        returned before it read the waiters and booked ahead of them."""
        for other_run in (False, True):
            with self.subTest(other_session_run=other_run):
                self.store.runs[:] = []
                shutil.rmtree(self.store.path(gatecap.TURNS), True)  # the last subtest's gates end
                self.store.add(state='waiting')
                a = self.start('a', cap=1)
                self.store.runs[:] = []                 # the session's run ends
                if other_run:
                    self.store.add()
                    self.store.runs[-1]['ancestors'] = [[912399, 99]]   # another session's
                lines = []

                def stop(_s):
                    raise SystemExit('it waits')
                with self.assertRaises(SystemExit):     # it waits: its first sleep ends it here
                    gatecap.wait_turn(self.store, REPO, lines.append, cap=1, who=AGENT,
                                      sleep=stop)
                self.assertIn('1 gate(s) wait ahead of it', lines[0])
                self.assertEqual(a.step(), 'done')

    def test_a_file_its_own_refused_remove_left_holds_back_neither_it_nor_others(self):
        """A remove refused (a reader had the file open, on Windows) leaves the gate's waiter file
        while its process lives. Given back by gave_way, the gate waits again in the same
        process. Red before: it counted its own leftover as a gate ahead of it, and at the cap it
        waited behind itself for good."""
        held = [self.store.add(), self.store.add()]
        refused = mock.patch('os.remove', side_effect=PermissionError('a reader has it open'))
        with refused, mock.patch.object(gatecap, 'DROP_WAIT_S', 0, create=True):
            gatecap.wait_turn(self.store, REPO, lambda t: None, cap=2, who=AGENT,
                              sleep=lambda s: self.store.drop(held[0]))
        files = lambda: [n for n in os.listdir(self.store.path(gatecap.WAITERS))  # noqa: E731
                         if n.endswith('.json')]
        self.assertEqual(len(files()), 1)                               # left behind
        gatecap.wait_turn(self.store, REPO, lambda t: None, cap=2, who=AGENT, sleep=self.fail)
        self.assertEqual(files(), [])                                   # removed now

    def test_a_waiter_file_it_cannot_write_lets_it_wait_unordered(self):
        held = [self.store.add(), self.store.add()]
        naps = []

        def sleep(s):
            naps.append(s)
            self.store.drop(held[0])
        with mock.patch.object(gatecap.lockstore, 'create_json', side_effect=OSError('full')):
            gatecap.wait_turn(self.store, REPO, lambda t: None, cap=2, who=AGENT, sleep=sleep)
        self.assertEqual(len(naps), 1)
        self.assertFalse(os.path.exists(self.store.path(gatecap.WAITERS)))

    def test_a_dead_waiters_file_holds_nobody_back(self):
        self.store.add(), self.store.add()
        os.makedirs(self.store.path(gatecap.WAITERS))
        dead = self.store.path(gatecap.WAITERS, 'dead.json')
        with open(dead, 'w', encoding='utf-8') as f:
            f.write('{"token": "dead", "repo": "%s", "since": 1, "pid": 0, "ctime": 1, '
                    '"ancestors": [[%d, %d]]}' % (REPO, HOST[0], HOST[1]))
        self.store.drop(self.store.runs[0]['id'])
        gatecap.wait_turn(self.store, REPO, lambda t: None, cap=2, who=AGENT, sleep=self.fail)
        self.assertFalse(os.path.exists(dead))


if __name__ == '__main__':
    unittest.main()
