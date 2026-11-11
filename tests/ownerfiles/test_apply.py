"""The hub applies an approved change (kitlib.ownerfiles.apply, docs/hub-approvals.md "Applying"):
the owner's approve on a real kitlib.hubsite.api commits the approved files on the branch the
request named, in its linked worktree, and consumes the approval with that commit; what cannot be
applied stays approved with the reason; a failure after the branch moved puts everything back."""
import datetime
import os
import unittest
from unittest import mock

from kitlib import config as kconfig
from kitlib.hubsite.api import AGENT, BASE
from kitlib.ownerfiles import apply as consumer
from kitlib.ownerfiles import approvals as ap
from kitlib.ownerfiles import rules as oc
from tests.gitfixture import TempRepo
from tests.home import temp_home
from tests.ownerfiles.test_approvals_add import KEY, NOW, URL, Hub, own

BRANCH = 'kit/w'
norm = lambda p: os.path.normcase(os.path.normpath(p))      # noqa: E731
PATH = 'docs/a.txt'


class Case(unittest.TestCase):
    """A checkout of URL whose `docs/a.txt` is owner-only, a linked worktree on kit/w, and a hub
    holding a pending request to change the file there."""

    def setUp(self):
        temp_home(self)
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.repo.git('remote', 'add', 'origin', URL)
        self.repo.write(PATH, 'one\n')
        self.head = self.repo.commit('a file', PATH)
        own(self.repo.root)
        self.wt = self.repo.worktree('w')
        own(self.wt)
        self.hub = Hub(self, self.repo.root)
        status, obj = self.hub.api.call('POST', BASE + '/approvals', AGENT, {
            'repo': URL, 'title': 'Change a', 'head': self.head, 'branch': BRANCH,
            'files': [{'path': PATH, 'base_blob': ap.blob_id('one\n'), 'content': 'two\n'}]},
            None)
        self.assertEqual(status, 200, obj)
        self.did = obj['id']

    def doc(self):
        return self.hub.approvals()[self.did]

    def tip(self):
        return self.repo.git('rev-parse', 'refs/heads/' + BRANCH)

    def write(self, data):
        with open(os.path.join(self.wt, *PATH.split('/')), 'wb') as f:
            f.write(data)

    def read(self):
        with open(os.path.join(self.wt, *PATH.split('/')), 'rb') as f:
            return f.read()


class Approve(Case):

    def test_approve_commits_in_named_worktree(self):
        self.hub.decide(self.did)
        doc, tip = self.doc(), self.tip()
        self.assertEqual((doc['status'], doc['commit']), (ap.CONSUMED, tip))
        self.assertEqual(norm(doc['applied']['worktree']), norm(self.wt))
        self.assertEqual(self.repo.git('rev-parse', tip + '^'), self.head)
        self.assertIn('Owner-Approval: hub:%s' % self.did,
                      self.repo.git('log', '-1', '--format=%B', tip))
        self.assertEqual(self.read(), b'two\n')
        self.assertEqual(self.repo.git('-C', self.wt, 'status', '--porcelain', '--', PATH), '')

    def test_commit_passes_owner_rules(self):
        self.hub.decide(self.did)
        rules = kconfig.load(self.wt).owner_rules()
        bad, why = oc.violations(self.wt, rules, self.tip(), (self.head,),
                                 approvals=oc.HubApprovals(client=self.hub.client))
        self.assertEqual((bad, why), ([], None))
        self.assertEqual(self.repo.git('log', '-1', '--format=%ae', self.tip()),
                         consumer.AUTHOR[1])
        self.assertFalse(rules.is_owner(consumer.AUTHOR[1]))

    def test_unappliable_stays_approved_with_reason(self):
        self.write(b'drift\n')
        self.hub.decide(self.did)
        doc = self.doc()
        self.assertEqual(doc['status'], ap.APPROVED)
        self.assertNotIn('commit', doc)
        self.assertIn("is not HEAD's copy", doc['apply']['error'])
        self.assertEqual((self.tip(), self.read()), (self.head, b'drift\n'))


class PutBack(Case):

    def setUp(self):
        super(PutBack, self).setUp()
        self.write(b'drift\n')
        self.hub.decide(self.did)          # unappliable: it stays approved
        self.write(b'one\n')
        self.assertEqual(self.doc()['status'], ap.APPROVED)

    def test_put_back_on_refusal(self):
        now = datetime.datetime.fromisoformat(NOW)
        with mock.patch.object(consumer, '_set_index',
                               side_effect=['index.lock is held', None]) as set_index:
            done, why = consumer.apply(self.wt, BRANCH, self.did, self.doc(), KEY, now)
        self.assertIsNone(done)
        self.assertIn('index.lock is held', why)
        self.assertIn('as they were', why)
        self.assertEqual(set_index.call_count, 2)
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))

    def test_a_probe_refusal_writes_nothing(self):
        now = datetime.datetime.fromisoformat(NOW)
        held = lambda root, paths: 'needs %s, held by L-ONE on kit/l' % paths[0]  # noqa: E731
        done, why = consumer.apply(self.wt, BRANCH, self.did, self.doc(), KEY, now, checks=(held,))
        self.assertIsNone(done)
        self.assertEqual(why, 'needs %s, held by L-ONE on kit/l' % PATH)
        self.assertEqual((self.tip(), self.read()), (self.head, b'one\n'))
        self.assertEqual(self.repo.git('-C', self.wt, 'status', '--porcelain', '-uno'), '')


class Where(unittest.TestCase):

    def test_branch_not_a_linked_worktree_refuses(self):
        repo = TempRepo()
        self.addCleanup(repo.cleanup)
        repo.worktree('w')
        self.assertIn("main checkout's branch", consumer.worktree_for(repo.root, 'main')[1])
        self.assertIn('no linked worktree', consumer.worktree_for(repo.root, 'kit/x')[1])
        self.assertIsNone(consumer.worktree_for(repo.root, BRANCH)[1])


if __name__ == '__main__':
    unittest.main()
