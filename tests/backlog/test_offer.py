"""The partition, the rank order and the offer (Partition, Rank and NextOffer cases,
`requires:` among them), on snapshots built in memory."""
import unittest
from unittest import mock

from kitlib.backlog import offer as omod
from kitlib.backlog import plan as pmod
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod

from tests.backlog import test_cli
from tests.backlog.helpers import TODAY, StoreCase, priced, rec, record_text


def snap(*records, rules=None):
    texts = {}
    for r in records:
        texts.update(r)
    rules = rules or rmod.Rules('upper', ('L0', 'L1'))
    parsed = [rmod.parse('backlog/%s.md' % stem, text, stem) for stem, text in texts.items()]
    return smod.Snapshot(parsed, 'a' * 40, 'test', rules)


def offer_ids(s, holding=(), **kw):
    (offers, passed), refusals, _ranks = omod.next_offer(s, list(holding), **kw)
    assert not refusals, refusals
    return [o.rec.id for o in offers], {p.rec.id: p.why for p in passed}


class Partition(unittest.TestCase):

    def test_every_record_lands_in_exactly_one_bucket(self):
        s = snap(priced('AAA'), rec('BBB'), rec('CCC', state='blocked', blocked_on='live-client'),
                 rec('DDD', state='parked'),
                 priced('FFF', state='pulled', pulled_at='2026-10-01', pulled_by='b'),
                 rec('GGG', state='weird'), {'HHH': 'not a record\n'})
        b = omod.Banner(s)
        got = {k: [r.stem for r in v] for k, v in b.buckets.items() if v}
        self.assertEqual(got, {omod.PULLABLE: ['AAA'], omod.UNPRIORITISED_OPEN: ['BBB'],
                               omod.HELD: ['CCC'], omod.PARKED: ['DDD'],
                               omod.IN_FLIGHT: ['FFF'],
                               omod.UNCLASSIFIED: ['GGG'], omod.NOT_READ: ['HHH']})
        line = b.lines()[0]
        self.assertIn('7 records', line)
        self.assertIn('1 pullable', line)
        self.assertIn('1 parked', line)

    def test_held_groups_count_each_record_once(self):
        s = snap(rec('AAA', state='blocked', blocked_on=['owner', 'live-client'], owner_ask='q?'),
                 rec('BBB', state='blocked', blocked_on='live-client'))
        groups, multi = omod.Banner(s).held_groups()
        self.assertEqual(sum(n for _, n in groups), 2)
        self.assertEqual(multi, 1)

    def test_the_queue_warning(self):
        self.assertIn('QUEUE DEPTH: 1 pullable', omod.Banner(snap(priced('AAA'))).queue_warning())
        many = [priced('R%02d' % i, priority='1' if i < 3 else '4') for i in range(10)]
        self.assertIsNone(omod.Banner(snap(*many)).queue_warning())


class Rank(unittest.TestCase):

    def test_preempt_then_pin_then_priority_then_effort_then_id(self):
        s = snap(priced('AAA', priority='3'), priced('BBB', priority='1', effort='l'),
                 priced('CCC', priority='1', effort='s'), priced('DDD', priority='5', pin='1'),
                 priced('EEE', priority='5', preempt='because reasons', preempt_by='b',
                        preempt_at='2026-10-01'),
                 priced('FFF', priority='1', effort='s'))
        ranks = omod.ranked(s)
        self.assertEqual(sorted(ranks, key=ranks.get), ['EEE', 'DDD', 'CCC', 'FFF', 'BBB', 'AAA'])

    def test_unpriced_records_have_no_rank(self):
        s = snap(rec('AAA'), priced('BBB'))
        self.assertEqual(omod.ranked(s), {'BBB': 1})
        self.assertEqual(omod.rank_label(s.get('AAA'), omod.ranked(s)), '-')


class NextOffer(unittest.TestCase):

    def test_the_offer_is_rank_order_up_to_the_cap(self):
        s = snap(*[priced('R%d' % i, priority=str(1 + i % 5)) for i in range(8)])
        ids, passed = offer_ids(s, cap=3)
        self.assertEqual(ids, ['R0', 'R5', 'R1'])
        self.assertEqual(set(passed), {'R2', 'R3', 'R4', 'R6', 'R7'})
        self.assertTrue(all('cap' in why for why in passed.values()))

    def test_only_pullable_records_are_offered(self):
        s = snap(priced('AAA'), rec('BBB'), rec('CCC', state='parked', priority='1',
                                                 priority_by='agent'),
                 priced('DDD', state='blocked', blocked_on='live-client'))
        self.assertEqual(offer_ids(s)[0], ['AAA'])

    def test_a_live_pull_occupies_its_scope(self):
        s = snap(priced('AAA', scope='link/**'), priced('BBB', scope='worker/**'),
                 priced('HOLD', state='pulled', pulled_at='2026-10-01', pulled_by='b',
                        scope='link/src/X.java'))
        ids, passed = offer_ids(s, holding=[s.get('HOLD')])
        self.assertEqual(ids, ['BBB'])
        self.assertIn('collides', passed['AAA'])

    def test_offers_do_not_collide_with_each_other(self):
        s = snap(priced('AAA', priority='1', scope='link/**'),
                 priced('BBB', priority='2', scope='link/x/**'),
                 priced('CCC', priority='3', scope='worker/**'))
        ids, passed = offer_ids(s)
        self.assertEqual(ids, ['AAA', 'CCC'])
        self.assertIn('collides', passed['BBB'])

    def test_an_undecidable_scope_is_a_collision_never_a_clearance(self):
        s = snap(priced('AAA', scope='*.java'), priced('BBB', scope='worker/**'))
        ids, passed = offer_ids(s)
        self.assertEqual(ids, ['BBB'])
        self.assertIn('undecidable', passed['AAA'])

    def test_an_undecidable_live_pull_refuses_the_offer(self):
        s = snap(priced('HOLD', state='pulled', pulled_at='2026-10-01', pulled_by='b',
                        scope='*.java'), priced('AAA'))
        result, refusals, _ = omod.next_offer(s, [s.get('HOLD')])
        self.assertIsNone(result)
        self.assertTrue(refusals)

    def test_session_filter_and_another_sessions_scope(self):
        s = snap(priced('AAA', priority='1', scope='link/**', session='gamma'),
                 priced('BBB', priority='2', scope='link/x/**', session='kit'),
                 priced('CCC', priority='3', scope='worker/**', session='kit'))
        ids, passed = offer_ids(s, session='kit')
        self.assertEqual(ids, ['CCC'])
        self.assertIn('AAA', passed['BBB'])
        self.assertNotIn('AAA', passed)
        self.assertIn('session `gamma`', omod.not_offered_why(s.get('AAA'), [], 'kit'))

    def test_a_seed_goes_first_and_takes_its_scope(self):
        s = snap(priced('AAA', priority='1', scope='link/**'),
                 priced('BBB', priority='5', scope='link/x/**'))
        (offers, _), _, _ = omod.next_offer(s, [], seeds=['BBB'])
        self.assertEqual([(o.rec.id, o.seeded) for o in offers], [('BBB', True)])

    def test_a_seed_that_collides_with_a_live_pull_is_passed_over(self):
        s = snap(priced('BBB', priority='5', scope='link/x/**'),
                 priced('HOLD', state='pulled', pulled_at='2026-10-01', pulled_by='b',
                        scope='link/**'))
        (offers, passed), _, _ = omod.next_offer(s, [s.get('HOLD')], seeds=['BBB'])
        self.assertEqual([o.rec.id for o in offers], [])
        self.assertIn('already taken by HOLD', {p.rec.id: p.why for p in passed}['BBB'])

    def test_an_unmet_requires_is_passed_over(self):
        s = snap(priced('AAA', priority='1', requires='item:BBB'), priced('BBB', priority='2'))
        ids, passed = offer_ids(s, unmet=lambda r: [('item:BBB', 'open')] if r.id == 'AAA' else [])
        self.assertEqual(ids, ['BBB'])
        self.assertIn('requires item:BBB', passed['AAA'])

    def test_why_a_record_is_not_offered(self):
        s = snap(rec('AAA', state='parked'), rec('BBB'),
                 rec('CCC', state='blocked', blocked_on='live-client'))
        self.assertEqual(omod.not_offered_why(s.get('AAA'), []), 'it is parked')
        self.assertIn('unprioritised', omod.not_offered_why(s.get('BBB'), []))
        self.assertIn('live-client', omod.not_offered_why(s.get('CCC'), []))


