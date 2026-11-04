"""kitlib.hubjobs.fill_watch: the hub's fill watch (docs/hub-fill.md): a live coordinator with a
free slot and startable work gets one nudge per change (stage 1), what was told survives a hub
restart (stage 2), and the nudge names the stand-down (stage 4)."""
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

from kitlib.hubjobs import coord_restart as C
from kitlib.hubjobs import fill_watch as W
from kitlib.hubsite import jobs
from kitlib.hubsite import push as P
from kitlib.launcher import settings as lsettings

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
        self.slots['game'] = (5, 2, ['EX-A', 'EX-B', 'EX-C'])      # one landed: in flight fell
        self.run_job()
        self.assertEqual((self.saved()['game']['since'], len(self.sent)), (1000.0, 2))
        self.assertEqual(self.saved()['game']['in_flight'], 2)
        self.clock.t = 1450.0
        self.slots['game'] = (5, 3, ['EX-A', 'EX-B', 'EX-C'])      # risen from the last read
        self.run_job()
        self.assertEqual(self.saved()['game']['since'], 1450.0)
        self.clock.t = 1600.0
        self.slots['game'] = (5, 4, ['EX-B', 'EX-C'])              # it pulled: in flight rose
        self.run_job()
        self.assertEqual(self.saved()['game']['since'], 1600.0)
        self.assertEqual(self.saved()['game']['in_flight'], 4)

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

    def test_no_file_is_an_empty_state(self):
        self.assertFalse(os.path.exists(self.path))
        self.run_job()
        self.assertEqual(len(self.sent), 1)


class StandDownNudge(Watch):
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


def tree(branch, klass, head=None, gate=None):
    row = {'path': '/w/' + branch, 'branch': branch, 'head': head or 'h-' + branch,
           'class': klass}
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
                mock.patch.object(verdicts, 'expired',
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

    def test_anything_unread_is_unread(self):
        from kitlib.gate import verdicts
        cases = [('main', (None, 'no origin/main')), ('common', (None, 'not a repo')),
                 ('entries', verdicts.VerdictError('bad file')),
                 ('trees', {'error': 'git cannot list the worktrees'})]
        for name, value in cases:
            self.setUp()
            setattr(self, name, value)
            with self.assertRaises(W.StandDownUnread):
                self.survey()


class Keys(unittest.TestCase):
    def test_the_two_keys_are_in_the_schema_with_their_defaults(self):
        from kitlib.config import schema
        backlog = schema.SECTIONS['backlog']
        self.assertIn('fill_queued_max', backlog)
        self.assertIn('fill_idle_max', backlog)
        self.assertEqual((backlog['fill_queued_max'].default, backlog['fill_idle_max'].default),
                         (3, 2))

if __name__ == '__main__':
    unittest.main()
