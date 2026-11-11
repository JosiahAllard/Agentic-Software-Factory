"""kitlib.hubsite.api: a question bound to a record parks it (park_item: the record waits on
the owner until the question is answered), off the request when the server says so; its
withdrawal, deletion or rebinding undoes it (unpark_item); POST questions/file parks what
a failure or a stop left unparked; a class-only question whose refs name a pulled record is
stored with a warning. An agent's to-do and a file approval bound to a record park it the same
way (AskParkTest), until the to-do is done or the approval decided."""
import datetime
import functools
import threading
import unittest

from kitlib.hubsite import api, serve
from kitlib.ownerfiles import approvals
from kitlib.hubsite.api import AGENT, OWNER
from kitlib.hubsite.store import HubError
from tests.gitfixture import TempRepo
from tests.hubsite.test_api import CLOCK, NOW, ApiCase, BindViews

BIND = {'repo': 'kit', 'id': 'EX-1'}


class ParkViews(BindViews):
    """BindViews with the two park views kept as calls, `fail` raised by record_park when set,
    and a backlog in which EX-9 is pulled."""

    def __init__(self):
        BindViews.__init__(self)
        self.parks, self.unparks, self.fail, self.unpark_fail = [], [], None, None
        self.askers = []
        self.during = None          # run inside record_park: a write that races the park
        self.asks = []              # what record_asks answers

    def record_park(self, repo, id, qid, cond, asker):
        self.parks.append((repo, id, qid, cond))
        self.askers.append(asker)
        if self.during is not None:
            self.during()
        if self.fail is not None:
            raise self.fail
        return {'lines': [], 'park': 'blocked'}

    def record_unpark(self, repo, id, cond):
        self.unparks.append((repo, id, cond))
        if self.unpark_fail is not None:
            raise self.unpark_fail
        return {'lines': [], 'dropped': True, 'unblocked': True}

    def record_asks(self):
        return list(self.asks)

    def backlog(self, repo='', state='', fresh=False):
        return [{'repo': 'kit', 'items': [{'id': 'EX-9', 's': 'pulled'},
                                          {'id': 'EX-1', 's': 'open'}]}]


