"""kitlib.hubjobs.fill_watch: the hub's fill watch (docs/hub-fill.md): a live coordinator with a
free slot and startable work gets one nudge per change (stage 1), what was told survives a hub
restart (stage 2), the hub pulls into a slot left free (stage 3), the nudge names the stand-down
(stage 4) and the PM hears of a slot that stays empty (stage 5)."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from kitlib.backlog import offer as omod
from kitlib.hubjobs import coord_restart as C
from kitlib.hubjobs import fill_watch as W
from kitlib.hubsite import jobs
from kitlib.hubsite import push as P
from kitlib.launcher import settings as lsettings
from tests import gitfixture
from tests.backlog import helpers as _bh

def ctx():
    return jobs.Ctx(threading.Event(), lambda word, detail=None: None)


COORD = {'repo': 'game', 'agent': True, 'stem': 'game-coordinator', 'name': 'game-coordinator-a1'}
LANE = {'repo': 'game', 'agent': True, 'stem': 'lane-coordinator', 'name': 'lane-coordinator-a1'}
PLAIN = {'repo': 'game', 'agent': False, 'stem': 'research', 'name': 'research-a2'}


class WatchFixture(object):
    """A mixin: the job with its readers replaced: `self.slots[key]` is (cap, in_flight, ids) or an
    exception; `self.states` the deliver results, in turn (default written)."""

    def setUp(self):
        self.sessions = {'game': dict(COORD)}
        self.live = {'game-coordinator-a1'}
        self.slots = {'game': (5, 3, ['EX-A', 'EX-B', 'EX-C'])}
        self.sent, self.reads, self.states = [], [], []

        def read(key, entry):
            self.reads.append(key)
            got = self.slots[key]
            if isinstance(got, Exception):
                raise got
            return got

        def deliver(key, text):
            self.sent.append((key, text))
            return self.states.pop(0) if self.states else P.WRITTEN

        self.job = W.FillWatch(lambda: self.sessions, lambda: self.live, read, deliver)

    def run_job(self):
        return self.job(ctx())

    def run_quietly(self):
        """The run, failing the test (not erroring it) when the run raises: the runner counts a
        raise as a job failure and pushes `failing` to the PM at the third."""
        try:
            return self.run_job()
        except Exception as e:      # noqa: BLE001
            self.fail('the run raised %s: %s' % (type(e).__name__, e))


class Watch(WatchFixture, unittest.TestCase):
    """Stage 1: the watch and the nudge."""

    def test_free_and_startable_nudges_the_coordinator_once_with_the_counts(self):
        out = self.run_job()
        self.assertEqual(len(self.sent), 1)
        key, text = self.sent[0]
        self.assertEqual(key, 'game')
        self.assertIn('fill: 2 free, 3 startable: EX-A, EX-B, EX-C', text.splitlines()[0])
        self.assertIn('backlog.py next', text)
        self.assertIn('unless the PM holds new work or you are checkpointing or standing down', text)
        self.assertNotIsInstance(out, jobs.Skip)
        self.assertIn('game', out)

    def test_many_ids_are_cut_with_a_count(self):
        ids = ['EX-%d' % n for n in range(8)]
        self.slots['game'] = (5, 0, ids)
        self.run_job()
        self.assertIn('fill: 5 free, 8 startable: EX-0, EX-1, EX-2, EX-3, EX-4 +3 more',
                      self.sent[0][1])

    def test_nothing_startable_sends_nothing(self):
        self.slots['game'] = (5, 1, [])
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(self.sent, [])

    def test_no_free_slot_sends_nothing(self):
        for in_flight in (5, 7):            # at the cap, and past it
            self.slots['game'] = (5, in_flight, ['EX-A'])
            self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(self.sent, [])

    def test_one_push_per_change(self):
        self.run_job()
        self.run_job()                      # the same state: no second push
        self.assertEqual(len(self.sent), 1)
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-D'])
        self.run_job()                      # a new startable id
        self.slots['game'] = (5, 4, ['EX-A', 'EX-B', 'EX-D'])
        self.run_job()                      # a new free count
        self.assertEqual(len(self.sent), 3)
        self.assertIn('fill: 1 free, 3 startable', self.sent[-1][1])

    def test_a_state_that_ends_and_comes_back_nudges_again(self):
        self.run_job()
        self.slots['game'] = (5, 1, [])
        self.run_job()                      # nothing startable: forgotten
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-C'])
        self.run_job()
        self.assertEqual(len(self.sent), 2)
        self.slots['game'] = (5, 5, ['EX-A', 'EX-B', 'EX-C'])
        self.run_job()                      # no free slot: forgotten
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-C'])
        self.run_job()
        self.assertEqual(len(self.sent), 3)

    def test_a_coordinator_that_stops_is_forgotten(self):
        self.run_job()
        self.live = set()
        self.run_job()
        self.live = {'game-coordinator-a1'}
        self.run_job()
        self.assertEqual(len(self.sent), 2)

    def test_a_push_not_written_is_tried_again_and_uncertain_is_not(self):
        self.states = [P.NOT_WRITTEN, P.NO_SESSION, P.UNCERTAIN]
        self.run_job()
        self.run_job()
        self.run_job()                      # uncertain: counted as told
        self.run_job()
        self.assertEqual(len(self.sent), 3)

    def test_a_deliver_that_raises_is_tried_again(self):
        calls = []

        def boom(key, text):
            calls.append(key)
            if len(calls) == 1:
                raise OSError('pipe')
            return P.WRITTEN
        self.job.deliver = boom
        self.run_job()
        self.run_job()
        self.run_job()
        self.assertEqual(len(calls), 2)

    def test_stopped_and_plain_sessions_are_never_read(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE), 'research': dict(PLAIN),
                         '_note': 'x'}
        self.live = {'game-coordinator-a1', 'research-a2'}       # lane is stopped
        self.slots['lane'] = self.slots['research'] = (5, 0, ['EX-Z'])
        self.run_job()
        self.assertEqual(self.reads, ['game'])
        self.assertEqual([k for k, _ in self.sent], ['game'])

    def test_an_unreadable_store_is_a_note_and_the_others_still_run(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE)}
        self.live = {'game-coordinator-a1', 'lane-coordinator-a1'}
        self.slots['game'] = C.BacklogUnread('the store could not be read (Failure)')
        self.slots['lane'] = (2, 0, ['EX-L'])
        out = self.run_job()
        self.assertEqual([k for k, _ in self.sent], ['lane'])
        self.assertIn('game', out)

    def test_no_push_object_sends_nothing_and_is_not_told(self):
        self.job.deliver = lambda key, text: None
        self.run_job()
        self.job.deliver = lambda key, text: self.sent.append(key) or P.WRITTEN
        self.run_job()
        self.assertEqual(self.sent, ['game'])

    def test_a_listing_that_times_out_is_a_skip(self):
        import subprocess

        def slow():
            raise subprocess.TimeoutExpired('ps', 15)
        self.job.live = slow
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(self.sent, [])

    def test_a_coordinator_the_crossover_is_moving_gets_no_push(self):
        self.job.claims = lambda: {'game'}
        out = self.run_job()
        self.assertEqual((self.sent, self.reads), ([], []))
        self.assertIsInstance(out, jobs.Skip)
        self.assertIn('usage crossover', out.note)

    def test_an_unreadable_crossover_state_is_a_skip(self):
        def bad():
            raise lsettings.LaunchError('usage-crossover.json: bad JSON')
        self.job.claims = bad
        out = self.run_job()
        self.assertIsInstance(out, jobs.Skip)
        self.assertIn('usage crossover', out.note)
        self.assertEqual(self.sent, [])

    def test_an_unreadable_sessions_file_is_a_skip(self):
        def bad():
            raise lsettings.LaunchError('sessions.json: bad JSON')
        self.job.sessions = bad
        out = self.run_quietly()
        self.assertIsInstance(out, jobs.Skip)
        self.assertIn('sessions.json', out.note)
        self.assertEqual(self.sent, [])

    def test_a_listing_that_fails_is_a_skip(self):
        for exc in (RuntimeError('ps failed'), OSError('no ps')):
            def bad(exc=exc):
                raise exc
            self.job.live = bad
            out = self.run_quietly()
            self.assertIsInstance(out, jobs.Skip)
            self.assertIn(type(exc).__name__, out.note)
        self.assertEqual(self.sent, [])


class Rec(object):
    def __init__(self, rid, session=None):
        self.id, self.session = rid, session

    def get(self, name):
        return self.session if name == 'session' else None


class Slots(unittest.TestCase):
    """read_slots over stand-ins for the store: the cap from the repo, the coordinator's own
    live pulls, the offer in order."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(self.repo)
        with open(os.path.join(self.repo, '.kit.json'), 'w', encoding='utf-8') as f:
            json.dump({'backlog': {'store': 'ref'}}, f)
        self.settings = lsettings.Settings({'state_dir': self.tmp, 'repos': {'game': self.repo}},
                                           home=self.tmp)
        self.holding = []
        self.by_session = {}
        self.opened, self.loads, self.caps = [], [], []

    def slots(self, key, entry, cap=4):
        from kitlib.backlog import offer as omod
        from kitlib.backlog import opener, plan

        class Store(object):
            def load(s):
                self.loads.append(1)
                return 'snap'

        class Ctx(object):
            def __init__(s, root, session):
                s.root, s.remote, s.store, s.session = root, 'origin', Store(), session

        def open_store(root, fetch=True, session=None, branch=None, *, probes):
            self.assertIs(probes, opener.NO_PROBES)     # an offer read stops no pull
            self.opened.append((root, fetch, session))
            return Ctx(root, session)

        def offer_for(snap, ctx, branch=None, cap=5, seeds=(), holding=None):
            self.assertEqual((snap, cap, holding), ('snap', None, self.holding))
            return ([mock.Mock(rec=Rec(r)) for r in self.by_session.get(ctx.session, ())], [],
                    holding, None)

        def repo_cap(top):
            self.caps.append(top)
            return cap
        with mock.patch.object(opener, 'open_store', open_store), \
                mock.patch.object(opener, 'repo_cap', repo_cap), \
                mock.patch.object(plan, 'offer_for', offer_for), \
                mock.patch.object(omod, 'in_flight_split',
                                  lambda snap, root, remote: (self.holding, [], None)):
            return W.read_slots(self.settings, key, entry)

    def test_cap_own_live_pulls_and_the_offer_in_order(self):
        self.holding = [Rec('EX-1', 'lane'), Rec('EX-2', None), Rec('EX-3', 'lane'),
                        Rec('EX-4', 'other')]
        self.by_session = {'lane': ['EX-C', 'EX-A', 'EX-B']}
        cap, flight, ids = self.slots('lane', dict(LANE))
        self.assertEqual((cap, flight, ids), (4, 2, ['EX-C', 'EX-A', 'EX-B']))
        self.assertEqual(self.caps, [self.repo])          # the repo's checkout, not the store's
        self.assertEqual(self.opened, [(self.repo, False, 'lane')])     # no fetch

    def test_the_main_coordinator_counts_and_offers_the_untagged_records_too(self):
        self.holding = [Rec('EX-1', 'game'), Rec('EX-2', None), Rec('EX-3', 'lane')]
        self.by_session = {'game': ['EX-G', 'EX-U'], None: ['EX-U', 'EX-N']}
        cap, flight, ids = self.slots('game', dict(COORD))
        self.assertEqual((flight, ids), (2, ['EX-G', 'EX-U', 'EX-N']))
        self.assertEqual(len(self.loads), 1)              # one store read for both offers

    def test_a_store_failure_is_unread(self):
        from kitlib.backlog import opener
        from kitlib.backlog.errors import Failure

        def boom(*a, **k):
            raise Failure('bad scope')
        with mock.patch.object(opener, 'open_store', boom), self.assertRaises(C.BacklogUnread):
            W.read_slots(self.settings, 'game', dict(COORD))
        with self.assertRaises(C.BacklogUnread):
            W.read_slots(self.settings, 'x', {'repo': 'nowhere'})

    def test_read_waits_gives_each_passed_over_pair_once_from_one_store_read(self):
        from kitlib.backlog import offer as omod
        from kitlib.backlog import opener, plan

        class R(object):
            def __init__(s, rid, **f):
                s.id, s.f = rid, f

            def get(s, name):
                return s.f.get(name)

        low = R('EX-L', priority='3', pulled_by='game/ex-l')
        high = R('EX-H', priority='1')
        passed = [omod.PassedOver(high, 1, 'why', waits=[low]), omod.PassedOver(R('EX-Q'), 2, 'q')]

        class Store(object):
            def load(s):
                self.loads.append(1)
                return 'snap'

        class Ctx(object):
            def __init__(s, root, session):
                s.root, s.remote, s.store, s.session = root, 'origin', Store(), session
        asked, why = [], [None]

        def offer_for(snap, ctx, cap=5, holding=None, narrow=False):
            asked.append((cap, narrow))
            return [], passed, holding, None
        with mock.patch.object(opener, 'open_store',
                               lambda root, fetch=True, session=None, *, probes:
                               Ctx(root, session)), \
                mock.patch.object(plan, 'offer_for', offer_for), \
                mock.patch.object(omod, 'in_flight_split',
                                  lambda snap, root, remote: ([low], [], why[0])):
            rows = W.read_waits(self.settings, 'game', dict(COORD))
            self.assertEqual(rows, [('EX-H', '1', 'EX-L', '3', 'game/ex-l')])   # once
            self.assertEqual(len(self.loads), 1)
            self.assertEqual(asked, [(None, True), (None, True)])   # `next --all`, narrowing
            why[0] = 'git failed'           # every pull would hold its scope: unread
            with self.assertRaises(C.BacklogUnread) as cm:
                W.read_waits(self.settings, 'game', dict(COORD))
            self.assertIn('git failed', str(cm.exception))

    def test_a_cap_that_cannot_be_read_is_unread(self):
        from kitlib.backlog import opener
        from kitlib.backlog.errors import Failure, Refusal
        for exc in (Refusal('backlog.cap must be a number'), Failure('.kit.json unreadable')):
            def bad(top, exc=exc):
                raise exc
            with mock.patch.object(opener, 'repo_cap', bad), self.assertRaises(C.BacklogUnread):
                W.read_slots(self.settings, 'game', dict(COORD))


