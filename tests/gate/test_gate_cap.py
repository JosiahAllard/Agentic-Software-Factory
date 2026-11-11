"""kitlib.gate.gatecap: the session gate cap (docs/gate-records.md "The session gate cap"). A branch gate
waits while its agent session has the cap's number of gate runs of the repository in flight; a
landing, the daily full run and the owner never wait. Every in-flight run here is a scratch-store
record (StoreCase.live_record) whose ancestors hold a made-up agent host."""
import contextlib
import functools
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from kitlib import gate as G
import kitlib.config as kconfig
from kitlib.config import user as kuser
from kitlib.gate import budget, gatecap, runner
from kitlib.gate import steps as S
from kitlib.lock import run as lockrun
from kitlib.lock import run_end
from kitlib.proc import process
from kitlib.records import place
from tests.gate import fixtures
from tests.home import machine_config
from tests.lock.helpers import own_affinity

HOST = [912345, 77]
OTHER_HOST = [912346, 78]
AGENT = {'role': 'agent', 'host': HOST, 'ancestors': [HOST], 'session': 'S1', 'name': 't'}
OTHER = {'role': 'agent', 'host': OTHER_HOST, 'ancestors': [OTHER_HOST], 'session': 'S2',
         'name': 't'}
# The owner: no agent host above it, and the same (untrusted) session id as the agent's runs.
OWNER = {'role': 'owner', 'host': None, 'ancestors': [], 'session': 'S1', 'name': 't'}
# An agent whose host cannot be read (kitlib.lock.store.caller: a detached run, or a tree or
# registry it could not read): role agent, no host.
HOSTLESS = {'role': 'agent', 'host': None, 'ancestors': [[912347, 79]], 'session': 'S1',
            'name': 't'}


class Wrongful(Exception):
    """Raised by a sleep that must never be called: a gate that waits fails at once."""


def never(_s):
    raise Wrongful('the gate waited for the session gate cap')


class TempHome(unittest.TestCase):
    def home(self, data):
        """A temp home whose machine config is `data`, in the fenced PM repository the pointer
        names (tests.home.machine_config); a later call replaces it."""
        h = tempfile.mkdtemp(prefix='gatecap-home-')
        self.addCleanup(shutil.rmtree, h, True)
        machine_config(h, data, case=self)
        return h


class Configured(TempHome):
    """The cap from "coordinator_start".gate_cap; unset, 0 and null mean no cap."""
    REPO = 'Some-Repo'

    def cap(self, data):
        return gatecap.configured(self.REPO, home=self.home(data))

    def test_the_key_passes_the_real_validator(self):
        data = {'coordinator_start': {'gate_cap': {self.REPO: 4}}}
        loaded = kuser.user_config(home=self.home(data))        # raises ConfigError if refused
        self.assertEqual(loaded['coordinator_start'], data['coordinator_start'])

    def test_exact_name_then_lower_case(self):
        self.assertEqual(self.cap({'coordinator_start': {'gate_cap': {self.REPO: 4}}}), (4, None))
        self.assertEqual(self.cap({'coordinator_start': {'gate_cap': {self.REPO.lower(): 3}}}),
                         (3, None))

    def test_off_by_default_and_for_zero_and_null(self):
        for data in ({}, {'coordinator_start': {}}, {'coordinator_start': {'gate_cap': None}},
                     {'coordinator_start': {'gate_cap': {}}},
                     {'coordinator_start': {'gate_cap': {'another-repo': 4}}},
                     {'coordinator_start': {'gate_cap': {self.REPO: 0}}},
                     {'coordinator_start': {'gate_cap': {self.REPO: None}}}):
            self.assertEqual(self.cap(data), (None, None), data)

    def test_a_value_it_cannot_use_warns_and_caps_nothing(self):
        for value in ('four', -1, 2.5, True):
            cap, warning = self.cap({'coordinator_start': {'gate_cap': {self.REPO: value}}})
            self.assertIsNone(cap, value)
            self.assertIn('WARNING: the session gate cap is not applied', warning)
        cap, warning = self.cap({'coordinator_start': {'gate_cap': [4]}})
        self.assertIsNone(cap)
        self.assertIn('is not an object', warning)


