"""kitlib.dailyfull.promote: the staging branch's promotion, in a scratch repository with a bare
remote (tests/gate/fixtures.GateRepoCase), and its pure parts with a stand-in git."""
import json
import os
import stat
import unittest
from unittest import mock

from kitlib import dailyfull as D
from kitlib.config import load as load_config
from kitlib.dailyfull import promote as P
from kitlib.git import cmd as gitcmd
from tests.gate import fixtures
from tests.home import temp_home
from tests.realgit import real_git

HOUR = 3600
NOW = 1_800_000_000


class Due(unittest.TestCase):
    def test_the_count_boundary(self):
        self.assertFalse(P.due(P.DUE_COUNT - 1, NOW - HOUR, NOW))
        self.assertTrue(P.due(P.DUE_COUNT, NOW - HOUR, NOW))

    def test_the_age_boundary(self):
        self.assertFalse(P.due(1, NOW - int(5.9 * HOUR), NOW))
        self.assertTrue(P.due(1, NOW - 6 * HOUR, NOW))

    def test_nothing_untested_is_never_due(self):
        self.assertFalse(P.due(0, None, NOW))


class FakeGit:
    """A stand-in for kitlib.dailyfull.git: `answers` maps an args tuple prefix to (rc, out, err)."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, cwd, *args):
        self.calls.append(args)
        for prefix, answer in self.answers.items():
            if args[:len(prefix)] == prefix:
                return answer
        return 1, '', 'not answered: %s' % ' '.join(args)


class Cfg:
    remote, main_branch = 'origin', 'main'


class Untested(unittest.TestCase):
    def results(self):
        return [{'verdict': 'green', 'sha': 'a' * 40}, {'verdict': 'red', 'sha': None},
                {'verdict': 'skipped', 'sha': None}]

    def test_from_the_last_tested_commit_skipping_results_without_a_sha(self):
        git = FakeGit({('merge-base',): (0, '', ''), ('log',): (0, '%d\n%d' % (NOW, NOW - 9), '')})
        self.assertEqual(P.untested('.', Cfg(), 'staging', self.results(), git),
                         ((2, NOW - 9), None))
        self.assertIn(('log', '--first-parent', '--format=%ct', '%s..origin/staging' % ('a' * 40)),
                      git.calls)

    def test_a_last_tested_commit_off_the_staging_tip_counts_from_main(self):
        git = FakeGit({('merge-base',): (1, '', ''), ('log',): (0, '', '')})
        self.assertEqual(P.untested('.', Cfg(), 'staging', self.results(), git), ((0, None), None))
        self.assertIn(('log', '--first-parent', '--format=%ct', 'origin/main..origin/staging'),
                      git.calls)

    def test_a_failing_log_says_so(self):
        git = FakeGit({('log',): (128, '', 'bad')})
        got, why = P.untested('.', Cfg(), 'staging', [], git)
        self.assertIsNone(got)
        self.assertIn('bad', why)


class StagingReadFailsClosed(unittest.TestCase):
    def test_a_ref_that_does_not_resolve_is_off(self):
        self.assertEqual(P.staging_read('.', Cfg(), FakeGit({})), (None, None))

    def test_a_show_that_fails_is_off(self):
        git = FakeGit({('rev-parse',): (0, 'b' * 40, ''), ('show',): (128, '', 'fatal: bad object')})
        self.assertEqual(P.staging_read('.', Cfg(), git), (None, None))


class PushForm(unittest.TestCase):
    def test_the_push_has_no_force_lease_verify_skip_or_delete(self):
        args = P.push_args('origin', 'c' * 40, 'main')
        self.assertEqual(args, ['push', '--quiet', 'origin', '%s:refs/heads/main' % ('c' * 40)])
        for bad in ('-f', '--force', '--no-verify', '-n'):
            self.assertNotIn(bad, args)
        self.assertFalse(any(a.startswith(('--force', '+', ':')) for a in args))

    def test_the_push_has_the_landing_timeout_and_the_network_env(self):
        seen = []

        def fake(argv, cwd, **kw):
            seen.append(kw)
            return gitcmd.Result(0, b'', '')
        with mock.patch.object(gitcmd, 'call', side_effect=fake):
            P._push(['push'], '.')
        self.assertEqual(seen[0]['timeout'], P.PUSH_TIMEOUT)
        self.assertEqual(seen[0]['env']['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(seen[0]['env']['GCM_INTERACTIVE'], 'never')


class StagingCase(fixtures.GateRepoCase):
    KIT = {'daily_full': {'enabled': True, 'kind': 'engine-test'}, 'staging': {'branch': 'staging'}}

    def setUp(self):
        temp_home(self)
        fixtures.GateRepoCase.setUp(self)
        self.main_dir = os.path.realpath(self.repo.root)
        self.repo.git('push', '-q', 'origin', 'main:refs/heads/staging')
        self.stg = self.repo.worktree('stg')
        self.repo.git('reset', '-q', '--hard', 'origin/staging', cwd=self.stg)
        self.n = 0

    def cfg(self):
        return load_config(self.main_dir)

    def land(self):
        """One commit on the remote's staging branch, as a landing pushes it: its sha."""
        self.n += 1
        name = 'landed-%d.txt' % self.n
        self.repo.write(name, 'x\n', root=self.stg)
        sha = self.repo.commit('landed %d' % self.n, name, cwd=self.stg)
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/staging', cwd=self.stg)
        return sha

    def push_to_main_directly(self):
        """A commit on the remote's main that is not on staging: its sha."""
        side = self.repo.worktree('side-%d' % self.n)
        self.repo.write('side.txt', 'x\n', root=side)
        sha = self.repo.commit('side', 'side.txt', cwd=side)
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/main', cwd=side)
        return sha

    def promote(self, sha, **kw):
        return P.promote(self.main_dir, self.cfg(), sha, 'staging', D.git, **kw)


