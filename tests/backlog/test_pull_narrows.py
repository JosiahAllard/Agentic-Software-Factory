"""`pull` of a record `next` offers by narrowing (NARROWS; KIT-NEXT-NARROW-AUTO S3a): the pull
recomputes the split from git, writes both records and their `narrowed:` lines in one store
write, and prints both narrowings. Narrowing the other branch's live record is the lead's
`pull-collision` row; a split that narrows only the puller's record is a plain pull."""
from unittest import mock

from kitlib.backlog import plan as pmod
from kitlib.backlog import policy
from kitlib.backlog import store as smod
from kitlib.backlog.errors import Refusal

from tests.backlog.helpers import BRANCH, TODAY, StoreCase, priced

FILES = '## Files\n- kitlib/y/c.py\n'


class PullNarrows(StoreCase):
    """HOLD is pulled on kit/holder, whose worktree changed kitlib/x/a.py (committed) and added
    kitlib/x/new.py (untracked); FOO is the open record a pull on BRANCH takes."""

    def setUp(self):
        super().setUp()
        for p in ('kitlib/x/a.py', 'kitlib/x/b.py', 'kitlib/y/c.py', 'kitlib/y/d.py'):
            self.repo.write(p, 'x\n')
        self.repo.commit('code', 'kitlib')
        self.repo.git('push', '-q', 'origin', 'main')
        self.wt = self.repo.worktree('holder', 'kit/holder')
        self.repo.write('kitlib/x/a.py', 'changed\n', root=self.wt)
        self.repo.commit('work', 'kitlib/x/a.py', cwd=self.wt)
        self.repo.write('kitlib/x/new.py', 'new\n', root=self.wt)          # untracked, dirty

    def seed_pair(self, hold_scope='kitlib/**', files=FILES, **hold):
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/holder',
                         scope=hold_scope, body=files, **hold),
                  priced('FOO', priority='3', scope='kitlib/**'))
        return self.remote_tip()

    def pull(self, may_collide=True):
        return self.verb('pull', 'FOO', None, may_collide)

    def test_a_lead_s_pull_narrows_both_records_in_one_store_commit(self):
        before = self.seed_pair()
        change = self.pull()
        foo, hold = self.get('FOO'), self.get('HOLD')
        self.assertEqual(foo.get('state'), 'pulled')
        self.assertEqual(foo.all('scope'), ['kitlib/x/b.py', 'kitlib/y/d.py'])
        self.assertEqual(foo.all('narrowed'), ['kitlib/** HOLD kit/holder'])
        self.assertEqual(hold.all('scope'), ['kitlib/x/a.py', 'kitlib/x/new.py', 'kitlib/y/c.py'])
        self.assertEqual(hold.all('narrowed'), ['kitlib/** FOO %s' % BRANCH])
        after = self.remote_tip()
        self.assertEqual(self.repo.git('rev-list', '--count', '%s..%s' % (before, after)), '1')
        self.assertEqual(sorted(self.repo.git('diff-tree', '--no-commit-id', '--name-only', '-r',
                                              after).split()),
                         ['backlog/FOO.md', 'backlog/HOLD.md'])
        self.assertIn('NARROWED FOO for HOLD on kit/holder: drops kitlib/x/a.py, kitlib/x/new.py, '
                      'kitlib/y/c.py from kitlib/** (keeps kitlib/x/b.py, kitlib/y/d.py)',
                      change.lines)
        self.assertIn('NARROWED HOLD for FOO on %s: drops kitlib/x/b.py, kitlib/y/d.py from '
                      'kitlib/** (keeps kitlib/x/a.py, kitlib/x/new.py, kitlib/y/c.py)' % BRANCH,
                      change.lines)
        self.assertIn('  narrowed:   kitlib/** HOLD kit/holder', change.lines)
        self.assertIn('  narrowed:   kitlib/** FOO %s' % BRANCH, change.lines)
        self.assertEqual(change.rows, [policy.COLLISION_ROWS['pull']])
        self.assertIsNone(foo.get('preempt'))

    def test_a_builder_s_pull_that_would_narrow_the_live_record_is_refused(self):
        before = self.seed_pair()
        with self.assertRaises(Refusal) as cm:
            self.pull(may_collide=False)
        msg = str(cm.exception)
        self.assertIn('HOLD', msg)
        self.assertIn('pull-collision', msg)
        self.assertEqual(self.remote_tip(), before)
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_split_without_files_narrows_only_the_puller_s_record_for_any_role(self):
        self.seed_pair(hold_scope='kitlib/x/**', files='')
        offers, _p, _h, _o = pmod.offer_for(self.store.load(), self.ctx, narrow=True)
        keep = offers[0].split.keep
        self.assertFalse([m for m in keep if m.startswith('kitlib/x')], keep)
        change = self.pull(may_collide=False)
        self.assertEqual(self.get('FOO').all('scope'), keep)
        self.assertEqual(self.get('FOO').all('narrowed'), ['kitlib/** HOLD kit/holder'])
        self.assertEqual(self.get('HOLD').all('scope'), ['kitlib/x/**'])
        self.assertEqual(self.get('HOLD').all('narrowed'), [])
        self.assertIn('HOLD keeps its scope (no ## Files)', change.lines)
        self.assertFalse(getattr(change, 'rows', None))

    def test_t_grown_since_the_offer_is_recomputed_and_the_smaller_keep_written(self):
        self.seed_pair()
        offers, _p, _h, _o = pmod.offer_for(self.store.load(), self.ctx, narrow=True)
        self.assertEqual(offers[0].split.keep, ['kitlib/x/b.py', 'kitlib/y/d.py'])
        self.repo.write('kitlib/x/b.py', 'dirty\n', root=self.wt)
        change = self.pull()
        self.assertEqual(self.get('FOO').all('scope'), ['kitlib/y/d.py'])
        self.assertTrue(any(line.startswith('NARROWED FOO for HOLD') and
                            line.endswith('(keeps kitlib/y/d.py)') for line in change.lines),
                        change.lines)

    def test_t_grown_over_everything_the_candidate_kept_refuses(self):
        before = self.seed_pair()
        self.repo.write('kitlib/x/b.py', 'dirty\n', root=self.wt)
        self.repo.write('kitlib/y/d.py', 'dirty\n', root=self.wt)
        with self.assertRaises(Refusal) as cm:
            self.pull()
        self.assertIn('narrowing no longer holds', str(cm.exception))
        self.assertEqual(self.remote_tip(), before)

    def test_t_that_cannot_be_read_refuses(self):
        before = self.seed_pair()
        self.repo.git('worktree', 'remove', '--force', self.wt)
        with self.assertRaises(Refusal) as cm:
            self.pull()
        self.assertIn('narrowing no longer holds', str(cm.exception))
        self.assertEqual(self.remote_tip(), before)

    def test_an_unshared_store_never_narrows_the_live_record(self):
        before = self.seed_pair()
        with mock.patch.object(pmod, '_shared_store', lambda ctx: False):
            offers, _p, _h, _o = pmod.offer_for(self.store.load(), self.ctx, narrow=True)
            self.assertEqual(offers, [])
            with self.assertRaises(Refusal):
                self.pull()
        self.assertEqual(self.remote_tip(), before)

    def test_a_second_narrowing_of_the_live_record_merges_its_lines(self):
        self.seed_pair(narrowed=['kitlib/** FOO kit/old', 'docs/** BAR kit/bar'])
        self.pull()
        self.assertEqual(self.get('HOLD').all('narrowed'),
                         ['kitlib/** FOO %s' % BRANCH, 'docs/** BAR kit/bar'])


