"""kitlib.hubsite.refile: the hub's refile job, which files the owner's answers and parks the
records of bound asks that an earlier try did not (docs/hub-questions.md "Filing the answer"),
and POST questions/file queueing its work there."""
import datetime
import shutil
import tempfile
import threading
import time
from unittest import mock

from kitlib.hubsite import api, jobs, recordwrite, refile, servesources, serveviews
from kitlib.hubsite.api import AGENT, BASE, OWNER
from kitlib.hubsite.store import HubError, Store
from tests.backlog.helpers import rec
from tests.hubsite import test_record_write as trw
from tests.hubsite.test_api import CLOCK, OPTIONS, ApiCase, FileViews

START = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
WAIT_S = 10


class Views(FileViews):
    """FileViews (record_answer) with a second repository `other`, and, when `parks` is set,
    the park views: record_park raising `park_fail` when set, record_unpark, and record_asks
    answering `asks`."""

    def __init__(self, parks=False):
        FileViews.__init__(self)
        self.parks, self.unparks, self.park_fail, self.asks = [], [], None, []
        if not parks:
            self.record_park = None

    def record(self, repo, id):
        if repo.lower() == 'other':
            return {'repo': 'other', 'id': id, 'where': 'open'}
        return FileViews.record(self, repo, id)

    def record_park(self, repo, id, qid, cond, asker):
        self.parks.append((repo, id, qid))
        if self.park_fail is not None:
            raise self.park_fail
        return {'lines': [], 'park': 'blocked'}

    def record_unpark(self, repo, id, cond):
        self.unparks.append((repo, id, cond))
        return {'lines': [], 'dropped': True, 'unblocked': True}

    def record_asks(self):
        return list(self.asks)


class Case(ApiCase):

    def views(self):
        return Views()

    def setUp(self):
        ApiCase.setUp(self)
        self.wall = START
        self.mono = [0.0]
        self.v = self.views()
        self.api = api.Api(self.store, views=self.v, clock=self.clock)
        self.said = []
        self.job = refile.Refile(self.api, log=self.said.append, now=lambda: self.mono[0])
        self.ctx = jobs.Ctx(threading.Event(), lambda *a: None)

    def clock(self):
        return self.wall.isoformat()

    def later(self, seconds):
        """The hub's clock and the job's both move on `seconds`."""
        self.wall += datetime.timedelta(seconds=seconds)
        self.mono[0] += seconds

    def doc(self, qid):
        return self.store.get('questions', qid)

    def bound(self, rid='EX-1', repo='kit'):
        return self.question(record={'repo': repo, 'id': rid})

    def answer(self, qid):
        return self.ok('POST', '/questions/%s/answer' % qid, OWNER, {'choice': 0})

    def tries(self, qid):
        return sum(1 for f in self.v.filed if f[2] == qid)

    def run_job(self):
        return self.job.run(self.ctx)


