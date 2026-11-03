"""kitlib.hubsite.api: a question bound to a record parks it (park_question: the record waits on
the owner until the question is answered), off the request when the server says so; its
withdrawal, deletion or rebinding undoes it (unpark_question); POST questions/file parks what
a failure or a stop left unparked; a class-only question whose refs name a pulled record is
stored with a warning."""
import unittest

from kitlib.hubsite import api
from kitlib.hubsite.api import AGENT, OWNER
from kitlib.hubsite.store import HubError
from tests.hubsite.test_api import CLOCK, ApiCase, BindViews

BIND = {'repo': 'kit', 'id': 'EX-1'}


class ParkViews(BindViews):
    """BindViews with the two park views kept as calls, `fail` raised by record_park when set,
    and a backlog in which EX-9 is pulled."""

    def __init__(self):
        BindViews.__init__(self)
        self.parks, self.unparks, self.fail = [], [], None

    def record_park(self, repo, id, qid, cond, asker):
        self.parks.append((repo, id, qid, cond))
        if self.fail is not None:
            raise self.fail
        return {'lines': [], 'park': 'blocked'}

    def record_unpark(self, repo, id, cond):
        self.unparks.append((repo, id, cond))
        return {'lines': [], 'dropped': True, 'unblocked': True}

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


class FiledAs(unittest.TestCase):

    def test_an_answer_that_brings_a_stopped_pull_back_is_pulled(self):
        self.assertEqual(api._filed_as('blocked', 'pulled'), 'pulled')
        self.assertEqual(api._filed_as('blocked', 'open'), 'freed')
        self.assertEqual(api._filed_as('blocked', 'blocked'), 'held')


if __name__ == '__main__':
    unittest.main()
