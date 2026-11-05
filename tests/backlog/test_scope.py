"""`complement` (kitlib/backlog/scope.py): the sibling members that cover a scope member minus the
paths removed from it, over the tracked tree, or None when it cannot be done."""
import json
import unittest

from kitlib.backlog import check as checkmod
from kitlib.backlog import offer as omod
from kitlib.backlog import plan as pmod
from kitlib.backlog import record as rmod
from kitlib.backlog import scope as smod
from kitlib.backlog import store as sstore
from kitlib.backlog.errors import Refusal

from tests import inproc
from tests.backlog.ownertree import setUpModule, tearDownModule  # noqa: F401 - the owner's tree
from tests.backlog import test_cli
from tests.backlog.helpers import (ADD_WHY, BRANCH, TODAY, StoreCase, kit_json_ref, priced,
                                   record_text)
from tests.backlog.test_offer import snap as osnap


def rparse(one):
    """The parsed record of `{stem: text}`."""
    (stem, text), = one.items()
    return rmod.parse('backlog/%s.md' % stem, text, stem)

TREE = ['kit/a.py', 'kit/b.py', 'kit/sub/c.py', 'kit/sub/d.py', 'kit/deep/e/f.py',
        'kit/deep/e/g.py', 'kit/deep/h.py', 'docs/x.md', 'docs/y.md', 'top.py']


def covered(members, path):
    return any(smod.members_collide(m, path) for m in members)


class Complement(unittest.TestCase):

    def check(self, member, removed, tree=TREE):
        out = smod.complement(member, set(removed), tree)
        self.assertIsNotNone(out)
        for m in out:
            for r in removed:
                self.assertFalse(smod.members_collide(m, r), (m, r))
        expect = set(p for p in tree if smod.members_collide(member, p)) - set(removed)
        self.assertEqual(set(p for p in tree if covered(out, p)), expect)
        return out

    def test_a_folder_member(self):
        out = self.check('kit', ['kit/a.py'])
        self.assertEqual(out, ['kit/b.py', 'kit/deep/**', 'kit/sub/**'])

    def test_a_glob_member(self):
        out = self.check('kit/**', ['kit/sub/c.py'])
        self.assertEqual(out, ['kit/a.py', 'kit/b.py', 'kit/deep/**', 'kit/sub/d.py'])

    def test_nested_removal_descends_level_by_level(self):
        out = self.check('kit/**', ['kit/deep/e/f.py'])
        self.assertEqual(out, ['kit/a.py', 'kit/b.py', 'kit/deep/e/g.py', 'kit/deep/h.py',
                               'kit/sub/**'])

    def test_several_removed_paths(self):
        self.check('kit/**', ['kit/a.py', 'kit/deep/e/g.py'])

    def test_over_the_cap_is_none(self):
        tree = ['big/f%02d.py' % i for i in range(40)]
        self.assertIsNone(smod.complement('big/**', {'big/f00.py'}, tree))
        self.assertIsNotNone(smod.complement('big/**', {'big/f00.py'}, tree[:31]))
        self.assertIsNone(smod.complement('big/**', {'big/f00.py'}, tree[:32]))

    def test_a_file_member_holding_a_removed_path_is_none(self):
        self.assertIsNone(smod.complement('docs/x.md', {'docs/x.md'}, TREE))
        self.assertIsNone(smod.complement('kit/a.py', {'kit/a.py'}, TREE))

    def test_a_file_member_without_a_removed_path_is_unchanged(self):
        self.assertEqual(smod.complement('docs/x.md', {'kit/a.py'}, TREE), ['docs/x.md'])

    def test_nothing_removed_leaves_the_member(self):
        self.assertEqual(smod.complement('kit/**', set(), TREE), ['kit/**'])

    def test_a_removed_path_outside_the_member_leaves_it(self):
        self.assertEqual(smod.complement('kit/**', {'docs/x.md'}, TREE), ['kit/**'])
        self.assertEqual(smod.complement('kit/sub', {'kit/a.py'}, TREE), ['kit/sub'])

    def test_none_is_unchanged(self):
        self.assertEqual(smod.complement('none', {'kit/a.py'}, TREE), ['none'])

    def test_odd_tracked_names_do_not_raise(self):
        tree = TREE + ['pages/[id].js', 'pages/a,b.txt', 'kit/[id].js', 'kit/a,b.txt']
        out = smod.complement('kit/**', {'kit/a.py'}, tree)
        self.assertEqual(out, ['kit/[id].js', 'kit/a,b.txt', 'kit/b.py', 'kit/deep/**',
                               'kit/sub/**'])
        self.assertEqual(smod.complement('kit/**', {'kit/[id].js'}, tree)[:1], ['kit/a,b.txt'])

    def test_malformed_input_raises(self):
        for member, removed in (('kit/*.py', {'kit/a.py'}), ('kit/**', {'kit\\a.py'}),
                                ('kit/**', {'kit/sub/**'}), ('kit/**', {''})):
            with self.subTest(member=member, removed=removed):
                with self.assertRaises(smod.UndecidableScope):
                    smod.complement(member, removed, TREE)

    def test_no_tracked_data_for_the_member_is_none(self):
        self.assertIsNone(smod.complement('kit/**', {'kit/a.py'}, []))
        self.assertIsNone(smod.complement('Kit/**', {'kit/a.py'}, TREE))
        self.assertIsNone(smod.complement('nowhere/**', {'nowhere/a.py'}, TREE))

    def test_removing_every_file_leaves_nothing(self):
        self.assertEqual(smod.complement('docs/**', {'docs/x.md', 'docs/y.md'}, TREE), [])


