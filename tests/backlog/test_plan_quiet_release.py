"""A quiet release (kitlib.backlog.plan plan_release, quiet_key_why, quiet_hold; docs/backlog.md
"Stopping a pull"): `pull` records the caller's `--session` as `pulled_session`, every end of the
pull clears it, and `release` of a live pull of another branch is allowed when the caller is that
key or a successor with its stem, and the holder's tree is quiet: no process in it, no machine or
landing lock, no waiting verdict and 4 h with no activity (the git dir's mtimes not counted)."""
import json
import os
import time
from unittest import mock

from kitlib.backlog import offer as omod
from kitlib.backlog import plan as pmod
from kitlib.backlog import record as rmod
from kitlib.backlog.errors import Refusal
from kitlib.gate import verdicts
from kitlib.worktree import classify, holders, remove
from tests.backlog.helpers import BRANCH, TODAY, StoreCase, messages, priced
from tests.realgit import real_git

OTHER = 'tools/other-branch'
WHY = 'its holder session ended days ago'
PULLED = dict(state='pulled', pulled_at=TODAY, preempt='2', preempt_by='R-9001',
              preempt_at=TODAY, displaced='BAR')
LIVE = ('active 2.0 h ago (newest: x), under the 24 h dead bar', None)
STEMS = {'kit-a': 'kit-coordinator', 'kit-b': 'kit-coordinator', 'other-x': 'other-coordinator'}
QUIET_S = 5 * 3600
NO_LOCK = (lambda: ([], None))
NO_PROCS = (lambda: (([], 0, 1), None))


class QuietCase(StoreCase):
    remote = False

    def setUp(self):
        super().setUp()
        self.repo.git('branch', OTHER)
        self.store.session = 'kit-a'
        self.ctx.liveness = lambda b: LIVE
        self.asked = []
        self.ctx.quiet = lambda b: (self.asked.append(b), (None, QUIET_S))[1]
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
            self.ctx.quiet = lambda b, why=why: (why, None)
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
        self.assertIn('pulled_session', pmod.WRITTEN_BY)