class ParkTest(ApiCase):

    def setUp(self):
        ApiCase.setUp(self)
        self.views = ParkViews()
        self.api = api.Api(self.store, views=self.views, clock=CLOCK)

    def doc(self, qid):
        return self.store.docs('questions')[qid]

    def ask(self, **kw):
        return self.ok('POST', '/questions', AGENT, dict({'title': 't', 'repo': 'kit'}, **kw))

    def test_a_bound_question_parks_its_record_and_marks_the_question(self):
        out = self.ask(record=BIND)
        qid = out['id']
        self.assertEqual(self.views.parks, [('kit', 'EX-1', qid, 'question:%s answered' % qid)])
        self.assertEqual(self.doc(qid)['park'], {'at': CLOCK(), 'code': 'blocked',
                                                 'message': api.PARK_SAYS['blocked']})
        self.assertEqual((out['park']['code'], out['version']), ('blocked', self.doc(qid)['version']))
        self.ok('POST', '/questions/file', AGENT, {})          # parked once: not again
        self.assertEqual(len(self.views.parks), 1)

    def test_park_later_runs_the_park_off_the_request(self):
        later = []
        self.api.park_later = lambda doc_id, fn: later.append((doc_id, fn))
        qid = self.ask(record=BIND)['id']
        self.assertEqual((self.views.parks, [d for d, _ in later]), ([], [qid]))
        later[0][1]()
        self.assertEqual(self.doc(qid)['park']['code'], 'blocked')

    def test_a_late_park_of_a_question_answered_or_withdrawn_meanwhile_does_nothing(self):
        later = []
        self.api.park_later = lambda doc_id, fn: later.append(fn)
        answered = self.ask(record=BIND, options=[{'label': 'a'}])['id']
        gone = self.ask(record=BIND)['id']
        self.ok('POST', '/questions/%s/answer' % answered, OWNER, {'choice': 0})
        self.ok('POST', '/questions/%s/delete' % gone, OWNER, None)
        for fn in later[:2]:
            fn()
        self.assertEqual(self.views.parks, [])

    def test_a_failed_park_is_marked_and_parked_by_questions_file(self):
        self.views.fail = HubError('store', 'C:\\work is busy', '')
        qid = self.ask(record=BIND)['id']
        self.assertEqual((self.doc(qid)['park']['code'], self.doc(qid)['park']['message']),
                         ('store', api.PARK_SAYS['store']))
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(got['park_failed'], [{'id': qid, 'code': 'store',
                                               'message': api.PARK_SAYS['store']}])
        self.views.fail = None
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['parked'], got['park_failed']), ([qid], []))
        self.assertEqual(self.doc(qid)['park']['code'], 'blocked')

    def test_withdrawing_a_bound_question_drops_its_line(self):
        qid = self.ask(record=BIND, **{'from': 'pm'})['id']
        self.ok('POST', '/questions/%s/delete' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version']})
        self.assertEqual(self.views.unparks, [('kit', 'EX-1', 'question:%s answered' % qid)])

    def test_a_rebind_moves_the_park_and_an_unbind_drops_it(self):
        qid = self.ask(record=BIND, **{'from': 'pm'})['id']
        self.ok('POST', '/questions/%s/edit' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version'],
                 'record': {'repo': 'kit', 'id': 'EX-2'}})
        cond = 'question:%s answered' % qid
        self.assertEqual(self.views.unparks, [('kit', 'EX-1', cond)])
        self.assertEqual(self.views.parks, [('kit', 'EX-1', qid, cond), ('kit', 'EX-2', qid, cond)])
        self.assertEqual(self.doc(qid)['park']['code'], 'blocked')
        self.ok('POST', '/questions/%s/edit' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version'], 'record': None,
                 'class': 'go'})
        self.assertEqual(self.views.unparks[-1], ('kit', 'EX-2', cond))
        self.assertIsNone(self.doc(qid)['park'])
        self.ok('POST', '/questions/%s/edit' % qid, AGENT,      # a text edit moves nothing
                {'from': 'pm', 'if_version': self.doc(qid)['version'], 'title': 'u'})
        self.assertEqual((len(self.views.parks), len(self.views.unparks)), (2, 2))

    def line(self, qid, rid='EX-1', parked=True):
        return {'repo': 'kit', 'id': rid, 'q': qid, 'parked': parked}

    def test_a_delete_whose_unpark_failed_is_logged_and_swept_at_the_next_refile(self):
        later = []
        self.api.park_later = lambda doc_id, fn: later.append(fn)
        qid = self.ask(record=BIND, **{'from': 'pm'})['id']
        self.assertIsNone(later[0]())                           # parked
        self.views.unpark_fail = HubError('store', 'C:\\work is busy', '')
        self.ok('POST', '/questions/%s/delete' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version']})
        self.assertEqual(later[1](), 'record not unparked: store')     # serve logs it
        self.views.asks = [self.line(qid)]
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(got['park_failed'], [{'id': qid, 'code': 'store',
                                               'message': api.UNPARK_FAILED}])
        self.views.unpark_fail = None
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['unparked'], got['park_failed']),
                         ([{'id': qid, 'record': BIND, 'unblocked': True}], []))
        self.assertEqual(self.views.unparks[-1], ('kit', 'EX-1', 'question:%s answered' % qid))

    def test_a_rebind_whose_unpark_failed_is_swept_at_the_next_refile(self):
        qid = self.ask(record=BIND, **{'from': 'pm'})['id']
        self.views.unpark_fail = HubError('store', 'C:\\work is busy', '')
        self.ok('POST', '/questions/%s/edit' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version'],
                 'record': {'repo': 'kit', 'id': 'EX-2'}})
        self.views.unpark_fail = None
        self.views.asks = [self.line(qid), self.line(qid, 'EX-2')]
        before = len(self.views.unparks)
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(got['unparked'], [{'id': qid, 'record': BIND, 'unblocked': True}])
        self.assertEqual(self.views.unparks[before:],
                         [('kit', 'EX-1', 'question:%s answered' % qid)])  # EX-2's line stays

    def test_the_server_logs_a_withdraw_that_failed_off_the_request(self):
        lines, done = [], threading.Event()

        def log(*a):
            lines.append(a)
            done.set()
        qid = self.ask(record=BIND, **{'from': 'pm'})['id']
        self.api.park_later = functools.partial(serve._park_later, log)
        self.views.unpark_fail = HubError('store', 'C:\\work is busy', '')
        self.ok('POST', '/questions/%s/delete' % qid, AGENT,
                {'from': 'pm', 'if_version': self.doc(qid)['version']})
        self.assertTrue(done.wait(10))
        self.assertEqual(lines, [('error', '%s: record not unparked: store' % qid)])

    def test_the_sweep_never_drops_a_hand_written_line(self):
        # a line a person wrote is their hold: one on a live question bound elsewhere is left,
        # and one on a missing question is left and listed for the PM to clear by hand
        qid = self.ask(record={'repo': 'kit', 'id': 'EX-2'})['id']
        before = len(self.views.unparks)
        self.views.asks = [self.line(qid, parked=False), self.line('Q-404', parked=False)]
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['unparked'], got['park_failed']), ([], []))
        self.assertEqual(got['orphan_hand_lines'], [{'repo': 'kit', 'id': 'EX-1', 'q': 'Q-404'}])
        self.assertEqual(len(self.views.unparks), before)               # nothing written
        self.views.asks = [self.line('Q-404')]                          # the hub's own line
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(([u['id'] for u in got['unparked']], got['orphan_hand_lines']),
                         (['Q-404'], []))

    def test_the_hub_start_logs_a_hand_line_on_a_missing_question(self):
        said = []

        class Timer(object):
            def start(self):
                pass
        self.views.asks = [self.line('Q-404', parked=False)]
        hub = serve.Hub(None, self.store, self.api, self.views, None, [], None, Timer(), [],
                        log=lambda level, text: said.append((level, text)))
        hub._refile()
        self.assertEqual(said, [('note', 'kit:EX-1: owner line cites missing Q-404')])

    def test_a_park_whose_mark_was_not_written_is_not_listed_parked(self):
        self.views.fail = HubError('store', 'C:\\work is busy', '')
        qid = self.ask(record=BIND)['id']
        self.views.fail = None
        self.views.during = lambda: self.store.delete('questions', qid)    # deleted meanwhile
        self.views.asks = []
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(got['parked'], [])
        self.assertEqual([f['id'] for f in got['park_failed']], [qid])

    def test_a_class_only_question_naming_a_pulled_record_is_warned_not_refused(self):
        out = self.ask(refs=['kit:EX-9', 'EX-1'], **{'class': 'go'})
        warned = [w for w in out['warnings'] if w['code'] == 'refs-pulled-record']
        self.assertEqual([(w['field'], w['level']) for w in warned], [('refs', 'warn')])
        self.assertIn('EX-9', warned[0]['message'])
        self.assertEqual(self.views.parks, [])
        quiet = self.ask(refs=['EX-1'], **{'class': 'go'})
        self.assertEqual([w for w in quiet['warnings'] if w['code'] == 'refs-pulled-record'], [])

    def test_a_server_with_no_park_view_binds_without_parking(self):
        self.api = api.Api(self.store, views=BindViews(), clock=CLOCK)
        out = self.ask(record=BIND)
        self.assertNotIn('park', out)
        self.assertNotIn('park', self.doc(out['id']))


