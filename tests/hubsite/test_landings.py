"""kitlib.hubsite.landings and the api's landings routes (docs/hub-approvals.md "landings/L-NNN"):
an event is the land ledger line's fields and the extras, posted best-effort; the hub keeps it, a
post it does not take is one WARNING and never an exception."""
import json
import os
import shutil
import socket
import tempfile
import unittest
import urllib.parse
from unittest import mock

from kitlib.gate import result
from kitlib.hubsite import api, auth, client, landings, schema, serve
from kitlib.hubsite.api import AGENT, BASE, OWNER
from kitlib.hubsite.store import Store

NOW = '2026-01-02T03:04:05+00:00'
CLOCK = lambda: NOW      # noqa: E731
TIP, MAIN = 'a' * 40, 'b' * 40
RECORD = {'branch': 'kit/x', 'tip': TIP, 'run_id': 'r1', 'started_at': NOW, 'finished_at': NOW,
          'verdict': 'pass', 'code': 0, 'summary': 'landed', 'gate_once': True,
          'escalated': False, 'kind': 'land', 'pid': 7, 'merge': MAIN, 'lines': ['x']}


def event(**kw):
    ev = landings.event(RECORD, 'kit', 'main', MAIN, records=['ONE'], mode='full')
    ev.update(kw)
    return ev


class Raises(object):
    """A client whose post raises `error`."""

    def __init__(self, error):
        self.error, self.posted = error, []

    def post(self, path, role, body=None):
        self.posted.append((path, role, body))
        raise self.error


class EventTest(unittest.TestCase):
    def test_an_event_is_the_ledger_lines_fields_and_the_extras(self):
        ev = event()
        self.assertEqual({k: ev[k] for k in result.LEDGER_FIELDS},
                         {k: RECORD[k] for k in result.LEDGER_FIELDS})
        # approvals only when the landing put one on main: a hub older than the field still
        # takes every other event
        self.assertEqual(sorted(ev), sorted(f for f in landings.FIELDS if f != 'approvals'))
        for k in ('kind', 'pid', 'merge', 'lines'):       # the record's other fields stay home
            self.assertNotIn(k, ev)
        self.assertEqual((ev['repo'], ev['main_branch'], ev['main'], ev['records'], ev['mode'],
                          ev['deploy']), ('kit', 'main', MAIN, ['ONE'], 'full', None))
        self.assertEqual(landings.event({}, 'kit', 'main', MAIN)['records'], [])
        self.assertNotIn('approvals', landings.event({}, 'kit', 'main', MAIN, approvals={}))
        self.assertEqual(landings.event({}, 'kit', 'main', MAIN,
                                        approvals={'A-001': TIP})['approvals'], {'A-001': TIP})

    def test_the_api_takes_the_fields_an_event_has_and_the_schema_types_them(self):
        self.assertEqual(set(landings.FIELDS), set(api.LANDING_FIELDS))
        self.assertEqual(set(schema.FIELDS['landings'][1]), set(landings.FIELDS) | {'posted_at'})
        self.assertEqual(schema.check('landings', event(deploy='restart')), [])
        self.assertEqual(schema.check('landings', event(approvals={'A-001': TIP})), [])
        self.assertEqual([f.field for f in schema.check('landings', event(code='0', records='x'))],
                         ['code', 'records'])
        self.assertEqual(json.loads(json.dumps(event())), event())

    def test_the_deploy_note_is_the_deploy_trailers_joined(self):
        self.assertIsNone(landings.deploy_note('subject\n\nbody\n'))
        self.assertIsNone(landings.deploy_note(None))
        self.assertEqual(landings.deploy_note('s\n\nb\n\nDeploy: restart the hub\n'),
                         'restart the hub')
        self.assertEqual(landings.deploy_note('s\n\nDeploy: one\nDeploy: two\nCloses: X\n'),
                         'one; two')