@real_git('git reflog: the fake has no reflog command, and quiet_hold reads the reflog stamps')
class QuietHoldProbe(QuietCase):
    """quiet_hold on a real worktree, the run-wide reads faked."""

    def setUp(self):
        super().setUp()
        self.repo.git('branch', '-D', OTHER)
        self.tree = self.repo.worktree('other', OTHER)
        self.later = time.time() + QUIET_S

    def hold(self, now=None, machine=NO_LOCK, procs=NO_PROCS, branch=OTHER, main=None):
        return pmod.quiet_hold(self.repo.root, branch, now=now or self.later, machine=machine,
                               procs=procs, main=main)

    def test_a_fresh_tree_is_active(self):
        why, quiet = self.hold(now=time.time())
        self.assertIsNone(quiet)
        self.assertIn('under the 4 h quiet bar', why)

    def test_a_tree_idle_past_the_bar_is_quiet(self):
        why, quiet = self.hold()
        self.assertIsNone(why)
        self.assertGreaterEqual(quiet, pmod.QUIET_AFTER_S)

    def test_git_dir_mtimes_are_not_activity_and_tree_files_are(self):
        soon = self.later - 600
        gitdir = self.repo.git('rev-parse', '--path-format=absolute', '--git-dir', cwd=self.tree)
        for path in (os.path.join(self.tree, '.git'), os.path.join(gitdir, 'HEAD'),
                     os.path.join(gitdir, 'index')):
            os.utime(path, (soon, soon))
        self.assertEqual(self.hold()[0], None)
        os.utime(self.repo.write('work.txt', 'x\n', root=self.tree), (soon, soon))
        why, _ = self.hold()
        self.assertIn('active 0.2 h ago', why)
        self.assertIn('work.txt', why)

    def test_a_reflog_entry_is_activity(self):
        # The only fresh signal is the reflog: a commit dated 10 days back, put on a branch no
        # tree is on (no file mtimes) now, so only its reflog entry is new.
        old = '%d +0000' % (int(time.time()) - 10 * 86400)
        with mock.patch.dict(os.environ, {'GIT_COMMITTER_DATE': old, 'GIT_AUTHOR_DATE': old}):
            sha = self.repo.git('commit-tree', self.repo.git('rev-parse', 'HEAD^{tree}'),
                                '-p', 'HEAD', '-m', 'old work')
        self.repo.git('branch', 'kit/reflog-only', sha)
        why, _ = self.hold(branch='kit/reflog-only', now=time.time() + 3600)
        self.assertIn('active 1.0 h ago (newest: a reflog entry of kit/reflog-only)', why or '')
        self.assertEqual(self.hold(branch='kit/reflog-only')[0], None)

    def test_a_locked_tree_refuses(self):
        self.repo.git('worktree', 'lock', '--reason', 'owner keeps it', self.tree)
        self.addCleanup(self.repo.git, 'worktree', 'unlock', self.tree, check=False)
        why, _ = self.hold()
        self.assertIn('is locked with `git worktree lock`: owner keeps it', why)

    def test_an_exempt_tree_refuses(self):
        with mock.patch.object(remove, 'exemption', return_value='kept by ruling X') as ex:
            why, _ = self.hold()
        self.assertIn('is exempt (kept by ruling X)', why)
        self.assertTrue(ex.called)

    def test_a_process_in_the_tree_refuses(self):
        procs = lambda: (([(4242, 'python.exe', os.path.join(self.tree, 'sub'))], 0, 1), None)
        why, _ = self.hold(procs=procs)
        self.assertIn('process pid 4242 python.exe has its current directory in', why)

    def test_an_unread_process_and_a_held_folder_refuse(self):
        with mock.patch.object(holders, 'held_folder', return_value=(self.tree, 32)):
            why, _ = self.hold(procs=lambda: (([], 2, 3), None))
        self.assertIn('2 process(es) could not be read', why)

    def test_a_machine_lock_hold_refuses(self):
        machine = lambda: ([{'repo': 'kit', 'slug': 'other', 'id': 'r1', 'note': 'gate'}], None)
        why, _ = self.hold(machine=machine)
        self.assertIn('machine lock: kit/other run r1 (gate)', why)

    def test_an_unreadable_machine_lock_refuses(self):
        why, _ = self.hold(machine=lambda: (None, 'no file'))
        self.assertIn('unreadable: the machine lock cannot be read', why)

    def test_the_landing_lock_naming_the_branch_refuses(self):
        with mock.patch.object(classify, 'landing_branches', return_value=({OTHER}, None)):
            why, _ = self.hold()
        self.assertEqual(why, 'the landing lock names %s' % OTHER)

    def queue(self, tip):
        path = verdicts.entry_path(self.repo.common_dir(), OTHER)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {'branch': OTHER, 'tip': tip, 'verdict': 'LAND', 'risk': 'Low',
                 'reviewer': 'pm-kit:analyst', 'reviewer_model': 'opus', 'reviewer_id': 'a1',
                 'author': 'pm-kit:worker', 'range': 'main..%s' % tip, 'session': 'kit-a',
                 'records': [], 'queued_utc': '2026-11-05T00:00:00Z', 'state': 'queued',
                 'last_result': None}
        self.assertIsNone(verdicts.check(entry))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(entry, f)
        return path

    def test_a_waiting_verdict_refuses_and_a_landed_one_does_not(self):
        tip = self.tip(OTHER)
        self.queue(tip)
        why, _ = self.hold()
        self.assertIn('a verdict for %s waits in the landing queue (queued at' % OTHER, why)
        self.assertEqual(self.hold(main=tip)[0], None)

    def test_a_malformed_verdict_entry_refuses(self):
        with open(self.queue(self.tip(OTHER)), 'w', encoding='utf-8') as f:
            f.write('{not json')
        self.assertIn('is not JSON', self.hold()[0])

    def test_the_main_checkout_branch_is_never_quiet(self):
        main = self.repo.git('symbolic-ref', '--short', 'HEAD')
        self.assertEqual(self.hold(branch=main)[0], 'the main checkout is on %s' % main)

    def test_no_local_branch_is_not_quiet(self):
        self.assertIn('no local branch kit/none', self.hold(branch='kit/none')[0])

    def test_a_branch_no_tree_is_on_reads_its_tip_and_reflog(self):
        self.assertEqual(self.hold(branch=BRANCH)[0], None)
        self.assertIn('under the 4 h quiet bar', self.hold(branch=BRANCH, now=time.time())[0])
