"""kitlib.staging.advance: an attempt's phases after its test, with a stand-in git, landing lock,
promote and sync, and its record in a scratch folder. A recovery is the same call on a record a
dead run left, so each phase is started from its record here."""
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.dailyfull import promote as P
from kitlib.records import landing as LL
from kitlib.staging import advance as A
from kitlib.staging import record as R

S = 'a' * 40            # the tested staging tip
BASE = 'b' * 40         # main when it was tested
MID = 'c' * 40          # a later main, between BASE and S
OTHER = 'd' * 40        # a main with commits S lacks


class Cfg:
    remote, main_branch = 'origin', 'main'


class FakeGit:
    """fetch answers `fetch`; rev-parse of origin/main answers `main`; merge-base --is-ancestor
    answers from `ancestors` ({(a, b)}: a is an ancestor of b; each sha its own)."""

    def __init__(self, main=BASE, ancestors=(), fetch=(0, '', '')):
        self.main, self.fetch = main, fetch
        self.ancestors = set(ancestors)
        self.calls = []

    def __call__(self, cwd, *args):
        self.calls.append(args)
        if args[0] == 'fetch':
            return self.fetch
        if args[0] == 'rev-parse':
            return (0, self.main, '') if self.main else (1, '', 'no such ref')
        if args[:2] == ('merge-base', '--is-ancestor'):
            a, b = args[2], args[3]
            return (0, '', '') if a == b or (a, b) in self.ancestors else (1, '', '')
        raise AssertionError('git %s' % ' '.join(args))


class FakeLock:
    """LandingLock's surface: take() raises the queued Busy/LockError values, then gives a token;
    every call goes into `log` (shared, so a test can order it against other events)."""

    def __init__(self, log, busy=(), dead=False, breaks=True):
        self.log, self.busy, self.dead, self.breaks = log, list(busy), dead, breaks
        self.held = None

    def take(self, branches, pid=None):
        self.log.append(('take', tuple(branches)))
        if self.busy:
            raise self.busy.pop(0)
        self.held = 'tok'
        return {'token': 'tok'}

    def holder_state(self, rec):
        return 'dead' if self.dead else 'alive'

    def break_lock(self):
        self.log.append(('break',))
        if self.breaks:
            self.busy = []
            return True, {}
        return False, 'a merge is in progress'

    def set_phase(self, token, phase):
        self.log.append(('phase', phase))

    def release(self, token):
        assert token == self.held, token
        self.held = None
        self.log.append(('release',))


def busy(token=None):
    return LL.Busy({'token': token, 'pid': 7, 'branches': ['kit/x'], 'phase': 'gate'})


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='staging-advance-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = []
        self.lock = FakeLock(self.log)
        self.answers = []           # promote statuses, in order; the last one repeats
        self.synced = []
        self.sleeps = []
        self.now = [0.0]

    def attempt(self, phase, **more):
        a = R.start(self.dir, S, BASE, phase)
        if more:
            a.set(phase, **more)
        return a

    def promote(self, main, cfg, sha, staging, git, push=None):
        self.log.append(('promote', sha))
        status = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(status, Exception):
            raise status
        if status == 'push':            # run the push the wrapper makes (P._push is patched)
            push(P.push_args(cfg.remote, sha, cfg.main_branch), main)
            status = 'not-promoted'
        return {'status': status, 'why': 'the stand-in said %s' % status, 'main': 'main',
                'from': BASE, 'to': sha}

    def sync(self, main, cfg, sha):
        self.synced.append(sha)
        self.log.append(('sync', sha))
        return []

    def sleep(self, s):
        self.sleeps.append(s)
        self.now[0] += s

    def mover(self, git=None, **kw):
        kw.setdefault('lock', self.lock)
        return A.Mover('/main', Cfg(), 'staging', git or FakeGit(), lock=kw.pop('lock'),
                       promote=self.promote, sync=self.sync, sleep=self.sleep,
                       clock=lambda: self.now[0], **kw)

    def phases(self, a):
        return [p for p, _ in R.attempts(self.dir)[0].rec['history']]