class PostTest(unittest.TestCase):
    def assertWarned(self, lines, why):
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith('WARNING: the landing of kit/x was not posted'), lines)
        self.assertIn(why, lines[0])
        self.assertTrue(lines[0].endswith('the landing stands'), lines)

    def test_each_failure_is_one_warning_and_nothing_raises(self):
        cases = [(serve.NotRunning(8790), 'no hub is running'),
                 (client.Refused(404, {'ok': False, 'error': {'code': 'not_found'}}),
                  'no /landings route'),
                 (serve.NoAnswer('x'), 'did not answer in 3s'),
                 (serve.ServeError('no agent token in C:\\tokens'), 'ServeError: no agent token'),
                 (client.Refused(400, {'ok': False, 'error': {'code': 'invalid'}}), 'Refused'),
                 (ValueError('bad\nline'), 'ValueError: bad line'),
                 (KeyboardInterrupt(), 'Ctrl+C')]
        for error, why in cases:
            with self.subTest(error=type(error).__name__):
                hub = Raises(error)
                self.assertWarned(landings.post([event()], client=hub), why)
                self.assertEqual(hub.posted, [('/landings', AGENT, event())])

    def test_a_client_that_cannot_be_made_drops_the_batch_with_one_warning(self):
        with mock.patch.object(landings, 'default_client', side_effect=OSError('no config')):
            lines = landings.post([event(), event(branch='kit/y')])
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('the landing of kit/x, kit/y was not posted', lines[0])
        self.assertIn('OSError: no config', lines[0])

    def test_no_hub_running_skips_the_rest_of_the_batch_with_one_warning(self):
        hub = Raises(serve.NotRunning(8790))
        evs = [event(), event(branch='kit/y'), event(branch='kit/z')]
        lines = landings.post(evs, client=hub)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('the landing of kit/x, kit/y, kit/z was not posted', lines[0])
        self.assertIn('no hub is running', lines[0])
        self.assertEqual(len(hub.posted), 1, 'nothing more is tried')

    def test_a_failure_that_is_not_the_hubs_absence_still_tries_the_rest(self):
        for error in (client.Refused(400, {'ok': False, 'error': {'code': 'invalid'}}),
                      serve.NoAnswer('x'), ValueError('bad')):
            with self.subTest(error=type(error).__name__):
                hub = Raises(error)
                lines = landings.post([event(), event(branch='kit/y')], client=hub)
                self.assertEqual(len(lines), 2, lines)
                self.assertEqual(len(hub.posted), 2)

    def test_no_events_post_nothing(self):
        hub = Raises(AssertionError('posted'))
        self.assertEqual(landings.post([], client=hub), [])
        self.assertEqual(hub.posted, [])


def tokens(test):
    folder = tempfile.mkdtemp(prefix='kit-landings-tokens-')
    test.addCleanup(shutil.rmtree, folder, True)
    auth.ensure_tokens(folder)
    return folder


class RealTransportTest(unittest.TestCase):
    """serve.ask over a real loopback socket (port 0): a hub that does not answer, and none."""

    def test_a_hub_that_takes_the_connection_and_never_answers_is_a_warning_in_its_time(self):
        lsock = socket.socket()
        self.addCleanup(lsock.close)
        lsock.bind(('127.0.0.1', 0))
        lsock.listen(4)                 # the connection is queued and never answered
        hub = client.Client(lsock.getsockname()[1], tokens=tokens(self), timeout=0.3)
        lines = landings.post([event()], client=hub)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('did not answer in 0.3s', lines[0])

    def test_no_agent_token_drops_the_batch_with_one_warning_and_asks_nothing(self):
        empty = tempfile.mkdtemp(prefix='kit-landings-notokens-')
        self.addCleanup(shutil.rmtree, empty, True)
        asked = []
        hub = client.Client(1, tokens=empty, timeout=0.3, ask=lambda *a, **k: asked.append(a))
        lines = landings.post([event(), event(branch='kit/y')], client=hub)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('the landing of kit/x, kit/y was not posted', lines[0])
        self.assertIn('ServeError: no agent token', lines[0])
        self.assertEqual(asked, [])


