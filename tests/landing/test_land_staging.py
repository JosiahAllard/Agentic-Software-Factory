"""A landing onto a staging branch (`.kit.json` "staging".branch, docs/gate-landing.md "Staging
branch"): it merges onto <remote>/<staging> and pushes there; the main branch, on the remote and in
the main checkout, is never moved (fix 1: no local fast-forward of main); the staging setting is
read from <remote>/<main> (fix 2: Merge.follow through kitlib.config.load_at(staging_from=)).
Then readers of the work base: the landing lock and the gate's default base. A STUCK landing
onto staging prints steps that never move main and the break step's real refusal (MERGE_HEAD)."""
import json
import os
import re
import unittest
from unittest import mock

import kitlib.config as kconfig
from kitlib import gate as G
from kitlib.backlog import record as rmod
from kitlib.backlog import reopen
from kitlib.backlog import store as smod
from kitlib.backlog import store_ref
from kitlib.config import pmdata
from kitlib.gate import runner
from kitlib.git import refs
from kitlib.records import landing
from tests.backlog.helpers import priced
from tests.landing import test_land_store as tls
from tests.landing.test_land import BRANCH, LandCase
from tests.landing.test_land_store import TODAY, StoreLandCase, pulled
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


class RevertCase(StoreLandCase):
    """A ref store and a staging branch (docs/backlog-store.md "Reopen at a revert"). Landings
    are merged onto staging by hand (`Merge kit/<name>`, as the lander's), and their closes are
    store commits made by hand (`closes <ID>: landed <merge>`), so most cases call
    kitlib.backlog.reopen directly, as the landing of a branch that reverts them would."""
    KIT = dict(StoreLandCase.KIT, staging={'branch': STAGING})

    def setUp(self):
        StoreLandCase.setUp(self)
        self.repo.git('push', '-q', 'origin', 'main:refs/heads/%s' % STAGING)
        self.repo.git('fetch', '-q', 'origin')
        day = mock.patch.object(reopen, 'today', return_value=TODAY)
        day.start()
        self.addCleanup(day.stop)

    remote_staging = StagingCase.remote_staging

    def staging(self):
        return self.repo.git('rev-parse', 'refs/remotes/origin/%s' % STAGING)

    def landed(self, name, message='work'):
        """kit/<name> merged onto staging as a landing merges it, and pushed: the merge."""
        wt = self.repo.worktree(name)
        self.repo.write('%s.py' % name, 'x = 1\n', root=wt)
        tip = self.repo.commit(message, '%s.py' % name, cwd=wt)
        self.repo.git('checkout', '-q', '--detach', 'origin/%s' % STAGING, cwd=wt)
        self.repo.git('merge', '-q', '--no-ff', '-m', 'Merge kit/%s' % name, tip, cwd=wt)
        merge = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        self.repo.git('push', '-q', 'origin', '%s:refs/heads/%s' % (merge, STAGING))
        self.repo.git('fetch', '-q', 'origin')
        return merge

    def close(self, rid, merge, message=None):
        """The store commit a landing's close makes: `rid` deleted, `closes <rid>: landed <merge>`."""
        backend = self.backend()
        return backend.publish(backend.load(rmod.Rules()), {rid: None},
                               message or 'closes %s: landed %s\n' % (rid, merge))

    def reverting(self, *shas, name='revert'):
        """kit/<name> on staging with one commit whose message has a Reverts-landing line for each
        of `shas`: its tip."""
        wt = self.repo.worktree(name)
        self.repo.git('reset', '-q', '--hard', 'origin/%s' % STAGING, cwd=wt)
        self.repo.write('r.py', 'r\n', root=wt)
        return self.repo.commit('revert\n\n' + ''.join('Reverts-landing: %s\n' % s for s in shas),
                                'r.py', cwd=wt)

    def reopen(self, tip, base=None):
        return reopen.reopen_reverted(self.repo.root, kconfig.load(self.repo.root), 'kit/revert',
                                      'r' * 40, tip, base or self.staging())

    def record(self, rid):
        return self.backend().load(rmod.Rules()).get(rid)


