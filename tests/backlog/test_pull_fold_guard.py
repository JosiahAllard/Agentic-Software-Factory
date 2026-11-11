"""The fold-in guard on `pull` (docs/backlog.md, "The fold-in guard"): a pull whose scope overlaps
a path another live pull's branch changed since its merge base with the code main belongs on that
branch, so it is the `pull-collision` row (kitlib/backlog/roles.py): refused unless the caller's
role may take it (`may_collide`) and gives `--reason`, the same route as a collision with a live
pull's scope. The caller's own branch never counts. `next` runs the same
guard and passes such a record over (NextFold). End to end through Store.write on a ref store
with a bare remote."""
import unittest
from unittest import mock

from kitlib.backlog import plan as pmod
from kitlib.backlog import plan_claim, plan_narrow
from kitlib.backlog import policy
from kitlib.backlog.errors import Failure, Refusal, Usage
from kitlib.git import cmd, refs

from kitlib.backlog import opener

from tests.backlog.helpers import BRANCH, TODAY, StoreCase, parse, priced
from tests.backlog.test_add_found_on import PmDataWorktree, _Wrap

HOLDER = 'kit/holder'
FOLD = 'fold it into kit/holder, or pull it after kit/holder lands'


def held(rid, by, scope, session=None):
    kw = {'session': session} if session else {}
    return priced(rid, state='pulled', pulled_at=TODAY, pulled_by=by, scope=scope, **kw)


class _Branches(StoreCase):

    def branch_with(self, branch, *paths, orphan=False, existing=False):
        """`branch` with one commit that adds `paths`, cut from main (or an orphan, or on the
        `existing` branch); main stays checked out."""
        if existing:
            self.repo.git('checkout', '-q', branch)
        else:
            self.repo.git('checkout', '-q', '--orphan' if orphan else '-b', branch)
        for p in paths:
            self.repo.write(p, 'x\n')
        self.repo.git('add', '-A')      # not by name: `[a]` in a pathspec is a glob
        self.repo.git('commit', '-q', '-m', 'work on %s' % branch)
        self.repo.git('checkout', '-q', '-f', 'main')

    def failing(self, module, **over):
        for mod in (pmod, plan_narrow, plan_claim):     # the plan modules that call git
            if hasattr(mod, module):
                patch = mock.patch.object(mod, module, _Wrap(getattr(mod, module), **over))
                patch.start()
                self.addCleanup(patch.stop)