class AskParkTest(ApiCase):
    """An agent's to-do (`todo:T-n done`) and a file approval (`approval:A-n decided`, by pm)
    bound to a record park it; the owner's to-do, a to-do edit and an op approval take no
    record; a withdrawal (or the owner's delete) drops the line, while done and decided are left
    to the unblock-conditions job; an approval expired undecided keeps its line until withdrawn."""

    KEY = 'k' * 64
    URL = 'https://github.com/Owner/Repo.git'
    LATER = (datetime.datetime.fromisoformat(NOW)
             + datetime.timedelta(seconds=approvals.PENDING_TTL_S + 60)).isoformat()

    @classmethod
    def setUpClass(cls):
        cls.repo = TempRepo()
        cls.addClassCleanup(cls.repo.cleanup)

    def setUp(self):
        ApiCase.setUp(self)
        self.views = ParkViews()
        self.now = NOW
        url = approvals.repo_id(self.URL)
        self.api = api.Api(self.store, views=self.views, clock=lambda: self.now,
                           approval_key=lambda: self.KEY,
                           checkout=lambda rid: self.repo.root if rid == url else None)

    def todo(self, **kw):
        return self.ok('POST', '/todos', AGENT,
                       dict({'title': 't', 'commands': ['python t.py'], 'from': 'kit'}, **kw))

    def approval(self, **kw):
        body = {'repo': self.URL, 'title': 'Edit the readme', 'head': self.repo.initial,
                'files': [{'path': 'docs/new.md', 'base_blob': None, 'content': 'fresh\n'}]}
        return self.ok('POST', '/approvals', AGENT, dict(body, **kw))

    def version(self, coll, doc_id):
        return self.store.get(coll, doc_id)['version']

    def test_an_agents_to_do_parks_its_record_until_done(self):
        tid = self.todo(record=BIND)['id']
        self.assertEqual((self.views.parks, self.views.askers),
                         ([('kit', 'EX-1', tid, 'todo:%s done' % tid)], ['kit']))
        d = self.store.get('todos', tid)
        self.assertEqual((d['record'], d['park']['code'], d['park']['message']),
                         (BIND, 'blocked', 'the record waits on this to-do: blocked on the owner '
                                           'until it is done'))
        self.ok('POST', '/todos/%s/done' % tid, OWNER, {'if_version': self.version('todos', tid)})
        self.assertEqual(self.views.unparks, [], "done is the unblock-conditions job's to read")
        self.views.asks = [{'repo': 'kit', 'id': 'EX-1', 'q': tid, 'kind': 'todo', 'parked': True}]
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['unparked'], got['parked'], self.views.unparks), ([], [], []))
        self.assertEqual(len(self.views.parks), 1, 'a done to-do is not parked again')

    def test_a_to_do_bound_to_a_record_not_there_is_refused(self):
        self.refused('not_found', 'record', 'POST', '/todos', AGENT,
                     {'title': 't', 'commands': ['python t.py'], 'from': 'kit',
                      'record': {'repo': 'kit', 'id': 'NO-1'}})
        self.assertEqual(self.views.parks, [])

    def test_the_owners_to_do_an_edit_and_an_op_approval_take_no_record(self):
        self.refused('invalid', 'record', 'POST', '/todos', OWNER, {'title': 't', 'record': BIND})
        tid = self.todo()['id']
        self.refused('invalid', 'record', 'POST', '/todos/%s/edit' % tid, AGENT,
                     {'from': 'kit', 'if_version': self.version('todos', tid), 'title': 'u',
                      'record': BIND})
        self.refused('invalid', 'record', 'POST', '/todos/%s/edit' % tid, OWNER,
                     {'title': 'u', 'record': BIND})
        self.refused('invalid', 'record', 'POST', '/approvals', AGENT,
                     {'kind': 'op', 'op': 'D-001', 'repo': 'pm', 'line': 'python x.py',
                      'title': 'Copy', 'record': BIND})
        self.assertEqual(self.views.parks, [])

    def test_withdrawing_or_the_owner_deleting_a_bound_to_do_drops_its_line(self):
        tid = self.todo(record=BIND)['id']
        self.ok('POST', '/todos/%s/delete' % tid, AGENT,
                {'from': 'kit', 'if_version': self.version('todos', tid)})
        other = self.todo(record=BIND)['id']
        self.ok('POST', '/todos/%s/delete' % other, OWNER, {})
        self.assertEqual(self.views.unparks, [('kit', 'EX-1', 'todo:%s done' % tid),
                                              ('kit', 'EX-1', 'todo:%s done' % other)])

    def test_a_file_approval_parks_its_record_by_pm_until_decided(self):
        aid = self.approval(record=BIND)['id']
        self.assertEqual((self.views.parks, self.views.askers),
                         ([('kit', 'EX-1', aid, 'approval:%s decided' % aid)], ['pm']))
        d = self.store.get('approvals', aid)
        self.assertEqual((d['record'], d['park']['message']),
                         (BIND, 'the record waits on this approval: blocked on the owner until '
                                'it is decided'))
        self.ok('POST', '/approvals/%s/decide' % aid, OWNER,
                {'decision': 'deny', 'if_version': self.version('approvals', aid)}, strong=True)
        self.assertEqual(self.views.unparks, [], "decided is the unblock-conditions job's to read")
        self.refused('conflict', 'status', 'POST', '/approvals/%s/delete' % aid, AGENT,
                     {'from': 'pm', 'if_version': self.version('approvals', aid)})
        self.assertEqual(self.views.unparks, [])

    def test_an_expired_approval_keeps_its_line_until_withdrawn(self):
        aid = self.approval(record=BIND)['id']
        self.now = self.LATER
        self.views.asks = [{'repo': 'kit', 'id': 'EX-1', 'q': aid, 'kind': 'approval',
                            'parked': True}]
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['unparked'], got['parked'], self.views.unparks), ([], [], []))
        self.ok('POST', '/approvals/%s/delete' % aid, AGENT,
                {'from': 'pm', 'if_version': self.version('approvals', aid)})
        self.assertEqual(self.views.unparks, [('kit', 'EX-1', 'approval:%s decided' % aid)])

    def test_questions_file_parks_a_to_do_and_an_approval_whose_park_failed(self):
        self.views.fail = HubError('store', 'the store is busy', '')
        tid = self.todo(record=BIND)['id']
        aid = self.approval(record=BIND)['id']
        self.assertEqual((self.store.get('todos', tid)['park']['code'],
                          self.store.get('approvals', aid)['park']['code']), ('store', 'store'))
        self.views.fail = None
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((sorted(got['parked']), got['park_failed']), (sorted([tid, aid]), []))
        self.assertEqual(self.store.get('todos', tid)['park']['code'], 'blocked')

    def test_the_sweep_drops_the_line_of_a_to_do_gone_without_its_unpark(self):
        tid = self.todo(record=BIND)['id']
        self.store.delete('todos', tid)
        self.views.asks = [{'repo': 'kit', 'id': 'EX-1', 'q': tid, 'kind': 'todo', 'parked': True},
                           {'repo': 'kit', 'id': 'EX-2', 'q': 'T-404', 'kind': 'todo',
                            'parked': False}]
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual(got['unparked'], [{'id': tid, 'record': BIND, 'unblocked': True}])
        self.assertEqual(got['orphan_hand_lines'], [{'repo': 'kit', 'id': 'EX-2', 'q': 'T-404'}])

    def test_a_question_says_what_it_said_before(self):
        self.assertEqual(api.PARK_SAYS['blocked'], 'the record waits on this question: blocked '
                                                   'on the owner until it is answered')
        self.assertEqual(api.UNPARK_FAILED, 'the record still waits on this question, which is '
                                            'gone or bound elsewhere: the hub tries again at its '
                                            'next start or questions file call')


class FiledAs(unittest.TestCase):

    def test_an_answer_that_brings_a_stopped_pull_back_is_pulled(self):
        self.assertEqual(api._filed_as('blocked', 'pulled'), 'pulled')
        self.assertEqual(api._filed_as('blocked', 'open'), 'freed')
        self.assertEqual(api._filed_as('blocked', 'blocked'), 'held')


if __name__ == '__main__':
    unittest.main()