class Promote(StagingCase):
    def test_main_moves_to_exactly_the_tested_commit_not_a_later_tip(self):
        tested = self.land()
        self.land()
        p = self.promote(tested)
        self.assertEqual(p['status'], 'promoted', p)
        self.assertEqual(self.remote_main(), tested)
        self.assertIn('daily: PROMOTED main', P.line(p))

    def test_a_commit_off_staging_is_never_pushed(self):
        before = self.remote_main()
        side = self.repo.worktree('elsewhere')
        self.repo.write('e.txt', 'x\n', root=side)
        other = self.repo.commit('elsewhere', 'e.txt', cwd=side)
        self.repo.git('push', '-q', 'origin', 'HEAD:refs/heads/elsewhere', cwd=side)
        p = self.promote(other, push=self.no_push)
        self.assertEqual(p['status'], 'off-staging', p)
        self.assertEqual(self.remote_main(), before)

    def no_push(self, args, cwd):
        raise AssertionError('pushed: %s' % args)

    def test_a_commit_main_already_holds_is_a_no_op(self):
        tested = self.land()
        self.assertEqual(self.promote(tested)['status'], 'promoted')
        p = self.promote(tested, push=self.no_push)
        self.assertEqual(p['status'], 'current', p)
        older = self.repo.git('rev-parse', tested + '^')
        self.assertEqual(self.promote(older, push=self.no_push)['status'], 'current')

    def test_main_with_commits_not_on_staging_is_diverged_and_never_pushed(self):
        tested = self.land()
        moved = self.push_to_main_directly()
        p = self.promote(tested, push=self.no_push)
        self.assertEqual(p['status'], 'diverged', p)
        self.assertIn('1 commit(s) that are not on staging', p['why'])
        self.assertEqual(self.remote_main(), moved)

    def test_a_push_that_failed_but_landed_is_promoted(self):
        tested = self.land()

        def hung_up(args, cwd):
            P._push(args, cwd)
            return 128, 'fatal: the remote end hung up unexpectedly'
        self.assertEqual(self.promote(tested, push=hung_up)['status'], 'promoted')
        self.assertEqual(self.remote_main(), tested)

    def test_a_push_that_exited_0_but_did_not_land_is_not_promoted(self):
        before = self.remote_main()
        p = self.promote(self.land(), push=lambda args, cwd: (0, ''))
        self.assertEqual(p['status'], 'not-promoted', p)
        self.assertIn('git exited 0', p['why'])
        self.assertEqual(self.remote_main(), before)

    def test_a_remote_that_cannot_be_read_after_the_push_is_unknown(self):
        p = self.promote(self.land(), push=lambda args, cwd: (0, ''),
                         ls_remote=lambda remote, ref, cwd: (None, 'down'))
        self.assertEqual(p['status'], 'unknown', p)

    def test_a_remote_main_this_checkout_cannot_place_is_unknown(self):
        p = self.promote(self.land(), push=lambda args, cwd: (0, ''),
                         ls_remote=lambda remote, ref, cwd: ('1' * 40, None))
        self.assertEqual(p['status'], 'unknown', p)

    def test_the_local_main_is_never_moved_and_its_lag_is_a_line(self):
        local = self.repo.git('rev-parse', 'refs/heads/main')
        self.assertIsNone(P.local_behind(self.main_dir, self.cfg(), D.git))
        self.assertEqual(self.promote(self.land())['status'], 'promoted')
        self.assertEqual(self.repo.git('rev-parse', 'refs/heads/main'), local)
        self.assertEqual(P.local_behind(self.main_dir, self.cfg(), D.git),
                         'daily     local main is 1 behind origin/main: '
                         'git merge --ff-only origin/main')