class PullNarrowsTwoHolders(StoreCase):
    """MOVEMENT-S5's shape: FOO's one broad member is hit by two live pulls, HOLD on kit/holder
    (kitlib/x/**, changed kitlib/x/a.py, added kitlib/x/new.py) and HOLD2 on kit/holder2
    (kitlib/y/**, changed kitlib/y/c.py); the pull narrows all three in one store write."""

    def setUp(self):
        super().setUp()
        for p in ('kitlib/x/a.py', 'kitlib/x/b.py', 'kitlib/y/c.py', 'kitlib/y/d.py'):
            self.repo.write(p, 'x\n')
        self.repo.commit('code', 'kitlib')
        self.repo.git('push', '-q', 'origin', 'main')
        self.wt = self.repo.worktree('holder', 'kit/holder')
        self.repo.write('kitlib/x/a.py', 'changed\n', root=self.wt)
        self.repo.commit('work', 'kitlib/x/a.py', cwd=self.wt)
        self.repo.write('kitlib/x/new.py', 'new\n', root=self.wt)          # untracked, dirty
        self.wt2 = self.repo.worktree('holder2', 'kit/holder2')
        self.repo.write('kitlib/y/c.py', 'changed\n', root=self.wt2)
        self.repo.commit('work', 'kitlib/y/c.py', cwd=self.wt2)

    def seed_three(self, files2='## Files\n- kitlib/y/c.py\n'):
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/holder',
                         scope='kitlib/x/**', body='## Files\n- kitlib/x/a.py\n'),
                  priced('HOLD2', state='pulled', pulled_at=TODAY, pulled_by='kit/holder2',
                         scope='kitlib/y/**', body=files2),
                  priced('FOO', priority='3', scope='kitlib/**'))
        return self.remote_tip()

    def test_the_pull_narrows_every_record_in_one_store_write(self):
        before = self.seed_three()
        change = self.verb('pull', 'FOO', None, True)
        foo, hold, hold2 = self.get('FOO'), self.get('HOLD'), self.get('HOLD2')
        self.assertEqual(foo.get('state'), 'pulled')
        self.assertEqual(foo.all('scope'), ['kitlib/x/b.py', 'kitlib/y/d.py'])
        self.assertEqual(foo.all('narrowed'), ['kitlib/** HOLD kit/holder',
                                               'kitlib/** HOLD2 kit/holder2'])
        self.assertEqual(hold.all('scope'), ['kitlib/x/a.py', 'kitlib/x/new.py'])
        self.assertEqual(hold.all('narrowed'), ['kitlib/x/** FOO %s' % BRANCH])
        self.assertEqual(hold2.all('scope'), ['kitlib/y/c.py'])
        self.assertEqual(hold2.all('narrowed'), ['kitlib/y/** FOO %s' % BRANCH])
        after = self.remote_tip()
        self.assertEqual(self.repo.git('rev-list', '--count', '%s..%s' % (before, after)), '1')
        self.assertEqual(sorted(self.repo.git('diff-tree', '--no-commit-id', '--name-only', '-r',
                                              after).split()),
                         ['backlog/FOO.md', 'backlog/HOLD.md', 'backlog/HOLD2.md'])
        self.assertIn('NARROWED HOLD2 for FOO on %s: drops kitlib/y/d.py from kitlib/y/** '
                      '(keeps kitlib/y/c.py)' % BRANCH, change.lines)
        self.assertEqual(change.rows, [policy.COLLISION_ROWS['pull']])

    def test_one_holder_that_cannot_be_narrowed_narrows_nothing(self):
        # HOLD2's `## Files` cannot be read: no narrowing, though HOLD alone would narrow.
        before = self.seed_three(files2='## Files\n- kitlib/y/**\n')
        offers, passed, _h, _o = pmod.offer_for(self.store.load(), self.ctx, narrow=True)
        self.assertEqual(offers, [])
        self.assertIsNotNone([p for p in passed if p.rec.id == 'FOO'][0].pair)
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO', None, True)
        self.assertIn('HOLD2', str(cm.exception))
        self.assertEqual(self.remote_tip(), before)
        self.assertEqual(self.get('HOLD').all('scope'), ['kitlib/x/**'])


class SharedStore(StoreCase):
    """`_shared_store`: a ref store is read by every branch; a folder store in a checkout that
    is not the PM data worktree is that checkout's own copy."""

    def test_a_ref_store_is_shared(self):
        self.assertTrue(pmod._shared_store(self.ctx))

    def test_a_folder_store_outside_the_pm_data_worktree_is_not(self):
        store = smod.Store(smod.MainBackend(self.repo.root, 'docs/backlog'), self.rules)
        ctx = pmod.Context(store, self.repo.root, branch=BRANCH, today=TODAY)
        self.assertFalse(pmod._shared_store(ctx))
        self.assertFalse(pmod._shared_store(pmod.Context(None, self.repo.root)))