class SharedBaseOffMain(_Branches):
    """Two branches cut from a commit that is not on main and changed kitlib/x/shared.py: what both
    inherited is no change of the other's (plan_narrow._one_branch `ours`)."""

    def setUp(self):
        super().setUp()
        self.repo.git('checkout', '-q', '-b', 'shared-base', 'main')
        self.repo.write('kitlib/x/shared.py', 'shared\n')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-q', '-m', 'off main')
        self.base = self.repo.head()
        self.repo.git('checkout', '-q', '-f', 'main')
        self.repo.git('branch', '-f', BRANCH, self.base)
        self.holding = [parse('HOLD', id='HOLD', state='pulled', pulled_at=TODAY, pulled_by=HOLDER,
                              scope='docs/held.md')]

    def other(self, *paths):
        self.repo.git('checkout', '-q', '-b', HOLDER, self.base)
        for p in paths:
            self.repo.write(p, 'x\n')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-q', '-m', 'work on holder')
        self.repo.git('checkout', '-q', '-f', 'main')

    def hits(self):
        return plan_narrow._pull_fold_hits(self.ctx, ['kitlib/x/shared.py'], self.holding,
                                           BRANCH)[0]

    def test_what_both_branches_inherited_is_no_overlap(self):
        self.other('kitlib/y/own.py')
        self.assertEqual(self.hits(), [])

    def test_a_change_after_the_shared_commit_is_still_an_overlap(self):
        self.other('kitlib/x/shared.py')
        self.assertEqual(self.hits(),
                         ['kitlib/x/shared.py vs kitlib/x/shared.py (changed on kit/holder)'])

    def test_the_main_checkouts_offer_is_not_read_from_a_worktrees_cache(self):
        self.other('kitlib/y/own.py')
        self.seed(held('HOLD', HOLDER, 'docs/held.md'), held('MINE', BRANCH, 'docs/mine.md'),
                  priced('FOO', scope='kitlib/x/shared.py'))
        snap, diffs = self.store.load(), []
        holding = plan_narrow.current_offer(snap, self.ctx, BRANCH, diffs=diffs)[2]   # worktree's
        why = plan_narrow._offer_folds(self.ctx, holding, None, diffs)(snap.get('FOO'))
        self.assertIn(HOLDER, why or '')        # measured from the code main, as `next` does

    def test_our_own_branch_is_never_measured_from_itself_in_any_letter_case(self):
        self.other('kitlib/y/own.py')
        holding = self.holding + [parse('MINE', id='MINE', state='pulled', pulled_at=TODAY,
                                        pulled_by=BRANCH, scope='docs/mine.md')]
        changes = plan_narrow._live_changes(self.ctx, holding, None, BRANCH.upper())[0]
        self.assertIn('kitlib/x/shared.py', changes[BRANCH][0][1])
        self.assertNotIn('kitlib/x/shared.py', changes[HOLDER][0][1])

    def test_a_git_that_cannot_find_the_second_merge_base_keeps_the_code_mains(self):
        self.other('kitlib/y/own.py')
        real, ours = refs.merge_base, self.repo.git('rev-parse', 'refs/heads/' + BRANCH)

        def merge_base(a, b, cwd):
            return (None, 'boom') if a == ours else real(a, b, cwd)
        with mock.patch.object(plan_narrow.refs, 'merge_base', merge_base):
            self.assertEqual(self.hits(),
                             ['kitlib/x/shared.py vs kitlib/x/shared.py (changed on kit/holder)'])

    def test_an_unreadable_own_tip_only_drops_the_refinement(self):
        self.other('kitlib/y/own.py')
        real = plan_narrow._branch_tips

        def tips(ctx, branch, names):
            if branch == BRANCH:
                raise Failure('cannot read branch')
            return real(ctx, branch, names)
        with mock.patch.object(plan_narrow, '_branch_tips', tips):
            changes, _notes, unknown = plan_narrow._live_changes(self.ctx, self.holding, None,
                                                                 BRANCH)
        self.assertEqual(unknown, {})
        self.assertIn('kitlib/x/shared.py', changes[HOLDER][0][1])

    def test_without_our_branch_the_diff_starts_at_the_code_main(self):
        self.other('kitlib/y/own.py')
        changes = plan_narrow._live_changes(self.ctx, self.holding, None)[0]
        self.assertIn('kitlib/x/shared.py', changes[HOLDER][0][1])


class PullOwnBranchOnly(_Branches):

    def test_a_code_main_that_does_not_resolve_is_silent_when_only_the_own_branch_holds(self):
        self.ctx = pmod.Context(self.store, self.repo.root, branch=self.ctx.branch, today=TODAY,
                                code_main='trunk')
        self.seed(held('MINE', BRANCH, 'docs/mine.md'), priced('FOO', scope='kitlib/other/**'))
        change = self.verb('pull', 'FOO', reason='bundled with MINE')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertFalse([l for l in change.lines if 'does not resolve' in l], change.lines)