class Make(unittest.TestCase):
    def job(self, push):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {'game': tmp}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            return W.make(mock.Mock(home=tmp, push=push)).run

    def test_deliver_reads_the_sent_state_and_no_push_object_is_none(self):
        sources = mock.Mock(home=None)
        sources.push.deliver.return_value = P.Sent(P.UNCERTAIN, 'game-coordinator-a1', 'sid', False)
        run = self.job(None)
        self.assertIsNone(run.deliver('game', 'text'))
        run = self.job(sources.push)
        self.assertEqual(run.deliver('game', 'text'), P.UNCERTAIN)
        sources.push.deliver.assert_called_once_with('game', 'text')

    def test_the_state_file_and_stand_down_are_the_repos(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {'game': tmp}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            run = W.make(mock.Mock(home=tmp, push=None)).run
        self.assertEqual(run.store.path, os.path.join(tmp, 'fill-watch.json'))
        with mock.patch.object(W, 'standdown', lambda top: 'at %s' % top):
            self.assertEqual(run.standdown('game', dict(COORD)), 'at %s' % tmp)
            with self.assertRaises(lsettings.LaunchError):
                run.standdown('x', {'repo': 'nowhere'})

    def test_told_states_are_push_written_and_uncertain(self):
        self.assertEqual(W.told_states(), (P.WRITTEN, P.UNCERTAIN))

    def test_no_repository_is_no_job(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            self.assertIsNone(W.make(mock.Mock(home=tmp, push=None)))

    def test_the_pull_the_reads_and_the_hand_offs_are_wired(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {'game': tmp}}, home=tmp)
        sources = mock.Mock(home=tmp, push=None, handoffs=None)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            run = W.make(sources).run
        self.assertIsNone(run.handoffs())       # the hub has not set its read yet: unread
        sources.handoffs = lambda: {'game': {'kind': 'stop'}}
        self.assertEqual(run.handoffs(), {'game': {'kind': 'stop'}})
        run.liveness = 'live'
        pulled, read = [], []
        with mock.patch.object(W, 'auto_pull', lambda *a: pulled.append(a) or ([], [])), \
                mock.patch.object(W, 'read_slots',
                                  lambda *a, **k: read.append((a, k)) or (5, 0, [])):
            run.pull('game', dict(COORD), ['EX-A'], 1, 'stop')
            run.read('game', dict(COORD))
        self.assertEqual(pulled, [(s, 'game', dict(COORD), ['EX-A'], 1, 'live', 'stop')])
        self.assertEqual(read, [((s, 'game', dict(COORD)), {'session_live': 'live'})])

    def test_a_job_named_fill_watch_every_five_minutes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {'game': tmp}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            job = W.make(mock.Mock(home=tmp, push=None))
        self.assertEqual((job.name, job.every_s), ('fill-watch', 300))


class Clock(object):
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Persist(Watch):
    """What was told, and when the episode began, kept in fill-watch.json across hub restarts."""

    def setUp(self):
        super(Persist, self).setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, W.STATE_FILE)
        self.clock = Clock()
        self.job = self.fresh()

    def fresh(self):
        """A new FillWatch over the same file: a hub restart."""
        return W.FillWatch(lambda: self.sessions, lambda: self.live, self.job.read,
                           self.job.deliver, store=W.FileState(self.path), now=self.clock)

    def saved(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def test_a_restart_reads_what_was_told_and_does_not_nudge_again(self):
        self.run_job()
        self.job = self.fresh()
        self.run_job()
        self.assertEqual(len(self.sent), 1)
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-D'])
        self.job = self.fresh()
        self.run_job()                      # a change after the restart is still pushed
        self.assertEqual(len(self.sent), 2)

    def test_the_file_holds_what_was_told_since_and_in_flight(self):
        self.run_job()
        self.assertEqual(self.saved(), {'game': {'told': [2, ['EX-A', 'EX-B', 'EX-C'], None],
                                                 'since': 1000.0, 'in_flight': 3}})

    def test_a_bad_state_file_fails_the_run_and_writes_nothing(self):
        for bad in ('{not json', '[]', '{"game": {"told": "x"}}', '{"game": 3}',
                    '{"game": {"told": [2, ["EX-A"], null], "since": "soon", "in_flight": 3}}'):
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(bad)
            with self.assertRaises(lsettings.LaunchError):
                self.run_job()
            with open(self.path, encoding='utf-8') as f:
                self.assertEqual(f.read(), bad)
        self.assertEqual(self.sent, [])

    def test_since_is_kept_across_a_restart_and_reset_when_in_flight_rises(self):
        self.run_job()
        self.clock.t = 1300.0
        self.job = self.fresh()
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-D'])      # ids only: same free count
        self.run_job()
        self.assertEqual((self.saved()['game']['since'], len(self.sent)), (1000.0, 2))
        self.clock.t = 1450.0
        self.slots['game'] = (6, 4, ['EX-A', 'EX-B', 'EX-D'])      # same free, one more pulled
        self.run_job()
        self.assertEqual((self.saved()['game']['since'], len(self.sent)), (1450.0, 2))
        self.clock.t = 1600.0
        self.slots['game'] = (6, 5, ['EX-B', 'EX-C'])              # it pulled: in flight rose
        self.run_job()
        self.assertEqual(self.saved()['game']['since'], 1600.0)
        self.assertEqual(self.saved()['game']['in_flight'], 5)

    def test_a_nudge_that_names_a_newly_freed_slot_restarts_since(self):
        self.run_job()
        self.clock.t = 1300.0
        self.slots['game'] = (5, 2, ['EX-A', 'EX-B', 'EX-C'])      # one landed: a slot freed
        self.run_job()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.saved()['game']['since'], 1300.0)
        self.assertEqual(self.saved()['game']['in_flight'], 2)

    def test_a_hub_stop_mid_run_keeps_the_keys_it_did_not_reach(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE)}
        self.live = {'game-coordinator-a1', 'lane-coordinator-a1'}
        self.slots['lane'] = (2, 0, ['EX-L'])
        self.run_job()
        before = self.saved()
        self.assertEqual(sorted(before), ['game', 'lane'])
        c = ctx()
        read = self.job.read

        def stop_after(key, entry):
            c.stop.set()                    # the hub stops while game is read
            return read(key, entry)
        self.job.read = stop_after
        self.clock.t = 1300.0
        self.job(c)
        self.assertEqual(self.saved(), before)
        self.job = self.fresh()
        self.run_job()
        self.assertEqual(len(self.sent), 2)                         # nobody nudged again

    def test_a_coordinator_the_crossover_claims_keeps_its_state(self):
        self.run_job()
        before = self.saved()
        self.job.claims = lambda: {'game'}
        self.clock.t = 1300.0
        self.run_job()
        self.assertEqual(self.saved(), before)
        self.job.claims = lambda: set()
        self.run_job()
        self.assertEqual(len(self.sent), 1)

    def test_a_key_that_ends_is_dropped_from_the_file(self):
        self.run_job()
        self.slots['game'] = (5, 5, ['EX-A'])
        self.run_job()
        self.assertEqual(self.saved(), {})

    def test_the_file_is_written_only_on_a_change(self):
        writes = []
        store = W.FileState(self.path)
        real = store.write
        store.write = lambda data: writes.append(1) or real(data)
        self.job = W.FillWatch(lambda: self.sessions, lambda: self.live, self.job.read,
                               self.job.deliver, store=store, now=self.clock)
        self.run_job()
        self.clock.t = 1300.0
        self.run_job()
        self.run_job()
        self.assertEqual(len(writes), 1)

    def test_a_stop_after_a_nudge_writes_that_nudge_and_leaves_the_rest(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE)}
        self.live = {'game-coordinator-a1', 'lane-coordinator-a1'}
        self.slots['lane'] = (2, 0, ['EX-L'])
        c = ctx()
        deliver = self.job.deliver

        def stop_after(key, text):
            c.stop.set()                    # the hub stops once game is nudged
            return deliver(key, text)
        self.job.deliver = stop_after
        self.job(c)
        self.assertEqual([k for k, _ in self.sent], ['game'])
        self.assertEqual(sorted(self.saved()), ['game'])           # lane was never reached
        self.job = self.fresh()
        self.run_job()
        self.assertEqual([k for k, _ in self.sent], ['game', 'lane'])   # game not nudged again

    def test_a_file_written_before_the_pull_marks_reads_and_pulls(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'game': {'told': [2, ['EX-A', 'EX-B', 'EX-C'], None], 'since': 100.0,
                                'in_flight': 3}}, f)
        pulls = []
        self.clock.t = 2000.0
        self.job = W.FillWatch(lambda: self.sessions, lambda: self.live, self.job.read,
                               self.job.deliver, store=W.FileState(self.path), now=self.clock,
                               pull=lambda *a: pulls.append(a) or ([], []))
        self.run_job()
        self.assertEqual(len(pulls), 1)
        self.assertEqual((self.saved()['game']['pulled'], self.saved()['game']['pm']),
                         (100.0, 100.0))

    def test_a_bad_pull_mark_fails_the_run(self):
        for name in ('pulled', 'pm'):
            for bad in ('"x"', 'true', '[1]', 'null'):
                with open(self.path, 'w', encoding='utf-8') as f:
                    f.write('{"game": {"told": [2, ["EX-A"], null], "since": 1.0, "in_flight": 3, '
                            '"%s": %s}}' % (name, bad))
                with self.assertRaises(lsettings.LaunchError):
                    self.run_job()

    def test_no_file_is_an_empty_state(self):
        self.assertFalse(os.path.exists(self.path))
        self.run_job()
        self.assertEqual(len(self.sent), 1)