class FromTested(Case):
    def test_main_still_the_base_moves_main_to_the_tested_sha_and_syncs(self):
        self.answers = ['promoted']
        a = self.attempt('tested')
        lines = self.mover().advance(a)
        self.assertEqual(a.phase, 'done', lines)
        self.assertEqual(self.phases(a), ['tested', 'pushing', 'pushed', 'done'])
        self.assertEqual([e for e in self.log if e[0] == 'promote'], [('promote', S)])
        self.assertEqual(self.synced, [S])
        self.assertEqual([e[0] for e in self.log],
                         ['take', 'phase', 'promote', 'sync', 'release'])
        self.assertIn('staging->main at %s' % S[:10], self.log[0][1][0])
        self.assertIn('promote: DONE main is at %s' % S[:10], lines)
        self.assertIsNone(self.lock.held)

    def test_main_moved_on_to_an_ancestor_of_the_tip_ends_behind_untouched(self):
        a = self.attempt('tested')
        git = FakeGit(main=MID, ancestors={(MID, S), (BASE, MID)})
        lines = self.mover(git).advance(a)
        self.assertEqual(a.phase, 'behind', lines)
        self.assertEqual(self.log, [])          # no lock, no push, no sync
        self.assertIn('tested again next time', lines[-1])

    def test_main_already_holding_the_tip_is_done_without_a_push(self):
        a = self.attempt('tested')
        lines = self.mover(FakeGit(main=OTHER, ancestors={(S, OTHER)})).advance(a)
        self.assertEqual(a.phase, 'done', lines)
        self.assertEqual(self.log, [])

    def test_main_with_commits_the_tip_lacks_is_diverged(self):
        a = self.attempt('tested')
        lines = self.mover(FakeGit(main=OTHER)).advance(a)
        self.assertEqual(a.phase, 'diverged', lines)
        self.assertEqual(self.log, [])

    def test_main_that_cannot_be_read_stays_tested(self):
        a = self.attempt('tested')
        lines = self.mover(FakeGit(fetch=(128, '', 'offline'))).advance(a)
        self.assertEqual(a.phase, 'tested')
        self.assertIn('offline', lines[0])
        self.assertEqual(self.log, [])


class FromLater(Case):
    def test_from_pushing_the_push_is_made_again_and_never_rechecks_the_base(self):
        self.answers = ['current']
        a = self.attempt('pushing', tries=1)
        git = FakeGit(main=OTHER)               # a recheck would end it diverged
        lines = self.mover(git).advance(a)
        self.assertEqual(a.phase, 'done', lines)
        self.assertEqual(a.rec['tries'], 2)
        self.assertFalse(any(c[0] == 'fetch' for c in git.calls))
        self.assertEqual(self.synced, [S])

    def test_from_pushed_only_the_sync_is_left(self):
        a = self.attempt('pushed')
        lines = self.mover().advance(a)
        self.assertEqual(a.phase, 'done', lines)
        self.assertFalse(any(e[0] == 'promote' for e in self.log))
        self.assertEqual([e[0] for e in self.log], ['take', 'sync', 'release'])

    def test_a_sync_warning_is_kept_and_the_attempt_is_still_done(self):
        a = self.attempt('pushed')
        m = self.mover()
        m.sync = lambda main, cfg, sha: ['WARNING: not fast-forwarded']
        lines = m.advance(a)
        self.assertEqual(a.phase, 'done')
        self.assertIn('WARNING: not fast-forwarded', lines)
        self.assertEqual(a.rec['why'], 'WARNING: not fast-forwarded')

    def test_a_sync_that_raises_is_a_warning_line(self):
        def boom(*a, **k):
            raise RuntimeError('no checkout')
        with mock.patch('kitlib.landing.push.sync_checkout', side_effect=boom, create=True):
            lines = A.sync_main('/main', Cfg(), S)
        self.assertEqual(len(lines), 1)
        self.assertIn('git -C "/main" merge --ff-only origin/main', lines[0])


