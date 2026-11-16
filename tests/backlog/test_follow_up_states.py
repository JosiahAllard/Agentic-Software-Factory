"""kitlib.backlog.opener.follow_up_states, the read tools/verdict.py add makes for --follow-up:
from the main checkout of a repository whose PM data worktree names the live store, the records
come from that store (store_live.live_store_root), not the main checkout's dead default ref, on a
real two-store layout (tests/backlog/test_worktree_live_store.py's). Each id also says whether the
landing of the queued branch would close it (kitlib.backlog.landable.follow_up_closes): a record
the branch holds pulled, in a store a landing closes in, with no Part-of line in its commits above
main or a Closes line naming it (at_landing.close_landed)."""
import json
import os
import unittest
from unittest import mock

from kitlib.config import pmdata
from kitlib.backlog import landable
from kitlib.backlog import opener
from kitlib.backlog import record as rmod
from kitlib.backlog import store as smod
from kitlib.backlog import store_ref

from tests.backlog.helpers import TODAY, priced
from tests.gitfixture import TempRepo, fixture_env
from tests.home import temp_home

STORE = 'kit/backlog'
BRANCH = 'kit/x'


class FollowUpStates(unittest.TestCase):

    def setUp(self):
        patch = mock.patch.dict(os.environ, fixture_env(), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.home = temp_home(self)
        self.repo = TempRepo(bare_remote=True)
        self.addCleanup(self.repo.cleanup)
        self.main = self.repo.head()
        pmdata.write(self.repo.common_dir(), 'pmdata', 'private', self.main)
        self.pm = self.repo.worktree('pm', branch='pmdata')
        self.repo.write('.kit.json', json.dumps({'backlog': {'store': STORE}}) + '\n', root=self.pm)
        paths = ['.kit.json']
        held = {'pulled_at': TODAY, 'pulled_by': BRANCH}
        for rid, state, extra in (('OPEN', 'open', {}), ('PARKED', 'parked', {}),
                                  ('PULLED', 'pulled', held),
                                  ('LIVE', 'pulled', dict(held, requires='live-run')),
                                  ('OTHERS', 'pulled', dict(held, pulled_by='kit/other'))):
            self.repo.write(STORE + '/%s.md' % rid, priced(rid, state=state, **extra)[rid],
                            root=self.pm)
            paths.append(STORE + '/%s.md' % rid)
        self.repo.commit('the live store', *paths, cwd=self.pm)
        self.wt = self.repo.worktree('x', branch=BRANCH)

    def tip_with(self, message):
        """A commit on BRANCH with `message`: its sha."""
        n = len(os.listdir(self.wt))
        self.repo.write('f%d.txt' % n, 'work\n', root=self.wt)
        return self.repo.commit(message, 'f%d.txt' % n, cwd=self.wt)

    def states(self, ids, tip, base='main'):
        return opener.follow_up_states(self.repo.root, ids, BRANCH, tip,
                                       self.main if base == 'main' else base)

    def test_the_main_checkout_reads_the_live_store(self):
        tip = self.tip_with('work')
        got, why = self.states(['OPEN', 'PARKED', 'PULLED', 'GONE'], tip)
        self.assertIsNone(why)
        self.assertEqual(got, {'OPEN': ('open', True, False), 'PARKED': ('parked', True, False),
                               'PULLED': ('pulled', True, True), 'GONE': (None, False, False)})
        self.assertTrue(all(s in rmod.STATES for s, ok, _ in got.values() if ok))

    def test_a_closed_record_is_not_open(self):
        """A closed or retired record leaves the store: it reads as none of that id."""
        os.remove(os.path.join(self.pm, STORE, 'OPEN.md'))
        self.repo.commit('close OPEN', STORE + '/OPEN.md', cwd=self.pm)
        got, why = self.states(['OPEN'], self.tip_with('work'))
        self.assertEqual((got, why), ({'OPEN': (None, False, False)}, None))

    def test_the_landing_closes_a_held_record_as_close_landed_decides(self):
        """No Part-of line, or a Closes line naming it: closed. A Part-of line naming it or
        another record, with no Closes line naming it: released, so it stays a follow-up."""
        cases = [('work', True),
                 ('part one\n\nPart-of: PULLED', False),
                 ('part one\n\nPart-of: OPEN', False),
                 ('all of it\n\nPart-of: PULLED\nCloses: PULLED', True)]
        for message, closes in cases:
            got, why = self.states(['PULLED'], self.tip_with(message))
            self.assertIsNone(why, message)
            self.assertEqual(got['PULLED'], ('pulled', True, closes), message)
            self.repo.git('reset', '-q', '--hard', self.main, cwd=self.wt)

    def test_only_the_reviewed_tip_is_read(self):
        reviewed = self.tip_with('work')
        self.tip_with('later\n\nPart-of: PULLED')
        got, _ = self.states(['PULLED'], reviewed)
        self.assertEqual(got['PULLED'], ('pulled', True, True))

    def test_a_live_proof_record_with_no_live_pass_line_is_released_not_closed(self):
        got, why = self.states(['LIVE'], self.tip_with('work'))
        self.assertEqual((got, why), ({'LIVE': ('pulled', True, False)}, None))
        got, _ = self.states(['LIVE'], self.tip_with('proved\n\nLive-pass: LIVE run-7 passed'))
        self.assertEqual(got['LIVE'], ('pulled', True, True))

    def test_another_branchs_pull_is_not_closed(self):
        got, why = self.states(['OTHERS'], self.tip_with('work'))
        self.assertEqual((got, why), ({'OTHERS': ('pulled', True, False)}, None))

    def test_main_unread_is_no_answer_only_when_a_follow_up_is_held(self):
        tip = self.tip_with('work')
        got, why = self.states(['OPEN', 'OTHERS'], tip, base=None)
        self.assertEqual((got, why), ({'OPEN': ('open', True, False),
                                       'OTHERS': ('pulled', True, False)}, None))
        got, why = self.states(['OPEN', 'PULLED'], tip, base=None)
        self.assertIsNone(got)
        self.assertIn('main as fetched cannot be read', why)

    def test_lines_git_cannot_read_are_no_answer_never_no_close(self):
        got, why = self.states(['PULLED'], self.tip_with('work'), base='0' * 40)
        self.assertIsNone(got)
        self.assertIn('Part-of', why)

    def test_a_pm_data_worktree_that_cannot_be_read_gives_why(self):
        with mock.patch.object(pmdata, 'read', side_effect=pmdata.PmDataError('bad json')):
            got, why = self.states(['OPEN'], self.main)
        self.assertIsNone(got)
        self.assertIn('bad json', why)

    def test_the_implicit_default_store_closes_nothing(self):
        """A landing only reads it (at_landing._read_default), so a pull held there never closes
        and no git is read."""
        implicit = store_ref.RefBackend(self.repo.root, 'origin', smod.STORE_REF, smod.STORE_REF,
                                        implicit=True)
        rec = rmod.parse('PULLED.md', priced('PULLED', state='pulled', pulled_at=TODAY,
                                             pulled_by=BRANCH)['PULLED'], 'PULLED')
        with mock.patch.object(landable, '_landing_acts') as acts:
            got = landable.follow_up_closes(implicit, [rec], self.repo.root, BRANCH,
                                            self.main, None)
        self.assertEqual(got, ((), None))
        acts.assert_not_called()


if __name__ == '__main__':
    unittest.main()