class InsideBareFolder(unittest.TestCase):
    """`inside_bare_folder`: a bare member (no `/**`) strictly above the other, which narrowing
    reads as a folder; `strictly_inside` (the commit side's) stays the glob's alone."""

    def test_a_member_below_a_bare_folder(self):
        self.assertTrue(smod.inside_bare_folder('kit/sub/c.py', 'kit/sub'))
        self.assertTrue(smod.inside_bare_folder('kit/sub/**', 'kit'))
        self.assertTrue(smod.inside_bare_folder('Kit/Sub/c.py', 'kit/sub'))
        self.assertFalse(smod.strictly_inside('kit/sub/c.py', 'kit/sub'))

    def test_not_strictly_below_or_a_glob_outer_is_false(self):
        for inner, outer in (('kit/sub', 'kit/sub'), ('kit', 'kit/sub'), ('kit/a.py', 'kit/sub'),
                             ('kit/sub/c.py', 'kit/sub/**'), ('kit/subx/c.py', 'kit/sub')):
            with self.subTest(inner=inner, outer=outer):
                self.assertFalse(smod.inside_bare_folder(inner, outer))

    def test_an_illegal_member_raises(self):
        with self.assertRaises(smod.UndecidableScope):
            smod.inside_bare_folder('kit/*.py', 'kit')


class AddBroadScope(StoreCase):
    """`add` warns, never refuses, on a scope broader than the change needs."""

    def add(self, summary, scopes, body=None):
        return self.verb('add', 'NEW', summary, scopes, found_on='none',
                         why_not_folded='it needs the owner to choose', body=body)

    def warnings(self, change):
        return [l for l in change.lines if l.startswith('WARNING:')]

    def test_a_top_level_glob_warns_and_still_adds(self):
        change = self.add('tidy the module', ['kitlib/**'])
        self.assertEqual(len(self.warnings(change)), 1, change.lines)
        self.assertIn('kitlib/**', self.warnings(change)[0])
        self.assertIsNotNone(self.get('NEW'))

    def test_a_broad_scope_with_paths_named_in_the_summary_suggests_them(self):
        change = self.add('fix kitlib/backlog/plan.py warning', ['kitlib/backlog/**'])
        w = self.warnings(change)
        self.assertEqual(len(w), 1, change.lines)
        self.assertIn('kitlib/backlog/plan.py', w[0])
        self.assertIsNotNone(self.get('NEW'))

    def test_paths_named_in_the_body_count(self):
        change = self.add('fix the warning', ['kitlib/backlog/**'],
                          body='The defect is in kitlib/backlog/scope.py.\n')
        self.assertIn('kitlib/backlog/scope.py', self.warnings(change)[0])

    def test_a_dotted_path_in_the_body_does_not_fail_the_add(self):
        change = self.add('fix the warning', ['kitlib/backlog/**'],
                          body='See ./kitlib/backlog/a.py and ../docs/x.md.\n')
        self.assertEqual(self.warnings(change), [])
        self.assertIsNotNone(self.get('NEW'))

    def test_a_literal_md_member_does_not_warn(self):
        change = self.add('fix docs/backlog.md', ['docs/backlog.md'])
        self.assertEqual(self.warnings(change), [])

    def test_a_narrow_scope_does_not_warn(self):
        change = self.add('fix kitlib/backlog/plan.py', ['kitlib/backlog/plan.py'])
        self.assertEqual(self.warnings(change), [])

    def test_a_broad_nested_scope_naming_no_path_does_not_warn(self):
        change = self.add('tidy the backlog', ['kitlib/backlog/**'])
        self.assertEqual(self.warnings(change), [])

    def test_a_path_outside_the_broad_scope_does_not_warn(self):
        change = self.add('fix docs/backlog.md', ['kitlib/backlog/**'])
        self.assertEqual(self.warnings(change), [])


