"""kitlib.hubjobs.fill_watch: the hub's fill watch, stage 1 (docs/hub-fill.md): a live coordinator
with a free slot and startable work gets one nudge per change."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from kitlib.hubjobs import coord_restart as C
from kitlib.hubsite import jobs
from kitlib.launcher import settings as lsettings

W = None


def setUpModule():
    global W
    from kitlib.hubjobs import fill_watch
    W = fill_watch


def ctx():
    return jobs.Ctx(threading.Event(), lambda word, detail=None: None)


COORD = {'repo': 'game', 'agent': True, 'stem': 'game-coordinator', 'name': 'game-coordinator-a1'}
LANE = {'repo': 'game', 'agent': True, 'stem': 'lane-coordinator', 'name': 'lane-coordinator-a1'}
PLAIN = {'repo': 'game', 'agent': False, 'stem': 'research', 'name': 'research-a2'}


class Watch(unittest.TestCase):
    """The job with its readers replaced: `self.slots[key]` is (cap, in_flight, ids) or an
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
            return self.states.pop(0) if self.states else W.WRITTEN

        self.job = W.FillWatch(lambda: self.sessions, lambda: self.live, read, deliver)

    def run_job(self):
        return self.job(ctx())

    def test_free_and_startable_nudges_the_coordinator_once_with_the_counts(self):
        out = self.run_job()
        self.assertEqual(len(self.sent), 1)
        key, text = self.sent[0]
        self.assertEqual(key, 'game')
        self.assertIn('fill: 2 free, 3 startable: EX-A, EX-B, EX-C', text.splitlines()[0])
        self.assertIn('backlog.py next', text)
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
        self.states = [W.NOT_WRITTEN, W.NO_SESSION, W.UNCERTAIN]
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
            return W.WRITTEN
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
        self.job.deliver = lambda key, text: self.sent.append(key) or W.WRITTEN
        self.run_job()
        self.assertEqual(self.sent, ['game'])

    def test_a_listing_that_times_out_is_a_skip(self):
        import subprocess

        def slow():
            raise subprocess.TimeoutExpired('ps', 15)
        self.job.live = slow
        self.assertIsInstance(self.run_job(), jobs.Skip)
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

        def open_store(root, fetch=True, session=None, branch=None):
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


class Make(unittest.TestCase):
    def test_the_states_are_the_push_workers(self):
        from kitlib.hubsite import push
        self.assertEqual((W.WRITTEN, W.NOT_WRITTEN, W.UNCERTAIN, W.NO_SESSION),
                         (push.WRITTEN, push.NOT_WRITTEN, push.UNCERTAIN, push.NO_SESSION))

    def test_no_repository_is_no_job(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            self.assertIsNone(W.make(mock.Mock(home=tmp, push=None)))

    def test_a_job_named_fill_watch_every_five_minutes(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        s = lsettings.Settings({'state_dir': tmp, 'repos': {'game': tmp}}, home=tmp)
        with mock.patch.object(lsettings, 'load', lambda home=None: s):
            job = W.make(mock.Mock(home=tmp, push=None))
        self.assertEqual((job.name, job.every_s), ('fill-watch', 300))


if __name__ == '__main__':
    unittest.main()
