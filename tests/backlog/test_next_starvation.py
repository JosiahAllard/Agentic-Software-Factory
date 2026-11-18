"""A short offer's causes in `next` (docs/backlog.md "next: a short offer's causes"): a hold whose
cited hub id or `item:` blocker is already met is a RELEASABLE line with its `unblock`; a record
passed over on a broad scope member (`x/**`, a literal `.md`) colliding with a live pull is a
NARROW line naming what that pull's branch actually changed there; a `counts:` line closes it. The
hub is read with a short timeout and any failure is one note line, never a traceback."""
import datetime
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.backlog import conditions as cmod
from kitlib.backlog import offer as omod
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod
from kitlib.backlog import store_ref
from kitlib.ledger import read as lread

from tests.backlog import test_cli
from tests.backlog.helpers import TODAY, priced, rec, record_text
from tests.backlog.test_offer import snap


def owner_held(rid, ask, **over):
    return rec(rid, priority='2', priority_by='agent', state='blocked', blocked_on='owner',
               owner_ask=ask, **over)


class Releasable(unittest.TestCase):

    def judge(self, record, statuses=None, closed=None):
        s = snap(record)
        r = s.records[0]
        return cmod.releasable(r, (statuses or {}).get, closed or (lambda rid: None))

    def test_an_owner_hold_whose_cited_question_is_answered_is_releasable(self):
        got = self.judge(owner_held('AAA', 'Q-1635 until the PM sends its re-cut scope',
                                    until='owner question:Q-1635 answered'),
                         {'Q-1635': 'answered'})
        self.assertEqual(got, [('owner', 'owner cites Q-1635 answered', 'Q-1635 answered')])

    def test_an_open_question_holds(self):
        self.assertEqual(self.judge(owner_held('AAA', 'Q-1635 first',
                                               until='owner question:Q-1635 answered'),
                                    {'Q-1635': 'open'}), [])

    def test_every_cited_id_must_be_met(self):
        r = rec('AAA', priority='2', priority_by='agent', state='blocked',
                blocked_on='deliberate', until='deliberate after Q-1 and T-2')
        self.assertEqual(self.judge(r, {'Q-1': 'done', 'T-2': 'open'}), [])
        got = self.judge(r, {'Q-1': 'done', 'T-2': 'done'})
        self.assertEqual(got[0][2], 'Q-1 done, T-2 done')

    def test_an_unknown_or_unread_id_is_never_met(self):
        self.assertEqual(self.judge(owner_held('AAA', 'Q-9 first',
                                               until='owner question:Q-9 answered'), {}), [])

    def test_an_owner_hold_with_a_line_per_ask_needs_every_line_met(self):
        r = owner_held('AAA', 'Q-1635 on the Action Hub',
                       until=['owner question:Q-1635 answered', 'owner question:Q-1636 answered'])
        self.assertEqual(self.judge(r, {'Q-1635': 'answered', 'Q-1636': 'open'}), [])
        got = self.judge(r, {'Q-1635': 'answered', 'Q-1636': 'filed'})
        self.assertEqual(got[0][2], 'Q-1635 answered, Q-1636 filed')

    def test_a_hold_that_cites_nothing_is_not_judged(self):
        self.assertEqual(self.judge(owner_held('AAA', 'will you pick a colour?')), [])

    def test_an_until_line_cites_for_its_member(self):
        r = rec('AAA', priority='2', priority_by='agent', state='blocked',
                blocked_on='deliberate', until='deliberate after T-7 lands')
        self.assertEqual(self.judge(r, {'T-7': 'done'})[0][:2],
                         ('deliberate', 'deliberate cites T-7 done'))

    def test_an_owner_hold_citing_the_answered_ruling_that_set_it_is_not_releasable(self):
        # The citation block --on owner needs is often the answered question that set the hold
        # (a "Defer" answer): it is the hold's ground, not its end.
        r = owner_held('AAA', 'Owner deferred the read (Q-71, 2026-01-02). Resume when asked')
        self.assertEqual(self.judge(r, {'Q-71': 'answered'}), [])

    def test_an_owner_until_question_ends_the_hold_once_answered(self):
        r = owner_held('AAA', 'Q-72: pick the colour', until='owner question:Q-72 answered')
        self.assertEqual(self.judge(r, {'Q-72': 'open'}), [])
        self.assertEqual(self.judge(r, {'Q-72': 'answered'}),
                         [('owner', 'owner cites Q-72 answered', 'Q-72 answered')])

    def test_a_closed_item_blocker_is_releasable(self):
        r = rec('AAA', priority='2', priority_by='agent', state='blocked', blocked_on='item:BBB')
        got = self.judge(r, closed=lambda rid: 'abc1234' if rid == 'BBB' else None)
        self.assertEqual(got, [('item:BBB', 'item:BBB closed (abc1234)', 'BBB closed')])
        self.assertEqual(self.judge(r), [])

    def test_closed_reader_reads_only_a_close(self):
        s = snap(priced('LIVE'))
        hist = {'GONE': 'abc1234 closes GONE: landed', 'RET': 'def5678 retires RET: dup'}.get
        closed = omod.closed_reader(s, hist)
        self.assertEqual(closed('GONE'), 'abc1234')
        self.assertIsNone(closed('RET'))
        self.assertIsNone(closed('LIVE'))
        self.assertIsNone(closed('NONE'))


