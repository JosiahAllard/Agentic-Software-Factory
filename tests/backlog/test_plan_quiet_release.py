"""A quiet release (kitlib.backlog.plan_claim plan_release, quiet_key_why; docs/backlog.md "Stopping
a pull"): `pull` records the caller's `--session` as `pulled_session`, every end of the pull clears
it, and `release` of a live pull of another branch is allowed when the caller is that key or a
successor with its stem, and the context's `quiet` probe finds the holder's tree quiet (faked here;
the probe itself, kitlib.worktree.quiet quiet_hold, is tests/worktree/test_quiet.py)."""
from unittest import mock

from kitlib.backlog import offer as omod
from kitlib.backlog import plan_edit
from kitlib.backlog import record as rmod
from kitlib.backlog.errors import Refusal
from tests.backlog.helpers import BRANCH, TODAY, StoreCase, messages, priced

OTHER = 'tools/other-branch'
WHY = 'its holder session ended days ago'
PULLED = dict(state='pulled', pulled_at=TODAY, preempt='2', preempt_by='R-9001',
              preempt_at=TODAY, displaced='BAR')
LIVE = ('active 2.0 h ago (newest: x), under the 24 h dead bar', None)
STEMS = {'kit-a': 'kit-coordinator', 'kit-b': 'kit-coordinator', 'other-x': 'other-coordinator'}
QUIET_S = 5 * 3600


class QuietCase(StoreCase):
    remote = False

    def setUp(self):
        super().setUp()
        self.repo.git('branch', OTHER)
        self.store.session = 'kit-a'
        self.ctx.liveness = lambda b: LIVE
        self.asked = []
        self.ctx.quiet = lambda b, main: (self.asked.append(b), (None, QUIET_S))[1]
        stems = mock.patch.object(omod, 'launcher_stems', return_value=dict(STEMS))
        stems.start()
        self.addCleanup(stems.stop)

    def seed_pull(self, session='kit-a', by=OTHER):
        over = dict(PULLED)
        if session:
            over['pulled_session'] = session
        self.seed(priced('FOO', pulled_by=by, **over))

    def tip(self, branch):
        return self.repo.git('rev-parse', 'refs/heads/%s' % branch)

    def assert_cleared(self, rid):
        r = self.get(rid)
        for key in rmod.PULL_CLEARS + rmod.PREEMPT_KEYS:
            self.assertIsNone(r.get(key), key)

    def assert_held(self):
        r = self.get('FOO')
        self.assertEqual((r.get('state'), r.get('pulled_by')), ('pulled', OTHER))