@real_git('hooks and a ref race: the fake runs no pre-push hook, and its push takes no race against a moved remote')
class PromoteOnRealGit(StagingCase):
    no_push = Promote.no_push

    def test_main_moved_after_the_fetch_is_rejected_and_left_alone(self):
        tested = self.land()
        moved = []

        def racing(args, cwd):
            moved.append(self.push_to_main_directly())     # after the fetch, before the push
            return P._push(args, cwd)
        p = self.promote(tested, push=racing)
        self.assertEqual(p['status'], 'diverged', p)
        self.assertEqual(self.remote_main(), moved[0])

    def test_a_refusing_pre_push_hook_is_obeyed(self):
        tested = self.land()
        hooks = os.path.join(self.tmp, 'hooks')
        os.makedirs(hooks)
        hook = os.path.join(hooks, 'pre-push')
        with open(hook, 'w', newline='\n') as f:
            f.write('#!/bin/sh\necho "the test hook refuses" >&2\nexit 1\n')
        os.chmod(hook, os.stat(hook).st_mode | stat.S_IEXEC)
        self.repo.git('config', 'core.hooksPath', hooks)
        before = self.remote_main()
        p = self.promote(tested)
        self.assertEqual(p['status'], 'not-promoted', p)
        self.assertIn('the test hook refuses', p['why'])
        self.assertEqual(self.remote_main(), before)


class StagingRead(StagingCase):
    def test_staging_on_at_the_remote_main(self):
        two, why = P.staging_read(self.main_dir, self.cfg(), D.git)
        self.assertIsNone(why)
        self.assertEqual(two.work_base, 'staging')

    def test_a_malformed_section_on_the_remote_main_refuses(self):
        cfg = self.cfg()
        self.repo.write('.kit.json', json.dumps({'gate': self.GATE, 'staging': 'staging'}) + '\n')
        self.repo.commit('bad staging', '.kit.json')
        self.repo.git('push', '-q', 'origin', 'main')
        two, why = P.staging_read(self.main_dir, cfg, D.git)
        self.assertIsNone(two)
        self.assertIn('staging', why)
        self.assertEqual(cfg.work_base, 'staging')        # the caller's config is untouched


if __name__ == '__main__':
    unittest.main()
