"""A landing onto a staging branch (`.kit.json` "staging".branch, docs/gate-landing.md "Staging
branch"): it merges onto <remote>/<staging> and pushes there; the main branch, on the remote and in
the main checkout, is never moved (fix 1: no local fast-forward of main); the staging setting is
read from <remote>/<main> (fix 2: Merge.follow through kitlib.config.load_at(staging_from=)).
Then readers of the work base: the landing lock and the gate's default base. A STUCK landing
onto staging prints steps that never move main and the break step's real refusal (MERGE_HEAD)."""
import json
import unittest
from unittest import mock

import kitlib.config as kconfig
from kitlib import gate as G
from kitlib.gate import runner
from kitlib.git import refs
from kitlib.records import landing
from tests.landing.test_land import BRANCH, LandCase
from tests.landing.test_stuck import _Stuck, commands, joined

STAGING = 'staging'


class StagingCase(LandCase):
    KIT = {'staging': {'branch': STAGING}}

    def setUp(self):
        LandCase.setUp(self)
        self.repo.git('push', '-q', 'origin', 'main:refs/heads/%s' % STAGING)
        self.repo.git('fetch', '-q', 'origin')

    def remote_staging(self):
        return self.repo.git('--git-dir', self.repo.remote, 'rev-parse', STAGING,
                             cwd=self.repo.base)

    def push_to(self, branch, rel, text, kit=None):
        """A commit on top of <remote>/<branch>, made in a scratch worktree and pushed there."""
        other = self.repo.worktree('push-%s-%s' % (branch, rel.replace('.', '-')))
        self.repo.git('checkout', '-q', '--detach', 'origin/%s' % branch, cwd=other)
        files = [rel]
        self.repo.write(rel, text, root=other)
        if kit is not None:
            self.repo.write('.kit.json', json.dumps(kit, indent=1) + '\n', root=other)
            files.append('.kit.json')
        sha = self.repo.commit('on %s' % branch, *files, cwd=other)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/%s' % (sha, branch))
        return sha


class LandsOnStaging(StagingCase):
    def test_a_landing_merges_onto_staging_and_pushes_there_alone(self):
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = self.lander(BRANCH, self.main, tip, summary='add a').run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertTrue(out.lines[-1].startswith('LANDED kit/feature on %s at %s'
                                                 % (STAGING, out.merge)), out.lines)
        self.assertEqual(self.remote_staging(), out.merge)
        self.assertEqual(self.repo.git('rev-parse', 'refs/remotes/origin/%s' % STAGING), out.merge)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^1'), self.main)
        self.assertEqual(self.remote_main(), self.main)        # main is never pushed
        self.assertEqual(self.main_now(), self.main)           # nor moved in the main checkout
        self.assertEqual(self.repo.git('rev-parse', '--abbrev-ref', 'HEAD'), 'main')
        self.assertIsNone(self.lock().read())

    def test_staging_moved_on_is_followed_and_main_is_not_fast_forwarded(self):
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        moved = self.push_to(STAGING, 's.py', 's\n')
        promoted = self.push_to('main', 'm.py', 'm\n')       # main ahead of the main checkout
        said = []
        out = self.lander(BRANCH, self.main, tip, say=said.append).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertIn('main moved on from %s to %s; merging onto %s' % (
            self.main[:10], moved[:10], moved[:10]), said)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^1'), moved)
        self.assertEqual(self.remote_staging(), out.merge)
        self.assertEqual(self.remote_main(), promoted)
        self.assertEqual(self.main_now(), self.main)           # fix 1: no local ff of main

    def test_main_naming_another_staging_branch_after_the_fetch_refuses_before_the_merge(self):
        # follow() reads the setting from <remote>/<main> as fetched (fix 2)
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        moved = self.push_to(STAGING, 's.py', 's\n')
        kit = {'staging': {'branch': 'other'}, 'gate': self.GATE}
        self.push_to('main', 'm.py', 'm\n', kit=kit)
        # the push wrote the tracking ref; as last fetched, origin/main is still the old main
        self.repo.git('update-ref', 'refs/remotes/origin/main', self.main)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('now names another staging branch than this landing holds (other, not %s)'
                      % STAGING, '\n'.join(out.lines))
        self.assertEqual(self.remote_staging(), moved)
        self.assertEqual(self.main_now(), self.main)
        self.assertIsNone(self.lock().read())

    def test_main_naming_another_staging_branch_before_the_landing_is_read_first(self):
        # the lander's first read takes <remote>/<main>'s setting over the main checkout's
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        kit = {'staging': {'branch': 'other'}, 'gate': self.GATE}
        self.push_to('main', 'm.py', 'm\n', kit=kit)
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.REFUSED, out.lines)
        self.assertIn('there is no origin/other after the fetch', '\n'.join(out.lines))
        self.assertEqual(self.remote_staging(), self.main)
        self.assertIsNone(self.lock().read())

    def test_a_staging_setting_written_on_staging_alone_is_not_read(self):
        # staging turned off on the staging branch: the setting is main's, so it stays on
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        moved = self.push_to(STAGING, 's.py', 's\n', kit={'gate': self.GATE})
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.repo.git('rev-parse', out.merge + '^1'), moved)
        self.assertEqual(self.remote_staging(), out.merge)
        self.assertEqual(self.remote_main(), self.main)