class DueTest(Case):

    def test_a_failure_to_retry_waits_its_backoff_doubled_each_time(self):
        qid = self.bound()
        self.v.fail = HubError('store', 'C:\\work\\repo moved', '')
        self.answer(qid)                                    # tried off the answer: 1
        self.assertNotIsInstance(self.run_job(), jobs.Skip)  # the first run's full pass: 2
        self.assertEqual(self.tries(qid), 2)
        for wait in (refile.BACKOFF_FIRST_S, 2 * refile.BACKOFF_FIRST_S,
                     4 * refile.BACKOFF_FIRST_S):
            with self.subTest(wait=wait):
                tried = self.tries(qid)
                self.later(wait - 1)
                self.assertIsInstance(self.run_job(), jobs.Skip)
                self.assertEqual(self.tries(qid), tried, 'not due before its backoff')
                self.later(1)
                self.assertEqual(self.run_job(), 'not filed 1')
                self.assertEqual(self.tries(qid), tried + 1)
        self.v.fail = None
        self.later(8 * refile.BACKOFF_FIRST_S)
        self.assertEqual(self.run_job(), 'filed 1')
        self.assertEqual(self.doc(qid)['status'], 'filed')
        self.assertIsInstance(self.run_job(), jobs.Skip)

    def test_the_backoff_stops_at_its_cap(self):
        qid = self.bound()
        self.v.fail = HubError('unavailable', 'no view', 'views')
        self.answer(qid)
        self.run_job()
        for _ in range(8):
            self.later(refile.BACKOFF_CAP_S)
            self.run_job()
        tried = self.tries(qid)
        self.later(refile.BACKOFF_CAP_S)
        self.assertEqual(self.run_job(), 'not filed 1')
        self.assertEqual(self.tries(qid), tried + 1, 'due again at the cap, never later')

    def test_the_log_has_the_view_s_own_text_and_the_question_the_fixed_phrase(self):
        qid = self.bound()
        self.v.fail = HubError('busy', 'C:\\work\\repo: the backlog store lock is held', '')
        self.answer(qid)
        self.run_job()
        self.assertEqual(self.said, ['%s not filed (busy): C:\\work\\repo: the backlog store lock '
                                     'is held' % qid])
        self.assertEqual(self.doc(qid)['filing']['message'], api.FILING_SAYS['busy'])
        self.assertNotIn('C:\\', self.doc(qid)['filing']['message'])

    def test_a_final_code_is_tried_by_the_full_pass_only_and_by_a_kick(self):
        qid = self.bound()
        self.v.fail = HubError('not_found', 'no such record', 'id')
        self.answer(qid)
        self.run_job()
        self.assertEqual(self.tries(qid), 2)
        self.later(10 * refile.BACKOFF_CAP_S)
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(self.tries(qid), 2, 'the PM handles it: the job never tries it again')
        self.job.kick([qid])                                # no Jobs: the marks only
        self.v.fail = None
        self.assertEqual(self.run_job(), 'filed 1')

    def test_an_answer_its_request_never_filed_is_filed_once_it_is_sweep_s_old(self):
        dropped = []
        self.api.file_later = lambda doc_id, fn: dropped.append(fn)    # a thread that died
        self.run_job()                                      # the first full pass, done
        qid = self.bound()
        self.answer(qid)
        self.assertEqual((self.doc(qid)['status'], self.doc(qid).get('filing')), ('answered', None))
        self.later(refile.SWEEP_S - 1)
        self.assertIsInstance(self.run_job(), jobs.Skip, 'its filing may still be running')
        self.later(1)
        self.assertEqual(self.run_job(), 'filed 1')
        self.assertEqual((self.tries(qid), self.doc(qid)['status']), (1, 'filed'))

    def test_a_new_answer_resets_the_backoff(self):
        qid = self.bound()
        self.v.fail = HubError('store', 'moved', '')
        self.answer(qid)
        self.run_job()
        self.later(refile.BACKOFF_FIRST_S)
        self.run_job()                                      # waits 60 s now
        self.later(refile.SWEEP_S)
        self.answer(qid)                                    # a new `at`: a new key
        tried = self.tries(qid)
        self.assertEqual(self.run_job(), 'not filed 1')
        self.assertEqual(self.tries(qid), tried + 1)


class BusyTest(Case):

    def test_busy_on_a_repository_leaves_its_other_ids_to_the_next_run(self):
        first, second = self.bound('EX-1'), self.bound('EX-2')
        elsewhere = self.bound('EX-9', repo='other')
        self.v.fail = HubError('busy', 'held', '')
        for qid in (first, second, elsewhere):
            self.answer(qid)
        tried = [self.tries(q) for q in (first, second, elsewhere)]
        self.assertEqual(self.run_job(), 'not filed 2, skipped busy 1')
        self.assertEqual([self.tries(q) for q in (first, second, elsewhere)],
                         [tried[0] + 1, tried[1], tried[2] + 1],
                         'one busy try per repository, another repository still tried')

    def test_a_kicked_id_skipped_for_busy_keeps_its_mark_and_one_tried_drops_it(self):
        first, second = self.bound('EX-1'), self.bound('EX-2')
        self.v.fail = HubError('busy', 'held', '')
        for qid in (first, second):
            self.answer(qid)
        self.run_job()
        self.job.kick([first, second])
        self.assertEqual(self.run_job(), 'not filed 1, skipped busy 1')
        self.assertEqual(sorted(self.job._forced), [second])
        self.v.fail = None
        self.assertEqual(self.run_job(), 'filed 1', 'the kept mark is due at once')
        self.assertEqual((self.doc(second)['status'], self.doc(first)['status']),
                         ('filed', 'answered'))
        self.assertEqual(self.job._forced, {})