class CapCase(fixtures.GateRepoCase):
    def setUp(self):
        fixtures.GateRepoCase.setUp(self)
        # This thread starts and ends with no turn (removed before the store's folder is): a turn
        # left by an earlier test would make gave_way write into that test's removed folder.
        gatecap._TURN.mine = None
        self.addCleanup(gatecap._leave)
        self.wt, _ = self.branch('feature', {'a.py': 'x = 1\n'})
        self.name = place.repo_name(kconfig.load(self.wt), self.wt)
        self.n = 0

    def run_rec(self, host=HOST, **fields):
        """A live gate run of this repo booked inside `host`'s session; its id."""
        self.n += 1
        rid = 'r-20261019T120000Z-%06x' % self.n
        fields.setdefault('repo', self.name)
        fields.setdefault('gate', True)
        fields.setdefault('session', AGENT['session'] if host == HOST else OTHER['session'])
        self.live_record(rid, ancestors=[host], **fields)
        return rid

    def drop(self, rid):
        os.remove(self.store.path('active', '%s.json' % rid))

    @contextlib.contextmanager
    def capped(self, cap=4, who=AGENT, sleep=never):
        """kitlib.gate.gatecap.wait_turn and gave_way with this cap, caller and sleep, as runner
        calls them."""
        with mock.patch.object(gatecap, 'wait_turn', functools.partial(
                gatecap.wait_turn, cap=cap, who=who, sleep=sleep)), \
                mock.patch.object(gatecap, 'gave_way', functools.partial(
                    gatecap.gave_way, cap=cap, who=who, sleep=sleep)):
            yield

    def branch_gate(self, say=None):
        return runner.branch_gate(self.wt, book_kw=self.book_kw, log_dir=self.logs, say=say)


class Counting(CapCase):
    def test_only_the_sessions_gate_runs_of_the_repo_count(self):
        mine = [self.run_rec() for _ in range(2)]
        self.run_rec(priority='land')                   # a landing's run
        self.run_rec(host=OTHER_HOST)                   # another session's
        self.run_rec(repo='another-repo')               # another repository's
        self.run_rec(gate=False)                        # not a gate run
        self.run_rec(repo=self.name.upper())            # the same repo, in other case: counted
        got = [r['id'] for r in gatecap.in_flight(self.store, AGENT, self.name)]
        self.assertEqual(got, mine + ['r-20261019T120000Z-%06x' % self.n])

    def test_a_run_this_process_is_inside_is_not_counted(self):
        """A gate nested in the session's own gate run never waits for that run."""
        self.run_rec()
        inside = dict(AGENT, ancestors=[HOST, [os.getpid(), process.own_ctime()]])
        self.assertEqual(gatecap.in_flight(self.store, inside, self.name), [])


class BranchGateWaits(CapCase):
    def test_at_the_cap_it_says_one_line_and_books_when_one_ends(self):
        ids = [self.run_rec() for _ in range(4)]
        naps = []

        def sleep(s):                   # two polls at the cap, then one run ends
            naps.append(s)
            if len(naps) == 2:
                self.drop(ids[0])
        lines = []
        with self.capped(sleep=sleep):
            out = self.branch_gate(say=lines.append)
        self.assertEqual(out.code, G.OK, out.lines)
        waits = [t for t in lines if 'session gate cap' in t]
        self.assertEqual(len(waits), 1, lines)          # one line, nothing per poll
        self.assertIn('4 of 4 gate runs of this session in %s' % self.name, waits[0])
        for rid in ids:
            self.assertIn(rid, waits[0])
        self.assertEqual(naps, [gatecap.POLL_S, gatecap.POLL_S])
        self.assertGreaterEqual(gatecap.POLL_S, self.store.settings.queue_poll_s)
        self.assertEqual([c.get('gate') for c in self.book_calls], [True])   # booked once, after

    def test_below_the_cap_it_books_at_once(self):
        for _ in range(3):
            self.run_rec()
        lines = []
        with self.capped():
            out = self.branch_gate(say=lines.append)
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertFalse([t for t in lines if 'session gate cap' in t], lines)

    def test_the_cap_from_the_machine_config_is_read_when_runs_are_in_flight(self):
        for _ in range(2):
            self.run_rec()
        home = TempHome.home(self, {'coordinator_start': {'gate_cap': {self.name: 2}}})
        with self.assertRaises(Wrongful):
            gatecap.wait_turn(self.store, self.name, lambda t: None, who=AGENT, home=home,
                              sleep=never)
        off = TempHome.home(self, {'coordinator_start': {'gate_cap': {self.name: 0}}})
        gatecap.wait_turn(self.store, self.name, lambda t: None, who=AGENT, home=off, sleep=never)

    def test_with_no_cap_it_reads_no_caller_and_takes_no_turn(self):
        with mock.patch.object(gatecap, 'actor', side_effect=AssertionError('caller read')), \
                mock.patch.object(gatecap, 'configured', return_value=(None, None)):
            gatecap.wait_turn(self.store, self.name, lambda t: None, sleep=never)
        self.assertFalse(os.path.exists(self.store.path(gatecap.TURNS)))

    def test_with_nothing_in_flight_it_takes_its_turn_and_the_next_counts_it(self):
        """The first gate of a burst sees nothing in flight, yet its turn counts for the next one
        before its booking is written. Red before: it returned without a trace."""
        gatecap.wait_turn(self.store, self.name, lambda t: None, cap=1, who=AGENT, sleep=never)
        self.assertEqual(gatecap.count(self.store, AGENT, self.name), ([], 1))
        done = threading.Event()

        def other():                    # another thread: the turn above is not its own
            with self.assertRaises(Wrongful):
                gatecap.wait_turn(self.store, self.name, lambda t: None, cap=1, who=AGENT,
                                  sleep=never)
            done.set()
        t = threading.Thread(target=other)
        t.start()
        t.join(20)
        self.assertTrue(done.is_set())
        self.assertEqual(gatecap.count(self.store, OWNER, self.name), ([], 0))  # owner: nothing