class RevertReopens(RevertCase):
    def test_a_landing_onto_staging_closes_and_the_landing_of_its_revert_reopens(self):
        self.seed(pulled('FOO', priority='3'), priced('BAZ'))
        _, tip = self.branch('feature', {'a.py': 'x = 1\n'})
        out = self.lander(BRANCH, self.main, tip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        landed = out.merge
        self.assertEqual(self.remote_staging(), landed)
        self.assertEqual((self.stems(), self.subjects()[0]),
                         (['BAZ'], 'closes FOO: landed %s' % landed))
        wt = self.repo.worktree('revert')
        self.repo.git('reset', '-q', '--hard', 'origin/%s' % STAGING, cwd=wt)
        self.repo.git('rm', '-q', 'a.py', cwd=wt)          # `git revert -m 1`: a.py was new
        self.repo.git('commit', '-q', '-m', 'Revert kit/feature\n\nReverts-landing: %s\n' % landed,
                      cwd=wt)
        self.assertEqual(self.repo.git('rev-parse', 'HEAD^{tree}', cwd=wt),
                         self.repo.git('rev-parse', self.main + '^{tree}'))
        rtip = self.repo.git('rev-parse', 'HEAD', cwd=wt)
        out = self.lander('kit/revert', self.main, rtip).run()
        self.assertEqual(out.code, G.OK, out.lines)
        self.assertEqual(self.stems(), ['BAZ', 'FOO'])
        rec = self.record('FOO')
        self.assertEqual((rec.get('state'), rec.get('priority'), rec.get('pulled_by'),
                          rec.get('pulled_at')), ('open', '3', None, None))
        self.assertEqual(self.subjects()[0],
                         'reopens FOO: landing %s reverted by kit/revert' % landed)
        said = [l for l in out.lines if l.startswith('reopened FOO in the backlog store ')]
        self.assertEqual(len(said), 1, out.lines)
        self.assertIn('the landing %s that closed it was reverted by kit/revert' % landed, said[0])
        self.assertFalse([l for l in out.lines if l.startswith('WARNING')
                          or 'Reverts-landing' in l], out.lines)

    def test_the_restored_record_is_open_and_unpulled_with_its_priority_kept(self):
        landed = self.landed('one')
        self.seed(pulled('FOO', by='kit/one', priority='4', pulled_session='s-1',
                         body='Why it is here.'))
        self.close('FOO', landed)
        lines = self.reopen(self.reverting(landed))
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('reopened FOO in the backlog store refs/backlog/main '
                                            'on origin: '), lines)
        self.assertTrue(lines[0].endswith('state: open, priority 4 kept'), lines)
        want = priced('FOO', priority='4', scope=['area/foo/**', 'a.py'], body=(
            'Why it is here.\n\nReopened (%s): the landing %s that closed it was reverted by '
            'kit/revert (merge %s).' % (TODAY, landed[:12], 'r' * 12)))['FOO']
        self.assertEqual(self.record('FOO').text, want)

    def test_only_what_the_landing_itself_closed_is_reopened(self):
        landed = self.landed('one', message='work\n\nCloses: ZED\n')
        self.seed(pulled('FOO', by='kit/one'), pulled('QUX', by='kit/other'),
                  pulled('ZED', by='main'), pulled('BAR', by='kit/one'))
        for rid in ('FOO', 'QUX', 'ZED'):
            self.close(rid, landed)
        self.close('BAR', landed, message='closes BAR: done by hand\n')
        lines = self.reopen(self.reverting(landed))
        self.assertEqual(self.stems(), ['FOO', 'ZED'])          # its own pull and its Closes line
        note = [l for l in lines if l.startswith('NOTE')]
        self.assertEqual(len(note), 1, lines)
        self.assertIn('QUX was closed as `landed %s`' % landed, note[0])
        self.assertIn('but not by that landing: its branch kit/one did not hold it pulled', note[0])
        self.assertEqual(sorted(l.split()[1] for l in lines if l.startswith('reopened ')),
                         ['FOO', 'ZED'])

    def test_a_record_closed_again_by_a_later_landing_is_left_alone(self):
        first, second = self.landed('one'), self.landed('two')
        self.seed(pulled('FOO', by='kit/one'))
        self.close('FOO', first)
        self.seed(pulled('FOO', by='kit/two'), message='reopens FOO: by hand\n')
        self.close('FOO', second)
        lines = self.reopen(self.reverting(first))
        self.assertEqual(self.stems(), [])
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('NOTE: kit/revert landed, and no backlog store this '
                                            'landing writes holds a close by the reverted landing '
                                            '%s, so nothing was reopened' % first), lines)

    def test_a_sha_that_is_not_a_full_landing_merge_on_the_base_reopens_nothing(self):
        landed = self.landed('one')
        self.seed(pulled('FOO', by='kit/one'))
        self.close('FOO', landed)
        side = self.repo.git('rev-parse', landed + '^2')        # in the base, off its chain
        plain = self.branch('plain', {'p.py': 'p\n'})[1]
        with mock.patch.object(store_ref.RefBackend, 'removed') as removed:
            lines = self.reopen(self.reverting(landed[:12], side, self.main))
            self.assertEqual(self.reopen(plain, base=self.main), [])   # no line: no store read
        removed.assert_not_called()
        self.assertEqual(self.stems(), [])
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(all(l.startswith('NOTE: kit/revert landed, and a Reverts-landing line of '
                                         'its commits names `') for l in lines), lines)
        self.assertIn('`%s`, which is not a full 40-character commit sha' % landed[:12], lines[0])
        self.assertIn('`%s`, which is not on the first-parent chain of %s'
                      % (side, self.staging()[:12]), lines[1])
        self.assertIn('`%s`, which is not a merge commit' % self.main, lines[2])

    def test_a_record_the_store_holds_again_at_write_time_is_left_as_it_is(self):
        landed = self.landed('one')
        self.seed(pulled('FOO', by='kit/one'))
        self.close('FOO', landed)
        real = smod.Store.removed

        def then_added_again(store, snap):        # after the first load, before the write
            found = real(store, snap)
            self.seed(priced('FOO', priority='5'), message='add FOO again\n')
            return found
        with mock.patch.object(smod.Store, 'removed', then_added_again):
            lines = self.reopen(self.reverting(landed))
        self.assertEqual(lines, ['NOTE: kit/revert landed, and FOO, which the reverted landing %s '
                                 'closed, is in the backlog store refs/backlog/main on origin '
                                 'again, so it was left as it is' % landed])
        self.assertEqual((self.record('FOO').get('priority'), self.subjects()[0]),
                         ('5', 'add FOO again'))

    def test_a_write_that_fails_names_the_full_restore(self):
        landed = self.landed('one')
        self.seed(pulled('FOO', by='kit/one', priority='4'))
        before = self.record('FOO').text
        self.close('FOO', landed)
        with mock.patch.object(smod.Store, 'write', side_effect=RuntimeError('boom')):
            lines = self.reopen(self.reverting(landed))
        self.assertEqual(len(lines), 1, lines)
        warn = lines[0]
        self.assertTrue(warn.startswith('WARNING: kit/revert landed, and FOO, which the reverted '
                                        'landing %s closed, was not reopened' % landed), warn)
        self.assertIn('(RuntimeError: boom)', warn)
        shown = re.search(r'git -C "([^"]+)" show (\S+)\^:(\S+), then', warn)
        self.assertIsNotNone(shown, warn)
        self.assertEqual(shown.group(3), 'backlog/FOO.md')
        self.assertEqual(self.repo.git('show', '%s^:%s' % (shown.group(2), shown.group(3)))
                         + '\n', before)                     # the command reads the old text
        self.assertIn(' add FOO --reopen --summary "', warn)
        self.assertIn('--body-file <its body>', warn)
        self.assertTrue(warn.endswith(' set FOO --priority 4 --priority-by agent'), warn)
        self.assertEqual(self.stems(), [])

    def test_two_reverts_landing_lines_reopen_the_union(self):
        first, second = self.landed('one'), self.landed('two')
        self.seed(pulled('FOO', by='kit/one'), pulled('BAR', by='kit/two'), pulled('KEEP'))
        self.close('FOO', first)
        self.close('BAR', second)
        lines = self.reopen(self.reverting(first, second))
        self.assertEqual(self.stems(), ['BAR', 'FOO', 'KEEP'])
        self.assertEqual(sorted(l.split()[1] for l in lines), ['BAR', 'FOO'])

    def test_a_record_in_both_stores_is_reopened_in_each(self):
        tree =self.repo.git('rev-parse', self.main + '^{tree}')
        pmdata.write(self.repo.common_dir(), 'pmdata', 'private',
                     self.repo.git('commit-tree', tree, '-m', 'PM data root'))
        landed = self.landed('one')
        wt = tls.PmDataClose.pm_worktree(self, pulled('FOO', by='kit/one'))
        self.repo.commit('seed the PM data store', 'kit/backlog', '.kit.json', cwd=wt)
        self.repo.git('rm', '-q', 'kit/backlog/FOO.md', cwd=wt)
        self.repo.git('commit', '-q', '-m', 'closes FOO: landed %s' % landed, cwd=wt)
        self.seed(pulled('FOO', by='kit/one'))
        self.close('FOO', landed)
        lines = self.reopen(self.reverting(landed))
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith('reopened FOO in the backlog store refs/backlog/main '
                                            'on origin: '), lines)
        self.assertTrue(lines[1].startswith('reopened FOO in the backlog store kit/backlog in '),
                        lines)
        self.assertEqual(self.stems(), ['FOO'])
        with open(os.path.join(wt, 'kit', 'backlog', 'FOO.md'), encoding='utf-8') as f:
            self.assertIn('state: open\n', f.read())
        self.assertEqual(self.repo.git('log', '-1', '--format=%s', cwd=wt),
                         'reopens FOO: landing %s reverted by kit/revert' % landed)


class RevertImplicitStore(RevertCase):
    """No "backlog".store: the default store is read only, so a revert reopens nothing there."""
    KIT = {'staging': {'branch': STAGING}}

    def test_the_implicit_default_store_is_skipped(self):
        landed = self.landed('one')
        self.seed(pulled('FOO', by='kit/one'))
        tip = self.close('FOO', landed)
        with mock.patch.object(store_ref.RefBackend, 'removed') as removed:
            lines = self.reopen(self.reverting(landed))
        removed.assert_not_called()
        self.assertEqual((self.stems(), self.remote_store()), ([], tip))
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('NOTE: kit/revert landed, and no backlog store this '
                                            'landing writes holds a close by'), lines)


if __name__ == '__main__':
    unittest.main()