class WorkBaseReaders(StagingCase):
    def cfg(self):
        return kconfig.load(self.repo.root)

    def test_the_landing_lock_guards_the_staging_branch(self):
        lock = landing.LandingLock.for_config(self.repo.root, self.cfg())
        self.assertEqual((lock.main, lock.base), ('main', STAGING))
        rec = lock.take(['kit/x'])
        self.addCleanup(lock.release, rec['token'])
        self.assertEqual(rec['main_branch'], STAGING)
        self.assertEqual(rec['origin_at_take'],
                         self.repo.git('rev-parse', 'refs/remotes/origin/%s' % STAGING))
        self.assertIsNone(lock.gate_refusal())

    def test_the_landing_lock_off_staging_is_as_before(self):
        cfg = self.cfg()
        cfg._raw.pop('staging')
        lock = landing.LandingLock.for_config(self.repo.root, cfg)
        self.assertEqual((lock.main, lock.base), ('main', 'main'))

    def test_the_gate_defaults_to_the_staging_branch(self):
        ref, _note = runner.default_base(self.cfg(), self.repo.root)
        self.assertEqual(ref, 'refs/remotes/origin/%s' % STAGING)


def staging_only(fake):
    """A `refs.ls_remote` side effect answering `fake` for the staging branch alone."""
    real = refs.ls_remote

    def ls_remote(remote, ref, cwd, **kw):
        full = ref if ref.startswith('refs/') else 'refs/heads/%s' % ref
        return fake if full == 'refs/heads/%s' % STAGING else real(remote, ref, cwd, **kw)
    return ls_remote