class StandDownNudge(WatchFixture, unittest.TestCase):
    """The stand-down reason, read once per repository per run, rides in the nudge."""

    def setUp(self):
        super(StandDownNudge, self).setUp()
        self.reasons, self.asked = [], []

        def standdown(key, entry):
            self.asked.append(entry['repo'])
            got = self.reasons[0] if self.reasons else None
            if isinstance(got, Exception):
                raise got
            return got
        self.job.standdown = standdown

    def test_no_stand_down_leaves_the_nudge_as_it_was(self):
        self.run_job()
        self.assertNotIn('stood down', self.sent[0][1])

    def test_a_stand_down_reason_is_in_the_nudge(self):
        self.reasons = ['queued 3>=3']
        self.run_job()
        first, second = self.sent[0][1].splitlines()[:2]
        self.assertIn('fill: 2 free, 3 startable', first)
        self.assertEqual(second, 'auto-pull stood down: queued 3>=3')

    def test_a_change_of_reason_pushes_again(self):
        self.run_job()
        self.reasons = ['idle 2>=2']
        self.run_job()
        self.run_job()                      # the same reason: no second push
        self.reasons = []
        self.run_job()                      # stand-down over: pushed again
        self.assertEqual(len(self.sent), 3)

    def test_an_unread_stand_down_fails_closed(self):
        self.reasons = [RuntimeError('git said no')]
        out = self.run_quietly()
        self.assertIn('auto-pull stood down: unread', self.sent[0][1])
        self.assertIn('RuntimeError', out)

    def test_read_once_per_repository_and_only_with_a_free_slot_and_work(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE)}
        self.live = {'game-coordinator-a1', 'lane-coordinator-a1'}
        self.slots['lane'] = (2, 0, ['EX-L'])
        self.run_job()
        self.assertEqual(self.asked, ['game'])
        self.slots['game'] = self.slots['lane'] = (5, 5, ['EX-A'])
        self.run_job()
        self.assertEqual(self.asked, ['game'])


class StandDown(unittest.TestCase):
    """standdown(top): the limits from the main checkout's .kit.json, the counts from survey."""

    def setUp(self):
        self.top = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.top, True)

    def kit(self, backlog):
        with open(os.path.join(self.top, '.kit.json'), 'w', encoding='utf-8') as f:
            json.dump({'backlog': backlog}, f)

    def reason(self, queued, idle):
        return W.standdown(self.top, survey=lambda top, cfg: (queued, idle))

    def test_the_keys_default_to_three_queued_and_two_idle(self):
        self.assertEqual(W.read_limits(self.top), (3, 2))
        self.assertIsNone(self.reason(2, 1))
        self.assertEqual(self.reason(3, 0), 'queued 3>=3')
        self.assertEqual(self.reason(0, 2), 'idle 2>=2')
        self.assertEqual(self.reason(4, 5), 'queued 4>=3')

    def test_the_keys_are_read_from_the_checkout(self):
        self.kit({'fill_queued_max': 5, 'fill_idle_max': 4})
        self.assertEqual(W.read_limits(self.top), (5, 4))
        self.assertIsNone(self.reason(4, 3))
        self.assertEqual(self.reason(1, 4), 'idle 4>=4')

    def test_fill_queued_max_zero_is_off_without_a_survey(self):
        self.kit({'fill_queued_max': 0})

        def survey(top, cfg):
            raise AssertionError('surveyed')
        self.assertEqual(W.standdown(self.top, survey=survey), 'off')

    def test_a_config_that_cannot_be_read_is_unread(self):
        for bad in ({'fill_queued_max': -1}, {'fill_idle_max': 'two'}):
            self.kit(bad)
            with self.assertRaises(W.StandDownUnread):
                self.reason(0, 0)


def tree(branch, klass, head=None, gate=None, reason=None, idle_s=None):
    row = {'path': '/w/' + branch, 'branch': branch, 'head': head or 'h-' + branch,
           'class': klass}
    if reason is not None:
        row['reason'] = reason
    if idle_s is not None:
        row['idle_s'] = idle_s
    if gate is not None:
        row['gate'] = gate
    return row