class Pushes(Case):
    def test_a_pre_push_refusal_ends_refused_and_is_never_tried_again(self):
        self.answers = ['push']
        a = self.attempt('tested')
        said = 'pre-push: refused, main may move only to a tested commit\nerror: failed to push'
        with mock.patch.object(P, '_push', return_value=(1, said)) as push:
            lines = self.mover().advance(a)
        self.assertEqual(a.phase, 'refused', lines)
        self.assertEqual(push.call_count, 1)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(a.rec['why'], 'pre-push: refused, main may move only to a tested commit')
        self.assertEqual(self.synced, [])
        self.assertIsNone(self.lock.held)
        self.assertIn('refused', R.HOLDS)

    def test_pushes_are_bounded_then_failed(self):
        self.answers = ['not-promoted']
        a = self.attempt('tested')
        lines = self.mover().advance(a)
        self.assertEqual(a.phase, 'failed', lines)
        self.assertEqual(len([e for e in self.log if e[0] == 'promote']), A.PUSH_TRIES)
        self.assertEqual(self.sleeps, [A.PUSH_PAUSE_S] * (A.PUSH_TRIES - 1))
        self.assertEqual(a.rec['tries'], A.PUSH_TRIES)
        self.assertIsNone(self.lock.held)

    def test_a_recovered_push_counts_the_tries_already_made(self):
        self.answers = ['unknown']
        a = self.attempt('pushing', tries=A.PUSH_TRIES - 1)
        self.mover().advance(a)
        self.assertEqual(a.phase, 'failed')
        self.assertEqual(len([e for e in self.log if e[0] == 'promote']), 1)

    def test_a_push_that_fails_once_then_lands_is_done(self):
        self.answers = ['not-promoted', 'promoted']
        a = self.attempt('tested')
        self.mover().advance(a)
        self.assertEqual(a.phase, 'done')
        self.assertEqual(a.rec['tries'], 2)

    def test_diverged_and_off_staging_end_at_once(self):
        for status, end in (('diverged', 'diverged'), ('off-staging', 'behind')):
            self.answers = [status]
            a = self.attempt('tested')
            self.mover().advance(a)
            self.assertEqual(a.phase, end, status)
        self.assertEqual(self.sleeps, [])

    def test_the_lock_is_released_when_the_push_raises(self):
        self.answers = [RuntimeError('boom')]
        a = self.attempt('tested')
        with self.assertRaises(RuntimeError):
            self.mover().advance(a)
        self.assertEqual(self.log[-1], ('release',))
        self.assertEqual(a.phase, 'pushing')    # the next run recovers it


class LandingLock(Case):
    def test_busy_then_free_waits_and_then_moves(self):
        self.answers = ['promoted']
        self.lock.busy = [busy('t1'), busy('t1')]
        said = []
        m = self.mover()
        m.say = said.append
        a = self.attempt('tested')
        m.advance(a)
        self.assertEqual(a.phase, 'done')
        self.assertEqual(self.sleeps, [A.LOCK_POLL_S] * 2)
        self.assertEqual(len(said), 1)
        self.assertIn('waiting for the landing lock', said[0])

    def test_busy_past_the_wait_leaves_the_attempt_tested(self):
        self.answers = ['promoted']
        self.lock.busy = [busy('t1')] * 1000
        a = self.attempt('tested')
        lines = self.mover(lock_wait_s=12).advance(a)
        self.assertEqual(a.phase, 'tested', lines)
        self.assertIn('WAITING at tested', lines[-1])
        self.assertFalse(any(e[0] in ('promote', 'release') for e in self.log))

    def test_a_dead_holder_is_broken(self):
        self.answers = ['promoted']
        self.lock = FakeLock(self.log, busy=[busy('t1')], dead=True)
        a = self.attempt('tested')
        lines = self.mover().advance(a)
        self.assertEqual(a.phase, 'done')
        self.assertIn(('break',), self.log)
        self.assertTrue(any('broke the landing lock' in l for l in lines), lines)
        self.assertEqual(self.sleeps, [])

    def test_a_dead_holder_that_cannot_be_broken_is_waited_on(self):
        self.answers = ['promoted']
        self.lock = FakeLock(self.log, busy=[busy('t1')] * 1000, dead=True, breaks=False)
        a = self.attempt('tested')
        self.mover(lock_wait_s=10).advance(a)
        self.assertEqual(a.phase, 'tested')

    def test_a_lock_that_cannot_be_read_is_waited_on_like_a_busy_one(self):
        self.answers = ['promoted']
        self.lock.busy = [LL.LockError('unreadable')]
        a = self.attempt('tested')
        self.mover().advance(a)
        self.assertEqual(a.phase, 'done')
        self.assertEqual(self.sleeps, [A.LOCK_POLL_S])


if __name__ == '__main__':
    unittest.main()