class Requires(unittest.TestCase):

    def test_item_met_only_when_closed_in_history(self):
        s = snap(priced('AAA', requires=['item:BBB', 'item:CCC', 'item:DDD', 'just a note']),
                 priced('BBB'))
        hist = {'CCC': 'abc1234 closes CCC: done and dusted', 'DDD': 'abc1234 retires DDD: no'}.get
        got = omod.unmet_requires(s.get('AAA'), s, hist, '.', 'main')
        self.assertEqual([m for m, _ in got], ['item:BBB', 'item:DDD'])


class CollisionHolder(unittest.TestCase):
    """A collision names who holds the scope; a pulled record's narrowed `scope:` lines are what
    it holds."""

    def hold(self, scope):
        return priced('HOLD', state='pulled', pulled_at='2026-10-01', pulled_by='kit/holder',
                      scope=scope)

    def test_a_collision_with_a_live_pull_names_the_holder_and_its_branch(self):
        s = snap(priced('AAA', scope='kitlib/backlog/**'), self.hold('kitlib/backlog/offer.py'))
        _ids, passed = offer_ids(s, holding=[s.get('HOLD')])
        self.assertEqual(passed['AAA'], 'scope kitlib/backlog/** collides with '
                         'kitlib/backlog/offer.py already taken by HOLD (in flight on kit/holder)')

    def test_a_narrowed_live_pull_clears_a_disjoint_record(self):
        wide = snap(priced('AAA', scope='kitlib/backlog/scope.py'), self.hold('kitlib/backlog/**'))
        ids, passed = offer_ids(wide, holding=[wide.get('HOLD')])
        self.assertEqual(ids, [])
        self.assertIn('already taken by HOLD (in flight on kit/holder)', passed['AAA'])
        narrowed = snap(priced('AAA', scope='kitlib/backlog/scope.py'),
                        self.hold('kitlib/backlog/offer.py'))
        ids, passed = offer_ids(narrowed, holding=[narrowed.get('HOLD')])
        self.assertEqual(ids, ['AAA'])
        self.assertNotIn('AAA', passed)

    def test_another_sessions_holder_is_still_named_by_session(self):
        s = snap(priced('AAA', priority='1', scope='binding/**', session='gamma'),
                 priced('BBB', priority='2', scope='binding/x/**', session='kit'))
        _ids, passed = offer_ids(s, session='kit')
        self.assertEqual(passed['BBB'], 'scope binding/x/** collides with binding/** already '
                         'taken by AAA (session gamma)')

    def test_a_record_offered_above_in_the_same_pass_is_named(self):
        s = snap(priced('AAA', priority='1', scope='binding/**'),
                 priced('BBB', priority='2', scope='binding/x/**'),
                 priced('CCC', priority='3', scope='worker/**'))
        ids, passed = offer_ids(s)
        self.assertEqual(ids, ['AAA', 'CCC'])
        self.assertEqual(passed['BBB'], 'scope binding/x/** collides with binding/** already '
                         'taken by AAA (offered above)')

    def test_a_seed_offered_first_is_named_as_offered_above(self):
        s = snap(priced('AAA', priority='1', scope='binding/**'),
                 priced('BBB', priority='5', scope='binding/x/**'))
        ids, passed = offer_ids(s, seeds=['BBB'])
        self.assertEqual(ids, ['BBB'])
        self.assertEqual(passed['AAA'], 'scope binding/** collides with binding/x/** already '
                         'taken by BBB (offered above)')

    def test_a_pull_with_no_branch_held_by_a_failed_probe_is_named_with_a_question_mark(self):
        from unittest import mock
        s = snap(priced('AAA', scope='kitlib/backlog/**'),
                 priced('HOLD', state='pulled', pulled_at='2026-10-01',
                        scope='kitlib/backlog/offer.py'))
        with mock.patch.object(omod, 'live_branches', return_value=(None, 'git failed')):
            holding, dead, why = omod.in_flight_split(s, '.', 'origin')
        self.assertEqual(([r.id for r in holding], dead, why), (['HOLD'], [], 'git failed'))
        _ids, passed = offer_ids(s, holding=holding)
        self.assertEqual(passed['AAA'], 'scope kitlib/backlog/** collides with '
                         'kitlib/backlog/offer.py already taken by HOLD (in flight on ?)')