class PullFold(_Branches):

    def setUp(self):
        super().setUp()
        # The holder's declared scope is docs/held.md, but its branch changed kitlib/x/a.py: the
        # scope-collision rule cannot see that work, the fold guard does.
        self.branch_with(HOLDER, 'kitlib/x/a.py')
        self.seed(held('HOLD', HOLDER, 'docs/held.md'))

    def test_a_scope_another_live_branch_changed_is_refused(self):
        self.seed(priced('FOO', scope='kitlib/x/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        msg = str(cm.exception)
        self.assertIn('cannot pull FOO: its scope overlaps the work of kit/holder', msg)
        self.assertIn('kitlib/x/** vs kitlib/x/a.py (changed on kit/holder)', msg)
        self.assertIn(FOLD, msg)
        self.assertIn('`pull-collision`', msg)
        self.assertNotIn('--ruling', msg)
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_reason_alone_never_overrides_it(self):
        self.seed(priced('FOO', scope='kitlib/x/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO', reason='the owner wants it today')
        self.assertIn(FOLD, str(cm.exception))
        self.assertIn('`pull-collision`', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_role_that_may_collide_is_refused_without_a_reason(self):
        self.seed(priced('FOO', scope='kitlib/x/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO', may_collide=True)
        self.assertIn('--reason', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_reason_from_a_role_that_may_collide_writes_it_and_names_the_row(self):
        self.seed(priced('FOO', scope='kitlib/x/**'))
        change = self.verb('pull', 'FOO', reason='the owner wants it today', may_collide=True)
        r = self.get('FOO')
        self.assertEqual((r.get('state'), r.get('pulled_by'), r.get('preempt')),
                         ('pulled', BRANCH, 'the owner wants it today'))
        self.assertEqual(change.rows, ['pull-collision'])
        self.assertTrue(change.subject.endswith(policy.COLLISION_MARK), change.subject)
        self.assertNotIn('FOO', ' '.join(r.all('displaced')))

    def test_an_out_of_order_pull_that_folds_names_the_row(self):
        self.seed(priced('TOP', priority='1', scope='docs/top.md'),
                  priced('FOO', priority='5', scope='kitlib/x/a.py'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        self.assertIn('`pull-collision`', str(cm.exception))

    def test_a_disjoint_scope_pulls_plainly(self):
        self.seed(priced('FOO', scope='kitlib/other/**'))
        change = self.verb('pull', 'FOO', may_collide=True)
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertFalse(getattr(change, 'rows', None))

    def test_the_callers_own_branch_diff_never_counts(self):
        self.branch_with(BRANCH, 'kitlib/own/b.py', existing=True)
        self.seed(held('MINE', BRANCH, 'docs/mine.md'), priced('FOO', scope='kitlib/own/**'))
        change = self.verb('pull', 'FOO', reason='bundled with MINE')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertFalse(getattr(change, 'rows', None))

    def test_one_pull_diffs_each_live_branch_once(self):
        self.seed(priced('FOO', scope='kitlib/other/**'))
        real = plan_narrow._branch_changes
        with mock.patch.object(plan_narrow, '_branch_changes', side_effect=real) as spy:
            self.verb('pull', 'FOO')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertEqual([c.args[1] for c in spy.call_args_list], [HOLDER])

    def test_a_branch_that_holds_no_pull_does_not_count(self):
        self.branch_with('kit/idle', 'kitlib/z/a.py')
        self.seed(priced('FOO', scope='kitlib/z/**'))
        self.verb('pull', 'FOO')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')

    def test_a_dead_pull_does_not_count(self):
        self.seed(held('GONE', 'kit/gone', 'docs/gone.md'), priced('FOO', scope='kitlib/q/**'))
        self.verb('pull', 'FOO')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')

    def far_on_origin_only(self):
        self.branch_with('kit/far', 'kitlib/r/a.py')
        self.repo.git('push', '-q', 'origin', 'kit/far')
        self.repo.git('branch', '-q', '-D', 'kit/far')
        self.seed(held('FAR', 'kit/far', 'docs/far.md'))

    def test_a_live_branch_only_a_remote_has_is_compared_as_last_fetched(self):
        self.far_on_origin_only()
        self.seed(priced('FOO', scope='kitlib/r/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        self.assertIn('kitlib/r/** vs kitlib/r/a.py (changed on origin/kit/far)', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_remote_only_branch_that_does_not_overlap_is_noted_and_pulls(self):
        self.far_on_origin_only()
        self.seed(priced('FOO', scope='kitlib/other/**'))
        change = self.verb('pull', 'FOO')
        self.assertIn('NOTE: branch kit/far is not a local branch; its diff was compared as last '
                      'fetched from origin', change.lines)
        self.assertEqual(self.get('FOO').get('state'), 'pulled')

    def test_a_diff_path_that_cannot_be_compared_is_refused(self):
        self.branch_with('kit/odd', 'kitlib/y/[a].py')
        self.seed(held('ODD', 'kit/odd', 'docs/odd.md'), priced('FOO', scope='docs/y.md'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        self.assertIn('kitlib/y/[a].py', str(cm.exception))
        self.assertIn('cannot be compared', str(cm.exception))

    def test_several_records_on_one_branch_diff_it_once(self):
        self.seed(held('HOLD2', HOLDER, 'docs/held2.md'), priced('FOO', scope='kitlib/x/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        self.assertEqual(str(cm.exception).count('(changed on kit/holder)'), 1)

    # --- the code main and git failures ------------------------------------------------------

    def test_a_code_main_that_does_not_resolve_is_noted_and_skips_the_diffs(self):
        self.ctx = pmod.Context(self.store, self.repo.root, branch=self.ctx.branch, today=TODAY,
                                code_main='trunk')
        self.seed(priced('FOO', scope='kitlib/x/**'))
        change = self.verb('pull', 'FOO')
        self.assertIn('NOTE: code main trunk does not resolve here; branch diffs not compared',
                      change.lines)
        self.assertEqual(self.get('FOO').get('state'), 'pulled')

    def test_an_orphan_own_branch_is_never_noted(self):
        self.repo.git('branch', '-q', '-D', BRANCH)     # `existing` would not make it an orphan
        self.branch_with(BRANCH, 'docs/mine.md', orphan=True)
        self.seed(held('MINE', BRANCH, 'docs/mine.md'), priced('FOO', scope='kitlib/other/**'))
        change = self.verb('pull', 'FOO', reason='bundled with MINE')
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertFalse([l for l in change.lines if BRANCH in l and 'NOTE' in l], change.lines)

    def test_a_live_branch_with_no_merge_base_is_noted_and_skipped(self):
        # No history shared with the code main: the code main is not this branch's history (a
        # store whose code_main is unset), the same case as a code main that does not resolve.
        self.branch_with('kit/orphan', 'docs/y.md', orphan=True)
        self.seed(held('ORPH', 'kit/orphan', 'docs/orph.md'), priced('FOO', scope='docs/y.md'))
        change = self.verb('pull', 'FOO')
        base = refs.default_base(self.repo.root, 'origin', 'main')[0]
        self.assertIn('NOTE: code main %s shares no history with kit/orphan; set '
                      'backlog.code_main; branch diffs not compared' % base, change.lines)
        self.assertEqual(self.get('FOO').get('state'), 'pulled')
        self.assertFalse(getattr(change, 'rows', None))

    def assert_git_failure(self):
        self.seed(priced('FOO', scope='docs/y.md'))
        with self.assertRaises(Failure) as cm:
            self.verb('pull', 'FOO')
        self.assertIn('git broke', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_code_main_git_cannot_read_fails(self):
        real = refs.resolve
        base = refs.default_base(self.repo.root, 'origin', 'main')[0]
        self.failing('refs', resolve=lambda ref, root, **kw: (None, 'git broke')
                     if ref == base else real(ref, root, **kw))
        self.assert_git_failure()

    def test_a_live_branch_git_cannot_read_fails(self):
        real = refs.resolve
        self.failing('refs', resolve=lambda ref, root, **kw: (None, 'git broke')
                     if ref == 'refs/heads/' + HOLDER else real(ref, root, **kw))
        self.assert_git_failure()

    def test_a_merge_base_git_cannot_find_fails(self):
        self.failing('refs', merge_base=lambda a, b, root: (None, 'git broke'))
        self.assert_git_failure()

    def test_a_failed_diff_fails(self):
        real = cmd.run
        self.failing('cmd', run=lambda args, root, **kw: (None, 'git broke')
                     if args[:2] == ['diff', '--no-renames'] else real(args, root, **kw))
        self.assert_git_failure()


class NextFold(_Branches):
    """`next` runs the fold-in guard `pull` runs (plan_narrow.offer_for, `_offer_folds`): a record whose
    scope folds into another live pull's branch is passed over, not offered, so `next` never
    offers what `pull` then refuses. A branch git cannot diff passes the candidate over with a
    "cannot tell" note: `next` never fails on it, `pull` still does."""

    def setUp(self):
        super().setUp()
        self.branch_with(HOLDER, 'kitlib/x/a.py')
        self.seed(held('HOLD', HOLDER, 'docs/held.md'))

    def offer(self):
        offers, passed, _holding, _own = plan_narrow.offer_for(self.store.load(), self.ctx)
        return [o.rec.id for o in offers], {p.rec.id: p.why for p in passed}

    def test_a_folding_record_is_passed_over_and_the_next_one_offered(self):
        self.seed(priced('FOO', priority='1', scope='kitlib/x/**'),
                  priced('BAR', priority='2', scope='kitlib/other/**'))
        offers, passed = self.offer()
        self.assertEqual(offers, ['BAR'])
        self.assertIn('scope overlaps the work of kit/holder (kitlib/x/** vs kitlib/x/a.py '
                      '(changed on kit/holder)); fold it into kit/holder, or pull it after '
                      'kit/holder lands', passed['FOO'])

    def test_its_pull_names_the_fold_not_the_offer(self):
        # Passed over by `next`, so the fold refusal must come before "not in next's offer":
        # that one would hide the reason and the collision row that overrides it.
        self.seed(priced('FOO', scope='kitlib/x/**'))
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'FOO')
        self.assertIn(FOLD, str(cm.exception))
        self.assertNotIn("not in `next`'s offer", str(cm.exception))

    def test_a_pull_onto_the_branch_takes_what_the_main_checkouts_next_offers(self):
        # R1 folds only into kit/holder's work, so the main checkout's `next` passes it over and
        # offers R2; as kit/holder, R1 is offered and R2 collides with it. `pull R2` onto
        # kit/holder must take it (current_offer's `also` is the main checkout's offer).
        self.seed(priced('R1', priority='1', scope='kitlib/x/**'),
                  priced('R2', priority='2', scope='kitlib/x/b.py'))
        snap = self.store.load()
        main, passed, _h, _o = plan_narrow.offer_for(snap, self.ctx, branch='main')
        self.assertEqual([o.rec.id for o in main], ['R2'])
        self.assertIn(FOLD, {p.rec.id: p.why for p in passed}['R1'])
        mine, passed, _h, _o = plan_narrow.offer_for(snap, self.ctx, branch=HOLDER)
        self.assertEqual([o.rec.id for o in mine], ['R1'])
        self.assertIn('R2', {p.rec.id for p in passed})
        self.ctx = pmod.Context(self.store, self.repo.root, branch=HOLDER, today=TODAY)
        self.verb('pull', 'R2')
        self.assertEqual((self.get('R2').get('state'), self.get('R2').get('pulled_by')),
                         ('pulled', HOLDER))

    def test_the_two_offers_a_pull_checks_read_the_diffs_once_per_way_of_measuring(self):
        self.seed(priced('R1', scope='kitlib/x/**'), priced('R2', scope='kitlib/y/**'))
        self.ctx = pmod.Context(self.store, self.repo.root, branch=HOLDER, today=TODAY)
        real, reads = plan_narrow._live_changes, []

        def live_changes(*args):
            reads.append(args[3])       # `ours`: one read per way of measuring
            return real(*args)
        with mock.patch.object(plan_narrow, '_live_changes', live_changes):
            _offers, _passed, _holding, also, _notes = plan_narrow.current_offer(self.store.load(),
                                                                                 self.ctx)
        self.assertEqual([r.id for r in also], ['R2'])
        self.assertEqual(reads, [HOLDER, None])

    def test_the_callers_own_branch_diff_never_passes_over(self):
        self.branch_with(BRANCH, 'kitlib/own/b.py', existing=True)
        self.seed(held('MINE', BRANCH, 'docs/mine.md'), priced('FOO', scope='kitlib/own/**'))
        offers, passed = self.offer()
        self.assertEqual(offers, ['FOO'], passed)

    def test_each_live_branch_is_diffed_once_per_offer(self):
        self.seed(priced('FOO', scope='kitlib/a/**'), priced('BAR', scope='kitlib/b/**'))
        real, diffs = cmd.run, []

        def run(args, root, **kw):
            if args[:2] == ['diff', '--no-renames']:
                diffs.append(args)
            return real(args, root, **kw)
        self.failing('cmd', run=run)
        offers, _passed = self.offer()
        self.assertEqual(sorted(offers), ['BAR', 'FOO'])
        self.assertEqual(len(diffs), 1)

    def test_a_branch_git_cannot_diff_passes_over_and_never_fails_next(self):
        self.seed(priced('FOO', scope='docs/y.md'))
        self.failing('refs', merge_base=lambda a, b, root: (None, 'git broke'))
        offers, passed = self.offer()
        self.assertEqual(offers, [])
        self.assertIn("cannot tell whether FOO folds into kit/holder's work; pull after "
                      "kit/holder lands or check git", passed['FOO'])
        self.assertIn('git broke', passed['FOO'])
        # `pull` keeps failing on it: "cannot tell" is never "no overlap".
        with self.assertRaises(Failure) as cm:
            self.verb('pull', 'FOO')
        self.assertIn('git broke', str(cm.exception))
        self.assertEqual(self.get('FOO').get('state'), 'open')

    def test_a_code_main_git_cannot_read_passes_over_too(self):
        real = refs.resolve
        base = refs.default_base(self.repo.root, 'origin', 'main')[0]
        self.failing('refs', resolve=lambda ref, root, **kw: (None, 'git broke')
                     if ref == base else real(ref, root, **kw))
        self.seed(priced('FOO', scope='docs/y.md'))
        offers, passed = self.offer()
        self.assertEqual(offers, [])
        self.assertIn("cannot tell whether FOO folds into kit/holder's work", passed['FOO'])


class SetScopeFold(_Branches):
    """`set --scope` that widens a pulled record runs the fold-in
    guard as `pull` does, on the members it adds, so `set` is not a door around it."""

    def setUp(self):
        super().setUp()
        self.branch_with(HOLDER, 'kitlib/x/a.py')
        self.branch_with(BRANCH, 'docs/mine.md', existing=True)
        self.seed(held('HOLD', HOLDER, 'docs/held.md'), held('MINE', BRANCH, 'docs/mine.md'))

    def test_widening_into_another_branchs_work_is_refused(self):
        with self.assertRaises(Refusal) as cm:
            self.verb('set', 'MINE', [('scope', ['docs/mine.md', 'kitlib/x/**'])])
        msg = str(cm.exception)
        self.assertIn('kitlib/x/** vs kitlib/x/a.py (changed on kit/holder)', msg)
        self.assertIn('`set-widen-collision`', msg)
        self.assertNotIn('--ruling', msg)
        self.assertEqual(self.get('MINE').all('scope'), ['docs/mine.md'])

    def test_widening_into_another_branchs_work_the_role_may_take_is_written(self):
        change = self.verb('set', 'MINE', [('scope', ['docs/mine.md', 'kitlib/x/**'])],
                           may_collide=True)
        self.assertEqual(self.get('MINE').all('scope'), ['docs/mine.md', 'kitlib/x/**'])
        self.assertEqual(change.rows, ['set-widen-collision'])
        self.assertTrue(change.subject.endswith(policy.COLLISION_MARK), change.subject)
        self.assertIn('  folds into the work of kit/holder: kitlib/x/** vs kitlib/x/a.py '
                      '(changed on kit/holder)', change.lines)

    def test_widening_into_free_space_is_free(self):
        change = self.verb('set', 'MINE', [('scope', ['docs/mine.md', 'kitlib/free/**'])])
        self.assertEqual(self.get('MINE').all('scope'), ['docs/mine.md', 'kitlib/free/**'])
        self.assertFalse(getattr(change, 'rows', None))

    def test_the_records_own_branch_diff_never_counts(self):
        self.branch_with(BRANCH, 'kitlib/own/b.py', existing=True)
        self.verb('set', 'MINE', [('scope', ['docs/mine.md', 'kitlib/own/**'])])
        self.assertEqual(self.get('MINE').all('scope'), ['docs/mine.md', 'kitlib/own/**'])

    def test_an_open_record_is_not_checked(self):
        self.seed(priced('FOO', scope='docs/foo.md'))
        self.verb('set', 'FOO', [('scope', ['kitlib/x/**'])])
        self.assertEqual(self.get('FOO').all('scope'), ['kitlib/x/**'])


class TakeoverFold(_Branches):
    """The dead-pull takeover is the same door: one that folds into a live branch's work is the
    `pull-collision` row, and takes no `--reason`, as a colliding takeover does."""

    def setUp(self):
        super().setUp()
        self.store.session = 'kit'
        self.branch_with(HOLDER, 'kitlib/x/a.py')
        self.seed(held('HOLD', HOLDER, 'docs/held.md', session='other'),
                  priced('DEAD', state='pulled', pulled_at='2026-10-01', pulled_by='kit/gone',
                         scope='kitlib/x/**', session='kit'))

    def test_a_folding_takeover_is_refused_unless_the_role_may_collide(self):
        with self.assertRaises(Refusal) as cm:
            self.verb('pull', 'DEAD')
        msg = str(cm.exception)
        self.assertIn('kitlib/x/** vs kitlib/x/a.py (changed on kit/holder)', msg)
        self.assertIn('`pull-collision`', msg)
        self.assertEqual(self.get('DEAD').get('pulled_by'), 'kit/gone')

    def test_a_folding_takeover_the_role_may_take_is_written_and_names_the_row(self):
        change = self.verb('pull', 'DEAD', may_collide=True)
        self.assertEqual(self.get('DEAD').get('pulled_by'), BRANCH)
        self.assertEqual(change.rows, ['pull-collision'])

    def test_a_folding_takeover_takes_no_reason(self):
        with self.assertRaises(Usage):
            self.verb('pull', 'DEAD', reason='the owner wants it', may_collide=True)


class PullFoldInThePmDataWorktree(PmDataWorktree):
    """`pull` in the PM data worktree diffs a live branch from the code remote's main, as `add`
    does (test_add_found_on.PmDataWorktree), not from a local main that lags it."""

    def test_a_live_branch_is_diffed_from_the_code_remotes_main(self):
        stale = self.later_on_main()
        self.branch_with(HOLDER, 'kitlib/y/own.py')
        self.lag_main(stale)
        ctx = opener.open_store(self.pm_worktree(), fetch=False, probes=opener.NO_PROBES)
        holding = [parse('HOLD', id='HOLD', state='pulled', pulled_at=TODAY, pulled_by=HOLDER,
                         scope='docs/held.md')]
        hits, notes = plan_narrow._pull_fold_hits(ctx, ['kitlib/x/later.py'], holding, BRANCH)
        self.assertEqual((hits, notes), ([], []))
        hits, _notes = plan_narrow._pull_fold_hits(ctx, ['kitlib/y/**'], holding, BRANCH)
        self.assertEqual(hits, ['kitlib/y/** vs kitlib/y/own.py (changed on kit/holder)'])


if __name__ == '__main__':
    unittest.main()