# --- shared paths (`.kit.json` "backlog".shared_paths; docs/backlog.md "Shared paths") -----------

LIST = 'docs/layer.md'          # a list file many records add a line to
SHARED = (LIST,)


class SharedPathPairs(unittest.TestCase):
    """scope.members_collide and friends with `shared`: a pair where either member IS a shared
    path does not collide; the rest of each scope collides as before."""

    def test_a_pair_sharing_only_a_listed_path_does_not_collide(self):
        a, b = [LIST, 'kit/a.py'], [LIST, 'kit/b.py']
        self.assertTrue(smod.scopes_collide(a, b))                  # as before, without the list
        self.assertFalse(smod.scopes_collide(a, b, SHARED))
        self.assertEqual(smod.colliding_pairs(a, b, SHARED), [])
        self.assertEqual(smod.shared_hits(a, b, SHARED), [LIST])

    def test_a_pair_also_sharing_an_unlisted_file_collides(self):
        a, b = [LIST, 'kit/a.py'], [LIST, 'kit/a.py']
        self.assertTrue(smod.scopes_collide(a, b, SHARED))
        self.assertEqual(smod.colliding_pairs(a, b, SHARED), [('kit/a.py', 'kit/a.py')])

    def test_a_glob_against_a_listed_file_under_it_is_exempt(self):
        for a, b in (('docs/**', LIST), (LIST, 'docs/**'), ('docs/**', 'Docs/Layer.md')):
            self.assertTrue(smod.members_collide(a, b), (a, b))
            self.assertFalse(smod.members_collide(a, b, SHARED), (a, b))
        # The rest of the glob still collides.
        self.assertTrue(smod.members_collide('docs/**', 'docs/other.md', SHARED))
        self.assertTrue(smod.members_collide('docs/**', 'docs/**', SHARED))

    def test_a_glob_or_a_folder_is_no_shared_path(self):
        self.assertIsNone(smod.shared_path_error(LIST))
        for bad, word in (('docs/**', 'glob'), ('docs/*.md', 'glob'), ('docs/', 'folder'),
                          ('none', 'none'), ('/abs.md', '')):
            why = smod.shared_path_error(bad)
            self.assertIsNotNone(why, bad)
            self.assertIn(word, why, bad)
        # A glob in the set exempts nothing.
        self.assertTrue(smod.members_collide('docs/**', 'docs/a.md', ('docs/**',)))

    def test_the_set_is_read_only_for_a_pair_that_collides_without_it(self):
        def boom():
            raise AssertionError('read the shared paths for a pair that does not collide')
        lazy = pmod.SharedPaths(boom)
        self.assertFalse(smod.members_collide('kit/a.py', 'kit/b.py', lazy))
        self.assertEqual(smod.colliding_pairs(['kit/**'], ['docs/**'], lazy), [])
        self.assertEqual(smod.shared_hits(['kit/**'], ['docs/**'], lazy), [])
        read = pmod.SharedPaths(lambda: [LIST])
        self.assertFalse(smod.members_collide('docs/**', LIST, read))


def pulled(rid, scope, by='kit/holder'):
    return priced(rid, state='pulled', pulled_at=TODAY, pulled_by=by, scope=scope)