class ReleasableHolds(unittest.TestCase):

    def test_the_line_names_the_pm_for_an_owner_hold(self):
        s = snap(owner_held('AAA', 'Q-5 first', until='owner question:Q-5 answered'),
                 priced('BBB'))
        out, note = omod.releasable_holds(s, None, lambda rid: None,
                                          hub=lambda: ({'Q-5': ('answered', None)}, None))
        self.assertIsNone(note)
        self.assertEqual([line for _, line in out], [
            'RELEASABLE AAA (rank 1): owner cites Q-5 answered -> the PM\'s (unblock-owner): '
            '`unblock AAA --on owner --reason "Q-5 answered"`'])

    def test_the_hub_is_not_asked_when_nothing_cites_an_id(self):
        s = snap(owner_held('AAA', 'a plain question'))
        hub = mock.Mock(side_effect=AssertionError('asked'))
        self.assertEqual(omod.releasable_holds(s, None, lambda rid: None, hub=hub), ([], None))

    def test_the_hub_is_not_asked_for_an_id_cited_only_in_owner_ask(self):
        s = snap(owner_held('AAA', 'Owner deferred the read (Q-73)'))
        hub = mock.Mock(side_effect=AssertionError('asked'))
        self.assertEqual(omod.releasable_holds(s, None, lambda rid: None, hub=hub), ([], None))

    def test_a_hub_that_cannot_be_read_is_one_note_and_item_ends_still_count(self):
        s = snap(owner_held('AAA', 'Q-5 first', until='owner question:Q-5 answered'),
                 rec('CCC', priority='3', priority_by='agent', state='blocked',
                     blocked_on='item:GONE'))
        out, note = omod.releasable_holds(
            s, None, {'GONE': 'abc1234 closes GONE: x'}.get,
            hub=lambda: (None, 'TimeoutError: timed out'))
        self.assertEqual(note, 'releasable: hub not read (TimeoutError: timed out); Q-/T- holds '
                               'not checked')
        self.assertEqual([r.id for r, _ in out], ['CCC'])

    def test_hub_statuses_never_raises(self):
        def boom(coll):
            raise TimeoutError('timed out')
        self.assertEqual(omod.hub_statuses(listing=boom), (None, 'TimeoutError: timed out'))

    def test_hub_statuses_reads_questions_and_todos(self):
        # each with its end time, as its own field says: a question's answer.at (else done_at), a
        # to-do's done_at; a time with no offset, or none, is None
        docs = {'questions': [{'id': 'Q-1', 'status': 'answered',
                               'answer': {'at': '2026-11-16T10:06:00+01:00'}},
                              {'id': 'Q-4', 'status': 'done', 'done_at': '2026-11-16T10:06:00'},
                              'junk'],
                'todos': [{'id': 'T-2', 'status': 'done', 'done_at': '2026-11-16T09:06:00+00:00'},
                          {'id': 'T-3'}]}
        self.assertEqual(omod.hub_statuses(listing=docs.get),
                         ({'Q-1': ('answered', 1794819960.0), 'Q-4': ('done', None),
                           'T-2': ('done', 1794819960.0)}, None))


def local(text):
    """The epoch of a local wall-clock time."""
    return datetime.datetime.fromisoformat(text).timestamp()