class HostlessAgentCountsAll(CapCase):
    """An agent with no readable host cannot tell its session's runs from another's: every gate
    run of the repository counts (fail closed). A cap that skips every caller with no host, as
    it did for the owner, fails these."""

    def test_every_gate_run_of_the_repo_counts(self):
        counted = [self.run_rec(), self.run_rec(host=OTHER_HOST)]
        self.run_rec(priority='land')                   # a landing's run: still not counted
        self.run_rec(repo='another-repo')
        self.run_rec(gate=False)
        for who in (HOSTLESS, dict(HOSTLESS, host=[1]), {'host': None, 'ancestors': []}):
            got = [r['id'] for r in gatecap.in_flight(self.store, who, self.name)]
            self.assertEqual(got, counted, who)

    def test_at_the_cap_its_branch_gate_waits(self):
        for _ in range(2):
            self.run_rec()
        for _ in range(2):
            self.run_rec(host=OTHER_HOST)
        lines = []
        with self.capped(who=HOSTLESS), self.assertRaises(Wrongful):
            self.branch_gate(say=lines.append)
        waits = [t for t in lines if 'session gate cap' in t]
        self.assertEqual(len(waits), 1, lines)
        self.assertIn("4 of 4 gate runs (all counted: this agent's host cannot be read)", waits[0])
        self.assertEqual(self.book_calls, [])           # nothing booked while it waits

    def test_a_run_it_is_inside_is_not_counted(self):
        self.run_rec()
        inside = dict(HOSTLESS, ancestors=[[os.getpid(), process.own_ctime()]])
        self.assertEqual(gatecap.in_flight(self.store, inside, self.name), [])


