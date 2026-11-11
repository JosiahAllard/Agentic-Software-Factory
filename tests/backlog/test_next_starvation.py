"""A short offer's causes in `next` (docs/backlog.md "next: a short offer's causes"): a hold whose
cited hub id or `item:` blocker is already met is a RELEASABLE line with its `unblock`; a record
passed over on a broad scope member (`x/**`, a literal `.md`) colliding with a live pull is a
NARROW line naming what that pull's branch actually changed there; a `counts:` line closes it. The
hub is read with a short timeout and any failure is one note line, never a traceback."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.backlog import conditions as cmod
from kitlib.backlog import offer as omod
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod

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
                                          hub=lambda: ({'Q-5': 'answered'}, None))
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
        docs = {'questions': [{'id': 'Q-1', 'status': 'answered'}, 'junk'],
                'todos': [{'id': 'T-2', 'status': 'done'}, {'id': 'T-3'}]}
        self.assertEqual(omod.hub_statuses(listing=docs.get),
                         ({'Q-1': 'answered', 'T-2': 'done'}, None))


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
        backend = smod.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF)
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
