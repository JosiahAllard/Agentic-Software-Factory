"""kitlib.backlog.opener.follow_up_states, the read tools/verdict.py add makes for --follow-up:
from the main checkout of a repository whose PM data worktree names the live store, the records
come from that store (store_live.live_store_root), not the main checkout's dead default ref, on a
real two-store layout (tests/backlog/test_worktree_live_store.py's)."""
import json
import os
import unittest
from unittest import mock

from kitlib.config import pmdata
from kitlib.backlog import opener
from kitlib.backlog import record as rmod

from tests.backlog.helpers import TODAY, priced
from tests.gitfixture import TempRepo, fixture_env
from tests.home import temp_home

STORE = 'kit/backlog'


class FollowUpStates(unittest.TestCase):

    def setUp(self):
        patch = mock.patch.dict(os.environ, fixture_env(), clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.home = temp_home(self)
        self.repo = TempRepo(bare_remote=True)
        self.addCleanup(self.repo.cleanup)
        pmdata.write(self.repo.common_dir(), 'pmdata', 'private', self.repo.head())
        self.pm = self.repo.worktree('pm', branch='pmdata')
        self.repo.write('.kit.json', json.dumps({'backlog': {'store': STORE}}) + '\n', root=self.pm)
        paths = ['.kit.json']
        for rid, state in (('OPEN', 'open'), ('PARKED', 'parked'), ('PULLED', 'pulled')):
            extra = {'pulled_at': TODAY, 'pulled_by': 'kit/x'} if state == 'pulled' else {}
            self.repo.write(STORE + '/%s.md' % rid, priced(rid, state=state, **extra)[rid],
                            root=self.pm)
            paths.append(STORE + '/%s.md' % rid)
        self.repo.commit('the live store', *paths, cwd=self.pm)

    def test_the_main_checkout_reads_the_live_store(self):
        got, why = opener.follow_up_states(self.repo.root, ['OPEN', 'PARKED', 'PULLED', 'GONE'])
        self.assertIsNone(why)
        self.assertEqual(got, {'OPEN': ('open', True), 'PARKED': ('parked', True),
                               'PULLED': ('pulled', True), 'GONE': (None, False)})
        self.assertTrue(all(s in rmod.STATES for s, ok in got.values() if ok))

    def test_a_closed_record_is_not_open(self):
        """A closed or retired record leaves the store: it reads as none of that id."""
        os.remove(os.path.join(self.pm, STORE, 'OPEN.md'))
        self.repo.commit('close OPEN', STORE + '/OPEN.md', cwd=self.pm)
        got, why = opener.follow_up_states(self.repo.root, ['OPEN'])
        self.assertEqual((got, why), ({'OPEN': (None, False)}, None))

    def test_a_pm_data_worktree_that_cannot_be_read_gives_why(self):
        with mock.patch.object(pmdata, 'read', side_effect=pmdata.PmDataError('bad json')):
            got, why = opener.follow_up_states(self.repo.root, ['OPEN'])
        self.assertIsNone(got)
        self.assertIn('bad json', why)


if __name__ == '__main__':
    unittest.main()