class CountedTogether(CapCase):
    """Gates of one session that count at the same moment: each takes its turn under the cap's
    mutex (a turn file each, gatecap.TURNS), so a burst books no more than the cap and the rest
    wait before they book. gatecap.gave_way, the backstop for a count that missed a run, gives a
    held slot back at the cap."""

    def book(self, branch='kit/demo'):
        slot = fixtures.REAL_BOOK('engine-test', self.name, 'demo', gate=True, branch=branch,
                                  **self.book_kw)
        self.addCleanup(slot.release)
        return slot

    def test_a_burst_of_gates_books_no_more_than_the_cap(self):
        """Four gates of one session start at once at a cap of 2: no more than 2 are ever in the
        lock (booked and not yet run), none gives its slot back, and each books once. Red before:
        every gate of the burst counted before any sibling's booking was written, saw nothing in
        flight and booked, so all four entered the lock queue."""
        cap, n = 2, 4
        own = own_affinity()
        if len([1 for ts in self.layout()['slots_cpu'] if set(ts) <= own]) < cap:
            self.skipTest('the gates need %d fake cpu slots this process may run on' % cap)
        anc = lockrun.run_ancestors(False, self.env)     # what the real book's records keep
        if not anc:
            self.skipTest('this process has no ancestor to stand for the agent host')
        who = dict(AGENT, host=anc[0], ancestors=anc)
        cfg = kconfig.load(self.wt)
        chosen = S.build(cfg.section('gate'), self.wt, self.main, cfg.tools_dir, full=True)
        guard, settled = threading.Lock(), threading.Event()
        state = {'in_lock': 0, 'most': 0, 'booked': 0, 'waits': 0, 'ran': 0}
        lines = []

        def settle():                   # under guard: every gate has booked or waits
            if state['booked'] + state['waits'] >= n:
                settled.set()

        def say(text):
            with guard:
                lines.append(text)
                if 'gives back run' in text:
                    state['in_lock'] -= 1
                if 'waits for the session gate cap' in text:
                    state['waits'] += 1
                    settle()

        booked = lockrun.book

        def book(*a, **k):              # no booking is written before the burst has settled
            with guard:
                state['booked'] += 1
                state['in_lock'] += 1
                state['most'] = max(state['most'], state['in_lock'])
                settle()
            settled.wait(20)
            return booked(*a, **k)

        def run_steps(*_a, **_k):
            with guard:
                state['ran'] += 1
                state['in_lock'] -= 1
            return []

        out = {}

        def gate(tag):
            logs = os.path.join(self.logs, tag)
            os.makedirs(logs)
            try:
                out[tag] = runner.gated(cfg, self.wt, self.main, [], chosen, 'engine-test',
                                        self.name, 'demo-%s' % tag, logs, say, self.book_kw,
                                        capped=True)
            except BaseException as e:      # noqa: BLE001 - reported by the assertion below
                out[tag] = e
        tags = [chr(ord('a') + i) for i in range(n)]
        with mock.patch.object(gatecap, 'configured', return_value=(cap, None)), \
                mock.patch.object(gatecap, 'actor', return_value=who), \
                mock.patch.object(gatecap, 'POLL_S', 0.02), \
                mock.patch.object(runner, 'run_steps', run_steps), \
                mock.patch.object(lockrun, 'book', book):
            threads = [threading.Thread(target=gate, args=(t,)) for t in tags]
            for t in threads:
                t.start()
            for t in threads:
                t.join(60)
        self.assertEqual(sorted(out), tags, lines)
        for tag, got in out.items():
            self.assertIsInstance(got, tuple, (tag, got))
            self.assertIsNone(got[1], (tag, got))
        self.assertLessEqual(state['most'], cap, lines)        # never more than the cap booked
        self.assertEqual((state['booked'], state['ran']), (n, n), lines)   # each booked once
        self.assertFalse([t for t in lines if 'gives back run' in t], lines)

    def test_at_the_cap_its_slot_goes_back_as_a_run_that_started_nothing(self):
        """The slot it gives back ends `not started`, which the branch gate budget never counts."""
        other = self.run_rec()
        slot = self.book()
        lines, naps = [], []
        self.assertTrue(gatecap.gave_way(self.store, self.name, slot, lines.append, cap=1,
                                         who=AGENT, sleep=naps.append))
        self.assertTrue(slot.released)
        got = [d for d in self.store.history_records() if d.get('id') == slot.id]
        self.assertEqual([d.get('how') for d in got], [budget.NOT_STARTED])
        self.assertEqual(budget.runs(self.store, self.name, 'kit/demo'), [])
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('gives back run %s' % slot.id, lines[0])
        self.assertIn('1 of 1 other gate runs of this session in %s' % self.name, lines[0])
        self.assertIn(other, lines[0])
        self.assertEqual(len(naps), 1)
        self.assertTrue(0 <= naps[0] <= max(gatecap.POLL_S, self.store.settings.queue_poll_s))

    def test_a_run_asked_to_stop_is_not_given_back(self):
        """A give-back settles the run and drops its stop request: the stop would be lost."""
        self.run_rec()
        slot = self.book()
        self.assertTrue(run_end.request_stop(self.store, slot.id, 'test'))
        self.assertFalse(gatecap.gave_way(self.store, self.name, slot, lambda t: None, cap=1,
                                          who=AGENT, sleep=lambda s: None))
        self.assertFalse(slot.released)
        self.assertTrue(slot.stop_asked())

    def stopped_gate(self, stop_on_give_back):
        """A capped branch gate at the cap (its wait_turn passed, as when two count together),
        whose run is asked to stop once booked, or as it is given back. (outcome, its slots,
        its pauses). A pause ends the other run, so a gate that books again runs."""
        other = self.run_rec()
        slots, naps, booked = [], [], lockrun.book

        def book(*a, **k):
            slot = booked(*a, **k)
            slots.append(slot)
            if not stop_on_give_back:
                run_end.request_stop(self.store, slot.id, 'test')
            return slot

        def say(text):
            if stop_on_give_back and 'gives back run' in text:
                run_end.request_stop(self.store, slots[-1].id, 'test')

        def sleep(s):
            naps.append(s)
            self.drop(other)
        with self.capped(cap=1, sleep=sleep), \
                mock.patch.object(gatecap, 'wait_turn', lambda *a, **k: None), \
                mock.patch.object(lockrun, 'book', book):
            out = self.branch_gate(say=say)
        return out, slots, naps

    def test_a_stop_before_the_give_back_ends_the_gate_stopped(self):
        """Red before: the run was given back, its stop dropped, and the gate booked again."""
        out, slots, naps = self.stopped_gate(stop_on_give_back=False)
        self.assertEqual((out.code, len(slots), naps), (G.STOPPED, 1, []), out.lines)
        self.assertEqual(slots[0].ended.get('how'), 'cancelled')

    def test_a_stop_while_it_is_given_back_ends_the_gate_stopped(self):
        """The stop request found as the run settles is kept as `stopped_by`: no booking again."""
        out, slots, naps = self.stopped_gate(stop_on_give_back=True)
        self.assertEqual((out.code, len(slots), naps), (G.STOPPED, 1, []), out.lines)
        self.assertEqual(slots[0].ended.get('how'), budget.NOT_STARTED)
        self.assertTrue(slots[0].ended.get('stopped_by'))

    def test_below_the_cap_it_keeps_its_slot(self):
        """Another session's run, and the session's own below the cap, leave the slot held."""
        self.run_rec(host=OTHER_HOST)
        self.run_rec()
        slot = self.book()
        self.assertFalse(gatecap.gave_way(self.store, self.name, slot, lambda t: None, cap=2,
                                          who=AGENT, sleep=never))
        self.assertFalse(slot.released)

    def test_with_no_other_run_it_reads_neither_caller_nor_config(self):
        slot = self.book()
        with mock.patch.object(gatecap, 'actor', side_effect=AssertionError('caller read')), \
                mock.patch.object(gatecap, 'configured', side_effect=AssertionError('config read')):
            self.assertFalse(gatecap.gave_way(self.store, self.name, slot, lambda t: None,
                                              sleep=never))
        self.assertFalse(slot.released)