class Survey(unittest.TestCase):
    """read_survey(top, cfg): (queued, idle) from the verdict queue and one --classify read."""

    def setUp(self):
        self.entries = []
        self.expired = set()
        self.trees = []
        self.green = set()
        self.in_main = set()
        self.dead_after = []
        self.main = ('m' * 40, None)
        self.common = ('/common', None)

    def survey(self):
        from kitlib.coordstate import render
        from kitlib.gate import verdicts
        from kitlib.git import refs
        from kitlib.records import verdict_entry
        from kitlib.git import repo
        from kitlib.worktree import classify

        def entries(common):
            if isinstance(self.entries, Exception):
                raise self.entries
            return [{'branch': b, 'tip': 't-' + b} for b in self.entries]

        def fake_classify(top, dead_after_s=None, **k):
            self.dead_after.append(dead_after_s)
            if isinstance(self.trees, dict):
                return classify.Result(1, [], self.trees)
            return classify.Result(1, [], {'trees': self.trees, 'counts': {}})

        def is_ancestor(a, d, cwd):
            return (a in self.in_main, None) if a != 'unknown' else (None, 'git failed')
        with mock.patch.object(render, 'main_as_fetched', lambda top: self.main), \
                mock.patch.object(repo, 'common_dir', lambda top: self.common), \
                mock.patch.object(verdicts, 'entries', entries), \
                mock.patch.object(verdict_entry, 'expired',
                                  lambda c, e, m: 'landed' if e['branch'] in self.expired else None), \
                mock.patch.object(classify, 'classify', fake_classify), \
                mock.patch.object(classify, 'gate_state',
                                  lambda main, head, cfg=None: 'green' if head in self.green
                                  else 'none'), \
                mock.patch.object(refs, 'is_ancestor', is_ancestor):
            return W.read_survey('/top', None)

    def test_queued_is_unexpired_verdicts_and_green_tips_not_in_main_once_each(self):
        self.entries = ['kit/a', 'kit/b', 'kit/gone']
        self.expired = {'kit/gone'}
        self.trees = [tree('kit/a', 'B', gate='green'),         # queued too: counted once
                      tree('kit/c', 'L'),                       # live, green, not in main
                      tree('kit/d', 'L'),                       # live, no gate-OK record
                      tree('kit/e', 'L'),                       # green but in main
                      tree('kit/f', 'C', gate='none')]
        self.green = {'h-kit/c', 'h-kit/e'}
        self.in_main = {'h-kit/e'}
        self.assertEqual(self.survey()[0], 3)                  # a, b, c

    def test_an_ancestry_git_cannot_tell_counts_as_queued(self):
        self.trees = [tree('kit/x', 'L', head='unknown')]
        self.green = {'unknown'}
        self.assertEqual(self.survey()[0], 1)

    def test_idle_counts_b_and_c_trees_at_two_hours_and_never_l(self):
        self.trees = [tree('kit/b', 'B', gate='green'), tree('kit/c', 'C', gate='none'),
                      tree('kit/l', 'L'), tree('kit/a', 'A'), tree('kit/e', 'E'),
                      tree('kit/o', 'O')]
        self.assertEqual(self.survey()[1], 2)
        self.assertEqual(self.dead_after, [7200])

    def test_a_classify_that_could_not_read_a_probe_is_unread(self):
        self.trees = {'trees': [tree('kit/b', 'L', reason='unreadable: the machine lock cannot be'
                                     ' read (x)')], 'counts': {},
                      'unreadable': 'the machine lock cannot be read (x)'}
        with self.assertRaises(W.StandDownUnread):
            self.survey()

    def test_a_tree_classify_could_not_read_is_unread(self):
        self.trees = [tree('kit/b', 'B', gate='green'),
                      tree('kit/x', 'L', reason='unreadable: its status (git failed)'),
                      tree('kit/l', 'L', reason='active 0.5 h ago')]
        with self.assertRaises(W.StandDownUnread):
            self.survey()

    HELD = ('unreadable: 2 process(es) could not be read and x cannot be read in full: a '
            'process we could not read may hold it')

    def test_an_unreadable_tree_dead_two_hours_counts_idle(self):
        self.trees = [tree('kit/b', 'B', gate='green'),
                      tree('kit/x', 'L', reason=self.HELD, idle_s=7200)]
        self.assertEqual(self.survey(), (1, 2))

    def test_an_unreadable_dead_tree_in_main_counts_neither_idle_nor_unread(self):
        self.trees = [tree('kit/x', 'L', reason=self.HELD, idle_s=9000)]
        self.in_main = {'h-kit/x'}              # landed: a tree awaiting removal
        self.assertEqual(self.survey(), (0, 0))

    def test_an_unreadable_dead_tree_git_cannot_place_counts_idle(self):
        self.trees = [tree('kit/x', 'L', head='unknown', reason=self.HELD, idle_s=9000)]
        self.assertEqual(self.survey()[1], 1)

    def test_an_unreadable_tree_under_the_bar_or_with_no_activity_is_unread(self):
        for why, idle_s in ((self.HELD, 7199), ('unreadable: its last activity (git)', None)):
            self.trees = [tree('kit/x', 'L', reason=why, idle_s=idle_s)]
            with self.assertRaises(W.StandDownUnread):
                self.survey()

    def test_anything_unread_is_unread(self):
        from kitlib.records import verdict_entry
        cases = [('main', (None, 'no origin/main')), ('common', (None, 'not a repo')),
                 ('entries', verdict_entry.VerdictError('bad file')),
                 ('trees', {'error': 'git cannot list the worktrees'})]
        for name, value in cases:
            self.setUp()
            setattr(self, name, value)
            with self.assertRaises(W.StandDownUnread):
                self.survey()


class EventCtx(object):
    def __init__(self):
        self.stop = threading.Event()
        self.events = []

    def event(self, word, detail=None):
        self.events.append((word, detail))


