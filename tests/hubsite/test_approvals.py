"""POST /approvals/{id}/supersede and `hub.py approvals supersede`: the PM, or the coordinator of the
approval's repository, marks a decided approval nobody consumed superseded, by another approval or
by a commit on main, so it leaves pending and cannot be used (docs/hub-approvals.md
"approvals/A-NNN"). Beside tests/hubsite/test_approvals_withdraw.py."""
import datetime
import unittest

from kitlib.hubsite import api
from kitlib.ownerfiles import approvals
from kitlib.hubsite.api import AGENT
from tests.gitfixture import TempRepo
from tests.hubsite import test_api, test_hub_cli

URL = 'https://github.com/o/r'
REPO = approvals.repo_id(URL)    # what the document stores
KEY = 'k' * 64
COORD = 'r'                         # the coordinator key of URL's repository (Api.repo_key)
FILES = [{'path': 'x.md', 'base_blob': None, 'content': 'x\n'}]
NOW = datetime.datetime.fromisoformat(test_api.NOW)


def doc(at, status='approved'):
    d = approvals.new_doc(URL, 'a draft', 'a' * 40, FILES, at)
    if status in ('approved', 'denied', 'consumed'):
        d.update(status=status, decision='deny' if status == 'denied' else 'approve',
                 decided_at=at.isoformat(), sig='s' * 64)
    if status == 'consumed':
        d.update(commit='c' * 40, consumed_at=at.isoformat())
    return d


class StatusTest(unittest.TestCase):
    def test_a_superseded_approval_reads_superseded_even_past_its_life(self):
        old = NOW - datetime.timedelta(seconds=approvals.APPROVAL_LIFE_S + 60)
        for at in (NOW, old):
            d = dict(doc(at), superseded={'by': 'A-002', 'from': 'pm', 'at': at.isoformat()})
            self.assertEqual(approvals.status_at(d, NOW), approvals.SUPERSEDED, at)
        self.assertEqual(approvals.status_at(doc(old), NOW), approvals.EXPIRED)

    def test_a_superseded_approval_is_not_pending_and_does_not_verify(self):
        from kitlib.hubsite import store
        d = dict(doc(NOW), superseded={'by': 'A-002', 'from': 'pm', 'at': NOW.isoformat()})
        self.assertIsNone(store.approval_decided(d, NOW))
        self.assertEqual(store.pending_items({'approvals': {'A-001': d}}, NOW), [])
        # superseded is final: a signed approval is neither consumed nor verified after
        d['sig'] = approvals.sign(KEY, 'A-001', d)
        with self.assertRaises(approvals.ApprovalError) as e:
            approvals.consume_fields(KEY, 'A-001', d, 'c' * 40, NOW)
        self.assertEqual(e.exception.field, 'status')
        blobs = {f['path']: f['blob'] for f in d['files']}
        self.assertEqual(approvals.check(KEY, 'A-001', d, REPO, d['head'], blobs, NOW),
                         (False, approvals.WHY_STATUS % approvals.SUPERSEDED))