class NeverWaits(CapCase):
    """Each exemption, against four runs that would hold a capped agent gate at the cap."""

    def test_the_owner_never_waits(self):
        """The owner has no agent host: nothing is its session's, even runs that carry its session
        id. A cap counted by session id or by repository fails this."""
        for _ in range(4):
            self.run_rec()
        with self.capped(who=OWNER):
            out = self.branch_gate()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(gatecap.in_flight(self.store, OWNER, self.name), [])

    def test_a_landings_runs_do_not_count(self):
        """Three gate runs and a landing's at cap 4: the gate books at once. Counting priority
        "land" runs fails this."""
        for _ in range(3):
            self.run_rec()
        self.run_rec(priority='land')
        with self.capped():
            out = self.branch_gate()
        self.assertEqual(out.code, G.OK, out.lines)

    def test_a_landings_merged_gate_never_waits(self):
        """merged_gate, the landing's gate, books through gated() and never asks the cap. A cap
        checked in gated() fails this."""
        for _ in range(4):
            self.run_rec()
        with self.capped():
            out = runner.merged_gate(self.wt, kconfig.load(self.wt), self.main, log_dir=self.logs,
                                     book_kw=self.book_kw)
        self.assertEqual(out.code, G.OK, out.lines)

    def test_the_daily_full_runs_gate_never_waits(self):
        """The daily full run calls runner.gated directly (kitlib.dailyfull run). A cap checked in
        gated() fails this."""
        for _ in range(4):
            self.run_rec()
        cfg = kconfig.load(self.wt)
        chosen = S.build(cfg.section('gate'), self.wt, self.main, cfg.tools_dir, full=True)
        os.makedirs(self.logs)                          # the daily run makes its log dir first
        with self.capped():
            ran, refused = runner.gated(cfg, self.wt, self.main, [], chosen, 'engine-test',
                                        self.name, 'daily', self.logs, lambda t: None,
                                        self.book_kw)
        self.assertIsNone(refused)
        self.assertTrue(runner.verdict(ran)[0], ran)


if __name__ == '__main__':
    unittest.main()