class QuietRelease(QuietCase):

    def test_the_pull_key_releases_a_quiet_live_pull_keeping_the_branch(self):
        self.seed_pull()
        tip = self.tip(OTHER)
        change = self.verb('release', 'FOO', WHY)
        self.assertEqual(self.asked, [OTHER])
        r = self.get('FOO')
        self.assertEqual(r.get('state'), 'open')
        self.assert_cleared('FOO')
        note = 'holder quiet 5 h, released by kit-a'
        self.assertIn('Stopped on branch %s at %s, branch kept.\nReleased (%s): %s (%s)\n'
                      % (OTHER, tip[:10], TODAY, WHY, note), r.body)
        self.assertEqual(change.subject, 'backlog: release FOO from %s: %s (%s)' % (OTHER, WHY, note))
        self.assertIn('  was pulled_by: %s (%s)' % (OTHER, note), change.lines)
        self.assertEqual(self.tip(OTHER), tip)

    def test_a_same_stem_successor_releases_it(self):
        self.seed_pull()
        self.store.session = 'kit-b'
        change = self.verb('release', 'FOO', WHY)
        self.assert_cleared('FOO')
        self.assertIn('released by kit-b', change.subject)

    def test_with_no_liveness_probe_the_quiet_rule_still_decides(self):
        self.seed_pull()
        self.ctx.liveness = None
        self.verb('release', 'FOO', WHY)
        self.assert_cleared('FOO')

    def test_another_stem_is_refused_and_the_tree_is_not_probed(self):
        self.seed_pull()
        self.store.session = 'other-x'
        with self.assertRaises(Refusal) as cm:
            self.verb('release', 'FOO', WHY)
        self.assertIn('neither kit-a nor a successor of it', str(cm.exception))
        self.assertEqual(self.asked, [])
        self.assert_held()

    def test_an_unread_sessions_json_allows_the_exact_key_only(self):
        self.seed_pull()
        self.store.session = 'kit-b'
        with mock.patch.object(omod, 'launcher_stems', side_effect=OSError('gone')):
            with self.assertRaises(Refusal) as cm:
                self.verb('release', 'FOO', WHY)
            self.assertIn('sessions.json cannot be read', str(cm.exception))
            self.assert_held()
            self.store.session = 'kit-a'
            self.verb('release', 'FOO', WHY)
        self.assert_cleared('FOO')

    def test_a_pull_with_no_pulled_session_keeps_the_old_refusal(self):
        self.seed_pull(session=None)
        with self.assertRaises(Refusal) as cm:
            self.verb('release', 'FOO', WHY)
        self.assertIn('is live', str(cm.exception))
        self.assertNotIn('quiet', str(cm.exception))
        self.assertEqual(self.asked, [])
        self.assert_held()

    def test_no_session_is_refused(self):
        self.seed_pull()
        self.store.session = None
        with self.assertRaises(Refusal) as cm:
            self.verb('release', 'FOO', WHY)
        self.assertIn('no `--session` names the caller', str(cm.exception))
        self.assert_held()

    def test_a_tree_that_is_not_quiet_refuses_naming_why(self):
        for why in ('process pid 7 python.exe has its current directory in x',
                    'machine lock: kit/other run r1 (no note)',
                    'active 1.0 h ago (newest: x), under the 4 h quiet bar'):
            self.ctx.quiet = lambda b, main, why=why: (why, None)
            self.seed_pull()
            with self.assertRaises(Refusal) as cm:
                self.verb('release', 'FOO', WHY)
            self.assertIn('Not a quiet release either: %s' % why, str(cm.exception))
            self.assert_held()

    def test_block_takes_no_quiet_rule(self):
        self.seed_pull()
        with self.assertRaises(Refusal):
            self.verb('block', 'FOO', ['live-client'], reason=WHY)
        self.assertEqual(self.asked, [])
        self.assert_held()

    def test_a_dead_tree_needs_no_key(self):
        self.seed_pull(session=None)
        self.store.session = None
        self.ctx.liveness = lambda b: (None, self.tip(OTHER))
        change = self.verb('release', 'FOO', WHY)
        self.assertEqual(self.asked, [])
        self.assertNotIn('quiet', change.subject)
        self.assert_cleared('FOO')


class PulledSessionField(QuietCase):

    def test_pull_records_the_session_key(self):
        self.seed(priced('BAZ', session='kit-a'))
        self.verb('pull', 'BAZ')
        r = self.get('BAZ')
        self.assertEqual((r.get('pulled_by'), r.get('pulled_session')), (BRANCH, 'kit-a'))

    def test_pull_without_a_session_records_none(self):
        self.store.session = None
        self.seed(priced('BAZ'))
        self.verb('pull', 'BAZ')
        self.assertIsNone(self.get('BAZ').get('pulled_session'))

    def test_a_takeover_records_the_new_key(self):
        self.seed(priced('DEAD', state='pulled', pulled_at=TODAY, pulled_by='kit/gone-branch',
                         pulled_session='kit-old', session='kit-a'))
        self.verb('pull', 'DEAD')
        r = self.get('DEAD')
        self.assertEqual((r.get('pulled_by'), r.get('pulled_session')), (BRANCH, 'kit-a'))

    def test_release_block_park_and_a_landing_release_clear_it(self):
        self.seed(priced('FOO', pulled_by=BRANCH, pulled_session='kit-a', **PULLED))
        self.verb('release', 'FOO', WHY)
        self.assert_cleared('FOO')
        self.seed(priced('FOO', pulled_by=BRANCH, pulled_session='kit-a', **PULLED))
        self.verb('block', 'FOO', ['live-client'], reason=WHY)
        self.assert_cleared('FOO')
        self.seed(priced('FOO', pulled_by=BRANCH, pulled_session='kit-a', **PULLED))
        self.verb('set_status', 'FOO', 'parked')
        self.assert_cleared('FOO')
        self.seed(priced('FOO', pulled_by=BRANCH, pulled_session='kit-a', **PULLED))
        self.verb('release', 'FOO', WHY, kept=False)
        self.assert_cleared('FOO')

    def test_check_reports_it_off_a_pull_and_a_bad_key(self):
        self.assertTrue(any('`pulled_session` is set' in m
                            for m in messages(pulled_session='kit-a')))
        self.assertTrue(any('not a session name' in m
                            for m in messages(state='pulled', pulled_at=TODAY, pulled_by=OTHER,
                                              pulled_session='Kit A')))
        self.assertEqual([m for m in messages(state='pulled', pulled_at=TODAY, pulled_by=OTHER,
                                              pulled_session='kit-a') if 'session' in m], [])

    def test_set_refuses_to_write_it(self):
        self.assertIn('pulled_session', plan_edit.WRITTEN_BY)