class AutoPull(WatchFixture, unittest.TestCase):
    """Stages 3 and 5: PULL_AFTER_S into an episode with no stand-down the hub pulls, once per
    episode; a stand-down, or slots the pull left free, tell the PM once per episode."""

    def setUp(self):
        super(AutoPull, self).setUp()
        self.clock = Clock()
        self.pulls = []
        self.pulled = [([('EX-A', 'game/fill-ex-a')], [('EX-B', 'refused')])]
        self.reasons = []
        self.handoffs = {}
        self.store = W.MemoryState()

        def pull(key, entry, ids, free, stop):
            self.pulls.append((key, list(ids), free, stop))
            got = self.pulled.pop(0) if self.pulled else ([], [])
            if isinstance(got, Exception):
                raise got
            return got

        def handoffs():
            if isinstance(self.handoffs, Exception):
                raise self.handoffs
            return self.handoffs
        self.job = W.FillWatch(lambda: self.sessions, lambda: self.live, self.job.read,
                               self.job.deliver, store=self.store, now=self.clock,
                               standdown=lambda key, entry: self.reasons[0] if self.reasons
                               else None, pull=pull, handoffs=handoffs)
        self.c = EventCtx()

    def run_at(self, t):
        self.clock.t = t
        self.c = EventCtx()
        return self.job(self.c)

    def test_twenty_minutes_after_the_episode_began_it_pulls_once(self):
        self.assertEqual(W.PULL_AFTER_S, 1200)
        self.run_at(1000.0)
        self.run_at(1000.0 + 1199)
        self.assertEqual(self.pulls, [])
        self.run_at(1000.0 + 1200)
        self.assertEqual([(k, ids, free) for k, ids, free, _ in self.pulls],
                         [('game', ['EX-A', 'EX-B', 'EX-C'], 2)])
        self.run_at(1000.0 + 1500)              # the same episode: never twice
        self.assertEqual(len(self.pulls), 1)

    def test_the_push_names_what_was_pulled_and_what_was_skipped(self):
        self.run_at(1000.0)
        out = self.run_at(2200.0)
        key, text = self.sent[-1]
        lines = text.splitlines()
        self.assertEqual((key, lines[0]),
                         ('game', '[hub push] fill: pulled EX-A as game/fill-ex-a route:game'))
        self.assertEqual(lines[1], 'skipped EX-B refused')
        self.assertIn('dispatch', lines[-1])
        self.assertNotIsInstance(out, jobs.Skip)
        self.assertIn('pulled', out)

    def test_a_new_episode_pulls_again(self):
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.slots['game'] = (5, 4, ['EX-B', 'EX-C'])           # the pull is in flight
        self.run_at(2500.0)                                     # the episode begins again
        self.run_at(2500.0 + 1199)
        self.assertEqual(len(self.pulls), 1)
        self.run_at(2500.0 + 1200)
        self.assertEqual(len(self.pulls), 2)

    def test_slots_left_free_tell_the_pm_once_per_episode(self):
        self.run_at(1000.0)
        self.run_at(2200.0)                     # one pulled of two free
        self.assertEqual(self.c.events, [('fill', 'game:1:unfilled')])
        self.run_at(2500.0)
        self.assertEqual(self.c.events, [])

    def test_every_slot_filled_tells_the_pm_nothing(self):
        self.pulled = [([('EX-A', 'game/fill-ex-a'), ('EX-B', 'game/fill-ex-b')], [])]
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual(self.c.events, [])

    def test_slots_taken_by_not_open_skips_are_not_unfilled(self):
        self.pulled = [([], [('EX-A', 'not-open'), ('EX-B', 'not-open')])]
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual(self.c.events, [])

    def test_an_unsure_skip_takes_a_slot_and_the_rest_is_unfilled(self):
        self.slots['game'] = (5, 2, ['EX-A', 'EX-B', 'EX-C'])   # free 3
        self.pulled = [([('EX-A', 'game/fill-ex-a')], [('EX-B', 'unsure+tree-kept')])]
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual(self.c.events, [('fill', 'game:1:unfilled')])

    def test_nothing_pulled_and_nothing_skipped_sends_no_pull_push(self):
        self.pulled = [([], [])]
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual(len(self.sent), 1)     # the nudge alone
        self.assertEqual(self.c.events, [('fill', 'game:2:unfilled')])

    def test_a_stand_down_never_pulls_and_tells_the_pm_once(self):
        self.reasons = ['queued 3>=3']
        self.run_at(1000.0)
        self.run_at(1000.0 + 1199)
        self.assertEqual(self.c.events, [])
        self.run_at(2200.0)
        self.assertEqual(self.c.events, [('fill', 'game:2:queued')])
        self.run_at(2500.0)
        self.assertEqual((self.c.events, self.pulls), ([], []))

    def test_fill_queued_max_zero_is_off(self):
        self.reasons = ['off']
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual((self.c.events, self.pulls), ([('fill', 'game:2:off')], []))

    def test_a_pending_hand_off_stands_down_and_is_named(self):
        self.handoffs = {'game': {'kind': 'restart', 'from': 'game'}}
        self.run_at(1000.0)
        self.assertEqual(self.sent[0][1].splitlines()[1], 'auto-pull stood down: handoff restart')
        self.run_at(2200.0)
        self.assertEqual((self.c.events, self.pulls), ([('fill', 'game:2:handoff')], []))

    def test_a_session_started_after_the_hand_off_ends_it_with_no_check_in(self):
        # a stop, then a fresh session (launch.py start or a coord-restart wake): no check-in
        at = '2026-11-04T09:00:00+00:00'
        stamp = C.item_epoch({'at': at})
        self.handoffs = {'game': {'kind': 'stop', 'at': at}}
        self.live = {'game-coordinator-a1': stamp + 60}
        self.run_at(1000.0)
        self.assertNotIn('stood down', self.sent[0][1])
        self.run_at(2200.0)
        self.assertEqual(len(self.pulls), 1)

    def test_a_session_older_than_the_hand_off_or_unread_times_keep_it_pending(self):
        at = '2026-11-04T09:00:00+00:00'
        stamp = C.item_epoch({'at': at})
        for live, rec in (({'game-coordinator-a1': stamp - 60}, {'kind': 'stop', 'at': at}),
                          ({'game-coordinator-a1': None}, {'kind': 'stop', 'at': at}),
                          ({'game-coordinator-a1': stamp + 60}, {'kind': 'stop', 'at': 'bad'}),
                          ({'game-coordinator-a1'}, {'kind': 'stop', 'at': at})):
            self.setUp()
            self.handoffs, self.live = {'game': rec}, live
            self.run_at(1000.0)
            self.assertIn('auto-pull stood down: handoff stop', self.sent[0][1])
            self.run_at(2200.0)
            self.assertEqual(self.pulls, [])

    def test_a_check_in_or_another_keys_hand_off_is_none(self):
        self.handoffs = {'game': {'kind': 'checkin'}, 'lane': {'kind': 'stop'}}
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual(len(self.pulls), 1)
        self.assertNotIn('stood down', self.sent[0][1])

    def test_hand_offs_that_cannot_be_read_stand_down(self):
        for bad in (None, RuntimeError('hub store')):
            self.setUp()
            self.handoffs = bad
            self.run_at(1000.0)
            self.assertIn('auto-pull stood down: handoff unread', self.sent[0][1])
            self.run_at(2200.0)
            self.assertEqual(self.pulls, [])

    def test_the_pull_is_marked_in_the_state_before_it_is_made(self):
        self.run_at(1000.0)
        since = self.store.read()['game']['since']
        seen = []
        pull = self.job.pull

        def look(*a):
            seen.append(self.store.read()['game'].get('pulled'))
            return pull(*a)
        self.job.pull = look
        self.run_at(2200.0)
        self.assertEqual(seen, [since])

    def test_a_pull_that_raises_is_a_note_never_retried_in_the_episode(self):
        self.pulled = [RuntimeError('git said no')]
        self.run_at(1000.0)
        out = self.run_at(2200.0)
        self.assertIn('RuntimeError', out.note if isinstance(out, jobs.Skip) else out)
        self.assertEqual(self.c.events, [('fill', 'game:2:unfilled')])
        self.run_at(2500.0)
        self.assertEqual(len(self.pulls), 1)

    def test_the_pull_is_given_the_hub_stop(self):
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertIs(self.pulls[0][3], self.c.stop)

    def test_each_run_judges_session_liveness_from_its_own_listing(self):
        self.sessions = {'game': dict(COORD), 'lane': dict(LANE)}
        self.slots['lane'] = (1, 1, [])
        self.run_at(1000.0)
        first = self.job.liveness
        self.assertEqual(first.live('game'), (True, None))
        self.assertEqual(first.live('lane'), (False, None))
        self.assertIsNone(first.live('nobody')[0])          # not in sessions.json: unread
        self.live = {'game-coordinator-a1', 'lane-coordinator-a1'}
        self.run_at(1300.0)
        self.assertIsNot(self.job.liveness, first)
        self.assertEqual(self.job.liveness.live('lane'), (True, None))

    def test_no_pull_wired_is_the_watch_alone(self):
        self.job.pull = None
        self.run_at(1000.0)
        self.run_at(2200.0)
        self.assertEqual((self.c.events, self.pulls), ([], []))