class SharedPathCallers(unittest.TestCase):
    """Every caller of the collision check agrees: the offer, `pull`'s live hits, `check`."""

    def test_the_offer_takes_a_record_that_shares_only_a_listed_path_and_names_it(self):
        s = osnap(pulled('HOLD', [LIST, 'area/hold/**']), priced('AAA', scope='docs/**'),
                  priced('BBB', scope=[LIST, 'area/hold/x.py']))
        holding = [s.get('HOLD')]
        (offers, passed), _bad, _r = omod.next_offer(s, holding)
        self.assertEqual(offers, [])
        self.assertEqual(sorted(p.rec.id for p in passed), ['AAA', 'BBB'])
        (offers, passed), _bad, _r = omod.next_offer(s, holding, shared=SHARED)
        self.assertEqual([o.rec.id for o in offers], ['AAA'])
        self.assertEqual(offers[0].shares, [(LIST, 'HOLD')])
        self.assertEqual(omod.shares_line(LIST, 'HOLD'), 'shares %s with HOLD (shared path)' % LIST)
        self.assertEqual([p.rec.id for p in passed], ['BBB'])
        self.assertIn('scope area/hold/x.py collides with area/hold/**', passed[0].why)

    def test_pull_live_hits_exempt_the_listed_path_and_report_the_share(self):
        held = rparse(pulled('HOLD', LIST))
        self.assertEqual(len(pmod.live_hits(['docs/**'], [held])), 1)
        self.assertEqual(pmod.live_hits(['docs/**'], [held], shared=SHARED), [])
        self.assertEqual(pmod.live_shares(['docs/**'], [held], shared=SHARED),
                         ['shares %s with HOLD (shared path)' % LIST])
        self.assertEqual(len(pmod.live_hits(['docs/**', 'area/hold/**'],
                                            [rparse(pulled('HOLD', [LIST, 'area/hold/a.py']))],
                                            shared=SHARED)), 1)

    def test_check_refuses_a_glob_or_a_folder_and_exempts_a_good_entry(self):
        s = osnap(pulled('ONE', [LIST, 'kit/a.py'], by='kit/one'),
                  pulled('TWO', [LIST, 'kit/b.py'], by='kit/two'))
        by = {r.name: r for r in checkmod.check_store(s)}
        self.assertEqual(len(by[checkmod.PULL_RECORDS].warnings), 1)
        self.assertNotIn(checkmod.SHARED_PATHS, by)
        entries = [(LIST, None), ('docs/**', smod.shared_path_error('docs/**')),
                   ('docs', '`docs` is a folder: a shared path is one literal file')]
        by = {r.name: r for r in checkmod.check_store(s, shared=entries)}
        self.assertEqual(by[checkmod.PULL_RECORDS].warnings, [])
        res = by[checkmod.SHARED_PATHS]
        self.assertTrue(res.red)
        self.assertEqual([f.line for f in res.findings], [2, 3])
        self.assertIn('glob', res.findings[0].message)
        self.assertIn('folder', res.findings[1].message)

    def test_split_all_narrows_a_broad_scope_off_the_live_pull_but_keeps_the_listed_path(self):
        tree = TREE + [LIST]
        held = osnap(pulled('HOLD', [LIST, 'docs/x.md'])).get('HOLD')
        for plan, protected in ((False, ['docs/x.md']), (True, [LIST, 'docs/x.md'])):
            with self.subTest(plan=plan):
                out, why = omod.split_all(['docs/**'], [(held, protected, plan)], tree)
                self.assertIsNone(why)
                self.assertNotIn(LIST, out.keep)        # without the list, it is given up
                out, why = omod.split_all(['docs/**'], [(held, protected, plan)], tree,
                                          shared=SHARED)
                self.assertIsNone(why)
                self.assertEqual(out.keep, [LIST, 'docs/y.md'])     # narrows off docs/x.md only
                self.assertEqual(out.c_changes,
                                 [('docs/**', [LIST, 'docs/x.md'], [LIST, 'docs/y.md'])])
                self.assertEqual(out.parts[0].other_keep, [LIST, 'docs/x.md'])


class SharedPathWrites(StoreCase):
    """The writes on a ref store: `set --scope` widening a pulled record onto a listed file the
    live pull holds (and its branch changed) is free; `add --found-on` that branch is still
    refused, as the add-time fold-in guard exempts no shared path."""

    def setUp(self):
        super().setUp()
        self.repo.git('checkout', '-q', '-b', 'kit/holder')
        self.repo.write(LIST, 'one line\n')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-q', '-m', 'work on kit/holder')
        self.repo.git('checkout', '-q', '-f', 'main')
        self.seed(pulled('HOLD', LIST), pulled('MINE', 'kit/mine.py', by=BRANCH))

    def share(self):
        self.ctx = pmod.Context(self.store, self.repo.root, branch=BRANCH, today=TODAY,
                                shared=lambda: [(LIST, None)])

    def test_set_scope_widening_onto_a_listed_file_is_free(self):
        with self.assertRaises(Refusal) as cm:
            self.verb('set', 'MINE', [('scope', ['kit/mine.py', LIST])])
        self.assertIn('widens its scope into a live pull', str(cm.exception))
        self.share()
        self.verb('set', 'MINE', [('scope', ['kit/mine.py', LIST])])
        self.assertEqual(self.get('MINE').all('scope'), ['kit/mine.py', LIST])

    def test_add_found_on_a_branch_that_changed_a_listed_file_is_refused(self):
        self.share()
        for scope in ([LIST], ['docs/**']):
            with self.subTest(scope=scope):
                with self.assertRaises(Refusal) as cm:
                    self.verb('add', 'NEW', 'a defect found while working', scope,
                              found_on='kit/holder', why_not_folded=ADD_WHY)
                msg = str(cm.exception)
                self.assertIn('its scope overlaps the work of kit/holder', msg)
                self.assertIn('(changed on kit/holder)', msg)
                self.assertIn('(scope of HOLD, pulled by kit/holder)', msg)
                self.assertIsNone(self.get('NEW'))