class StuckOnStaging(_Stuck, StagingCase):
    def land_unreadable(self, tip, tracking_set=True):
        """The landing with ls-remote of staging failing after the push. `tracking_set` False: the
        push left the tracking ref where it was, so this checkout cannot tell it landed."""
        fake = staging_only((None, 'network down'))

        def ls_remote(remote, ref, cwd, **kw):
            if not tracking_set and ref.endswith('/' + STAGING):
                self.repo.git('update-ref', 'refs/remotes/origin/%s' % STAGING, self.main)
            return fake(remote, ref, cwd, **kw)
        with mock.patch.object(refs, 'ls_remote', side_effect=ls_remote):
            out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.STUCK, out.lines)
        text = joined(out)
        self.assertIn('may have landed', text)
        self.assertNotIn('--ff-only', text)            # main is never brought to the staging tip
        self.assertNotIn('differs from', text)         # break_lock asks only about MERGE_HEAD
        self.assertIn('or while a merge is in progress in the main checkout (MERGE_HEAD)', text)
        return out, text, commands(out)

    def assert_cleared(self, out, cmds):
        self.sh(cmds[0])
        self.assertEqual(self.sh(cmds[1]), out.merge)  # it landed on staging
        self.assert_broken(self.sh(cmds[-1]))
        self.assertEqual(self.main_now(), self.main)
        self.assertEqual(self.remote_main(), self.main)
        self.assertEqual(self.remote_staging(), out.merge)

    def test_a_push_the_tracking_ref_shows_clears_without_moving_main(self):
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out, _text, cmds = self.land_unreadable(tip)
        self.assertEqual(len(cmds), 3, cmds)           # fetch, check, break
        self.assert_cleared(out, cmds)

    def test_a_push_that_may_have_landed_never_moves_main(self):
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out, text, cmds = self.land_unreadable(tip, tracking_set=False)
        self.assertIn('the landing is done: nothing in the main checkout is to be moved (a '
                      'landing onto the staging branch %s never moves main; only a promotion '
                      'does); go to the last step' % STAGING, text)
        self.assertIn('the merge is landed: nothing in the main checkout is to be moved', text)
        self.assertEqual(len(cmds), 4, cmds)           # fetch, check, branch -r, break
        self.assert_cleared(out, cmds)

    def landed(self):
        """A green landing onto staging, and its lander, whose clear steps are then read."""
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        lander = self.lander(BRANCH, self.main, tip)
        out = lander.run()
        self.assertEqual(out.code, G.OK, out.lines)
        return lander, out.merge

    def test_a_staging_tip_this_checkout_cannot_place_never_asks_to_move_main(self):
        lander, merge = self.landed()
        left = lander.leftovers()
        left['pushed'] = '1' * 40                      # a staging tip this clone lacks
        steps = lander.clear_steps(left)
        self.assertEqual(len(steps), 1, steps)          # check_first
        text, cmds = steps[0]
        self.assertIn('origin/%s is at 1111111111, which this checkout cannot place against the '
                      'merge %s' % (STAGING, merge[:10]), text)
        self.assertIn('If the fetch failed, the list is stale: stop here. If the list names '
                      'origin/%s, the merge is landed: nothing in the main checkout is to be moved '
                      '(a landing onto the staging branch %s never moves main; only a promotion '
                      'does); go to the last step. If it does not' % (STAGING, STAGING), text)
        self.assertNotIn('do not move', text)
        self.assertNotIn('by hand', text)
        self.assertEqual(len(cmds), 2, cmds)            # fetch, branch -r --contains

    def test_a_lagging_tracking_ref_and_main_s_own_commit_on_staging(self):
        lander, merge = self.landed()
        self.repo.write('own.txt', 'own\n')
        own = self.repo.commit('main checkout own commit', 'own.txt')   # local main moves alone
        self.repo.git('update-ref', 'refs/remotes/origin/%s' % STAGING, self.main)   # lags
        left = lander.leftovers()
        self.assertEqual((left['main'], left['pushed'], left['tracking']), (own, merge, self.main))
        self.assertEqual(left['extra'], [])             # main is not the base: not "on top"
        steps = lander.clear_steps(left)                # on_origin_steps
        self.assertEqual(steps[0], (
            'the tracking ref origin/%s is at %s, not %s as on origin: fetch it, so the next '
            'landing starts from it' % (STAGING, self.main[:10], merge[:10]),
            ['git -C "%s" fetch origin' % self.repo.root]))
        self.assertNotIn('break_lock refuses', '\n'.join(t for t, _ in steps))
        with mock.patch.object(type(lander), 'staging', new_callable=mock.PropertyMock,
                               return_value=False):     # the control: off staging they count
            self.assertEqual([l[:40] for l in lander.leftovers()['extra']], [own])

if __name__ == '__main__':
    unittest.main()