class Hub(object):
    """A real api over a temp store, and a client.Client whose transport calls it in process."""

    def __init__(self, test):
        folder = tempfile.mkdtemp(prefix='kit-landings-')
        test.addCleanup(shutil.rmtree, folder, True)
        self.store = Store.open(os.path.join(folder, 'store'), clock=CLOCK)
        test.addCleanup(self.store.close)
        toks = tokens(test)
        roles = {auth.read_token(toks, AGENT): AGENT, auth.read_token(toks, OWNER): OWNER}
        # the configured repositories by remote: the event names the repo, an approval its remote
        names = {'github.com/o/kit': 'kit', 'github.com/o/other': 'other'}
        self.api = api.Api(self.store, clock=CLOCK, repo_name=names.get)

        def ask(port, method, path, token=None, body=None, timeout=None):
            parts = urllib.parse.urlsplit(path)
            query = dict(urllib.parse.parse_qsl(parts.query)) or None
            return self.api.call(method, parts.path, roles[token], body, query)
        self.client = client.Client(0, tokens=toks, ask=ask)


class RoundTripTest(unittest.TestCase):
    def setUp(self):
        self.hub = Hub(self)

    def call(self, method, path, role, body=None, query=None):
        status, obj = self.hub.api.call(method, BASE + path, role, body, query)
        json.dumps(obj)
        return status, obj

    def test_a_posted_event_is_stored_as_sent_with_posted_at_and_read_newest_first(self):
        evs = [event(), event(branch='kit/y', records=[], deploy='restart the hub',
                              approvals={'A-001': TIP}),
               event(branch='kit/z', repo='other')]
        self.assertEqual(landings.post(evs, client=self.hub.client), [])
        docs = self.hub.store.docs('landings')
        self.assertEqual(sorted(docs), ['L-001', 'L-002', 'L-003'])
        for doc_id, ev in zip(('L-001', 'L-002', 'L-003'), evs):
            # an event without approvals stores none (the field is optional, landings.event)
            self.assertEqual({k: docs[doc_id].get(k) for k in landings.FIELDS
                              if k in ev or k in docs[doc_id]}, ev)
            self.assertEqual(docs[doc_id]['posted_at'], NOW)
        got = self.hub.client.listing('landings')
        self.assertEqual([d['id'] for d in got], ['L-003', 'L-002', 'L-001'])
        self.assertEqual([d['id'] for d in self.hub.client.listing('landings', repo='KIT')],
                         ['L-002', 'L-001'])
        status, obj = self.call('GET', '/landings', OWNER, query={'limit': '2'})
        self.assertEqual([d['id'] for d in obj['items']], ['L-003', 'L-002'])
        # a landing is a feed, never an item that waits on anyone
        self.assertEqual(self.call('GET', '/pending', AGENT)[1]['items'], [])

    def test_ids_sort_by_number_and_the_snapshot_and_list_carry_the_newest_feed_max(self):
        with mock.patch.object(api, 'FEED_MAX', 3):
            for i in range(11):
                self.assertEqual(landings.post([event(branch='kit/b%d' % i)],
                                               client=self.hub.client), [])
            status, obj = self.call('GET', '/snapshot', OWNER)
            self.assertEqual(sorted(obj['collections']['landings'], key=api._natural),
                             ['L-009', 'L-010', 'L-011'])
            status, obj = self.call('GET', '/landings', AGENT)
            self.assertEqual([d['id'] for d in obj['items']], ['L-011', 'L-010', 'L-009'])
        self.assertEqual(len(self.hub.store.docs('landings')), 11, 'every event is kept')

    def test_what_the_route_refuses(self):
        cases = [({'branch': 'kit/x', 'tip': TIP, 'main': MAIN}, 'repo'),
                 (dict(event(), extra=1), 'extra'),
                 (dict(event(), posted_at=NOW), 'posted_at'),
                 (dict(event(), records=['']), 'records'),
                 (dict(event(), records='ONE'), 'records'),
                 (dict(event(), code='0'), 'code'),
                 (dict(event(), approvals=['A-001']), 'approvals'),
                 (dict(event(), approvals={'A-001': 'x'}), 'approvals'),
                 (dict(event(), approvals={'A-001': TIP.upper()}), 'approvals'),
                 (dict(event(), approvals={'Q-001': TIP}), 'approvals'),
                 (dict(event(), approvals={'A-1x': TIP}), 'approvals')]
        for body, field in cases:
            with self.subTest(field=field):
                status, obj = self.call('POST', '/landings', AGENT, body)
                self.assertEqual((status, obj['error']['field']), (400, field), obj)
        self.assertEqual(self.call('POST', '/landings', OWNER, event())[0], 403)
        for limit in ('0', 'x', '-1', '1.5', '1' * 10, '9' * 5000, '١'):
            with self.subTest(limit=limit):
                status, obj = self.call('GET', '/landings', OWNER, query={'limit': limit})
                self.assertEqual((status, obj['error']['field']), (400, 'limit'))
        self.assertEqual(self.hub.store.docs('landings'), {})
        status, obj = self.call('GET', '/landings', OWNER, query={'limit': '9' * 9})
        self.assertEqual((status, obj['items']), (200, []))

    def approval(self, doc_id, **kw):
        doc = {'status': 'consumed', 'repo': 'github.com/o/kit', 'title': 't',
               'created_at': NOW, 'head': MAIN, 'files': [], 'commit': TIP}
        doc.update(kw)
        self.hub.store.put('approvals', doc_id, doc)

    def test_an_event_marks_each_approval_it_put_on_main_spent_once(self):
        landed, first = 'c' * 40, 'd' * 40
        self.approval('A-001')
        self.approval('A-002', commit='f' * 40)
        self.approval('A-003', spent=first, spent_at='2025-12-01T00:00:00+00:00')
        self.approval('A-004', kind='op', op='o', line='l', args_sha256='e' * 64)
        ids = ['A-001', 'A-002', 'A-003', 'A-004', 'A-009']
        status, obj = self.call('POST', '/landings', AGENT,
                                event(approvals={i: landed for i in ids}))
        self.assertEqual(status, 200, obj)
        self.assertEqual(obj['spent'], ['A-001', 'A-002'])
        got = self.hub.store.docs('approvals')
        self.assertEqual([(got[i].get('spent'), got[i].get('spent_at')) for i in sorted(got)],
                         [(landed, NOW), (landed, NOW),
                          (first, '2025-12-01T00:00:00+00:00'), (None, None)])
        self.assertEqual([got[i]['status'] for i in sorted(got)], ['consumed'] * 4,
                         'display only')
        self.assertEqual(self.hub.store.docs('landings')['L-001']['approvals'],
                         {i: landed for i in ids})

    def test_only_a_consumed_approval_of_the_events_repo_is_marked(self):
        # an agent token posts the event: it cannot mark an approval it never used, nor one of
        # another repository
        for n, status in enumerate(('pending', 'approved', 'denied', 'expired'), 1):
            self.approval('A-%03d' % n, status=status)
        self.approval('A-005', status='approved', commit=None)
        self.approval('A-006', repo='github.com/o/other')
        self.approval('A-007', repo='github.com/o/unconfigured')
        self.approval('A-008')
        ids = ['A-%03d' % n for n in range(1, 9)]
        status, obj = self.call('POST', '/landings', AGENT,
                                event(approvals={i: TIP for i in ids}))
        self.assertEqual((status, obj['spent']), (200, ['A-008']), obj)
        got = self.hub.store.docs('approvals')
        self.assertEqual([i for i in sorted(got) if got[i].get('spent')], ['A-008'])
        status, obj = self.call('POST', '/landings', AGENT,
                                event(repo='other', approvals={'A-006': TIP, 'A-008': TIP}))
        self.assertEqual((status, obj['spent']), (200, ['A-006']), obj)
        status, obj = self.call('POST', '/landings', AGENT, event())
        self.assertEqual((status, obj['spent']), (200, []))

    def test_an_unknown_repo_is_kept_with_the_stores_warning(self):
        self.hub.store.put('repos', 'kit', {'name': 'kit'})
        status, obj = self.call('POST', '/landings', AGENT, event(repo='ghost'))
        self.assertEqual(status, 200, obj)
        self.assertEqual([w['code'] for w in obj['warnings']], ['repo-alias'])

    def test_a_hub_older_than_the_route_answers_404_and_the_post_warns(self):
        routes = tuple(r for r in api.ROUTES if not r.pattern.endswith('/landings'))
        with mock.patch.object(api, 'ROUTES', routes):
            lines = landings.post([event()], client=self.hub.client)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('no /landings route', lines[0])
        self.assertEqual(self.hub.store.docs('landings'), {})


if __name__ == '__main__':
    unittest.main()