def deliberate(rid, reason, day='2026-11-16', body=None, **over):
    """A record held on `deliberate` by `block --reason` on `day`: its `Blocked on` note."""
    return rec(rid, priority='2', priority_by='agent', state='blocked', blocked_on='deliberate',
               body=body or 'Blocked on deliberate (%s): %s' % (day, reason), **over)


LEDGER = """## R-9197 | 2026-11-01 08:00Z | alpha | typed
Q: q
A: a
Decides: Widget records wait.
Source: s
Supersedes: -

## R-9204 | 2026-11-12 08:00Z | alpha | typed
Q: q
A: a
Decides: The widget-holds rule.
Source: s
Supersedes: R-9197

## R-9201 | 2026-11-02 08:00Z | alpha | typed
Retired: spent - done (see -)

## R-9202 | 2026-11-02 08:00Z | alpha | typed
Q: q
A: a
Decides: Still live.
Source: s
Supersedes: -

## H-alpha-9301 | 2026-11-03 08:00Z | alpha | typed
Q: q
A: a
Decides: Per-repo rule.
Source: s
Supersedes: -

## H-alpha-9302 | 2026-11-12 08:00Z | alpha | typed
Q: q
A: a
Decides: Its successor.
Source: s
Supersedes: H-alpha-9301
"""