class SharedPathsCli(unittest.TestCase):
    """Through tools/backlog.py on a ref store: `next` and `pull` print the share, `check`
    refuses a glob or a folder in the list."""
    setUp_cli = test_cli.Cli.setUp
    run_tool = test_cli.Cli.run_tool
    ok = test_cli.Cli.ok

    def setUp(self):
        self.setUp_cli()
        backend = sstore.RefBackend(self.repo.root, 'origin', sstore.STORE_REF, sstore.STORE_REF)
        snap = sstore.Store(backend, rmod.Rules()).load()
        backend.publish(snap, {
            'HOLD': record_text(id='HOLD', priority='2', priority_by='agent', state='pulled',
                                pulled_at=TODAY, pulled_by='tools/hold', scope=LIST),
            'AAA': record_text(id='AAA', priority='2', priority_by='agent', scope='docs/**'),
        }, 'seed\n')
        self.repo.git('branch', 'tools/hold')
        self.repo.git('branch', 'tools/take')

    def test_next_and_pull_print_the_share(self):
        # The holder's branch has changed the listed file, so the fold checks of `next` and
        # `pull` (_offer_folds, _pull_fold_hits) see it too, and exempt it as the scope check does.
        self.repo.git('checkout', '-q', 'tools/hold')
        self.repo.write(LIST, 'one line\n')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-q', '-m', 'work on tools/hold')
        self.repo.git('checkout', '-q', '-f', 'main')
        self.assertIn('passed over AAA', self.ok('next'))
        kit_json_ref(self.repo, {'backlog': {'shared_paths': [LIST]}})
        out = self.ok('next')
        self.assertNotIn('passed over AAA', out)
        self.assertIn('    shares %s with HOLD (shared path)' % LIST, out)
        rc, out = self.run_tool('--as', 'tools/take', 'pull', 'AAA')
        self.assertEqual(rc, 0, out)
        self.assertIn('shares %s with HOLD (shared path)' % LIST, out)
        self.assertIn('pulled_by: tools/take', self.ok('show', 'AAA'))

    def test_check_reports_an_unreadable_config_as_one_fail_and_the_rest(self):
        wt = self.repo.worktree('wt', branch='tools/wt')
        # The main checkout's file now fails to load; the worktree's own is fine.
        self.repo.write('.kit.json', json.dumps({'backlog': {'store': 'ref', 'cap': 0}}) + '\n')
        rc, out, err = inproc.run_main(test_cli.BACKLOG.main, ['-C', wt, 'check'], self.env, wt)
        out += err
        self.assertEqual(rc, 1, out)
        self.assertNotIn('ERROR', out)
        self.assertIn('blocked_on', out)                # the other results still print
        fails = [l for l in out.splitlines() if l.startswith('    FAIL')]
        self.assertEqual(len(fails), 1, out)
        self.assertIn('.kit.json "backlog".shared_paths:0 cannot read: ', fails[0])
        self.assertTrue(out.rstrip().endswith('RED'), out)

    def test_check_refuses_a_glob_or_a_folder(self):
        self.repo.write('docs/layer.md', 'one line\n')
        kit_json_ref(self.repo, {'backlog': {'shared_paths': ['docs/**', 'docs']}})
        rc, out = self.run_tool('check')
        self.assertEqual(rc, 1, out)
        self.assertIn('`docs/**` is a glob', out)
        self.assertIn('`docs` is a folder', out)
        self.assertIn('passed over AAA', self.ok('next'))     # neither exempts anything
        kit_json_ref(self.repo, {'backlog': {'shared_paths': [LIST]}})
        rc, out = self.run_tool('check')
        self.assertEqual(rc, 0, out)
        self.assertIn('shared paths', out)


if __name__ == '__main__':
    unittest.main()