class ParkCase(Case):

    def views(self):
        return Views(parks=True)


class ParkTest(ParkCase):

    def test_a_park_to_retry_waits_its_backoff_and_one_no_longer_asking_is_not_due(self):
        self.v.park_fail = HubError('store', 'moved', '')
        qid = self.bound()
        self.assertEqual(self.doc(qid)['park']['code'], 'store')
        self.run_job()
        self.assertEqual(self.said, ['%s: record not parked (store): %s'
                                     % (qid, api.PARK_SAYS['store'])])
        self.v.park_fail = None
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.later(refile.BACKOFF_FIRST_S)
        self.assertEqual(self.run_job(), 'parked 1')
        self.assertEqual(self.doc(qid)['park']['code'], 'blocked')
        # an answered question keeps a failed park's mark, and is never due for it
        self.v.park_fail = HubError('store', 'moved', '')
        other = self.bound('EX-2')
        self.answer(other)
        self.assertEqual((self.doc(other)['status'], self.doc(other)['park']['code']),
                         ('filed', 'store'))
        parks = len(self.v.parks)
        self.later(refile.BACKOFF_CAP_S)
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(len(self.v.parks), parks)

    def test_a_forced_all_runs_the_full_pass_and_its_withdraw_sweep(self):
        self.v.asks = [{'repo': 'kit', 'id': 'EX-1', 'q': 'Q-404', 'parked': True}]
        self.run_job()
        self.assertEqual(len(self.v.unparks), 1, 'the first run sweeps')
        self.assertIsInstance(self.run_job(), jobs.Skip)
        self.assertEqual(len(self.v.unparks), 1, 'a later run does not')
        self.job.kick('all')
        self.assertEqual(self.run_job(), 'unparked 1')
        self.assertEqual(len(self.v.unparks), 2)
        self.assertIsInstance(self.run_job(), jobs.Skip, "'all' is made once")


class KickTest(Case):

    def test_a_kick_while_a_run_is_going_makes_that_run_pass_again(self):
        first, late = self.bound('EX-1'), self.bound('EX-2')
        self.v.fail = HubError('not_found', 'gone', 'id')
        self.answer(late)                                   # final: never due on its own
        self.run_job()                                      # the first full pass, done
        self.v.fail = HubError('store', 'moved', '')
        self.answer(first)
        self.later(refile.SWEEP_S)
        self.assertEqual(self.run_job(), 'not filed 1')
        self.later(refile.BACKOFF_FIRST_S)
        self.v.fail = None
        # inside the run's write of `first`, `late` is kicked: no Jobs, so only the marks
        self.v.during = lambda: self.job.kick([late])
        self.assertEqual(self.run_job(), 'filed 2')
        self.assertEqual((self.doc(first)['status'], self.doc(late)['status']), ('filed', 'filed'))
        self.assertEqual(self.job._forced, {})

    def test_a_kick_runs_the_job_at_once_on_a_thread_of_its_own(self):
        qid = self.bound()
        self.v.fail = HubError('not_found', 'gone', 'id')
        self.answer(qid)
        timed = jobs.Jobs([self.job.job()])
        self.job.jobs = timed
        self.run_job()                                      # the first full pass, done
        self.v.fail = None
        self.job.kick([qid])
        deadline = time.monotonic() + WAIT_S
        while self.doc(qid)['status'] != 'filed' and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.doc(qid)['status'], 'filed')
        while timed.status()[refile.JOB]['runs'] < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(timed.status()[refile.JOB]['note'], 'filed 1')

    def test_the_job_is_named_and_timed_as_its_module_says(self):
        j = self.job.job()
        self.assertEqual((j.name, j.every_s, j.first_s),
                         (refile.JOB, refile.EVERY_S, refile.FIRST_S))

    def test_a_stop_ends_the_run_between_ids(self):
        ids = [self.bound('EX-1'), self.bound('EX-2')]
        self.v.fail = HubError('store', 'moved', '')
        for qid in ids:
            self.answer(qid)
        self.v.during = self.ctx.stop.set
        self.v.fail = None
        self.run_job()
        self.assertEqual(self.doc(ids[1])['status'], 'answered', 'not tried after the stop')


