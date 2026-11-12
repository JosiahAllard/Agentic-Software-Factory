"""kitlib.hubsite.refile: the hub's refile job, which files the owner's answers into the records
they are bound to and parks the records of bound asks when an earlier try did not
(docs/hub-questions.md "Filing the answer").

  Refile(api, log=None, now=time.monotonic)   the job's state for one Api
      .job()                                  its jobs.Job: JOB, every EVERY_S, first FIRST_S
      .run(ctx)                               one run (jobs.Ctx)
      .kick(ids | 'all')                      queue work now (Api.refile_later)
      .jobs                                   the jobs.Jobs it runs in, set by serve.build

The first run is the full pass that POST questions/file made at hub start before this job:
every answered bound question not filed yet is filed (kitlib.hubsite.api file_answer), every
open bound ask not parked yet is parked (api.park_item: a question, a to-do, an approval), and
the lines of asks gone or bound elsewhere are dropped (api._sweep_asks). Each later run does only
what is due:
  - a question whose filing failed with a code to retry (apipark.PARK_RETRY: store, busy,
    unavailable, internal); a final code (api.FINAL_FILING) is the PM's and never retried;
  - an answered bound question with no `filing` and no `filed_at` whose answer is SWEEP_S old:
    its filing off the answer's request never marked it (a crash, a thread that raised);
  - an ask still asking whose park failed with a code to retry.
Each id that failed waits BACKOFF_FIRST_S, doubled after each failure up to BACKOFF_CAP_S, before
it is due again. The wait is kept in memory, keyed on the question's answer `at` (a park's on the
record it is bound to), so the failure's own mark, which moves the version, does not reset it and
a new answer does. Once a write on a repository answers `busy` (another local writer held that
backlog store's lock past recordwrite.HUB_LOCK_WAIT), the rest of that repository's ids wait for
the next run, so Api.record_lock is not held 5 s per id. The run looks at ctx.stop between ids.
Each failed filing is a 'note' line of the hub log with the record view's own text (file_answer
full=True; the log is the owner's), while the question keeps the fixed phrase of
api.FILING_SAYS; a failed park's line has the park's code and phrase (park_item keeps no more).
A run with nothing due is a jobs.Skip.

kick: POST questions/file queues its work here when the server set Api.refile_later. An id is
marked forced (filed and parked whatever its backoff, even when nothing else is due); 'all'
forces the whole full pass of the first run, the withdraw sweep (_sweep_asks) included. The kick
runs the job at once (jobs.Jobs.run_now) on a thread of its own; when a run is going already,
run_now does nothing and the `again` flag the kick set makes that run make one more pass. A
forced mark is cleared only after its id's attempt (an id skipped because its repository was
busy keeps it), and a forced 'all' only after the pass it asked for ends. A kick that lands
after a run's last look at `again` but before the run ends waits for the next tick, up to
EVERY_S: a delay, never a loss, since its mark stays until an attempt clears it.
"""
import threading
import time

from . import api as _api
from . import jobs as _jobs

JOB = 'refile'
EVERY_S = 30
FIRST_S = 5             # the hub serves first; the full pass that hub start made follows at once
SWEEP_S = 300           # an unmarked answer this old was not filed off its request
BACKOFF_FIRST_S = 30
BACKOFF_CAP_S = 900
VIA = 'hub-refile'      # `via` of every write the job makes