class StaleHolds(unittest.TestCase):
    """A `deliberate` hold with no `until` whose note cites an id that has ended since the hold
    was written is flagged `stale hold: <id> ended`, never released (offer.stale_holds)."""

    def judge(self, *records, hub=None, since=None, ledger=LEDGER):
        s = snap(*records)
        got = omod.stale_holds(
            omod.held_records(s), lambda: (hub, None) if hub is not None else
            (None, 'TimeoutError: timed out'),
            since or mock.Mock(side_effect=AssertionError('hold time read')),
            ledger=lambda: ({e.id: e for e in lread.parse(ledger)}, None) if ledger else
            (None, 'no ledger_path or ledger_dir'))
        return got

    def test_a_question_ended_on_a_later_day_is_stale_without_reading_the_hold_time(self):
        found, notes = self.judge(deliberate('NODE', 'Q-2289: the WSL runtime choice'),
                                  hub={'Q-2289': ('done', local('2026-11-17T10:06'))})
        self.assertEqual((found, notes),
                         ({'NODE': [('Q-2289', 'done 2026-11-17 10:06')]}, []))
        self.assertEqual(omod.stale_line(snap(deliberate('NODE', 'Q-2289')).get('NODE'), 3,
                                         found['NODE']),
                         'STALE HOLD NODE (rank 3): stale hold: Q-2289 ended (done 2026-11-17 '
                         '10:06) -> unblock NODE --on deliberate, or block it again citing what '
                         'holds it now')

    def test_on_the_notes_day_the_end_must_come_after_the_block_commit(self):
        hold = deliberate('NODE', 'Q-2289: the WSL runtime choice')
        hub = {'Q-2289': ('answered', local('2026-11-16T10:06'))}
        asked = []

        def since(rid, members):
            asked.append((rid, members))
            return local(at)
        at = '2026-11-16T09:00'          # held at 09:00, answered at 10:06: ended since
        self.assertEqual(self.judge(hold, hub=hub, since=since)[0],
                         {'NODE': [('Q-2289', 'answered 2026-11-16 10:06')]})
        self.assertEqual(asked, [('NODE', 'deliberate')])
        at = '2026-11-16T11:00'          # answered before the hold: the ground it rests on
        self.assertEqual(self.judge(hold, hub=hub, since=since)[0], {})
        # no block commit that day (a later same-day block on another member, a store that
        # commits nothing): no time, no flag
        self.assertEqual(self.judge(hold, hub=hub, since=lambda rid, m: None)[0], {})

    def test_an_end_before_the_notes_day_or_an_open_ask_is_not_stale(self):
        hold = deliberate('NODE', 'Q-2289 and T-0400 first')
        self.assertEqual(self.judge(hold, hub={
            'Q-2289': ('answered', local('2026-11-15T10:06')),
            'T-0400': ('open', None)})[0], {})

    def test_a_hub_that_cannot_be_read_flags_nothing_and_is_one_note(self):
        found, notes = self.judge(deliberate('NODE', 'Q-2289 first'))
        self.assertEqual((found, notes), ({}, [
            'releasable: hub not read (TimeoutError: timed out); Q-/T- holds not checked']))

    def test_a_superseded_or_retired_ruling_is_stale(self):
        found, notes = self.judge(deliberate('CMB', 'R-9197: widget waits'),
                                  deliberate('OLD', 'R-9201 and R-9202'),
                                  deliberate('LIVE', 'R-9202'),
                                  deliberate('HOLD', 'H-alpha-9301: per-repo wait'),
                                  deliberate('HLIVE', 'H-alpha-9302'))
        self.assertEqual((found, notes), ({'CMB': [('R-9197', 'superseded by R-9204')],
                                           'HOLD': [('H-alpha-9301', 'superseded by H-alpha-9302')],
                                           'OLD': [('R-9201', 'retired: spent')]}, []))
        # H- holds alone still send the ledger read (no R- hold asks for it)
        found, notes = self.judge(deliberate('HOLD', 'H-alpha-9301: per-repo wait'),
                                  deliberate('HLIVE', 'H-alpha-9302'))
        self.assertEqual((found, notes),
                         ({'HOLD': [('H-alpha-9301', 'superseded by H-alpha-9302')]}, []))

    def test_a_ruling_re_cited_with_its_successor_is_not_stale(self):
        found, _ = self.judge(deliberate('CMB', 'R-9197, now R-9204: widget waits'))
        self.assertEqual(found, {})

    def test_a_ledger_that_cannot_be_read_flags_nothing_and_is_one_note(self):
        found, notes = self.judge(deliberate('CMB', 'R-9197'), ledger=None)
        self.assertEqual((found, notes), ({}, [
            'stale holds: ledger not read (no ledger_path or ledger_dir); ruling cites not checked']))

    def test_only_a_deliberate_hold_with_no_until_and_a_live_note_is_judged(self):
        # an `until` states the end (RELEASABLE's); an `Unblocked (` line after the note ended its
        # wait; an owner hold's ask clears it itself; no note (set by hand): nothing to read
        hub = mock.Mock(side_effect=AssertionError('hub read'))
        s = snap(deliberate('UNTIL', 'Q-2289', until='deliberate question:Q-2289 answered'),
                 deliberate('ENDED', '', body='Blocked on deliberate (2026-11-16): Q-2289\n'
                            'Unblocked (2026-11-17): pull by kit/x restored.'),
                 owner_held('OWN', 'Q-2289 first'),
                 deliberate('BARE', '', body='notes'))
        self.assertEqual(omod.stale_holds(omod.held_records(s), hub, None), ({}, []))

    def test_the_note_head_follows_a_stopped_pull_line_and_names_several_members(self):
        body = ('Stopped on branch kit/x at abc1234567, branch kept.\n'
                'Blocked on deliberate, item:FOO (2026-11-16): Q-2289 first')
        note = rmod.deliberate_note(snap(deliberate('NODE', '', body=body)).get('NODE'))
        self.assertEqual(note, rmod.DeliberateNote('2026-11-16', 'deliberate, item:FOO',
                                                   'Q-2289 first', ['Q-2289']))

    def test_an_end_time_the_platform_cannot_place_flags_nothing(self):
        # fromtimestamp raises on a time out of range (before 1970 on Windows): fail closed
        found, notes = self.judge(deliberate('NODE', 'Q-2289 first'),
                                  hub={'Q-2289': ('answered', 1e20)})
        self.assertEqual((found, notes), ({}, []))

    def test_under_a_session_only_its_held_records_are_judged(self):
        s = snap(deliberate('MINE', 'Q-2289', session='kit'),
                 deliberate('THEIRS', 'Q-2289', session='other'), deliberate('NONE', 'Q-2289'))
        self.assertEqual([r.id for r in omod.held_records(s, 'kit')], ['MINE'])
        self.assertEqual([r.id for r in omod.held_records(s)], ['MINE', 'NONE', 'THEIRS'])


class Broad(unittest.TestCase):

    def test_a_folder_glob_and_a_literal_doc_are_broad(self):
        for m in ('brain/**', 'shared/game-api/**', 'docs/architecture.md', 'A.MD'):
            self.assertTrue(omod.is_broad(m), m)
        for m in ('tools/x.py', 'docs', 'none'):
            self.assertFalse(omod.is_broad(m), m)