class Waits(WatchFixture, unittest.TestCase):
    """The waits notices: a record waiting on a lower live pull it outranks is one push
    to the coordinator per pair, and one `fill` event to the PM 24 h after first sight."""

    ROW = ('EX-H', '1', 'EX-L', '3', 'game/ex-l')

    def setUp(self):
        super(Waits, self).setUp()
        self.slots = {'game': (3, 3, ['EX-A'])}     # no free slot: the notice runs anyway
        self.rows = {'game': [self.ROW]}
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, W.STATE_FILE)
        self.clock = Clock()
        self.job = self.fresh()
        self.c = EventCtx()

    def waits(self, key, entry):
        got = self.rows[key]
        if isinstance(got, Exception):
            raise got
        return list(got)

    def fresh(self):
        """A new FillWatch over the same file: a hub restart."""
        return W.FillWatch(lambda: self.sessions, lambda: self.live, self.job.read,
                           self.job.deliver, store=W.FileState(self.path), now=self.clock,
                           waits=self.waits)

    def run_at(self, t):
        self.clock.t = t
        self.c = EventCtx()
        return self.job(self.c)

    def saved(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def test_a_pair_first_seen_is_one_push_with_the_mark_and_no_stop(self):
        out = self.run_at(1000.0)
        self.assertEqual(len(self.sent), 1)
        key, text = self.sent[0]
        self.assertEqual(key, 'game')
        self.assertIn('p1 EX-H waits on p3 pull EX-L on game/ex-l (lands first)', text)
        self.assertIn('route:game', text)
        self.assertIn('Never stop or override the live pull', text)
        self.assertIn('waits game (EX-H>EX-L)', out)
        self.run_at(2000.0)
        self.job = self.fresh()             # a hub restart reads first sight from the file
        self.run_at(3000.0)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.saved()[W.WAITS],
                         {'game': {'EX-H>EX-L': {'since': 1000.0, 'told': True, 'pm': False}}})

    def test_the_pm_hears_once_24_hours_after_first_sight(self):
        self.assertEqual(W.WAITS_PM_AFTER_S, 86400)
        self.run_at(1000.0)
        self.run_at(1000.0 + 86399)
        self.assertEqual(self.c.events, [])
        self.job = self.fresh()
        self.run_at(1000.0 + 86400)
        self.assertEqual(self.c.events, [('fill', 'game:waits:EX-H:EX-L')])
        self.run_at(1000.0 + 90000)
        self.assertEqual(self.c.events, [])
        self.assertEqual(len(self.sent), 1)

    def test_a_pair_gone_is_dropped_and_its_return_is_a_new_first_sight(self):
        self.run_at(1000.0)
        self.rows['game'] = []
        self.run_at(2000.0)
        self.assertNotIn(W.WAITS, self.saved())
        self.rows['game'] = [self.ROW]
        self.run_at(3000.0)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.saved()[W.WAITS]['game']['EX-H>EX-L']['since'], 3000.0)

    def test_an_unread_store_keeps_the_pairs_and_a_dead_coordinator_drops_them(self):
        self.run_at(1000.0)
        self.rows['game'] = C.BacklogUnread('x')
        self.run_at(2000.0)
        self.assertIn('EX-H>EX-L', self.saved()[W.WAITS]['game'])
        self.live = set()
        self.run_at(3000.0)
        self.assertNotIn(W.WAITS, self.saved())

    def test_a_push_not_written_is_tried_again_next_run(self):
        self.states = [P.NOT_WRITTEN]
        self.run_at(1000.0)
        self.assertFalse(self.saved()[W.WAITS]['game']['EX-H>EX-L']['told'])
        self.run_at(2000.0)
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.saved()[W.WAITS]['game']['EX-H>EX-L']['told'])

    def test_a_bad_waits_state_raises_before_anything_is_sent(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({W.WAITS: {'game': {'EX-H>EX-L': {'since': 1.0}}}}, f)
        with self.assertRaises(lsettings.LaunchError):
            self.run_at(1000.0)
        self.assertEqual(self.sent, [])


class PullText(unittest.TestCase):
    def test_ids_branches_and_codes_only(self):
        text = W.pull_text('game', [('EX-A', 'game/fill-ex-a')], [('EX-B', 'not-theirs')])
        self.assertEqual(text.splitlines()[:2], ['[hub push] fill: pulled EX-A as game/fill-ex-a '
                                                 'route:game', 'skipped EX-B not-theirs'])
        self.assertIn('pulled none route:game', W.pull_text('game', [], [('EX-B', 'refused')]))

    def test_the_fill_event_is_owed_once_per_detail_with_its_text(self):
        self.assertIn('fill', P.JOB_EACH)
        self.assertIn('unfilled', P.JOB_EACH['fill'])


class AutoPullSteps(unittest.TestCase):
    """auto_pull over stand-ins: the branch, the tree made before the pull, removed on a skip,
    the count up to `free`, the stop between records."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.settings = lsettings.Settings({'state_dir': self.tmp, 'repos': {'game': self.tmp}},
                                           home=self.tmp)
        self.made, self.asked, self.removed = [], [], []
        self.codes, self.create_code, self.kept = {}, {}, set()
        self.stop = threading.Event()
        self.stop_on_remove = False

    def run_pull(self, ids, free, key='game', entry=None):
        from kitlib.worktree import new

        def create(main, branch):
            self.made.append((main, branch))
            return new.Outcome(self.create_code.get(branch, new.OK), ['said'])

        def pull(root, rid, branch, sessions, liveness):
            self.asked.append((root, rid, branch, sessions, liveness))
            got = self.codes.get(rid)
            if isinstance(got, BaseException):
                raise got
            return got

        def remove(main, branch):
            self.removed.append(branch)
            if self.stop_on_remove:
                self.stop.set()
            return branch not in self.kept
        with mock.patch.object(C, 'store_root', lambda settings, repo: '/store'):
            return W.auto_pull(self.settings, key, entry or dict(COORD), ids, free, 'live',
                               self.stop, create=create, pull=pull, remove=remove)

    def test_each_record_gets_its_tree_then_its_pull_up_to_free(self):
        got = self.run_pull(['EX-A', 'EX-B', 'EX-C'], 2)
        self.assertEqual(got, ([('EX-A', 'game/fill-ex-a'), ('EX-B', 'game/fill-ex-b')], []))
        self.assertEqual(self.made, [(self.tmp, 'game/fill-ex-a'), (self.tmp, 'game/fill-ex-b')])
        self.assertEqual(self.asked[0], ('/store', 'EX-A', 'game/fill-ex-a', ['game', None],
                                         'live'))

    def test_a_skipped_pull_removes_its_tree_and_the_next_is_tried(self):
        self.codes = {'EX-A': 'refused', 'EX-B': 'not-theirs'}
        self.kept = {'game/fill-ex-b'}
        got = self.run_pull(['EX-A', 'EX-B', 'EX-C'], 1)
        self.assertEqual(got, ([('EX-C', 'game/fill-ex-c')],
                               [('EX-A', 'refused'), ('EX-B', 'not-theirs+tree-kept')]))
        self.assertEqual(self.removed, ['game/fill-ex-a', 'game/fill-ex-b'])

    def test_a_record_already_not_open_takes_a_slot_so_the_cap_holds(self):
        # the coordinator pulled it between the offer and the hub's pull: one slot fewer
        self.codes = {'EX-A': 'not-open'}
        got = self.run_pull(['EX-A', 'EX-B', 'EX-C'], 2)
        self.assertEqual(got, ([('EX-B', 'game/fill-ex-b')], [('EX-A', 'not-open')]))
        self.assertEqual([a[1] for a in self.asked], ['EX-A', 'EX-B'])
        self.assertEqual(self.removed, ['game/fill-ex-a'])

    def test_a_pull_that_may_have_landed_keeps_its_tree_and_takes_a_slot(self):
        self.codes = {'EX-A': 'unsure'}
        got = self.run_pull(['EX-A', 'EX-B'], 1)
        self.assertEqual(got, ([], [('EX-A', 'unsure+tree-kept')]))
        self.assertEqual((self.removed, [a[1] for a in self.asked]), ([], ['EX-A']))

    def test_a_pull_that_raises_removes_its_tree_and_raises(self):
        self.codes = {'EX-A': RuntimeError('boom')}
        with self.assertRaises(RuntimeError):
            self.run_pull(['EX-A', 'EX-B'], 2)
        self.assertEqual((self.removed, [a[1] for a in self.asked]), (['game/fill-ex-a'], ['EX-A']))

    def test_a_tree_not_made_is_skipped_and_never_pulled(self):
        from kitlib.worktree import new
        self.create_code = {'game/fill-ex-a': new.REFUSED}
        got = self.run_pull(['EX-A', 'EX-B'], 1)
        self.assertEqual(got, ([('EX-B', 'game/fill-ex-b')], [('EX-A', 'worktree')]))
        self.assertEqual([a[1] for a in self.asked], ['EX-B'])
        self.assertEqual(self.removed, [])

    def test_the_branch_is_the_coordinators_key_lowercased(self):
        got = self.run_pull(['EX-UP'], 1, key='Kit', entry=dict(COORD, repo='game'))
        self.assertEqual(got[0], [('EX-UP', 'kit/fill-ex-up')])

    def test_a_branch_new_worktree_would_refuse_is_skipped(self):
        got = self.run_pull(['EX_A'], 1, key='_lane')
        self.assertEqual(got, ([], [('EX_A', 'branch')]))
        self.assertEqual(self.made, [])

    def test_a_stop_ends_it_between_records(self):
        self.codes = {'EX-A': 'refused'}
        self.stop_on_remove = True
        got = self.run_pull(['EX-A', 'EX-B'], 1)
        self.assertEqual(got, ([], [('EX-A', 'refused')]))
        self.assertEqual(len(self.made), 1)


class FakeRec(dict):
    def all(self, key):
        got = self.get(key)
        return [got] if got else []


class PullRecordCodes(unittest.TestCase):
    """pull_record's codes over a stand-in store: the checks made in the plan, and each store
    error: a write that surely did not land is `store`, one that may have is `unsure`."""

    def setUp(self):
        self.snap = {'EX-A': FakeRec(state='open', session='game'),
                     'EX-H': FakeRec(state='open', session='game', blocked_on='owner'),
                     'EX-P': FakeRec(state='pulled', session='game'),
                     'EX-O': FakeRec(state='pulled', session='lane')}
        self.raises, self.planned = None, []
        test = self

        class Store(object):
            backend = mock.Mock(implicit=False)

            def write(self, plan):
                if test.raises is not None:
                    raise test.raises
                return plan(test.snap)

        class Ctx(object):
            store = Store()

        def plan_pull(snap, ctx, rid):
            self.planned.append((rid, ctx.offer_session, ctx.session_live))
        from kitlib.backlog import opener, pull_branch
        for target, name, value in ((opener, 'open_store', lambda *a, **k: Ctx()),
                                    (pull_branch, 'plan_pull', plan_pull)):
            patch = mock.patch.object(target, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def code(self, rid):
        return W.pull_record('/store', rid, 'game/fill-x', ['game', None], 'live')

    def test_an_open_record_of_the_coordinators_is_planned_under_its_session(self):
        self.assertIsNone(self.code('EX-A'))
        self.assertEqual(self.planned, [('EX-A', 'game', 'live')])

    def test_the_plans_own_checks(self):
        self.assertEqual([self.code(r) for r in ('EX-H', 'EX-P', 'EX-O', 'NOPE')],
                         ['held', 'not-open', 'not-theirs', 'gone'])
        self.assertEqual(self.planned, [])

    def test_a_write_that_surely_did_not_land_is_store(self):
        from kitlib.backlog import errors as E
        for exc in (E.Busy('lock'), E.Dirty('dirty'), E.Overtaken('moved')):
            self.raises = exc
            self.assertEqual(self.code('EX-A'), 'store', type(exc).__name__)

    def test_a_write_that_may_have_landed_is_unsure(self):
        from kitlib.backlog import errors as E
        for exc in (E.Failure('push reported failure: the write may have landed'),
                    E.Uncommitted('commit failed')):
            self.raises = exc
            self.assertEqual(self.code('EX-A'), 'unsure', type(exc).__name__)

    def test_a_stale_index_is_pulled_and_a_refusal_refused(self):
        from kitlib.backlog import errors as E
        self.raises = E.IndexStale('index')
        self.assertIsNone(self.code('EX-A'))
        self.raises = E.Refusal('not offered')
        self.assertEqual(self.code('EX-A'), 'refused')


class LiveNames(unittest.TestCase):
    def test_each_live_session_with_its_earliest_start(self):
        from kitlib.launcher import lookup
        rows = [{'CommandLine': 'a', 'Created': '2026-11-04T09:00:00+00:00'},
                {'CommandLine': 'a', 'Created': '2026-11-04T08:00:00+00:00'},
                {'CommandLine': 'b', 'Created': 'bad'},
                {'CommandLine': None}]
        with mock.patch.object(C, 'list_processes', lambda: rows), \
                mock.patch.object(lookup, 'launched', lambda ps: ps), \
                mock.patch.object(lookup, 'rc_name', lambda line: line):
            got = W.live_names()
        self.assertEqual(got, {'a': C.item_epoch({'at': '2026-11-04T08:00:00+00:00'}), 'b': None})


class PullOnAStore(unittest.TestCase):
    """auto_pull and pull_record against a ref store on a temp repository with a bare origin:
    the tree is made, the record pulled onto its branch under Session hub-fill and the
    coordinator's offer; a pull the store refuses removes the tree it made."""

    def setUp(self):
        from kitlib.backlog import record as rmod
        from kitlib.backlog import store as smod
        from tests.backlog import helpers as bh
        from tests.home import temp_home
        patch = mock.patch.dict(os.environ, gitfixture.fixture_env(), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.home = temp_home(self)
        self.repo = gitfixture.TempRepo(bare_remote=True)
        self.addCleanup(self.repo.cleanup)
        self.root = os.path.realpath(self.repo.root)
        bh.kit_json_ref(self.repo)
        self.repo.git('push', '-q', 'origin', 'main')
        self.backend = smod.RefBackend(self.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        self.store = smod.Store(self.backend, rmod.Rules())
        writes = {}
        for r in (bh.priced('FOO', session='kit'), bh.priced('BAR', session='other'),
                  bh.priced('BAZ', session='kit', scope='area/shared/**'),
                  bh.priced('LIVE', state='pulled', pulled_at=bh.TODAY, pulled_by='kit/live',
                            scope='area/shared/**', session='kit')):
            writes.update(r)
        self.backend.publish(self.store.load(), writes, 'seed\n')
        self.repo.git('branch', 'kit/live')
        self.settings = lsettings.Settings({'state_dir': self.home, 'repos': {'kit': self.root}},
                                           home=self.home)
        self.liveness = omod.SessionLiveness(sessions=lambda: {'kit': 'kit-coord'},
                                             names=lambda: ['kit-coord-a1'])
        self.entry = {'repo': 'kit', 'agent': True, 'stem': 'kit-coord', 'name': 'kit-coord-a1'}

    def pull(self, ids, free):
        import functools
        from kitlib.worktree import new
        create = functools.partial(new.create, wait=False, prep=lambda main, folders: [])
        return W.auto_pull(self.settings, 'kit', self.entry, ids, free, self.liveness,
                           threading.Event(), create=create)

    def trees(self):
        from kitlib.worktree import remove
        return {e['branch']: e['path']
                for e in remove.worktree_entries(remove.worktree_listing(self.root))}

    def branch(self, name):
        return self.repo.git('rev-parse', '--verify', '--quiet', 'refs/heads/' + name,
                             check=False)

    def test_a_record_in_the_offer_is_pulled_onto_its_own_tree(self):
        from kitlib.backlog import audit_check, opener
        from kitlib.backlog import store as smod
        got = self.pull(['FOO'], 1)
        self.assertEqual(got, ([('FOO', 'kit/fill-foo')], []))
        self.assertTrue(os.path.isdir(self.trees()['kit/fill-foo']))
        rec = self.store.load().get('FOO')
        self.assertEqual((rec.get('state'), rec.get('pulled_by'), rec.get('session')),
                         ('pulled', 'kit/fill-foo', 'kit'))
        tip = self.repo.git('ls-remote', 'origin', smod.STORE_REF).split('\t')[0]
        self.assertIn('Session: hub-fill', self.repo.git('log', '-1', '--format=%B', tip))
        top, cfg, rules = opener.open_repo(self.root)
        report = audit_check.check(smod.open_backend(top, cfg, fetch=False), rules, [], 0)
        self.assertEqual(report.changes, 2)                 # the seed and the pull
        self.assertEqual([f for f in report.findings if tip[:10] in f.text], [])

    def test_a_refused_pull_removes_the_tree_and_branch_it_made(self):
        got = self.pull(['BAZ'], 1)             # collides with the live pull on kit/live
        self.assertEqual(got, ([], [('BAZ', 'refused')]))
        self.assertNotIn('kit/fill-baz', self.trees())
        self.assertEqual(self.branch('kit/fill-baz'), '')
        from kitlib.backlog import opener
        cfg = opener.open_repo(self.root)[1]
        self.assertFalse(os.path.exists(
            os.path.join(self.root, cfg.repo['worktrees_dir'], 'fill-baz')))
        self.assertEqual(self.store.load().get('BAZ').get('state'), 'open')

    def test_another_sessions_record_and_a_missing_one_are_skipped(self):
        got = self.pull(['BAR', 'NOPE'], 2)
        self.assertEqual(got, ([], [('BAR', 'not-theirs'), ('NOPE', 'gone')]))
        self.assertEqual(self.store.load().get('BAR').get('state'), 'open')
        trees = self.trees()
        self.assertNotIn('kit/fill-bar', trees)
        self.assertNotIn('kit/fill-nope', trees)
        self.assertEqual((self.branch('kit/fill-bar'), self.branch('kit/fill-nope')), ('', ''))

    def test_a_skip_moves_on_to_the_next_record_up_to_free(self):
        got = self.pull(['BAZ', 'FOO', 'BAR'], 1)
        self.assertEqual(got, ([('FOO', 'kit/fill-foo')], [('BAZ', 'refused')]))
        self.assertNotIn('kit/fill-bar', self.trees())


class WaitsOnAStore(_bh.StoreCase):
    """read_waits on a real store and branch: HOLD (p2) is pulled on kit/holder, whose tree
    changed kitlib/x/a.py; FOO (p1, kitlib/**) overlaps it, but `next`'s narrowing clears it,
    so it waits on nothing."""

    def setUp(self):
        super().setUp()
        for p in ('kitlib/x/a.py', 'kitlib/y/d.py'):
            self.repo.write(p, 'x\n')
        self.repo.commit('code', 'kitlib')
        self.repo.git('push', '-q', 'origin', 'main')
        wt = self.repo.worktree('holder', 'kit/holder')
        self.repo.write('kitlib/x/a.py', 'changed\n', root=wt)
        self.repo.commit('work', 'kitlib/x/a.py', cwd=wt)
        self.seed(_bh.priced('HOLD', state='pulled', pulled_at=_bh.TODAY,
                             pulled_by='kit/holder', scope='kitlib/x/**'),
                  _bh.priced('FOO', priority='1', scope='kitlib/**'))

    def test_a_record_the_narrowing_clears_is_not_a_waits_pair(self):
        from kitlib.backlog import opener, plan
        _o, passed, _h, _w = plan.offer_for(self.store.load(), self.ctx)      # unnarrowed
        self.assertEqual([(p.rec.id, [w.id for w in p.waits]) for p in passed],
                         [('FOO', ['HOLD'])])
        with mock.patch.object(C, 'store_root', lambda settings, repo: self.repo.root), \
                mock.patch.object(opener, 'open_store',
                                  lambda root, fetch=True, session=None, *, probes: self.ctx):
            self.assertEqual(W.read_waits(None, 'game', dict(COORD)), [])


class Keys(unittest.TestCase):
    def test_the_two_keys_are_in_the_schema_with_their_defaults(self):
        from kitlib.config import schema
        backlog = schema.SECTIONS['backlog']
        self.assertIn('fill_queued_max', backlog)
        self.assertIn('fill_idle_max', backlog)
        self.assertEqual((backlog['fill_queued_max'].default, backlog['fill_idle_max'].default),
                         (3, 2))


class HoldAndUsage(unittest.TestCase):
    """The quiet stand-downs through make's own stand-down read: a PM hold (fill-hold.json) or
    every account at the usage limit sends no nudge and pulls nothing; once it ends, the same run
    nudges and then pulls."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = lsettings.Settings({'state_dir': self.tmp, 'repos': {'game': self.tmp}},
                                    home=self.tmp)
        self.path = os.path.join(self.tmp, W.HOLD_FILE)
        self.rows = []
        for target, attr, value in ((W, 'standdown', lambda top: None),
                                    (W.A, 'week_rows', lambda s, now: self.rows)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(lsettings, 'load', lambda home=None: self.s):
            self.job = W.make(mock.Mock(home=self.tmp, push=None, handoffs=lambda: {})).run
        self.sent, self.pulls, self.events, self.clock = [], [], [], Clock()

        def deliver(key, text):
            self.sent.append(text)
            return P.WRITTEN

        def pull(key, entry, ids, free, stop):
            self.pulls.append(list(ids))
            return [(ids[0], W.fill_branch(key, ids[0]))], []
        self.job.sessions = lambda: {'game': dict(COORD)}
        self.job.live = lambda: {'game-coordinator-a1'}
        self.job.read = lambda key, entry: (5, 3, ['EX-A', 'EX-B'])
        self.job.deliver, self.job.pull, self.job.now = deliver, pull, self.clock
        self.job.store, self.job.waits = W.MemoryState(), None

    def run_at(self, t):
        self.clock.t = t
        ctx = EventCtx()
        out = self.job(ctx)
        self.events.extend(ctx.events)
        return out

    def assert_quiet(self, *times):
        for t in times:
            out = self.run_at(t)
            self.assertIsInstance(out, jobs.Skip)
        self.assertEqual((self.sent, self.pulls), ([], []))

    def assert_fills_from(self, t):
        self.run_at(t)
        self.assertEqual(len(self.sent), 1)             # the nudge
        self.assertEqual(self.pulls, [])
        self.run_at(t + W.PULL_AFTER_S)
        self.assertEqual(self.pulls, [['EX-A', 'EX-B']])

    def test_a_repo_hold_stops_the_nudge_and_the_pull_until_cleared(self):
        W.set_hold(self.path, 'game', 'owner hold', None, 900.0)
        self.assert_quiet(1000.0, 2200.0, 5000.0)
        self.assertTrue(W.clear_hold(self.path, 'game'))
        self.assert_fills_from(6000.0)

    def test_a_machine_hold_ends_at_its_until(self):
        W.set_hold(self.path, None, 'owner hold', 3000.0, 900.0)
        self.assert_quiet(1000.0, 2200.0, 2999.0)
        self.assert_fills_from(3000.0)

    def test_another_repos_hold_does_not_hold_this_one(self):
        W.set_hold(self.path, 'tools', 'owner hold', None, 900.0)
        self.assert_fills_from(1000.0)

    def test_a_hold_that_cannot_be_read_fails_closed(self):
        for bad in ('not json', '{"machine": {"reason": 3}}', '{"other": {}}'):
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(bad)
            self.job.store = W.MemoryState()
            self.sent = []
            self.run_at(1000.0)
            self.run_at(1000.0 + W.PULL_AFTER_S)
            self.assertEqual(self.pulls, [])
            self.assertIn('auto-pull stood down: unread', self.sent[0])

    def test_every_account_at_the_limit_stands_down(self):
        self.rows = [('1', 97, None), ('2', W.USAGE_LIMIT, None)]
        self.assert_quiet(1000.0, 2200.0)

    def test_one_account_with_headroom_keeps_it_going(self):
        self.rows = [('1', 97, None), ('2', 40, None)]
        self.assert_fills_from(1000.0)

    def test_an_unknown_week_counts_as_headroom(self):
        self.rows = [('1', 97, None), ('2', None, None)]
        self.assert_fills_from(1000.0)

    def test_readings_that_cannot_be_read_count_as_headroom(self):
        def broken(s, now):
            raise lsettings.LaunchError('accounts unreadable')
        with mock.patch.object(W.A, 'week_rows', broken):
            self.assert_fills_from(1000.0)

    def test_a_hold_after_the_nudge_starts_the_episode_afresh_once_cleared(self):
        self.run_at(1000.0)
        self.assertEqual(len(self.sent), 1)             # nudged; the pull is due at 2200
        W.set_hold(self.path, 'game', 'owner hold', None, 1100.0)
        self.run_at(1200.0)
        self.run_at(1000.0 + W.PULL_AFTER_S + 1300.0)   # well past the first episode's pull
        self.assertEqual((len(self.sent), self.pulls), (1, []))
        self.assertTrue(W.clear_hold(self.path, 'game'))
        self.run_at(3600.0)
        self.assertEqual(len(self.sent), 2)             # told afresh, not the old episode
        self.run_at(3600.0 + W.PULL_AFTER_S - 1)
        self.assertEqual(self.pulls, [])
        self.run_at(3600.0 + W.PULL_AFTER_S)
        self.assertEqual(self.pulls, [['EX-A', 'EX-B']])

    def test_a_hold_or_usage_beats_a_repository_stand_down(self):
        """A repository stand-down alone nudges and tells the PM; a hold or usage over it is
        silent: no nudge, no PM event, nothing pulled."""
        def held():
            W.set_hold(self.path, 'game', 'owner hold', None, 900.0)

        def used():
            self.rows = [('1', 99, None)]
        for quiet in (held, used):
            quiet()
            with mock.patch.object(W, 'standdown', lambda top: 'queued 3>=3'):
                self.assert_quiet(1000.0, 1000.0 + W.PULL_AFTER_S, 1000.0 + 2 * W.PULL_AFTER_S)
            self.assertEqual(self.events, [])
            W.clear_hold(self.path, 'game')
            self.rows = []
        with mock.patch.object(W, 'standdown', lambda top: 'queued 3>=3'):
            self.run_at(5000.0)
            self.run_at(5000.0 + W.PULL_AFTER_S)
        self.assertIn('auto-pull stood down: queued 3>=3', self.sent[0])
        self.assertEqual([w for w, _d in self.events], ['fill'])
        self.assertEqual(self.pulls, [])

    def test_a_hold_silences_the_waits_notices(self):
        self.job.waits = lambda key, entry: [('EX-H', 1, 'EX-L', 3, 'b')]
        self.job.read = lambda key, entry: (3, 3, [])   # no free slot: the waits alone
        W.set_hold(self.path, 'game', 'owner hold', None, 900.0)
        self.assert_quiet(1000.0, 1000.0 + W.WAITS_PM_AFTER_S + 1)
        self.assertEqual(self.events, [])
        W.clear_hold(self.path, 'game')
        self.run_at(1000.0 + W.WAITS_PM_AFTER_S + 2)
        self.assertEqual(len(self.sent), 1)             # the waits push, once the hold ends
        self.assertIn('EX-H', self.sent[0])


class HoldFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.s = lsettings.Settings({'state_dir': self.tmp, 'repos': {'game': self.tmp}},
                                    home=self.tmp)
        self.path = os.path.join(self.tmp, W.HOLD_FILE)

    def test_no_file_is_no_hold(self):
        self.assertEqual(W.read_hold(self.path), {})
        self.assertIsNone(W.hold_reason({}, 'game', 1000.0))

    def test_set_and_clear_round_trip(self):
        W.set_hold(self.path, 'game', 'r', 2000.0, 1000.0)
        W.set_hold(self.path, None, 'm', None, 1000.0)
        data = W.read_hold(self.path)
        self.assertEqual(data, {'repos': {'game': {'reason': 'r', 'set_at': 1000.0,
                                                    'until': 2000.0}},
                                'machine': {'reason': 'm', 'set_at': 1000.0}})
        self.assertEqual(W.hold_reason(data, 'game', 1500.0), 'hold machine')
        self.assertTrue(W.clear_hold(self.path, None))
        self.assertFalse(W.clear_hold(self.path, None))
        data = W.read_hold(self.path)
        self.assertEqual(W.hold_reason(data, 'game', 1999.0), 'hold repo')
        self.assertIsNone(W.hold_reason(data, 'game', 2000.0))
        self.assertIsNone(W.hold_reason(data, 'tools', 1500.0))

    def test_the_verb_sets_lists_and_clears(self):
        lines = W.fill_hold(self.s, 'game', False, 'owner hold', '2999-01-01T11:00', False, 1000.0)
        self.assertIn('Held game', lines[0])
        listed = W.fill_hold(self.s, None, False, None, None, False, 1000.0)
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0].startswith('game: owner hold (set '))
        self.assertEqual(W.fill_hold(self.s, 'game', False, None, None, True, 1000.0),
                         ['Cleared the hold on game.'])
        self.assertEqual(W.fill_hold(self.s, None, False, None, None, False, 1000.0),
                         ['No hold.'])

    def test_the_verb_refuses_a_bad_repo_a_bad_or_past_until(self):
        for args in (('nowhere', False, 'r', None, False), ('game', False, 'r', 'soon', False),
                     ('game', False, 'r', '2000-01-01T00:00', False),
                     ('game', False, None, '2999-01-01T00:00', False),
                     ('game', False, 'r', None, True)):
            with self.assertRaises(lsettings.LaunchError):
                W.fill_hold(self.s, *(args + (1000.0 * 1e6,)))

    def test_a_stale_reading_counts_at_its_last_value_until_its_reset(self):
        """The owner's rule: a stale reading is not a wrong one; usage only falls at the
        reset. make's stand-down reads every account's newest file, whatever its age."""
        dirs = {}
        for n in ('1', '2'):
            d = os.path.join(self.tmp, 'acct' + n)
            os.makedirs(d)
            open(os.path.join(d, '.credentials.json'), 'w').close()
            dirs[n] = [d]
        s = lsettings.Settings({'state_dir': self.tmp, 'repos': {'game': self.tmp},
                                'accounts': dirs}, home=self.tmp)
        now = 2000000000.0                              # 2033-05-18
        ahead = '2033-05-20T00:00:00+00:00'

        def reading(n, pct, updated='2033-05-01T00:00:00+00:00', resets=ahead):   # 17 days stale
            with open(os.path.join(dirs[n][0], 'usage-status.json'), 'w', encoding='utf-8') as f:
                json.dump({'updated_at': updated, 'five_hour': None,
                           'seven_day': {'used_percentage': pct, 'resets_at': resets}}, f)
        with mock.patch.object(lsettings, 'load', lambda home=None: s), \
                mock.patch.object(W, 'standdown', lambda top: None):
            run = W.make(mock.Mock(home=self.tmp, push=None, handoffs=lambda: {})).run
            run.now = lambda: now
            reading('1', 97)
            reading('2', 95)
            self.assertEqual(run.quiet('game', dict(COORD)), 'usage all>=95%')
            reading('2', 99, resets='2033-05-10T00:00:00+00:00')    # its reset passed: 0%
            self.assertIsNone(run.quiet('game', dict(COORD)))
            os.remove(os.path.join(dirs['2'][0], 'usage-status.json'))  # no reading: headroom
            self.assertIsNone(run.quiet('game', dict(COORD)))

    def test_an_unreadable_file_is_refused_but_machine_clear_removes_it(self):
        for bad in ('not json', '[1]', '{"machine": {"reason": 3}}'):
            with open(self.path, 'w', encoding='utf-8') as f:
                f.write(bad)
            for args in ((None, False, None, None, False), ('game', False, None, None, True),
                         ('game', False, 'r', None, False)):
                with self.assertRaises(lsettings.LaunchError) as cm:
                    W.fill_hold(self.s, *(args + (1000.0,)))
                self.assertIn('--machine --clear', str(cm.exception))
            lines = W.fill_hold(self.s, None, True, None, None, True, 1000.0)
            self.assertTrue(lines[0].startswith('Removed the unreadable'))
            self.assertFalse(os.path.exists(self.path))
            self.assertEqual(W.fill_hold(self.s, None, False, None, None, False, 1000.0),
                             ['No hold.'])

    def test_usage_reason(self):
        self.assertIsNone(W.usage_reason([]))
        self.assertEqual(W.usage_reason([('1', 95, None)]), 'usage all>=95%')
        self.assertIsNone(W.usage_reason([('1', 94, None)]))
        self.assertEqual(W.usage_reason([('1', 50, None)], limit=50), 'usage all>=50%')

if __name__ == '__main__':
    unittest.main()