class SupersedeRouteTest(test_api.ApiCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = TempRepo()
        cls.addClassCleanup(cls.repo.cleanup)
        cls.main = cls.repo.head()
        cls.repo.git('update-ref', 'refs/remotes/origin/main', cls.main)
        cls.repo.write('side.md', 'side\n')
        cls.side = cls.repo.commit('not on main', 'side.md')

    def setUp(self):
        super().setUp()
        self.api = api.Api(self.store, clock=test_api.CLOCK, session_keys=lambda: {COORD},
                           checkout=lambda repo: self.repo.root if repo == REPO else None,
                           repo_name=lambda repo: COORD if repo == REPO else None)
        self.api.coordinators = lambda: {}      # never the machine's own sessions.json

    def seed(self, status='approved', at=None):
        return self.store.add('approvals', doc(at or NOW, status)).id

    def supersede(self, doc_id, by, who='pm', version=1):
        body = {'by': by, 'if_version': version}
        if who is not None:
            body['from'] = who
        return '/approvals/%s/supersede' % doc_id, AGENT, body

    def pending_ids(self):
        return [p.id for p in self.store.pending()]

    def test_superseded_by_an_approval_leaves_pending_and_keeps_the_decision(self):
        first, second = self.seed(), self.seed()
        self.assertIn(first, self.pending_ids())
        before = dict(self.store.get('approvals', first))
        self.ok('POST', *self.supersede(first, second))
        d = self.store.get('approvals', first)
        self.assertEqual(d['superseded'], {'by': second, 'from': 'pm', 'at': test_api.NOW})
        for name in ('status', 'decision', 'decided_at', 'sig'):
            self.assertEqual(d[name], before[name], name)
        self.assertEqual(approvals.status_at(d, NOW), approvals.SUPERSEDED)
        self.assertNotIn(first, self.pending_ids())
        for key in ('pm', COORD):
            self.assertNotIn(first, [p['id'] for p in self.api.pending(key)], key)
        self.assertIn(second, [p['id'] for p in self.api.pending(COORD)])
        self.assertIn(second, self.pending_ids())

    def test_superseded_by_a_commit_on_main_by_the_repos_coordinator(self):
        doc_id = self.seed('denied')
        self.ok('POST', *self.supersede(doc_id, self.main, who=COORD))
        d = self.store.get('approvals', doc_id)
        self.assertEqual(d['superseded']['by'], self.main)
        self.assertEqual(d['superseded']['from'], COORD)
        self.assertNotIn(doc_id, self.pending_ids())

    def test_a_pending_or_consumed_approval_is_refused(self):
        for status in ('pending', 'consumed'):
            doc_id = self.seed(status)
            self.refused('conflict', 'status', 'POST', *self.supersede(doc_id, self.main))
        doc_id = self.seed()
        self.ok('POST', *self.supersede(doc_id, self.main))
        self.refused('conflict', 'status', 'POST', *self.supersede(doc_id, self.main, version=2))

    def test_an_approval_that_does_not_exist_is_refused(self):
        err = self.refused('not_found', 'id', 'POST', *self.supersede('A-099', self.main))
        self.assertIn('approvals/A-099', err['error']['message'])     # the handler's, not no route

    def test_a_by_that_is_not_a_known_approval_or_a_commit_main_reaches_is_refused(self):
        doc_id = self.seed()
        for by in ('A-099', doc_id, self.side, 'f' * 40, 'main', ''):
            self.refused('invalid', 'by', 'POST', *self.supersede(doc_id, by))
        self.assertIn(doc_id, self.pending_ids())

    def test_only_the_pm_or_the_repos_coordinator_may_call_it(self):
        doc_id = self.seed()
        for who in ('someone', 'other-repo', 'you'):
            self.refused('forbidden', 'from', 'POST', *self.supersede(doc_id, self.main, who=who))
        self.refused('invalid', 'from', 'POST', *self.supersede(doc_id, self.main, who=None))
        self.assertIn(doc_id, self.pending_ids())
        # a repo with no coordinator (repo_key ''): an empty or blank from never matches that
        # empty key; the required check refuses it before the caller check runs
        other = self.store.add('approvals', dict(doc(NOW), repo='github.com/o/none')).id
        for who in ('', '  '):
            self.refused('invalid', 'from', 'POST', *self.supersede(other, doc_id, who=who))
        self.assertIn(other, self.pending_ids())
        self.assertIn(other, self.pending_ids())


class SupersedeCoordinatorKeyTest(SupersedeRouteTest):
    """A coordinator keyed differently from its repository's name (the kit's `kit`, whose
    sessions.json repo is the repository's configured name) may supersede; a coordinator of
    another repository, or a session of this one that is not a coordinator, may not."""

    def setUp(self):
        super().setUp()
        sessions = {
            COORD: {'repo': COORD, 'agent': True},              # keyed by the repo's name
            'kit':{'repo': COORD.upper(), 'agent': True},      # matched without case
            'agents': {'repo': COORD, 'agent': 'pm-kit:coordinator'},
            'other-repo': {'repo': 'other-repo', 'agent': True},
            COORD + '-x': {'repo': 'elsewhere', 'agent': True},
            'plain': {'repo': COORD, 'agent': False},
            'brief': {'repo': COORD},
        }
        self.api._session_keys = lambda: set(sessions)
        self.api.coordinators = lambda: {k: v.get('repo') for k, v in sessions.items()
                                         if v.get('agent')}

    def test_a_coordinator_session_of_the_repo_keyed_otherwise_may_supersede(self):
        for who in ('kit', 'agents', COORD, 'pm'):
            doc_id = self.seed()
            self.ok('POST', *self.supersede(doc_id, self.main, who=who))
            self.assertEqual(self.store.get('approvals', doc_id)['superseded']['from'], who)

    def test_another_repos_coordinator_or_a_plain_session_is_refused(self):
        doc_id = self.seed()
        for who in ('other-repo', COORD + '-x', 'plain', 'brief', 'KIT'):
            self.refused('forbidden', 'from', 'POST', *self.supersede(doc_id, self.main, who=who))
        self.assertIn(doc_id, self.pending_ids())

    def test_unreadable_sessions_leave_only_the_pm_and_the_repo_name(self):
        def broken():
            raise OSError('no sessions.json')
        self.api.coordinators = broken
        doc_id = self.seed()
        self.refused('forbidden', 'from', 'POST', *self.supersede(doc_id, self.main, who='kit'))
        self.ok('POST', *self.supersede(doc_id, self.main, who=COORD))


class SupersedeVerbTest(test_hub_cli.VerbCase):
    def setUp(self):
        super().setUp()
        # a minute back: the server's clock is read to the second
        now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
        self.assertEqual(self.hub.store.add('approvals', doc(now)).id, 'A-001')
        self.assertEqual(self.hub.store.add('approvals', doc(now)).id, 'A-002')

    def test_supersede_prints_superseded_and_show_gives_the_note(self):
        err = self.refused('approvals', 'supersede', 'A-001', '--by', 'A-002', '--from', 'x',
                           '--if-version', '1')
        self.assertTrue(err.startswith('REFUSED forbidden: '), err)
        self.assertEqual(self.ok('approvals', 'supersede', 'A-001', '--by', 'A-002', '--from', 'pm',
                                 '--if-version', '1'), ['SUPERSEDED A-001 by A-002'])
        shown = self.ok('approvals', 'show', 'A-001')
        self.assertTrue(shown[0].startswith('A-001 superseded [github.com/o/r] '), shown)
        note = [line for line in shown if line.startswith('superseded by ')]
        self.assertEqual(len(note), 1, shown)
        self.assertTrue(note[0].startswith('superseded by A-002 from pm at '), note)
        self.assertEqual([line.split()[0] for line in self.ok('approvals', '--status', 'superseded')],
                         ['A-001'])


if __name__ == '__main__':
    unittest.main()