class Narrowings(unittest.TestCase):

    def setUp(self):
        self.hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                           scope=['brain/**', 'tools/t.py'])

    def lines(self, cand_scope, changes, unknown=None, dirty=None, hold=None):
        if dirty is None:       # a clean worktree, read
            dirty = {'kit/b': ([], None)}
        s = snap(hold or self.hold, priced('FOO', priority='3', scope=cand_scope))
        holding = [s.get('HOLD')]
        (_offers, passed), _, _ = omod.next_offer(s, holding)
        self.assertTrue(omod.wants_narrowing(passed))
        return omod.narrowings(passed, holding, changes, unknown or {}, dirty)

    def test_the_set_keeps_the_uncommitted_files_of_the_worktree(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]},
                         dirty={'kit/b': (['brain/wip.py', 'other/z.py'], None)})
        self.assertIn('which changed 2 file(s) there: brain/a.py, brain/wip.py -> `set HOLD '
                      '--scope tools/t.py --scope brain/a.py --scope brain/wip.py`', got[0])

    def test_the_set_keeps_the_files_plan(self):
        hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                      scope=['brain/**', 'tools/t.py'], body='## Files\n\n- `brain/plan.py`\n')
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]},
                         hold=hold)
        self.assertIn('--scope brain/a.py --scope brain/plan.py`', got[0])

    def test_unreadable_uncommitted_files_drop_the_set(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]},
                         dirty={'kit/b': (None, 'no worktree')})
        self.assertNotIn('`set ', got[0])
        self.assertIn('not narrowing HOLD: no worktree', got[0])

    def test_uncommitted_files_not_read_at_all_drop_the_set(self):
        for dirty in ({}, {'kit/x': ([], None)}, {'kit/b': None}):
            s = snap(self.hold, priced('FOO', priority='3', scope='brain/b.py'))
            holding = [s.get('HOLD')]
            (_o, passed), _, _ = omod.next_offer(s, holding)
            got = omod.narrowings(passed, holding, {'kit/b': [('kit/b', ['brain/a.py'])]}, {},
                                  dirty)
            self.assertNotIn('`set ', got[0])
            self.assertIn('not narrowing HOLD: its uncommitted files were not read', got[0])
        s = snap(self.hold, priced('FOO', priority='3', scope='brain/b.py'))
        (_o, passed), _, _ = omod.next_offer(s, [s.get('HOLD')])
        got = omod.narrowings(passed, [s.get('HOLD')],
                              {'kit/b': [('kit/b', ['brain/a.py'])]}, {})
        self.assertNotIn('`set ', got[0])

    def test_nothing_committed_under_the_glob_but_unreadable_is_not_nothing_yet(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['other/z.py'])]},
                         dirty={'kit/b': (None, 'no worktree')})
        self.assertEqual(len(got), 1)
        self.assertNotIn('changed nothing', got[0])
        self.assertTrue(got[0].endswith('; not narrowing HOLD: no worktree'), got)

    def test_a_second_glob_member_is_kept_and_single_quoted(self):
        hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                      scope=['brain/**', 'tools/**'])
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]}, hold=hold)
        self.assertIn("-> `set HOLD --scope 'tools/**' --scope brain/a.py`", got[0])

    def test_a_kept_member_with_a_quote_drops_the_set(self):
        hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                      scope=['brain/**', "tools/it's.py"])
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]}, hold=hold)
        self.assertNotIn('`set ', got[0])
        self.assertIn('has a quote', got[0])

    def test_a_kept_path_a_shell_would_split_drops_the_set(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ["brain/it's.py"])]})
        self.assertNotIn('`set ', got[0])
        self.assertIn("not narrowing HOLD: brain/it's.py has a quote", got[0])

    def test_an_added_path_a_shell_would_split_is_single_quoted(self):
        for odd in ('brain/a b.py', 'brain/a;b.py', 'brain/a"b.py'):
            got = self.lines('brain/b.py', {'kit/b': [('kit/b', [odd])]})
            self.assertIn("--scope tools/t.py --scope '%s'`" % odd, got[0])

    def test_a_own_member_with_a_space_is_single_quoted(self):
        hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                      scope=['brain/**', 'docs/my notes.md'])
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]}, hold=hold)
        self.assertIn("-> `set HOLD --scope 'docs/my notes.md' --scope brain/a.py`", got[0])

    def test_an_unreadable_files_plan_drops_the_set(self):
        hold = priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                      scope=['brain/**', 'tools/t.py'], body='## Files\n\n- a.py: rewrite\n')
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py'])]},
                         hold=hold)
        self.assertNotIn('`set ', got[0])
        self.assertIn('not narrowing HOLD', got[0])

    def test_a_broad_live_pull_is_narrowed_to_what_its_branch_changed(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', ['brain/a.py', 'other/z.py'])]})
        self.assertEqual(got, [
            'NARROW FOO (rank 2): brain/b.py collides with brain/** of HOLD on kit/b, which '
            'changed 1 file(s) there: brain/a.py -> `set HOLD --scope tools/t.py --scope '
            'brain/a.py`'])

    def test_real_overlap_waits_for_the_landing(self):
        got = self.lines('brain/a.py', {'kit/b': [('kit/b', ['brain/a.py'])]})
        self.assertIn('brain/a.py overlaps that work too (brain/a.py): pull FOO after kit/b '
                      'lands', got[0])

    def test_nothing_changed_yet_is_said(self):
        got = self.lines('brain/b.py', {'kit/b': [('kit/b', [])]})
        self.assertTrue(got[0].endswith('which changed nothing under brain/** yet'), got)

    def test_a_branch_git_cannot_read_is_cannot_tell(self):
        got = self.lines('brain/b.py', {}, {'kit/b': 'no such ref'})
        self.assertTrue(got[0].endswith('cannot tell what kit/b changed (no such ref)'), got)

    def test_many_files_are_capped_in_the_list(self):
        paths = ['brain/f%02d.py' % i for i in range(10)]
        got = self.lines('brain/zz.py', {'kit/b': [('kit/b', paths)]})
        self.assertIn('brain/f07.py +2 more ->', got[0])

    def test_a_narrow_collision_gets_no_line(self):
        s = snap(priced('HOLD', state='pulled', pulled_at=TODAY, pulled_by='kit/b',
                        scope='tools/t.py'), priced('FOO', priority='3', scope='tools/t.py'))
        (_o, passed), _, _ = omod.next_offer(s, [s.get('HOLD')])
        self.assertFalse(omod.wants_narrowing(passed))
        self.assertEqual(omod.narrowings(passed, [s.get('HOLD')], {}, {}, {}), [])

    def test_counts_by_reason(self):
        s = snap(self.hold, priced('FOO', priority='3', scope='brain/b.py'))
        (_o, passed), _, _ = omod.next_offer(s, [s.get('HOLD')])
        self.assertEqual(omod.pass_counts(passed), (1, [('scope', 1), ('fold', 0), ('resource', 0),
                                                        ('not reached', 0), ('other', 0)]))


class _Hub(object):
    """A hub client stand-in: `docs` per collection, or `error` raised by every listing."""

    def __init__(self, docs=None, error=None):
        self.docs, self.error, self.timeouts = docs or {}, error, []

    def __call__(self, port, timeout=None, **_kw):
        self.timeouts.append(timeout)
        return self

    def listing(self, coll, status=None, role=None):
        if self.error is not None:
            raise self.error
        return self.docs.get(coll, [])


class NextCli(unittest.TestCase):
    """Through tools/backlog.py `next`."""
    setUp = test_cli.Cli.setUp
    run_tool = test_cli.Cli.run_tool
    ok = test_cli.Cli.ok

    def publish(self, texts):
        backend = store_ref.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
        backend.publish(smod.Store(backend, rmod.Rules()).load(), texts, 'seed\n')

    def hub(self, fake):
        from kitlib.hubclient import client, settings
        for target, value in ((client, 'Client'), (settings, 'load')):
            patch = mock.patch.object(target, value, fake if value == 'Client'
                                      else mock.Mock(return_value=mock.Mock(port=1)))
            patch.start()
            self.addCleanup(patch.stop)

    def seed_held(self):
        self.publish({
            'AAA': record_text(id='AAA', priority='2', priority_by='agent', state='blocked',
                               blocked_on='owner', scope='a/aaa.py',
                               owner_ask='Q-1635 until the PM sends its re-cut scope',
                               until='owner question:Q-1635 answered'),
            'BBB': record_text(id='BBB', priority='3', priority_by='agent', scope='a/bbb.py')})

    def test_an_answered_hold_is_releasable_and_counted(self):
        self.seed_held()
        fake = _Hub({'questions': [{'id': 'Q-1635', 'status': 'answered'}]})
        self.hub(fake)
        out = self.ok('next')
        self.assertIn('held 1: 1 owner  (1 releasable, below)', out)
        self.assertIn('RELEASABLE AAA (rank 1): owner cites Q-1635 answered -> the PM\'s '
                      '(unblock-owner): `unblock AAA --on owner --reason "Q-1635 answered"`', out)
        self.assertIn('counts: 1 offered | 1 pullable | 0 passed over | 1 releasable | 0 narrow',
                      out)
        self.assertEqual(fake.timeouts, [omod.HUB_TIMEOUT_S])
        self.assertLessEqual(omod.HUB_TIMEOUT_S, 3)

    def test_a_hub_timeout_is_one_note_and_the_offer_still_prints(self):
        self.seed_held()
        self.hub(_Hub(error=TimeoutError('timed out')))
        out = self.ok('next')
        self.assertIn('releasable: hub not read (TimeoutError: timed out); Q-/T- holds not '
                      'checked', out)
        self.assertNotIn('Traceback', out)
        self.assertNotIn('RELEASABLE', out)
        self.assertIn('NEXT (1 offered)', out)
        self.assertIn('BBB', out)
        self.assertIn('| 0 releasable |', out)

    def ledger(self, text):
        from tests import home
        path = os.path.join(self.home, 'rulings.md')
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        home.machine_config(self.home, {'ledger_path': path}, case=self)
        # the tool runs under self.env alone: give it the PM-repo pointer machine_config set
        self.env[home.PM_REPO_FENCE_ENV] = os.environ[home.PM_REPO_FENCE_ENV]

    def test_a_stale_deliberate_hold_is_flagged_in_next_all_and_list_blocked(self):
        def held(rid, reason):
            return record_text(id=rid, priority='2', priority_by='agent', state='blocked',
                               blocked_on='deliberate', scope='a/%s.py' % rid.lower(),
                               body='Blocked on deliberate (2026-11-10): %s' % reason)
        self.publish({'DONE': held('DONE', 'Q-2289: the WSL runtime choice'),
                      'OPEN': held('OPEN', 'Q-2290: still asked'),
                      'CMB': held('CMB', 'R-9197: widget waits'),
                      'BBB': record_text(id='BBB', priority='3', priority_by='agent',
                                         scope='a/bbb.py')})
        self.hub(_Hub({'questions': [
            {'id': 'Q-2289', 'status': 'done', 'done_at': '2026-11-12T10:06:00+00:00'},
            {'id': 'Q-2290', 'status': 'open'}]}))
        self.ledger(LEDGER)
        out = self.ok('next', '--all')
        self.assertIn('STALE HOLD DONE (rank 2): stale hold: Q-2289 ended (done ', out)
        self.assertIn('STALE HOLD CMB (rank 1): stale hold: R-9197 ended (superseded by R-9204) '
                      '-> unblock CMB --on deliberate, or block it again citing what holds it '
                      'now', out)
        self.assertNotIn('STALE HOLD OPEN', out)
        self.assertIn(' | 2 stale', out.splitlines()[-1])
        rows = {line.split()[1]: line for line in self.ok('list', '--blocked').splitlines()
                if line[:4].strip().isdigit() or line.startswith('   ')}
        self.assertIn('[stale hold: Q-2289 ended (done ', rows['DONE'])
        self.assertTrue(rows['CMB'].endswith('[deliberate]  [stale hold: R-9197 ended '
                                             '(superseded by R-9204)]'), rows['CMB'])
        self.assertTrue(rows['OPEN'].endswith('[deliberate]'), rows['OPEN'])

    def test_next_under_a_session_judges_only_its_held_records(self):
        def held(rid, session):
            return record_text(id=rid, priority='2', priority_by='agent', state='blocked',
                               blocked_on='deliberate', scope='a/%s.py' % rid.lower(),
                               session=session,
                               body='Blocked on deliberate (2026-11-10): Q-2289 first')
        self.publish({'MINE': held('MINE', 'kit'), 'THEIRS': held('THEIRS', 'other')})
        self.hub(_Hub({'questions': [
            {'id': 'Q-2289', 'status': 'done', 'done_at': '2026-11-12T10:06:00+00:00'}]}))
        out = self.ok('--session', 'kit', 'next')
        self.assertIn('STALE HOLD MINE ', out)
        self.assertNotIn('STALE HOLD THEIRS', out)
        self.assertIn(' | 1 stale', out.splitlines()[-1])

    def test_on_the_hold_day_the_block_commit_decides(self):
        # the store's own block commit is the hold time: an answer after it ends the hold, one
        # before it is the ground the hold rests on; a later block on deliberate the same day
        # moves the hold time past the answer (the known limit: unflagged)
        self.publish({rid: record_text(id=rid, priority='2', priority_by='agent',
                                       scope='a/%s.py' % rid.lower()) for rid in ('AFT', 'BEF')})
        self.ledger(LEDGER)
        self.ok('block', 'AFT', '--on', 'deliberate', '--reason', 'Q-0300: wait for it')
        self.ok('block', 'BEF', '--on', 'deliberate', '--reason', 'Q-0301: deferred by it')
        now = datetime.datetime.now().astimezone()
        self.hub(_Hub({'questions': [
            {'id': 'Q-0300', 'status': 'answered',
             'answer': {'at': (now + datetime.timedelta(seconds=90)).isoformat()}},
            {'id': 'Q-0301', 'status': 'answered',
             'answer': {'at': (now - datetime.timedelta(hours=1)).isoformat()}}]}))
        out = self.ok('next')
        self.assertIn('STALE HOLD AFT ', out)
        self.assertNotIn('STALE HOLD BEF', out)

    def live_pull(self, worktree=True, dirty=None):
        """HOLD (broad `brain/**`) live on kit/holder, which committed brain/a.py; `worktree`
        checks the branch out (in a temp folder) and `dirty` writes an uncommitted file there."""
        self.repo.git('checkout', '-q', '-b', 'kit/holder')
        self.repo.write('brain/a.py', 'x\n')
        self.repo.git('add', '-A')
        self.repo.git('commit', '-q', '-m', 'work on kit/holder')
        self.repo.git('checkout', '-q', '-f', 'main')
        if worktree:
            wt = os.path.join(tempfile.mkdtemp(), 'holder')
            self.repo.git('worktree', 'add', '-q', wt, 'kit/holder')
            self.addCleanup(shutil.rmtree, os.path.dirname(wt), True)
            if dirty:
                os.makedirs(os.path.join(wt, 'brain'), exist_ok=True)
                with open(os.path.join(wt, dirty), 'w') as f:
                    f.write('wip\n')
        self.publish({
            'HOLD': record_text(id='HOLD', priority='2', priority_by='agent', state='pulled',
                                pulled_at=TODAY, pulled_by='kit/holder', scope='brain/**'),
            'FOO': record_text(id='FOO', priority='3', priority_by='agent', scope='brain/b.py')})

    def test_a_broad_live_pull_gets_a_narrow_line_from_its_branch_diff(self):
        self.live_pull()
        out = self.ok('next')
        self.assertIn('passed over FOO', out)
        self.assertIn('NARROW FOO (rank 2): brain/b.py collides with brain/** of HOLD on '
                      'kit/holder, which changed 1 file(s) there: brain/a.py -> `set HOLD '
                      '--scope brain/a.py`', out)
        self.assertTrue(out.rstrip('\n').endswith(
            'counts: 0 offered | 1 pullable | 1 passed over (1 scope) | 0 releasable | 1 narrow'),
            out)

    def test_the_narrow_set_keeps_an_uncommitted_file_of_the_live_pull(self):
        self.live_pull(dirty='brain/wip.py')
        out = self.ok('next')
        self.assertIn('which changed 2 file(s) there: brain/a.py, brain/wip.py -> `set HOLD '
                      '--scope brain/a.py --scope brain/wip.py`', out)

    def test_no_set_when_the_live_pulls_worktree_cannot_be_read(self):
        self.live_pull(worktree=False)
        out = self.ok('next')
        self.assertIn('NARROW FOO', out)
        self.assertNotIn('`set HOLD', out)
        self.assertIn('not narrowing HOLD', out)


if __name__ == '__main__':
    unittest.main()
