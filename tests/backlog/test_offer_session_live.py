"""`next`: another session's unpulled record reserves its scope only while that session runs
(KIT-OFFER-DEAD-SESSION). Liveness is omod.SessionLiveness: the launcher's sessions.json key ->
its stem, live when a running session's name starts with that stem; unreadable reserves as
before and says so. The offer cases run on snapshots built in memory with stubbed readers; the
CLI case runs tools/backlog.py in process with the readers patched."""
import unittest
from unittest import mock

from kitlib.backlog import offer as omod
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod

from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog import test_cli
from tests.backlog.helpers import priced, record_text
from tests.backlog.test_offer import snap


def liveness(stems=None, names=(), fail=None):
    """A SessionLiveness over stub readers: `stems` {key: stem}, `names` the running sessions'
    names, `fail` an exception the sessions reader raises."""
    def sessions():
        if fail is not None:
            raise fail
        return dict(stems or {})
    return omod.SessionLiveness(sessions=sessions, names=lambda: list(names))


def offer(s, live, session='kit'):
    (offers, passed), refusals, _ranks = omod.next_offer(s, [], session=session,
                                                         session_live=live)
    assert not refusals, refusals
    return [o.rec.id for o in offers], {p.rec.id: p for p in passed}


def two():
    """A higher record of session combat and a lower one of session kit, on the same scope."""
    return snap(priced('HIGH', priority='1', scope='artifacts/models/**', session='combat'),
                priced('LOW', priority='2', scope='artifacts/models/x/**', session='kit'))


class SessionLive(unittest.TestCase):

    def test_a_session_not_running_reserves_nothing_and_is_noted(self):
        live = liveness({'combat': 'combat-coordinator'}, names=['kit-coordinator-a1'])
        ids, passed = offer(two(), live)
        self.assertEqual(ids, ['LOW'])
        self.assertEqual(passed, {})
        self.assertEqual(live.idle, [('HIGH', 'combat')])
        self.assertEqual(omod.idle_lines(live),
                         ['NOTE: HIGH (session combat not running) reserves no scope'])

    def test_a_running_session_reserves_as_before(self):
        live = liveness({'combat': 'combat-coordinator'}, names=['combat-coordinator-a2-r1'])
        ids, passed = offer(two(), live)
        self.assertEqual(ids, [])
        self.assertEqual(passed['LOW'].why, 'scope artifacts/models/x/** collides with '
                         'artifacts/models/** already taken by HIGH (session combat)')
        self.assertEqual(omod.blocker_summary(list(passed.values())),
                         ['blocks 1: session combat (HIGH)'])
        self.assertEqual(omod.idle_lines(live), [])

    def test_unreadable_liveness_reserves_and_says_so(self):
        for live, why in ((liveness(fail=OSError('no sessions.json')), 'no sessions.json'),
                          (liveness({'other': 'other-coordinator'}), 'not in sessions.json')):
            ids, passed = offer(two(), live)
            self.assertEqual(ids, [])
            self.assertEqual(passed['LOW'].why, 'scope artifacts/models/x/** collides with '
                             'artifacts/models/** already taken by HIGH (session combat) '
                             '(liveness unread, so reserved)')
            self.assertEqual(omod.blocker_summary(list(passed.values())),
                             ['blocks 1: session combat (HIGH)'])
            notes = omod.unread_lines(live, list(passed.values()))
            self.assertEqual(len(notes), 1, notes)
            self.assertTrue(notes[0].startswith('NOTE: session combat liveness unread ('), notes)
            self.assertIn(why, notes[0])
            self.assertTrue(notes[0].endswith('): its records reserve their scope as before'),
                            notes)

    def test_no_reader_is_todays_offer(self):
        (offers, passed), _r, _k = omod.next_offer(two(), [], session='kit')
        self.assertEqual([o.rec.id for o in offers], [])
        self.assertTrue(passed[0].why.endswith('already taken by HIGH (session combat)'))

    def test_a_dead_session_takes_no_resource_unit(self):
        s = snap(priced('HIGH', priority='1', scope='a/**', session='combat', uses='gpu'),
                 priced('LOW', priority='2', scope='b/**', session='kit', uses='gpu'))
        live = liveness({'combat': 'combat-coordinator'})
        (offers, _p), _r, _k = omod.next_offer(s, [], session='kit', capacity={'gpu': 1},
                                               session_live=live)
        self.assertEqual([o.rec.id for o in offers], ['LOW'])

    def test_the_listing_is_read_once_and_only_for_a_known_stem(self):
        calls = []

        def names():
            calls.append(1)
            return []
        live = omod.SessionLiveness(sessions=lambda: {'combat': 'combat-coordinator'},
                                    names=names)
        self.assertEqual(live.live('other'), (None, 'session other is not in sessions.json'))
        self.assertEqual(calls, [])
        self.assertEqual(live.live('combat'), (False, None))
        self.assertEqual(live.live('combat'), (False, None))
        self.assertEqual(calls, [1])

    def test_a_failing_listing_is_unread_never_a_raise(self):
        def names():
            raise OSError('listing timed out')
        live = omod.SessionLiveness(sessions=lambda: {'combat': 'combat-coordinator'},
                                    names=names)
        state, why = live.live('combat')
        self.assertIsNone(state)
        self.assertIn('listing timed out', why)


class SessionLiveCli(unittest.TestCase):
    """`next` prints the NOTE for a record whose session is not running, and offers below it."""
    run_tool, run_process, ok = test_cli.Cli.run_tool, test_cli.Cli.run_process, test_cli.Cli.ok

    def setUp(self):
        test_cli.Cli.setUp(self)

    def test_next_notes_the_idle_session_and_offers_below_it(self):
        backend = smod.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        snapshot = smod.Store(backend, rmod.Rules()).load()
        backend.publish(snapshot, {
            'HOLD': record_text(id='HOLD', priority='1', priority_by='agent', session='combat',
                                scope='a/**'),
            'R1': record_text(id='R1', priority='2', priority_by='agent', scope='a/x.py')},
            'seed\n')
        # the readers `next` reads liveness with (patched below, so no machine read)
        self.assertTrue(hasattr(omod, 'launcher_stems') and hasattr(omod, 'running_names'))
        with mock.patch.object(omod, 'launcher_stems',
                               return_value={'combat': 'combat-coordinator'}), \
                mock.patch.object(omod, 'running_names', return_value=['kit-coordinator-a1']):
            out = self.ok('next')
        self.assertIn('NOTE: HOLD (session combat not running) reserves no scope', out)
        self.assertNotIn('passed over R1', out)
        self.assertIn('NEXT (1 offered)', out)


if __name__ == '__main__':
    unittest.main()