class Refile(object):
    def __init__(self, api, log=None, now=None):
        self.api = api
        self.log = log or (lambda text: None)
        self.now = now or time.monotonic
        self.jobs = None
        self._lock = threading.Lock()   # the kick's marks: _forced, _all_asked, _again
        self._forced = {}               # question id -> the kick's token that forced it
        self._token = 0
        self._all_asked = 0             # forced full passes asked for, and made
        self._all_done = 0
        self._again = False
        self._ran = False               # the first (full) pass has run
        self._wait = {}                 # backoff key -> (due at, in self.now() seconds; delay)

    def job(self):
        return _jobs.Job(JOB, EVERY_S, self.run, first_s=FIRST_S)

    # ------------------------------------------------------------------ the kick
    def kick(self, what):
        """Queue `what` (a list of question ids, or 'all') and run the job now: module docstring
        "kick"."""
        with self._lock:
            if what == 'all':
                self._all_asked += 1
            else:
                for doc_id in what:
                    self._token += 1
                    self._forced[doc_id] = self._token
            self._again = True
        if self.jobs is not None:
            threading.Thread(target=self._run_now, name='hub-refile-kick', daemon=True).start()

    def _run_now(self):
        try:
            self.jobs.run_now(JOB)
        except Exception:               # noqa: BLE001 - a thread's raise would reach no one;
            pass                        # run_now counts a run's own raise as the job's failure

    def _unforce(self, doc_id, token):
        with self._lock:
            if self._forced.get(doc_id) == token:
                del self._forced[doc_id]

    # ------------------------------------------------------------------ a run
    def run(self, ctx):
        counts = {}
        did = False
        while not ctx.stop.is_set():
            with self._lock:
                self._again = False
                asked = self._all_asked
                full = not self._ran or asked > self._all_done
                forced = dict(self._forced)
            self._ran = True
            did = self._pass(ctx, full, forced, counts) or did
            if ctx.stop.is_set():
                break
            with self._lock:
                if full:
                    self._all_done = max(self._all_done, asked)
                if not self._again:
                    break
        if not did:
            return _jobs.Skip('nothing due')
        return ', '.join('%s %d' % (k, counts[k]) for k in sorted(counts)) or 'nothing changed'

    def _pass(self, ctx, full, forced, counts):
        """One pass: True when it tried anything."""
        api = self.api
        now = self.now()
        parks = _api._parks(api)
        busy, live, did = set(), set(), False
        for coll in ('questions', 'todos', 'approvals'):
            docs = api.store.docs(coll)
            for doc_id in sorted(docs, key=_api._natural):
                if ctx.stop.is_set():
                    return did
                d = docs[doc_id]
                if not isinstance(d, dict) or not isinstance(d.get('record'), dict):
                    continue
                fkey = _file_key(doc_id, d) if coll == 'questions' else None
                pkey = ('park', doc_id, d['record'].get('repo'), d['record'].get('id'))
                live.update(k for k in (fkey, pkey) if k is not None)
                token = forced.get(doc_id) if coll == 'questions' else None
                want_file = fkey is not None and (full or token is not None
                                                  or self._file_due(d, fkey, now))
                want_park = parks and (full or token is not None
                                       or self._park_due(coll, d, pkey, now))
                if not (want_file or want_park):
                    continue
                repo = d['record'].get('repo')
                if repo in busy:
                    _count(counts, 'skipped busy')
                    continue
                did = True
                if want_file and self._file(doc_id, fkey, now, counts) == 'busy':
                    busy.add(repo)
                if want_park and repo not in busy and self._park(doc_id, pkey, now,
                                                                 counts) == 'busy':
                    busy.add(repo)
                if token is not None:
                    self._unforce(doc_id, token)
        if full and parks:
            did = True
            unparked, failed, hand = _api._sweep_asks(api)
            if unparked:
                counts['unparked'] = counts.get('unparked', 0) + len(unparked)
            for f in failed:
                _count(counts, 'park failed')
                self.log('%s: record not unparked: %s' % (f.get('id'), f.get('message')))
            for h in hand:
                self.log('%s:%s: owner line cites missing %s' % (h.get('repo'), h.get('id'),
                                                                h.get('q')))
        # the backoff of an answer replaced, an ask gone or rebound, is dropped
        self._wait = {k: v for k, v in self._wait.items() if k in live}
        return did

    def _file(self, doc_id, key, now, counts):
        """File doc_id's answer once: its failure's code, or None."""
        got = _api.file_answer(self.api, doc_id, VIA, full=True)
        if got is None or got.get('moved'):
            self._wait.pop(key, None)
            return None
        if got.get('filed'):
            _count(counts, 'filed')
            self._wait.pop(key, None)
            return None
        code = got.get('code')
        _count(counts, 'not filed')
        self._failed(key, now, code)
        self.log('%s not filed (%s): %s' % (doc_id, code, got.get('message')))
        return code

    def _park(self, doc_id, key, now, counts):
        """Park the record of the ask doc_id once: its failure's code, or None."""
        got = _api.park_item(self.api, doc_id, VIA)
        code = _api._park_failed(got)
        if got is None or got.get('moved'):
            self._wait.pop(key, None)
            return None
        if code is None:
            _count(counts, 'parked')
            self._wait.pop(key, None)
            return None
        _count(counts, 'park failed')
        self._failed(key, now, code)
        self.log('%s: record not parked (%s): %s' % (doc_id, code, got.get('message')))
        return code

    # ------------------------------------------------------------------ what is due
    def _file_due(self, d, key, now):
        filing = d.get('filing')
        if isinstance(filing, dict):
            return filing.get('code') in _api.PARK_RETRY and self._due(key, now)
        if d.get('filed_at'):
            return False
        answered = _api._when((d.get('answer') or {}).get('at'), '')
        clock = _api._when(self.api.clock(), '')
        if answered is not None and clock is not None \
                and (clock - answered).total_seconds() < SWEEP_S:
            return False                # its filing off the answer's request may still run
        return self._due(key, now)

    def _park_due(self, coll, d, key, now):
        # an ask answered, done or expired since keeps its failed mark: park_item would park
        # nothing, so it is never due again
        park = d.get('park')
        return (isinstance(park, dict) and park.get('code') in _api.PARK_RETRY
                and _api._asking(self.api, coll, d) and self._due(key, now))

    def _due(self, key, now):
        w = self._wait.get(key)
        return w is None or now >= w[0]

    def _failed(self, key, now, code):
        if code not in _api.PARK_RETRY:
            self._wait.pop(key, None)   # a final code: never due again on its own
            return
        w = self._wait.get(key)
        delay = BACKOFF_FIRST_S if w is None else min(w[1] * 2, BACKOFF_CAP_S)
        self._wait[key] = (now + delay, delay)


def _file_key(doc_id, d):
    """The backoff key of question d's filing, or None when it has no answer to file."""
    if d.get('status') != 'answered' or not isinstance(d.get('answer'), dict):
        return None
    return ('file', doc_id, d['answer'].get('at'))


def _count(counts, what):
    counts[what] = counts.get(what, 0) + 1