class BlockerSummary(unittest.TestCase):
    """`blocker_summary` ranks the live pulls and other sessions' records by how many passed-over
    records they block."""

    def passed_for(self, s, holding=(), **kw):
        (_offers, passed), _refusals, _ranks = omod.next_offer(s, list(holding), **kw)
        return passed

    def test_the_pull_blocking_most_records_comes_first(self):
        hold_a = priced('HOLDA', state='pulled', pulled_at='2026-10-01', pulled_by='kit/a',
                        scope='a/**')
        hold_b = priced('HOLDB', state='pulled', pulled_at='2026-10-01', pulled_by='kit/b',
                        scope='b/**')
        s = snap(hold_a, hold_b, priced('R1', priority='1', scope='b/x'),
                 priced('R2', priority='2', scope='b/y'), priced('R3', priority='3', scope='a/x'))
        passed = self.passed_for(s, holding=[s.get('HOLDA'), s.get('HOLDB')])
        self.assertEqual(omod.blocker_summary(passed),
                         ['blocks 2: kit/b (HOLDB)', 'blocks 1: kit/a (HOLDA)'])

    def test_another_session_counts_and_an_offered_record_does_not(self):
        s = snap(priced('AAA', priority='1', scope='binding/**', session='gamma'),
                 priced('BBB', priority='2', scope='binding/x/**', session='kit'),
                 priced('CCC', priority='3', scope='w/**', session='kit'),
                 priced('DDD', priority='4', scope='w/y', session='kit'))
        passed = self.passed_for(s, session='kit')
        self.assertEqual(omod.blocker_summary(passed), ['blocks 1: session gamma (AAA)'])

    def test_it_keeps_the_top_few_and_is_empty_with_no_collision(self):
        self.assertEqual(omod.blocker_summary([]), [])
        recs = [priced('H%d' % i, state='pulled', pulled_at='2026-10-01', pulled_by='kit/h%d' % i,
                       scope='h%d/**' % i) for i in range(7)]
        recs += [priced('R%d' % i, priority='1', scope='h%d/x' % i) for i in range(7)]
        s = snap(*recs)
        passed = self.passed_for(s, holding=[s.get('H%d' % i) for i in range(7)])
        self.assertEqual(len(omod.blocker_summary(passed)), 5)
        # H0..H6 block 1, 2, 3, 1, 2, 3, 1 records: top five by count, ties by name
        more = [priced('X%d' % i, priority='1', scope=sc) for i, sc in
                enumerate(['h1/y', 'h2/y', 'h2/z', 'h5/y', 'h5/z', 'h6/y'])]
        s = snap(*(recs + more))
        passed = self.passed_for(s, holding=[s.get('H%d' % i) for i in range(7)])
        self.assertEqual(omod.blocker_summary(passed),
                         ['blocks 3: kit/h2 (H2)', 'blocks 3: kit/h5 (H5)', 'blocks 2: kit/h1 (H1)',
                          'blocks 2: kit/h6 (H6)', 'blocks 1: kit/h0 (H0)'])


# --- NARROWS: a record held only by a live pull's protected set (KIT-NEXT-NARROW-AUTO S2) ------

TREE = ['docs/x.md', 'kit/a.py', 'kit/b.py', 'kit/sub/c.py', 'kit/sub/d.py']


def live(rid='HOLD', scope='kit/**', body='', by='kit/b'):
    kw = {'body': body} if body else {}
    return priced(rid, state='pulled', pulled_at='2026-10-01', pulled_by=by, scope=scope, **kw)


class FilesSection(unittest.TestCase):

    def test_the_list_items_of_the_section_are_its_paths(self):
        body = ('## Observed\n- kit/no.py\n\n## Files\nIntro prose.\n- `kit/a.py` - the reason\n'
                '* kit/b.py\n- `kit/c.py` — a note\n- `kit/a.py`\n\n## Next\n- kit/z.py\n')
        self.assertEqual(omod.files_section(body), (['kit/a.py', 'kit/b.py', 'kit/c.py'], None))

    def test_the_section_ends_only_at_a_heading_of_level_one_or_two(self):
        self.assertEqual(omod.files_section('## Files\n- kit/a.py\n# Top\n- kit/b.py\n'),
                         (['kit/a.py'], None))
        self.assertEqual(omod.files_section('## files\n- kit/a.py\n## Next\n- kit/b.py\n'),
                         (['kit/a.py'], None))

    def test_a_heading_inside_a_fence_before_the_section_is_not_one(self):
        body = '```\n## Files\n- kit/no.py\n```\n## Files\n- kit/a.py\n'
        self.assertEqual(omod.files_section(body), (['kit/a.py'], None))
        self.assertEqual(omod.files_section('```\n## Files\n- kit/no.py\n```\n'), ([], None))

    def test_a_subheading_or_a_fence_inside_the_section_cannot_be_read(self):
        for body in ('## Files\n- kit/a.py\n### More\n- kit/b.py\n',
                     '## Files\n- kit/a.py\n```\n# not a heading\n```\n- kit/b.py\n',
                     '## Files\n~~~\n- kit/a.py\n~~~\n'):
            with self.subTest(body=body):
                paths, why = omod.files_section(body)
                self.assertIsNone(paths)
                self.assertTrue(why)

    def test_a_list_item_of_another_shape_cannot_be_read(self):
        # Read as prose, a `+` or numbered item would leave a partial plan.
        for item in ('+ `kit/b.py`', '1. `kit/b.py`', '2) kit/b.py', '+'):
            with self.subTest(item=item):
                paths, why = omod.files_section('## Files\n- kit/a.py\n%s\n' % item)
                self.assertIsNone(paths)
                self.assertIn('another list shape', why)
        self.assertEqual(omod.files_section('## Files\n- kit/a.py\n2026 was the year.\n'),
                         (['kit/a.py'], None))

    def test_an_item_that_is_not_one_path_cannot_be_read(self):
        # Reading `edit` or `kitlib/x/b.py:` as the path would leave the real file unprotected.
        for item in ('- edit `kitlib/x/b.py`', '- kitlib/x/b.py: rewrite', '- kit/a.py the reason',
                     '- `kit/a.py` the reason', '- `kit/a.py` `kit/b.py`', '- `kit/a.py` -'):
            with self.subTest(item=item):
                paths, why = omod.files_section('## Files\n%s\n' % item)
                self.assertIsNone(paths)
                self.assertIn('not one path', why)

    def test_no_section_or_an_empty_one_is_no_plan(self):
        self.assertEqual(omod.files_section('## Observed\n- kit/a.py\n'), ([], None))
        self.assertEqual(omod.files_section('## Files\n\n## Next\n'), ([], None))
        self.assertEqual(omod.files_section(None), ([], None))

    def test_an_item_that_is_not_a_plain_path_cannot_be_read(self):
        for item in ('- kit/**', '- kit\\a.py', '- /abs.py', '- ', '- `kit/*.py`'):
            with self.subTest(item=item):
                paths, why = omod.files_section('## Files\n%s\n' % item)
                self.assertIsNone(paths)
                self.assertTrue(why)