class QueuedRouteTest(Case):

    def test_both_forms_queue_for_the_job_and_answer_at_once(self):
        queued = []
        self.api.refile_later = queued.append
        qid = self.bound()
        self.v.fail = HubError('store', 'moved', '')
        self.answer(qid)
        tried = len(self.v.filed)
        got = self.ok('POST', '/questions/file', AGENT, {})
        self.assertEqual((got['ok'], got['queued']), (True, 'all'))
        got = self.ok('POST', '/questions/file', AGENT, {'id': qid})
        self.assertEqual(got['queued'], [qid])
        self.assertEqual(queued, ['all', [qid]])
        self.assertEqual(len(self.v.filed), tried, 'the route itself files nothing')
        self.refused('not_found', 'id', 'POST', '/questions/file', AGENT, {'id': 'Q-404'})
        self.assertEqual(len(queued), 2, 'an unknown id is refused, not queued')


class RealStoreTest(trw.Case):
    """A filing that loses the store's compare-and-swap on every attempt (another writer
    publishes between each plan and its publish), then is filed by the job's timer once the
    other writer stops and its backoff is over."""

    def setUp(self):
        trw.Case.setUp(self)
        self.named()
        self.seed(**rec('FOO', state='parked', q='Q-1000', parked_at='2026-10-01'))
        root = self.repo.root

        class Backlog(object):
            def _named(self, repo):
                return 'r', root

            def reread(self, name, at):
                pass

            def record(self, repo, rid):
                return {'repo': repo, 'id': rid, 'where': 'open'}

        self.views = serveviews.SiteViews(None, servesources.Sources(), backlog_views=Backlog())
        hub = tempfile.mkdtemp(prefix='kit-hubrefile-')
        self.addCleanup(shutil.rmtree, hub, True)
        self.store = Store.open(hub, clock=CLOCK)
        self.addCleanup(self.store.close)
        self.api = api.Api(self.store, views=self.views, clock=CLOCK)

    def test_a_lost_race_on_every_attempt_is_filed_by_the_timer_after_its_backoff(self):
        real, interfere, n = recordwrite.opener.open_store, [True], [0]

        def opened(*a, **kw):
            ctx = real(*a, **kw)

            def hook(attempt):
                if interfere[0]:                # another writer publishes first, every time
                    n[0] += 1
                    self.seed(**rec('OTHER%d' % n[0]))
            ctx.store.before_publish = hook
            return ctx
        status, got = self.api.call('POST', BASE + '/questions', AGENT,
                                    {'title': 'Which one?', 'options': OPTIONS,
                                     'record': {'repo': 'r', 'id': 'FOO'}})
        self.assertEqual(status, 200, got)
        qid = got['id']
        said, at = [], [0.0]
        job = refile.Refile(self.api, log=said.append, now=lambda: at[0])
        timed = jobs.Jobs([job.job()])
        job.jobs = timed
        with mock.patch.object(recordwrite.opener, 'open_store', opened):
            status, got = self.api.call('POST', BASE + '/questions/%s/answer' % qid, OWNER,
                                        {'choice': 1}, via='web')
            self.assertEqual((status, got['status'], got['filing']['code']),
                             (200, 'answered', 'store'), got)
            self.assertTrue(timed.run_now(refile.JOB))  # the first run, still losing
            self.assertEqual(len(said), 1, said)
            self.assertTrue(said[0].startswith('%s not filed (store): ' % qid), said)
            self.assertIn('moved under', said[0])      # the compare-and-swap lost each time
            self.assertEqual(self.store.get('questions', qid)['filing']['message'],
                             api.FILING_SAYS['store'])
            interfere[0] = False
            at[0] = refile.BACKOFF_FIRST_S - 1
            timed.run_now(refile.JOB)
            self.assertEqual(timed.status()[refile.JOB]['note'], 'nothing due')
            self.assertEqual(self.store.get('questions', qid)['status'], 'answered')
            at[0] = refile.BACKOFF_FIRST_S
            timed.run_now(refile.JOB)
        self.assertEqual(timed.status()[refile.JOB]['note'], 'filed 1')
        self.assertEqual(self.store.get('questions', qid)['status'], 'filed')
        self.assertEqual(self.field('FOO', 'answer'), ['%s %s' % (qid, CLOCK())])
        self.assertGreater(n[0], 1, 'the store lost its race more than once')