class Split(unittest.TestCase):

    def other(self, scope='kit/**'):
        return snap(live(scope=scope)).get('HOLD')

    def split(self, c, l, protected, plan, tree=TREE):
        return omod.split_all(c, [(self.other(l), protected, plan)], tree)

    def test_no_plan_narrows_the_broad_candidate_off_the_whole_live_member(self):
        out, why = self.split(['kit/**'], 'kit/sub/**', ['kit/sub/c.py'], False)
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/a.py', 'kit/b.py'])
        self.assertEqual(out.parts[0].other_keep, ['kit/sub/**'])
        self.assertEqual(out.c_changes, [('kit/**', ['kit/sub/**'], ['kit/a.py', 'kit/b.py'])])
        self.assertEqual(out.parts[0].l_changes, [])

    def test_no_plan_and_a_candidate_under_the_live_member_stays_held(self):
        out, why = self.split(['kit/sub/d.py'], 'kit/**', ['kit/a.py'], False)
        self.assertIsNone(out)
        self.assertIn('## Files', why)

    def test_a_plan_narrows_both_sides(self):
        out, why = self.split(['kit/**'], 'kit/**', ['kit/a.py', 'kit/sub/c.py'], True)
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/b.py', 'kit/sub/d.py'])
        self.assertEqual(out.parts[0].other_keep, ['kit/a.py', 'kit/sub/c.py'])

    def test_a_plan_takes_only_the_candidates_part_from_a_broader_live_member(self):
        out, _why = self.split(['kit/sub/d.py'], 'kit/**', ['kit/a.py'], True)
        self.assertEqual(out.keep, ['kit/sub/d.py'])
        self.assertEqual(out.parts[0].other_keep, ['kit/a.py', 'kit/b.py', 'kit/sub/c.py'])
        self.assertEqual(out.c_changes, [])

    def test_a_new_file_the_live_pull_adds_stays_its_own(self):
        out, _why = self.split(['kit/**'], 'kit/**', ['kit/a.py', 'kit/new.py'], True)
        self.assertEqual(out.keep, ['kit/b.py', 'kit/sub/**'])
        self.assertEqual(out.parts[0].other_keep, ['kit/a.py', 'kit/new.py'])

    def test_no_plan_narrows_a_bare_folder_candidate_off_a_file_inside_it(self):
        # ERRAND-VALUES-DOWN's shape: a bare folder (no `/**`) hit by one file of a live pull.
        out, why = self.split(['kit/sub'], 'kit/sub/c.py', ['kit/sub/c.py'], False)
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/sub/d.py'])
        self.assertEqual(out.c_changes, [('kit/sub', ['kit/sub/c.py'], ['kit/sub/d.py'])])
        self.assertEqual(out.parts[0].l_changes, [])

    def split_all(self, c, parts, tree=TREE):
        s = snap(*[live(rid, scope=scope, by='kit/%s' % rid.lower()) for rid, scope, _p, _f
                   in parts])
        return omod.split_all(c, [(s.get(rid), prot, plan) for rid, _s, prot, plan in parts],
                              tree)

    def test_two_live_pulls_on_different_files_narrow_the_candidate_clear_of_both(self):
        # MOVEMENT-S5's shape: one broad member, two live pulls each on its own file.
        out, why = self.split_all(['kit/**'], [('H1', 'kit/a.py', ['kit/a.py'], False),
                                               ('H2', 'kit/sub/c.py', ['kit/sub/c.py'], True)])
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/b.py', 'kit/sub/d.py'])
        self.assertEqual([(p.other.id, p.other_keep, p.l_changes) for p in out.parts],
                         [('H1', ['kit/a.py'], []), ('H2', ['kit/sub/c.py'], [])])
        self.assertEqual(out.c_changes, [('kit/**', ['kit/a.py', 'kit/sub/c.py'],
                                          ['kit/b.py', 'kit/sub/d.py'])])
        self.assertEqual([p.c_members for p in out.parts], [['kit/**'], ['kit/**']])

    def test_two_live_pulls_with_plans_each_give_up_the_overlap_outside_every_t(self):
        out, why = self.split_all(['kit/sub/**'], [('H1', 'kit/**', ['kit/a.py'], True),
                                                   ('H2', 'kit/sub/**', ['kit/sub/c.py'], True)])
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/sub/d.py'])
        # H1 keeps H2's protected kit/sub/c.py: no path either live pull gives up is left
        # held by nobody.
        self.assertEqual([p.other_keep for p in out.parts],
                         [['kit/a.py', 'kit/b.py', 'kit/sub/c.py'], ['kit/sub/c.py']])

    def test_a_file_one_live_pull_adds_in_another_s_given_up_part_stays_held(self):
        # H2's `## Files` names kit/new2.py, a file not yet in the tree, outside its own scope
        # but inside H1's kit/**: the candidate gives it up (it is in T) and H2 cannot keep it,
        # so H1 must, or no record holds it.
        out, why = self.split_all(['kit/**'],
                                  [('H1', 'kit/**', ['kit/a.py'], True),
                                   ('H2', 'kit/sub/**', ['kit/sub/c.py', 'kit/new2.py'], True)])
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/b.py', 'kit/sub/d.py'])
        self.assertEqual([p.other_keep for p in out.parts],
                         [['kit/a.py', 'kit/new2.py', 'kit/sub/c.py'], ['kit/sub/c.py']])

    def test_a_live_pull_without_files_inside_another_s_given_up_part_keeps_its_scope(self):
        # Three ways, mixed kinds: H3 (no `## Files`) sits in what H1 gives up; H3 keeps its
        # member, the candidate gives it up whole, H1 keeps only its T.
        out, why = self.split_all(['kit/**'], [('H1', 'kit/**', ['kit/a.py'], True),
                                               ('H3', 'kit/b.py', [], False)])
        self.assertIsNone(why)
        self.assertEqual(out.keep, ['kit/sub/**'])
        self.assertEqual([(p.other.id, p.other_keep) for p in out.parts],
                         [('H1', ['kit/a.py']), ('H3', ['kit/b.py'])])

    def test_one_hitter_s_exact_file_clash_keeps_the_whole_record_held(self):
        # H2 holds the very file the candidate names: no split, though H1 alone would narrow.
        out, why = self.split_all(['kit/**', 'kit/b.py'],
                                  [('H1', 'kit/a.py', ['kit/a.py'], False),
                                   ('H2', 'kit/b.py', ['kit/b.py'], False)])
        self.assertIsNone(out)
        self.assertIn('H2', why)

    def test_every_case_complement_cannot_narrow_stays_held(self):
        cases = [(['kit/a.py'], 'kit/**', ['kit/a.py'], True),          # a single file
                 (['kit/sub/**'], 'kit/**', ['kit/sub/c.py', 'kit/sub/d.py'], True),  # nothing left
                 (['kit/**'], 'kit/**', ['kit\\x.py'], True),            # undecidable
                 (['kit/**'], 'kit/**', ['kit/a.py'], True, []),         # no tree
                 (['kit/**'], 'kit/**', [], True)]                       # nothing protected
        for case in cases:
            with self.subTest(case=case):
                out, why = self.split(*case)
                self.assertIsNone(out)
                self.assertTrue(why)

    def test_the_narrows_line(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/**'))
        out, _ = omod.split_all(['kit/**'], [(s.get('HOLD'), ['kit/a.py', 'kit/sub/c.py'], True)],
                              TREE)
        self.assertEqual(omod.narrows_line(omod.Offer(s.get('FOO'), 4, split=out)),
                         'NARROWS FOO (rank 4) with HOLD on kit/b: FOO drops kit/a.py, '
                         'kit/sub/c.py from kit/** (keeps kit/b.py, kit/sub/d.py); HOLD drops '
                         'kit/b.py, kit/sub/d.py from kit/** (keeps kit/a.py, kit/sub/c.py)')
        s = snap(live(scope='kit/sub/**'), priced('FOO', priority='3', scope='kit/**'))
        out, _ = omod.split_all(['kit/**'], [(s.get('HOLD'), [], False)], TREE)
        self.assertEqual(omod.narrows_line(omod.Offer(s.get('FOO'), 4, split=out)),
                         'NARROWS FOO (rank 4) with HOLD on kit/b: FOO drops kit/sub/** from '
                         'kit/** (keeps kit/a.py, kit/b.py); HOLD keeps its scope (no ## Files)')


class NextNarrows(unittest.TestCase):
    """`next_offer(narrow=...)`: a record whose only collision is one live pull is offered with
    the split, at its rank, taking only the scope it keeps."""

    def narrow(self, protected, plan=True, seen=None, tree=TREE):
        def narrow(rec, others):
            if seen is not None:
                seen.append((rec.id,) + tuple(o.id for o in others))
            return omod.split_all(rec.all('scope'), [(o, protected, plan) for o in others],
                                  tree)[0]
        return narrow

    def offer(self, s, holding, **kw):
        (offers, passed), refusals, _ = omod.next_offer(s, holding, **kw)
        self.assertEqual(refusals, [])
        return offers, {p.rec.id: p for p in passed}

    def test_offered_at_its_rank_taking_only_what_it_keeps(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/**'),
                 priced('BAR', priority='4', scope='kit/b.py'),
                 priced('BAZ', priority='5', scope='kit/sub/c.py'))
        offers, passed = self.offer(s, [s.get('HOLD')],
                                    narrow=self.narrow(['kit/a.py', 'kit/sub/c.py']))
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(offers[0].split.keep, ['kit/b.py', 'kit/sub/d.py'])
        # BAR collides with what FOO keeps as well as with HOLD: no narrowing, passed over.
        self.assertIsNotNone(passed['BAR'].pair)
        # BAZ is in HOLD's protected set: no split leaves it anything, so it is held as today.
        self.assertIsNotNone(passed['BAZ'].pair)

    def test_without_narrow_nothing_changes(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/**'))
        offers, passed = self.offer(s, [s.get('HOLD')])
        self.assertEqual(offers, [])
        self.assertTrue(omod.wants_narrowing(list(passed.values())))

    def test_a_split_that_fails_keeps_todays_pass_over(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/a.py'))
        holding = [s.get('HOLD')]
        offers, passed = self.offer(s, holding, narrow=self.narrow(['kit/a.py']))
        self.assertEqual(offers, [])
        lines = omod.narrowings(list(passed.values()), holding,
                                {'kit/b': [('kit/b', ['kit/a.py'])]}, {})
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('NARROW FOO (rank 2): kit/a.py collides'), lines)

    def test_a_record_offered_above_keeping_a_live_pulls_string_is_its_own_holder(self):
        # HOLD's plan is all under other/: R (rank 1) is narrowed and keeps kit/**, the very
        # string HOLD holds. C then hits kit/** twice, HOLD's and R's: judged by who took each
        # entry, not by the string, so C is not narrowed onto R's kit/.
        tree = TREE + ['other/x.py', 'other/y.py']
        s = snap(live(scope=['kit/**', 'other/**']),
                 priced('R', priority='1', scope='kit/**'),
                 priced('C', priority='3', scope='kit/a.py'))
        seen = []
        offers, passed = self.offer(s, [s.get('HOLD')],
                                    narrow=self.narrow(['other/x.py'], seen=seen, tree=tree))
        self.assertEqual([o.rec.id for o in offers], ['R'])
        self.assertEqual(offers[0].split.keep, ['kit/**'])
        self.assertEqual(seen, [('R', 'HOLD')])
        self.assertIn('C', passed)

    def test_a_record_hit_by_two_live_pulls_is_narrowed_clear_of_both(self):
        # MOVEMENT-S5's shape: one broad member, each live pull on its own file.
        s = snap(live(scope='kit/a.py'), live('HOLD2', scope='kit/sub/c.py', by='kit/c'),
                 priced('FOO', priority='3', scope='kit/**'))
        seen = []
        offers, passed = self.offer(s, [s.get('HOLD'), s.get('HOLD2')],
                                    narrow=self.narrow([], plan=False, seen=seen))
        self.assertEqual(seen, [('FOO', 'HOLD', 'HOLD2')])
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(offers[0].split.keep, ['kit/b.py', 'kit/sub/d.py'])
        self.assertEqual(omod.narrows_line(offers[0]),
                         'NARROWS FOO (rank 3) with HOLD on kit/b, HOLD2 on kit/c: FOO drops '
                         'kit/a.py, kit/sub/c.py from kit/** (keeps kit/b.py, kit/sub/d.py); '
                         'HOLD keeps its scope (no ## Files); HOLD2 keeps its scope (no ## Files)')

    def test_a_bare_folder_hit_by_one_file_inside_it_is_narrowed(self):
        s = snap(live(scope='kit/sub/c.py'), priced('FOO', priority='3', scope='kit/sub'))
        offers, passed = self.offer(s, [s.get('HOLD')], narrow=self.narrow([], plan=False))
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(offers[0].split.keep, ['kit/sub/d.py'])

    def test_one_hitter_that_cannot_be_narrowed_keeps_the_record_held(self):
        # HOLD2 takes the very file FOO names (an exact-file clash): FOO waits, though HOLD
        # alone would narrow, and its NARROW line stays.
        s = snap(live(scope='kit/a.py'), live('HOLD2', scope='kit/b.py', by='kit/c'),
                 priced('FOO', priority='3', scope=['kit/**', 'kit/b.py']))
        holding = [s.get('HOLD'), s.get('HOLD2')]
        offers, passed = self.offer(s, holding, narrow=self.narrow([], plan=False))
        self.assertEqual(offers, [])
        self.assertIsNotNone(passed['FOO'].pair)
        self.assertTrue(omod.wants_narrowing(list(passed.values())))

    def test_a_collision_with_a_record_offered_above_is_no_narrowing(self):
        s = snap(live(scope='kit/sub/**'), priced('TOP', priority='1', scope='kit/b.py'),
                 priced('FOO', priority='3', scope='kit/**'))
        seen = []
        offers, passed = self.offer(s, [s.get('HOLD')],
                                    narrow=self.narrow(['kit/sub/c.py'], seen=seen))
        self.assertEqual(([o.rec.id for o in offers], seen), (['TOP'], []))
        self.assertIn('FOO', passed)

    def test_a_live_pull_and_a_record_offered_above_in_the_way_is_no_narrowing(self):
        s = snap(live(scope='kit/sub/**'), priced('TOP', priority='1', scope='kit/a.py'),
                 priced('FOO', priority='3', scope='kit/**'))
        seen = []
        offers, passed = self.offer(s, [s.get('HOLD')],
                                    narrow=self.narrow(['kit/sub/c.py'], plan=False, seen=seen))
        self.assertEqual(([o.rec.id for o in offers], seen), (['TOP'], []))
        self.assertIn('FOO', passed)

    def test_an_unpriced_or_held_record_is_never_narrowed(self):
        s = snap(live(), rec('FOO', scope='kit/**'),
                 priced('BAR', priority='3', scope='kit/**', state='blocked', blocked_on='owner',
                        owner_ask='q?'))
        seen = []
        offers, _passed = self.offer(s, [s.get('HOLD')],
                                     narrow=self.narrow(['kit/a.py'], seen=seen))
        self.assertEqual((offers, seen), ([], []))

    def test_it_counts_toward_the_cap(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/**'),
                 priced('ZED', priority='4', scope='other/**'))
        offers, passed = self.offer(s, [s.get('HOLD')], cap=1,
                                    narrow=self.narrow(['kit/a.py', 'kit/sub/c.py']))
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(passed['ZED'].why, 'not reached - the cap was already full')

    def test_the_fold_check_judges_the_kept_scope(self):
        s = snap(live(), priced('FOO', priority='3', scope='kit/**'))
        asked = []

        def folded(rec, scopes=None):
            asked.append(scopes)
            return None
        offers, _ = self.offer(s, [s.get('HOLD')], folded=folded,
                               narrow=self.narrow(['kit/a.py', 'kit/sub/c.py']))
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(asked, [['kit/b.py', 'kit/sub/d.py']])

    def test_a_seed_is_narrowed_the_same_way(self):
        s = snap(live(), priced('AAA', priority='1', scope='other/**'),
                 priced('FOO', priority='5', scope='kit/**'))
        offers, _ = self.offer(s, [s.get('HOLD')], seeds=['FOO'],
                               narrow=self.narrow(['kit/a.py', 'kit/sub/c.py']))
        self.assertEqual([(o.rec.id, o.seeded) for o in offers], [('FOO', True), ('AAA', False)])
        self.assertEqual(offers[0].split.keep, ['kit/b.py', 'kit/sub/d.py'])


class ProtectedSet(StoreCase):
    """`next`'s narrowing end to end on a real repository (plan.offer_for(narrow=True)): T is the
    live branch's diff, its worktree's dirty paths and its `## Files`, read from git; any part
    that cannot be read keeps today's pass-over."""

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

    def offer(self, files='## Files\n- kitlib/y/c.py\n'):
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/holder',
                         scope='kitlib/**', body=files),
                  priced('FOO', priority='3', scope='kitlib/**'))
        snap = self.store.load()
        offers, passed, _holding, _own = pmod.offer_for(snap, self.ctx, narrow=True)
        return offers, {p.rec.id: p for p in passed}

    def test_t_is_the_diff_the_dirty_paths_and_the_files_section(self):
        offers, _passed = self.offer()
        self.assertEqual([o.rec.id for o in offers], ['FOO'])
        self.assertEqual(offers[0].split.keep, ['kitlib/x/b.py', 'kitlib/y/d.py'])
        self.assertEqual(offers[0].split.parts[0].other_keep,
                         ['kitlib/x/a.py', 'kitlib/x/new.py', 'kitlib/y/c.py'])

    def test_offer_for_without_narrow_is_todays_offer(self):
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/holder',
                         scope='kitlib/**', body='## Files\n- kitlib/y/c.py\n'),
                  priced('FOO', priority='3', scope='kitlib/**'))
        offers, passed, _h, _o = pmod.offer_for(self.store.load(), self.ctx)
        self.assertEqual(offers, [])
        self.assertIn('FOO', [p.rec.id for p in passed])

    def test_a_files_section_that_cannot_be_read_keeps_the_pass_over(self):
        offers, passed = self.offer(files='## Files\n- kitlib/y/**\n')
        self.assertEqual(offers, [])
        self.assertIsNotNone(passed['FOO'].pair)

    def test_no_worktree_for_the_live_branch_keeps_the_pass_over(self):
        self.repo.git('worktree', 'remove', '--force', self.wt)
        offers, passed = self.offer()
        self.assertEqual(offers, [])
        self.assertIsNotNone(passed['FOO'].pair)

    def test_dirty_paths_count_both_sides_of_a_rename_and_a_staged_delete(self):
        self.repo.git('mv', 'kitlib/y/d.py', 'kitlib/y/e.py', cwd=self.wt)
        self.repo.git('rm', '-q', 'kitlib/x/b.py', cwd=self.wt)
        self.assertEqual(pmod._dirty_paths(self.ctx, 'kit/holder'),
                         ['kitlib/x/b.py', 'kitlib/x/new.py', 'kitlib/y/d.py', 'kitlib/y/e.py'])

    def test_each_live_branch_is_diffed_once_per_offer(self):
        calls = []
        real = pmod._live_changes

        def counted(*a, **kw):
            calls.append(a)
            return real(*a, **kw)
        self.seed(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/holder',
                         scope='kitlib/**', body='## Files\n- kitlib/y/c.py\n'),
                  priced('FOO', priority='3', scope='kitlib/**'),
                  priced('BAR', priority='4', scope='kitlib/x/**'))
        with mock.patch.object(pmod, '_live_changes', counted):
            pmod.offer_for(self.store.load(), self.ctx, narrow=True)
        self.assertEqual(len(calls), 1)

    def test_a_status_git_cannot_run_keeps_the_pass_over(self):
        real = pmod.cmd.run

        def run(args, cwd, *a, **kw):
            if args and args[0] == 'status':
                return None, 'git status failed'
            return real(args, cwd, *a, **kw)
        with mock.patch.object(pmod.cmd, 'run', run):
            offers, passed = self.offer()
        self.assertEqual(offers, [])
        self.assertIsNotNone(passed['FOO'].pair)



class Restore(unittest.TestCase):
    """`next`'s RESTORE line (KIT-NEXT-NARROW-AUTO S3a): once the record a `narrowed:` line names
    is closed, the `set` that gives the narrowed record its original member back and clears that
    line; merged with RELEASABLE when the record is also held on the closed one."""

    HIST = {'L-ONE': 'abc1234 closes L-ONE: landed'}.get

    def test_a_held_record_s_restore_merges_with_its_releasable_line(self):
        s = snap(rec('C-ONE', priority='3', priority_by='agent', state='blocked',
                     blocked_on='item:L-ONE', scope='kitlib/x/b.py',
                     narrowed='kitlib/** L-ONE kit/l'))
        out, _note = omod.releasable_holds(s, None, self.HIST)
        self.assertEqual([line for _r, line in out], [
            'RESTORE+RELEASABLE C-ONE (rank 1): L-ONE landed (closed in abc1234) -> `set C-ONE '
            '--scope "kitlib/**" --unset narrowed`; `unblock C-ONE --on item:L-ONE --reason '
            '"L-ONE closed"`'])
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [])

    def test_a_record_not_held_gets_the_plain_restore_line(self):
        s = snap(rec('C-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/c', scope='kitlib/x/b.py',
                     narrowed='kitlib/** L-ONE kit/l'))
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [
            'RESTORE C-ONE: L-ONE landed (closed in abc1234) -> `set C-ONE --scope "kitlib/**" '
            '--unset narrowed`'])

    def test_lines_naming_a_live_record_stay(self):
        s = snap(rec('C-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/c', scope=['kitlib/x/b.py', 'docs/a.md'],
                     narrowed=['kitlib/** L-ONE kit/l', 'docs/** M-ONE kit/m']),
                 rec('M-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/m', scope='docs/b.md'))
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [
            'RESTORE C-ONE: L-ONE landed (closed in abc1234) -> `set C-ONE --scope "kitlib/**" '
            '--scope "docs/a.md" --narrowed "docs/** M-ONE kit/m"`'])

    def test_no_line_while_the_other_record_is_live(self):
        s = snap(rec('C-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/c', scope='kitlib/x/b.py',
                     narrowed='kitlib/** L-ONE kit/l'),
                 rec('L-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/l', scope='kitlib/x/a.py'))
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [])

    def mine(self, **kw):
        return rec('C-ONE', priority='3', priority_by='agent', state='pulled', pulled_at=TODAY,
                   pulled_by='kit/c', scope='kitlib/x/b.py', narrowed='kitlib/** L-ONE kit/l',
                   **kw)

    def test_a_released_other_record_restores(self):
        s = snap(self.mine(), rec('L-ONE', priority='3', priority_by='agent'))
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [
            'RESTORE C-ONE: L-ONE released (open again) -> `set C-ONE --scope "kitlib/**" '
            '--unset narrowed`'])

    def test_a_retired_or_deleted_other_record_restores(self):
        for verb, said in (('retires', 'retired'), ('deletes', 'deleted')):
            hist = {'L-ONE': 'def5678 %s L-ONE: gone' % verb}.get
            self.assertEqual(omod.restore_lines(snap(self.mine()), None, hist), [
                'RESTORE C-ONE: L-ONE %s (in def5678) -> `set C-ONE --scope "kitlib/**" '
                '--unset narrowed`' % said])

    def held_on_l(self):
        return rec('C-ONE', priority='3', priority_by='agent', state='blocked',
                   blocked_on='item:L-ONE', scope='kitlib/x/b.py',
                   narrowed='kitlib/** L-ONE kit/l')

    def restore_type(self, s, hist):
        out, _note = omod.releasable_holds(s, None, hist)
        return [line for _r, line in out if line.startswith('RESTORE')] + \
            omod.restore_lines(s, None, hist)

    def test_a_held_record_gets_one_restore_line_when_its_other_record_was_released(self):
        s = snap(self.held_on_l(), rec('L-ONE', priority='3', priority_by='agent'))
        self.assertEqual(self.restore_type(s, self.HIST), [
            'RESTORE C-ONE: L-ONE released (open again) -> `set C-ONE --scope "kitlib/**" '
            '--unset narrowed`'])

    def test_a_held_record_gets_one_restore_line_when_its_other_record_was_retired_or_deleted(
            self):
        for verb, said in (('retires', 'retired'), ('deletes', 'deleted')):
            hist = {'L-ONE': 'def5678 %s L-ONE: gone' % verb}.get
            self.assertEqual(self.restore_type(snap(self.held_on_l()), hist), [
                'RESTORE C-ONE: L-ONE %s (in def5678) -> `set C-ONE --scope "kitlib/**" '
                '--unset narrowed`' % said])

    def test_a_held_record_gets_one_restore_line_when_its_other_record_landed(self):
        lines = self.restore_type(snap(self.held_on_l()), self.HIST)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('RESTORE+RELEASABLE C-ONE'), lines)

    def test_a_blocked_other_record_or_an_unknown_history_does_not(self):
        s = snap(self.mine(), rec('L-ONE', priority='3', priority_by='agent', state='blocked',
                                  blocked_on='owner'))
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [])
        self.assertEqual(omod.restore_lines(snap(self.mine()), None, lambda rid: None), [])

    def test_a_member_narrowed_for_two_records_waits_until_both_have_ended(self):
        # A split against two live pulls: kitlib/** carries a line for each. Given back when
        # only L-ONE has landed, it would cover M-ONE's live paths again.
        def two(**kw):
            return rec('C-ONE', priority='3', priority_by='agent', pulled_at=TODAY,
                       pulled_by='kit/c', scope='kitlib/x/b.py',
                       narrowed=['kitlib/** L-ONE kit/l', 'kitlib/** M-ONE kit/m'], **kw)
        m_live = rec('M-ONE', priority='3', priority_by='agent', state='pulled',
                     pulled_at=TODAY, pulled_by='kit/m', scope='kitlib/y/c.py')
        s = snap(two(state='pulled'), m_live)
        self.assertEqual(omod.restore_lines(s, None, self.HIST), [])
        held = snap(two(state='blocked', blocked_on='item:L-ONE'), m_live)
        out, _note = omod.releasable_holds(held, None, self.HIST)
        self.assertEqual([line for _r, line in out if line.startswith('RESTORE')], [])
        both = {'L-ONE': 'abc1234 closes L-ONE: landed', 'M-ONE': 'fed4321 closes M-ONE: x'}.get
        self.assertEqual(omod.restore_lines(snap(two(state='pulled')), None, both), [
            'RESTORE C-ONE: L-ONE landed (closed in abc1234), M-ONE landed (closed in fed4321) '
            '-> `set C-ONE --scope "kitlib/**" --unset narrowed`'])


class NextCliNarrows(unittest.TestCase):
    """Through tools/backlog.py `next`: the offer row's `(narrows, below)`, the NARROWS line and
    the counts line's `| N narrows`."""

    def setUp(self):
        test_cli.Cli.setUp(self)
        for p in ('brain/a.py', 'brain/b.py', 'brain/c.py'):
            self.repo.write(p, 'x\n')
        self.repo.commit('code', 'brain')
        self.repo.git('push', '-q', 'origin', 'main')
        wt = self.repo.worktree('holder')
        self.repo.write('brain/a.py', 'changed\n', root=wt)
        self.repo.commit('work', 'brain/a.py', cwd=wt)

    def test_next_shows_the_narrowing_and_writes_nothing(self):
        backend = smod.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        store = smod.Store(backend, rmod.Rules())
        backend.publish(store.load(), {
            'HOLD': record_text(id='HOLD', priority='2', priority_by='agent', state='pulled',
                                pulled_at=TODAY, pulled_by='kit/holder', scope='brain/**',
                                body='## Files\n- `brain/c.py`\n'),
            'FOO': record_text(id='FOO', priority='3', priority_by='agent', scope='brain/**')},
            'seed\n')
        before = self.repo.git('ls-remote', 'origin', smod.STORE_REF)
        rc, out = test_cli.Cli.run_tool(self, 'next')
        self.assertEqual(rc, 0, out)
        rows = [line for line in out.splitlines() if 'FOO' in line and '(narrows, below)' in line]
        self.assertEqual(len(rows), 1, out)
        self.assertIn('NARROWS FOO (rank 2) with HOLD on kit/holder: FOO drops brain/a.py, '
                      'brain/c.py from brain/** (keeps brain/b.py); HOLD drops brain/b.py from '
                      'brain/** (keeps brain/a.py, brain/c.py)', out)
        self.assertNotIn('NARROW FOO', out)
        self.assertTrue(out.rstrip('\n').endswith(
            'counts: 1 offered | 1 pullable | 0 passed over | 0 releasable | 0 narrow '
            '| 1 narrows'), out)
        self.assertEqual(self.repo.git('ls-remote', 'origin', smod.STORE_REF), before)


class NextCliRestore(unittest.TestCase):
    """Through tools/backlog.py `next`: the RESTORE line for a record whose narrowed: line names
    a record the store's history closed, and the counts line's `| N restore`."""

    def setUp(self):
        test_cli.Cli.setUp(self)

    def test_next_prints_the_restore_and_counts_it(self):
        backend = smod.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        store = smod.Store(backend, rmod.Rules())
        backend.publish(store.load(), {
            'C-ONE': record_text(id='C-ONE', priority='3', priority_by='agent', state='pulled',
                                 pulled_at=TODAY, pulled_by='kit/c', scope='brain/b.py',
                                 narrowed='brain/** L-ONE kit/l'),
            'L-ONE': record_text(id='L-ONE', priority='3', priority_by='agent', state='pulled',
                                 pulled_at=TODAY, pulled_by='kit/l', scope='brain/a.py')},
            'seed\n')
        backend.publish(store.load(), {'L-ONE': None}, 'closes L-ONE: landed\n')
        before = self.repo.git('ls-remote', 'origin', smod.STORE_REF)
        rc, out = test_cli.Cli.run_tool(self, 'next')
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r'RESTORE C-ONE: L-ONE landed \(closed in [0-9a-f]+\) -> '
                              r'`set C-ONE --scope "brain/\*\*" --unset narrowed`')
        counts = [line for line in out.splitlines() if line.startswith('counts: ')]
        self.assertEqual(len(counts), 1, out)
        self.assertTrue(counts[0].endswith(' | 1 restore'), out)
        self.assertEqual(self.repo.git('ls-remote', 'origin', smod.STORE_REF), before)


if __name__ == '__main__':
    unittest.main()
