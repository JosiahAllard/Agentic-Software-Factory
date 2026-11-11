"""kitlib.hubsite.serve: the hub server, wired from its parts (docs/modules/hub-design.md
"Contract" and "Packages and files").

  build(settings, sources, log=None) -> Hub     open the store, make the views, the Api and the
                                                listeners; nothing is started. ServeError.
  Hub   .settings .store .api .views .creds .listeners .sync .backlog .push .notes .tunnel .port
        .url .stopping                          (stopping: the event the owner's stop route sets;
                                                push: the push-notification worker or None)
        .start()                                start the git sync, the backlog timer, the push
                                                notifications, the listeners
        .watch()                                log a git sync failure that is new (below)
        .close(timeout=CLOSE_TIMEOUT_S)         the clean close (below); safe to call twice
  run(hub, started=None, stop=None)             start, serve until `stop` is set, the owner's
                                                stop route is called or Ctrl+C, close
  Sources, machine_sources                      where each part comes from, and this machine's
                                                (kitlib.hubsite.servesources)
  SiteViews                                     the Api's `views` object over those sources
                                                (kitlib.hubsite.serveviews)
  health_route, status_lines                    GET health and what `hub status` prints of it
                                                (kitlib.hubsite.servehealth)
  ServeError                                    kitlib.hubclient.errors's, raised here too

The server's parts, one file each: servesources (Sources and machine_sources), serveviews
(SiteViews), servehealth (GET health, `hub status`), serverepos (the kit checkout and the
repositories' origin remotes the Api asks for); gitsync.scrub takes the `user:password@` of a URL
out of a text.

build() takes its parts from a Sources, so a test serves a temp store on port 0 with stand-ins
and never reads the machine config, a real repository or the network. Nothing here reads a
setting that could name an address: every listener is httpd.Listener on httpd.LOOPBACK.

Start-up, in this order; a failure releases everything taken before it and is a ServeError whose
text is for the owner's console (it may name a folder):
  1. the store (Store.open). A store another server holds is refused at once, never waited for.
     A lock left by a server that died is taken over, with a note.
  2. the token files (auth.ensure_tokens) and the approval key beside them
     (auth.ensure_approval_key): a file that cannot serve is refused, named.
  3. the backlog views and the git sync, from the Sources' factories. A sync that is refused
     (gitsync.Refused: the folder is no work tree, the wrong branch, ...) does not stop the
     server: the reason goes to `Hub.notes` for the console, the server serves with sync off,
     and GET health says so in a fixed phrase (the reason itself names folders).
  4. the Api over the store and a SiteViews (with approval_key reading the approval key from
     the token folder on each use, and checkout matching a repo_id to the backlog's configured
     repositories by their origin remote: _checkout, and repo_name giving that repository's
     name, the coordinator a decided approval is for: _repo_name), then the local listener on
     settings.port with httpd.local_hosts, httpd.local_gate, httpd.pair_routes over an auth.Credentials made for
     the port that was really bound, httpd.stop_route (it sets Hub.stopping and answers this
     process's pid and creation time; the tunnel listener never has it) and health_route. The
     Credentials keep the browser
     sessions in the token folder's sessions file for that port (auth, persist=True), so a
     paired browser stays paired when the server starts again; a file it cannot read whole is a
     line in `Hub.notes`. With settings.port 0 (a new port each run) no sessions file is kept.
  5. the tunnel listener on settings.tunnel_port (kitlib.hubsite.tunnel.start), only when
     settings.access can serve it. It never stops the server: with no Access settings at all
     there is no second listener and no line about it; with settings that cannot serve
     (tunnel.TunnelConfigError) or a port that cannot be bound, a line in `Hub.notes` says why
     and the local listener serves alone. So it does when tunnel.start raises anything else
     (the maker of the key source, say): the note then names the exception's class and none
     of its text, and the reason is TUNNEL_FAILED. `Hub.tunnel` is then TUNNEL_OFF with the
     reason in fixed words. With a listener bound, a note says which port needs an Access
     token and `Hub.tunnel` is what "GET health and the sync's reason" gives below.
  6. the push-notification worker (kitlib.hubsite.push.PushWorker), only when Sources.push and
     Sources.push_dir are both set, with a line in `Hub.notes`. It is made here and started by
     start(); one that fails to start is an 'error' line and the hub serves without it.
  7. the timed jobs (kitlib.hubsite.jobs.Jobs), one per maker in Sources.jobs (GET health
     `jobs` names them); a maker that raises, or makes a job whose name an earlier one has, is
     a line in `Hub.notes` and the hub serves without that job; one that gives None is left
     out. Their events are pushed through the push worker when there is one, their
     failures are 'error' lines of the log, and GET health's `jobs` reads them. Started by
     start(), before the listeners.
`Hub.listeners` is a list: the local listener first, then the tunnel's when there is one.
start() and close() carry every listener in it, and a start-up that fails after one was bound
closes its door. Each gets a health_route of its own: the local listener's answers as it did
with no tunnel, the tunnel's (fixed=True) answers fixed words only, so the tunnel's owner is
told that a sync failed and not why. The tunnel listener has no local pairing routes, only
httpd.strong_pair_route (an Access login asks there for a code that pairs a local browser as
the strong owner; the code is redeemed on the local listener), and no Host rule, and its error answers are the agent's wording for the owner too (tunnel.start makes it
with httpd's agent_words), so none names the store folder; the local listener's rules are what
they were with no tunnel, and it never reads the Access header.

The log. `log(level, text)` gets the listeners' lines (httpd: 'access', 'error', and the local
listener's 'slow', to which _local_line adds whether the backlog timer runs; _tunnel_line drops
the tunnel listener's, which would time the remote network), the tunnel
gate's ('note' or 'error' for a request it refused, at most one a reason a minute:
kitlib.hubsite.tunnel), the tunnel listener's 'error' tunnel.HOST_LINE for a write refused
because its Origin host is not its Host (httpd's origin_refused, tunnel.host_note: at most one
a minute) and the hub's own: 'error' for a close step that failed, ran out of
time or was cut short and for a git sync pass that failed, 'note' for what is no error (the
wait for git begins at the close; the sync works again). Every line of the hub's own goes
through scrub(), so none holds the user or password of a remote URL; the listeners' and the
gate's lines do not, so what prints them applies scrub().

The git sync's failures. GitSync has no log of its own: it keeps the step its last pass failed
at and git's reason (`detail`) in status(). Hub.watch() reads that status (it never waits for a
pass) and logs an 'error' line with the step and the reason when a failure is new or its reason
changed, and a 'note' when the sync works again; run() calls it on every turn of its wait
(POLL_S). A Hub served without run() logs a failing sync only when its owner calls watch().
A sync with push on and no remote to push to is failing from the start (gitsync.NO_REMOTE), so
the first look logs that.

The clean close, in this order (Hub.close; run() ends with it, also on Ctrl+C and on the
owner's stop route):
  1. every listener's gate that has a stop() (the tunnel's: tunnel.Gate.stop), then every
     listener's door. The gate first: a tunnel request held on a fetch of the Access keys
     (up to access.FETCH_DEADLINE_S, longer than a step's `timeout`) is refused at once with
     the tunnel's one answer, so step 3 does not wait for it; a stop admits nobody. Then no
     new connection, and no write begins (httpd refuses it);
  2. the backlog timer, then the timed jobs (no job begins a run after this; one in flight, a
     plugin refresh say, may outlast `timeout`, and its thread, a daemon, ends with the process);
  3. the requests in flight, except the long waits: after this no write is in flight;
  4. the git sync, only when the server was started: a 'note' line says that the wait for git
     begins, the sync's thread is stopped, then one last pass (sync_once) runs while the store
     is still open and locked, so what was written since the last pass is committed whole. A
     pass that fails is an error line in the log and is tried again at the next start;
  5. the store: the push-notification worker is told to begin no further delivery, every long
     wait ends and the store lock is released, then the worker's thread is joined (for the
     longer of `timeout` and push.STOP_S, which outlasts a delivery in flight) and it writes its
     outbox;
  6. the handler threads.
Each step is given `timeout` seconds. One that raises, or that has not ended when its time is
up, is an 'error' line in the log that names the step; the next step still runs, and the store
is closed whatever happened before it. close() answers True only when every step ended in time
and none raised. Three things about step 4:
  - The wait for git has no limit of this module's. The last pass is not given `timeout`: it
    waits for a pass still in flight, then runs to its end, each as long as git's own limits
    let it (gitsync.NET_TIMEOUT for a fetch and again for a push, in each pass). A pass must
    end before the store closes, and a git command stopped half way may leave an `index.lock`
    behind, after which every pass ends at `busy` until a person removes it
    (kitlib.hubsite.gitsync). The 'note' line (LAST_PASS) is logged before the wait begins
    and says all of this. When the sync's thread has not ended in `timeout`, the wait for
    its pass in flight is made in the sync's stop(None), which begins no pass, and the last
    pass is called after it: no call of sync_once is left queued behind a pass in flight.
  - The last pass is skipped when step 3 ran out of time or raised. A request is then still in
    flight and may write while git adds the store's files (GitSync takes no store lock), so the
    commit could hold half of that write with no later pass in this process to bring the rest.
    An 'error' line says so (LAST_PASS_SKIPPED); the first pass of the next start commits what
    is left. The sync's thread is still stopped, after a 'note' (IN_FLIGHT). When it has not
    ended in `timeout`, a pass is in flight: close() says so (a 'note', STILL_WAITING) and
    waits for it with no limit, so the store is not closed beside a running git. Only when
    that wait itself fails is the store closed with a pass that may still run, and an 'error'
    line (LEFT_RUNNING) says what to check.
  - A second Ctrl+C ends the wait. Every wait of step 4 is made on a thread of its own
    (GIT_WAIT_THREAD) while close() waits for that thread in turns of POLL_S, so a
    KeyboardInterrupt reaches a close() that runs on the main thread. For that one, and for
    one in steps 1 to 3 of a server whose sync was started, an 'error' line (CUT_SHORT) says
    that a git command may have been left half way and what to check; the sync is told to
    begin no further pass, steps 5 and 6 still run, and close() answers False. No pass
    begins after that: the wait that was left is one in the sync's stop(), or a last pass
    that had begun already, and that one, like a pass in flight, is left to itself. (A sync
    whose stop(None) raises or does not answer True gets no last pass either: the raise is
    an 'error' line, then LEFT_RUNNING. GitSync's stop(None) answers True.)
    With no sync started, a KeyboardInterrupt leaves close() as it came, after steps 5 and
    6. So does one that arrives in step 5 or 6 itself, sync or none: step 4 has ended, so
    there is no CUT_SHORT line, and what is left of those two steps is not run (the system
    drops the store lock with the process when step 5 was the one cut).
A process that is killed closes nothing: the system drops the store lock with it, and the next
start takes it over.

The request every local client makes (`hub status`, `hub open`, and kitlib.hubclient.client for
the other verbs) is kitlib.hubclient.localask.ask, its errors kitlib.hubclient.errors: neither
is this server's.
"""
import functools
import os
import threading
import time

from kitlib.config import user
from kitlib.hubclient import codestale
from kitlib.hubclient.errors import ServeError
from kitlib.proc import process

from . import api as _api
from . import jobs as _jobs
from . import push as _push
from . import auth, gitsync, httpd, tunnel
from . import selfrestart, servehealth, serverepos, serveviews
from .store import HubError, Store, StoreBusy

CLOSE_TIMEOUT_S = 10            # each step of the clean close
STALE_CODE = codestale.text()   # the console line; GET health's `code` and the page say it too
CODE_LOOK_S = 30                # at most one look at the hub code's files in this many seconds
POLL_S = 0.5                    # run()'s wait between two looks at `stop` (and at Ctrl+C)
TUNNEL_OFF_NOTE = 'the tunnel listener is off: %s'
TUNNEL_FAILED_NOTE = 'it could not be made (%s)'    # the exception's class name, never its text
TUNNEL_ON_NOTE = ('the tunnel listener is on %s: every request there needs a Cloudflare Access '
                  'token')
STOP_ASKED = 'closing: the owner asked the server to stop (hub.py stop)'
GIT_WAIT_THREAD = 'hub-close-git'       # the thread a wait for git is made on at the close
_GIT_LIMITS = ('git may take %d s for a fetch and again for a push' % gitsync.NET_TIMEOUT)
_AGAIN = 'Ctrl+C again ends the wait'
LAST_PASS = ('closing: the last git sync pass: finishing a sync pass, waiting for git. A pass in '
             'flight ends first, then the last one runs; each runs to its end, and in each %s. %s.'
             % (_GIT_LIMITS, _AGAIN))
LAST_PASS_SKIPPED = ('closing: the last git sync pass is skipped: a request was still in flight '
                     'and could write during it; the next start commits what is left')
IN_FLIGHT = ('closing: finishing a sync pass that may be in flight, waiting for git: it has to '
             'end before the store closes (%s). %s.' % (_GIT_LIMITS, _AGAIN))
STILL_WAITING = ('closing: still waiting for git: the sync pass in flight has to end before the '
                 'store closes. %s.' % _AGAIN)
_CHECK = ('Look for a leftover index.lock in the git directory of the store\'s work tree (`git '
          'rev-parse --git-dir` in the store folder names it): while it is there every sync '
          'pass ends at `busy`. Remove it once no git runs there; the next start commits what '
          'is left.')
CUT_SHORT = ('closing: interrupted before the git sync had ended: a git command may have been '
             'left half-way. ' + _CHECK)
LEFT_RUNNING = ('closing: the store is closed while a git sync pass may still be running: a git '
                'command may be left half-way. ' + _CHECK)


def _under(path, folder):
    """True when `path` is `folder` or below it."""
    a, b = (os.path.normcase(os.path.abspath(p)) for p in (path, folder))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:              # two drives
        return False


def _open_store(root):
    try:
        return Store.open(root)
    except StoreBusy as e:
        raise ServeError('%s: another hub server has this store open; stop that one first' % e)
    except HubError as e:
        raise ServeError(str(e))
    except OSError as e:
        raise ServeError('cannot open the hub store %s: %s' % (root, e))


def _make_sync(settings, sources, notes):
    """(the git sync or None, the phrase health gives when there is none)."""
    if sources.sync is None:
        return None, serveviews.SYNC_OFF
    pm = sources.pm_data
    if not (pm and pm.get('path') and _under(settings.store, pm['path'])):
        pm = None                   # a store outside the data worktree: its own branch, no remote
    try:
        sync = sources.sync(settings.store, settings.commit_every_s,
                            remote=pm['remote'] if pm else None,
                            branch=pm['branch'] if pm else None, push=settings.push)
    except (gitsync.Refused, ValueError, OSError) as e:
        notes.append(gitsync.scrub('git sync is off: %s' % e))
        return None, serveviews.SYNC_REFUSED
    return sync, ''


def _make_tunnel(settings, sources, api, notes, log):
    """(the tunnel listener, None) or (None, what health says of there being none: TUNNEL_OFF
    with a fixed reason): module docstring, "Start-up", step 5. The listener is bound and not
    started, with no route yet. The note of a port that could not be bound carries the system's
    reason; health carries TUNNEL_BIND instead. Whatever else tunnel.start raises is no reason
    to refuse the local listener: the note names the exception's class and nothing of its text
    (a key source's maker may put a setting in it), and health carries TUNNEL_FAILED."""
    try:
        # through the module, never a name bound at import: a test replaces tunnel.start
        lis = tunnel.start(settings, api, static_dir=sources.static_dir,
                           log=None if log is None else functools.partial(_tunnel_line, log),
                           keys=sources.access_keys, now=sources.access_now)
    except tunnel.TunnelConfigError as e:
        if not e.unset:
            notes.append(TUNNEL_OFF_NOTE % e)
        return None, servehealth.TUNNEL_OFF % e
    except httpd.BindRefused as e:
        notes.append(gitsync.scrub(TUNNEL_OFF_NOTE % e))
        return None, servehealth.TUNNEL_OFF % servehealth.TUNNEL_BIND
    except Exception as e:          # noqa: BLE001 - the local listener serves alone
        notes.append(TUNNEL_OFF_NOTE % (TUNNEL_FAILED_NOTE % type(e).__name__))
        return None, servehealth.TUNNEL_OFF % servehealth.TUNNEL_FAILED
    notes.append(TUNNEL_ON_NOTE % lis.url)
    return lis, None


def _job_line(log, text):
    """The timed jobs' one-argument log, as an 'error' line of the hub's log (scrubbed)."""
    if log is None:
        return
    try:
        log('error', gitsync.scrub(text))
    except Exception:               # noqa: BLE001 - a log that fails must not stop a job
        pass


def _file_later(log, doc_id, fn):
    """Api.file_later: fn, the filing of the owner's answer to question doc_id (kitlib.hubsite.api
    file_answer), on
    a daemon thread of its own, so the answer route returns before the record's fetch and push.
    A raise is an 'error' line of the log; the question stays answered, and POST questions/file
    or the next hub start files it (docs/hub-questions.md "Filing the answer")."""
    def run():
        try:
            fn()
        except Exception as e:      # noqa: BLE001 - a thread's raise would reach no one
            _job_line(log, 'filing an answer to %s failed: %s' % (doc_id, type(e).__name__))
    threading.Thread(target=run, name='hub-file-answer', daemon=True).start()


def _park_later(log, doc_id, fn):
    """Api.park_later: fn, the park of the record the ask doc_id is bound to, or its undoing
    (kitlib.hubsite.api park_item, unpark_item), on a daemon thread of its own, as
    _file_later: the ask's route returns before the record's fetch and push. A raise, or the
    failure fn answers (a phrase with its code: api._later_said), is an 'error' line of the log;
    the next hub start makes a park not made, and drops a line whose ask is gone or bound
    elsewhere (POST questions/file)."""
    def run():
        try:
            said = fn()
        except Exception as e:      # noqa: BLE001 - a thread's raise would reach no one
            _job_line(log, 'parking the record of %s failed: %s' % (doc_id, type(e).__name__))
            return
        if isinstance(said, str):
            _job_line(log, '%s: %s' % (doc_id, said))
    threading.Thread(target=run, name='hub-park-record', daemon=True).start()


def _make_jobs(sources, pusher, notes, log):
    """The timed jobs of `sources` (Sources.jobs) as one kitlib.hubsite.jobs.Jobs, their events
    pushed through `pusher` when there is one; None with none. A job whose maker raises, gives
    something other than a jobs.Job, or whose name a job made before it has, is left out with a
    line in `notes`: the hub serves
    without it. A maker that gives None (the job does not apply here) is left out silently."""
    made = []
    for make in sources.jobs:
        where = getattr(make, '__module__', '?')
        try:
            job = make(sources)
        except Exception as e:      # noqa: BLE001 - the hub serves without that job
            notes.append('a timed job was not made (%s): %s: %s' % (where, type(e).__name__, e))
            continue
        if job is None:
            continue
        if not isinstance(job, _jobs.Job):
            notes.append('a timed job was not made (%s): its make gave a %s, not a jobs.Job'
                         % (where, type(job).__name__))
            continue
        if any(j.name == job.name for j in made):
            notes.append('a timed job was not made (%s): a job named %r is made already'
                         % (where, job.name))
            continue
        made.append(job)
    if not made:
        return None
    return _jobs.Jobs(made, notify=pusher.job_event if pusher is not None else None,
                      log=functools.partial(_job_line, log))


def _push_line(log, text):
    """The push worker's one-argument log, as a 'note' line of the hub's log (scrubbed)."""
    if log is None:
        return
    try:
        log('note', gitsync.scrub(text))
    except Exception:               # noqa: BLE001 - a log that fails must not stop the worker
        pass


def _local_line(log, timer, level, text):
    """The local listener's log: httpd's 'slow' line gains ` timer=running`, `stopped`, or `-`
    with no backlog timer (`timer`: the backlog's timer_running, or None); every other line is
    passed on as it is."""
    if level == 'slow':
        state = '-'
        if callable(timer):
            try:
                state = 'running' if timer() else 'stopped'
            except Exception:       # noqa: BLE001 - a log line must not fail a request
                state = '?'
        text = '%s timer=%s' % (text, state)
    log(level, text)


def _tunnel_line(log, level, text):
    """The tunnel's log: every line but httpd's 'slow', which would time the remote client's
    network, not the hub."""
    if level != 'slow':
        log(level, text)


def build(settings, sources, log=None):
    """The Hub for `settings` (kitlib.hubclient.settings.Settings) from `sources` (a Sources):
    module docstring, "Start-up". Nothing is started; close() the Hub to release the store.
    `log(level, text)` gets the listeners' lines (httpd) and the hub's own (module docstring,
    "The log"). ServeError."""
    if not settings.store:
        raise ServeError('no store folder: set hub.store or pm_data in the machine config, '
                         'or name one')
    if not sources.tokens:
        raise ServeError('no credential folder')
    notes = []
    store = _open_store(settings.store)
    if sources.known_repos is not None:
        store.known_repos = sources.known_repos
    listeners = []
    try:
        if store.took_over:
            notes.append('the store lock was left by a server that did not close: taken over')
        try:
            auth.ensure_tokens(sources.tokens)
            auth.ensure_approval_key(sources.tokens)
        except auth.TokenFileError as e:
            raise ServeError(str(e))
        except OSError as e:
            raise ServeError('cannot write the token files in %s: %s' % (sources.tokens, e))
        views_b = None
        if sources.backlog is not None:
            views_b = sources.backlog(settings.backlog_refresh_s)
        sync, sync_off = _make_sync(settings, sources, notes)
        views = serveviews.SiteViews(store, sources, views_b, sync, sync_off, log=log)
        if views_b is not None:
            serveviews._follow_rounds(views_b, views)
        remote_ids = serverepos._RemoteIds()
        repo_name = serverepos._repo_name(views_b, _api.launcher_keys, remote_ids)
        config = _api.ApiConfig(
            approval_key=functools.partial(auth.read_approval_key, sources.tokens),
            checkout=serverepos._checkout(views_b), repo_name=repo_name,
            kit_key=serverepos._kit_key(repo_name, remote_ids), kit_blob=serverepos._kit_blob(),
            kit_text=serverepos._kit_text())
        api = _api.Api(store, views, lock=sources.lock, config=config)
        api.file_later = functools.partial(_file_later, log)    # the answer returns at once
        api.park_later = functools.partial(_park_later, log)    # the question returns at once
        api.record_lock = threading.RLock()     # those threads and the requests write records
        try:
            local = httpd.Listener((httpd.LOOPBACK, settings.port), api, None,
                                   static_dir=sources.static_dir, host_rule=httpd.local_hosts,
                                   log=None if log is None else functools.partial(
                                       _local_line, log, getattr(views_b, 'timer_running', None)))
        except httpd.BindRefused as e:
            raise ServeError('%s: is a hub server running already?' % e)
        listeners.append(local)
        # port 0 binds a new port each run: a sessions file per run would never be read again
        creds = auth.Credentials(sources.tokens, port=local.port, persist=settings.port != 0,
                                 log=log)
        notes.extend(creds.notes)
        local.gate = httpd.local_gate(creds)
        local.routes.update(httpd.pair_routes(creds))
        # the local listener's alone: the tunnel listener is never given a stop route
        stopping = threading.Event()
        local.routes.update(httpd.stop_route(creds, stopping.set,
                                             {'pid': os.getpid(), 'ctime': process.own_ctime()}))
        tunnelled, said = _make_tunnel(settings, sources, api, notes, log)
        health = ('GET', _api.BASE + '/health')
        if tunnelled is not None:
            listeners.append(tunnelled)     # at once: a failure below closes its door too
            # the tunnel listener's alone: only an Access login may ask for a strong code
            tunnelled.routes.update(httpd.strong_pair_route(creds))
            # the local listener's health and Hub.tunnel say why there are no keys; the
            # tunnel's own health never does
            said = functools.partial(servehealth._tunnel_state, tunnelled, True)
            tunnelled.routes[health] = servehealth.health_route(
                api, views, functools.partial(servehealth._tunnel_state, tunnelled), fixed=True)
        local.routes[health] = servehealth.health_route(api, views, said)
        pusher = None
        if sources.push is not None and sources.push_dir:
            pusher = _push.PushWorker(store, api, sources.push, sources.push_dir,
                                     log=functools.partial(_push_line, log))
            notes.append('push notifications on: the asking session and the PM hear of an answer '
                         'or a done to-do')
        sources.pending = api.pending
        sources.handoffs = lambda: _api.newest_handoffs(store.docs('handoffs'))
        timed = _make_jobs(sources, pusher, notes, log)
        if timed is not None:
            api.jobs_status = timed.status   # GET health `jobs` (Api.jobs_seen)
        hub = Hub(settings, store, api, views, creds, listeners, sync, views_b, notes, log,
                  tunnel=said, stopping=stopping, push=pusher, jobs=timed,
                  tokens=sources.tokens, code=code_folders(sources.jobs))
        api.code_status = hub.code_state     # GET health and GET snapshot `code` (Api.code_seen)
        return hub
    except BaseException:
        for lis in listeners:
            lis.close_door()
        store.close()
        raise


def code_folders(jobs=()):
    """The folders whose .py files are the hub's code: this package, the folder of each of the
    timed jobs' makers `jobs` (each one's `folder`: Sources.jobs), so a job added or changed
    on disk is stale code too, and the package the server reads its machine settings through
    (kitlib.config). Each folder once, in that order."""
    out = [os.path.dirname(os.path.abspath(__file__))]
    for maker in jobs or ():
        folder = os.path.abspath(maker.folder)
        if folder not in out:
            out.append(folder)
    return out + [os.path.dirname(os.path.abspath(user.__file__))]


def code_stamp(folders=None):
    """What the hub's code is now: the folder, name, modified time and size of each .py file in
    `folders` (default code_folders()), sorted. A server that started on a different stamp runs
    older code (watch)."""
    out = []
    for folder in folders or code_folders():
        for name in sorted(os.listdir(folder)):
            if name.endswith('.py'):
                st = os.stat(os.path.join(folder, name))
                out.append((os.path.basename(folder), name, st.st_mtime_ns, st.st_size))
    return tuple(out)


class Hub(object):
    """One built server (build): its parts, start() and close(). `notes` are start-up lines for
    the console; `port` and `url` are the local listener's; `tunnel` is what the local listener's
    GET health says of the tunnel listener at this moment (TUNNEL_ON, TUNNEL_BOUND or
    TUNNEL_BOUND_WHY, or TUNNEL_OFF with the reason; '' for a Hub made without build), and
    TUNNEL_CLOSED from the moment close() was called when there was a tunnel listener (with
    none it stays what it was). The `tunnel` given here is that text or a callable giving it.
    `stopping` is the event the local listener's stop route sets (httpd.stop_route); run()
    closes the hub once it is set. A Hub made without one gets an event nothing sets. `push` is
    the push-notification worker (kitlib.hubsite.push.PushWorker: start, stop) or None; `jobs`
    the timed jobs (kitlib.hubsite.jobs.Jobs: start, stop) or None. `tokens` is the token
    folder (selfrestart.LOG_NAME goes there) or None; `code` the folders the code stamp reads
    (build: code_folders of its Sources.jobs); `self_restart` the selfrestart.Watcher, made at
    the first look that finds the code changed unless one was set before."""

    def __init__(self, settings, store, api, views, creds, listeners, sync, backlog_views, notes,
                 log=None, tunnel='', stopping=None, push=None, jobs=None, tokens=None, *, code):
        self.tokens = tokens
        self._folders = code
        self.self_restart = None
        self.push = push
        self.jobs = jobs
        self.settings = settings
        self.store = store
        self.api = api
        self.views = views
        self.creds = creds
        self.listeners = listeners
        self.sync = sync
        self.backlog = backlog_views
        self.notes = notes
        self._tunnel = tunnel
        self.stopping = stopping if stopping is not None else threading.Event()
        self._log = log
        self._life = threading.Lock()       # start() and close()
        self._started = False
        self._closed = False
        self._clean = None
        self._seen = ('', '')               # the sync failure watch() last logged
        self._stale = False                 # watch() said the code is newer than this process
        try:
            self._code = code_stamp(self._folders)
        except Exception:                   # noqa: BLE001 - no stamp, no stale look
            self._code = None
        self._looked = time.monotonic()     # when watch() last took the stamp

    @property
    def port(self):
        return self.listeners[0].port

    @property
    def url(self):
        return self.listeners[0].url

    @property
    def tunnel(self):
        if not callable(self._tunnel):
            return self._tunnel         # no tunnel listener: why, and the same after the close
        # read with no lock: one flag that close() sets once, before it stops the gate
        return servehealth.TUNNEL_CLOSED if self._closed else self._tunnel()

    def code_state(self):
        """codestale.state of this server: stale once watch() saw hub code newer than this
        process. Read with no lock: one flag that _watch_code sets once."""
        return codestale.state(self._stale)

    def _say(self, text, level='error'):
        """One line of the hub's own to the log, with scrub() applied."""
        if self._log is None:
            return
        try:
            self._log(level, gitsync.scrub(text))
        except Exception:           # noqa: BLE001 - a log that fails must not stop the close
            pass

    def watch(self):
        """Look at the git sync once and log what is new (module docstring, "The git sync's
        failures"): an 'error' line when its last pass failed at another step or for another
        reason than the last look saw, a 'note' when it works again. Also the hub code's look
        (_watch_code): a server whose code on disk changed restarts itself through its
        scheduled task (kitlib.hubsite.selfrestart), and logs an 'error' line, once, when it
        does not (STALE_CODE). Never waits for a pass and never raises. One caller at a time:
        run() is it."""
        self._watch_code()
        if self.sync is None:
            return
        try:
            seen = gitsync._failure(self.sync)
        except Exception:           # noqa: BLE001 - a look that fails is tried again next turn
            return
        was, self._seen = self._seen, seen
        if seen == was:
            return
        if seen[0]:
            self._say('the git sync failed at %s: %s' % (seen[0], seen[1] or '-'))
        else:
            self._say('the git sync works again', 'note')

    def _watch_code(self):
        """At most every CODE_LOOK_S: the code stamp to the self-restart (selfrestart.Watcher,
        made at the first look that finds the code changed), then STALE_CODE, once, when it
        says the stale warning shows."""
        if self._code is None:
            return
        at = time.monotonic()
        if at - self._looked < CODE_LOOK_S:
            return
        self._looked = at
        try:
            now = code_stamp(self._folders)
        except Exception:           # noqa: BLE001 - a look that fails is tried again next turn
            return
        if self.self_restart is None:
            if now == self._code:
                return
            self.self_restart = selfrestart.Watcher(self._code, self.port, self.store.root,
                                                    self.tokens, self._say, self._quiet)
        try:
            self.self_restart.look(now)
        except Exception as e:      # noqa: BLE001 - the warning shows, the server goes on
            self._say('the self-restart look failed: %s: %s' % (type(e).__name__, e))
            self.self_restart.stale = True
        if self.self_restart.stale and not self._stale:
            self._stale = True
            self._say(STALE_CODE)

    def _quiet(self):
        """True when no request is in flight on any listener (its counted waits aside: they end
        with the close) and no timed job is running: when a restart may begin."""
        for lis in self.listeners:
            if lis.drain(0) is not True:
                return False
        if self.jobs is not None:
            try:
                blocks = self.jobs.status()
            except Exception:       # noqa: BLE001 - not known to be quiet
                return False
            if any(isinstance(b, dict) and b.get('running') for b in blocks.values()):
                return False
        return True

    def start(self):
        """Start the git sync, the backlog timer, the push-notification worker, the timed jobs
        and every listener, the listeners last so the first request finds the rest running. A push worker
        that fails to start is an 'error' line and the hub serves without it. False, and nothing
        started, when it was started or closed already."""
        with self._life:
            if self._started or self._closed:
                return False
            self._started = True
            if self.sync is not None:
                self.sync.start()
            if self.backlog is not None:
                self.backlog.start()
            if self.push is not None:
                try:
                    self.push.start()
                except Exception as e:      # noqa: BLE001 - the hub serves without pushes
                    self._say('push notifications did not start: %s: %s' % (type(e).__name__, e))
                    self.push = None
            if self.jobs is not None:
                self.jobs.start()
            for lis in self.listeners:
                lis.start()
            if self.backlog is not None:
                threading.Thread(target=self._refile, name='hub-refile', daemon=True).start()
            return True

    def _refile(self):
        """File, once at start, each answer to a bound question that is not filed yet (kitlib.
        hubsite.api file_answer): one a crash or a failure left behind; and park the record of
        each open bound ask not parked yet (api.park_item: a question, a to-do, an approval);
        a hand-written owner line whose ask is gone is logged, never dropped (api._sweep_asks). A failure is logged as a
        note, with its code and fixed phrase only, and the PM sees the question pending."""
        try:
            got = self.api.call('POST', _api.BASE + '/questions/file', _api.AGENT, {},
                                via='hub-start')[1]
        except Exception as e:      # noqa: BLE001 - the hub serves without the refile
            self._say('filing answers at start failed: %s' % type(e).__name__)
            return
        for f in got.get('failed') or []:
            self._say('%s not filed: %s' % (f.get('id'), f.get('message')), 'note')
        for f in got.get('park_failed') or []:
            self._say('%s: record not parked: %s' % (f.get('id'), f.get('message')), 'note')
        for h in got.get('orphan_hand_lines') or []:
            self._say('%s:%s: owner line cites missing %s' % (h.get('repo'), h.get('id'),
                                                              h.get('q')), 'note')

    def _step(self, what, timeout, fn, *args):
        """One step of the close: True when fn(*args) answered True, which is how a step says
        it ended in time. A step that raised, or that answered anything else, is logged by
        its name `what` and answers False."""
        try:
            done = fn(*args)
        except Exception as e:      # noqa: BLE001 - the next step of the close still runs
            self._say('closing: %s failed: %s: %s' % (what, type(e).__name__, e))
            return False
        if done is True:
            return True
        self._say('closing: %s did not end within %s'
                  % (what, 'its time' if timeout is None else '%g s' % timeout))
        return False

    def _last_pass(self):
        """The last git sync pass of the close: it is given no time limit (module docstring;
        _close_sync said so in the log before this). False when it raised; a pass that failed
        is logged and is no unclean close (the next start tries again)."""
        try:
            last = _apart(self.sync.sync_once)
        except Exception as e:      # noqa: BLE001 - the store is still closed
            self._say('closing: the last git sync pass failed: %s: %s' % (type(e).__name__, e))
            return False
        if isinstance(last, dict) and last.get('failed'):
            self._say('the last git sync pass failed at %s: %s; the next start tries again'
                      % (last.get('failed'), last.get('detail') or '-'))
        return True

    def _close_sync(self, timeout, drained):
        """Step 4 of the close (module docstring): True when the sync's thread ended in
        `timeout` and, when the requests had `drained`, the last pass did not raise. Every wait
        for git here is announced in the log first and made through _apart, so a
        KeyboardInterrupt leaves it (close() answers that one)."""
        if drained:
            self._say(LAST_PASS, 'note')
        else:
            self._say(LAST_PASS_SKIPPED)
            self._say(IN_FLIGHT, 'note')
        stopped = self._step('the git sync', timeout, _apart, self.sync.stop, timeout)
        if stopped:
            return self._last_pass() if drained else True
        if drained:
            # A pass is in flight. Wait for it in stop(), which begins no pass, and only then
            # call the last one: a sync_once left waiting behind that pass by a Ctrl+C would
            # begin its own pass after the store closed. LAST_PASS announced this wait. When
            # the wait itself failed the pass may still run: no last pass is called beside it.
            if self._pass_ended():
                self._last_pass()
            else:
                self._say(LEFT_RUNNING)
            return False
        # no last pass will wait for the pass in flight: wait for it here, before the store closes
        self._say(STILL_WAITING, 'note')
        if not self._pass_ended():
            self._say(LEFT_RUNNING)
        return False

    def _pass_ended(self):
        """Wait, with no time limit, for the sync's thread and so for its pass in flight: True
        when it ended. No pass begins here. False when the wait raised (an 'error' line says
        what) or answered anything else; the caller says what follows, and the store is still
        closed."""
        try:
            return _apart(self.sync.stop, None) is True
        except Exception as e:      # noqa: BLE001 - the store is still closed
            self._say('closing: the wait for the git sync pass in flight failed: %s: %s'
                      % (type(e).__name__, e))
            return False

    def _cut_short(self):
        """A KeyboardInterrupt ended the close before the git sync had: say what may be left
        half way, and tell the sync to begin no further pass (no wait: a pass in flight, or a
        last pass that had begun, is left to itself). No wait _close_sync makes is one that
        begins a pass later: module docstring, "The clean close"."""
        self._say(CUT_SHORT)
        try:
            self.sync.stop(0)
        except Exception:           # noqa: BLE001 - the store is still closed
            pass

    def close(self, timeout=CLOSE_TIMEOUT_S):
        """The clean close (module docstring). True when every step ended within `timeout`
        seconds (None: wait) and none raised; a second call answers what the first did (False
        after one that a KeyboardInterrupt left)."""
        with self._life:
            if self._closed:
                return self._clean
            self._closed = True
            self._clean = False
            clean = []
            synced = self.sync is not None and self._started
            try:
                for lis in self.listeners:
                    # first: a request a gate holds (the tunnel's, on a key fetch) is refused
                    # now, so it is not in flight when the requests are waited for below
                    stop = getattr(getattr(lis, 'gate', None), 'stop', None)
                    if callable(stop):
                        clean.append(self._step('the listener gate', timeout, stop))
                for lis in self.listeners:
                    clean.append(self._step('the listener door', timeout, _shut, lis))
                if self.backlog is not None:
                    clean.append(self._step('the backlog timer', timeout, self.backlog.stop,
                                            timeout))
                if self.jobs is not None:
                    clean.append(self._step('the timed jobs', timeout, self.jobs.stop, timeout))
                drained = True
                for lis in self.listeners:
                    done = self._step('the requests in flight', timeout, lis.drain, timeout)
                    drained = drained and done
                    clean.append(done)
                if synced:
                    clean.append(self._close_sync(timeout, drained))
            except KeyboardInterrupt:
                if not synced:
                    raise
                clean.append(False)
                self._cut_short()
            finally:
                if self.push is not None:
                    try:
                        self.push.stop(0)   # no wait: no deliver begins after this
                    except Exception:       # noqa: BLE001 - its own step below says so
                        pass
                try:
                    self.store.close()
                    clean.append(True)
                except Exception as e:      # noqa: BLE001 - the threads are still joined
                    clean.append(False)
                    self._say('closing: the store failed: %s: %s' % (type(e).__name__, e))
                if self.push is not None:
                    # at least STOP_S: a deliver in flight ends, and the outbox is saved
                    wait = _push.STOP_S if timeout is None else max(timeout, _push.STOP_S)
                    clean.append(self._step('the push worker', wait, self.push.stop, wait))
                for lis in self.listeners:
                    clean.append(self._step('the handler threads', timeout, lis.join, timeout))
            self._clean = all(x is True for x in clean)
            return self._clean


def _shut(listener):
    """Close a listener's door: True, as a close step that ended (close_door answers nothing)."""
    listener.close_door()
    return True


def _apart(fn, *args):
    """What fn(*args) answered or raised, with the call made on a thread of its own
    (GIT_WAIT_THREAD, a daemon) while this caller waits for it in turns of POLL_S. A wait with no
    time limit is not interrupted by Ctrl+C on Windows; this one is: a KeyboardInterrupt in the
    caller leaves here within one turn, and the call is then left running on its thread."""
    box = []

    def work():
        try:
            box.append((True, fn(*args)))
        except BaseException as e:     # noqa: BLE001 - raised again in the caller, below
            box.append((False, e))

    thread = threading.Thread(target=work, name=GIT_WAIT_THREAD, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(POLL_S)
    ok, value = box[0]
    if ok:
        return value
    raise value


def run(hub, started=None, stop=None):
    """Start `hub`, call started(hub), serve until the event `stop` is set, the owner's stop
    route sets hub.stopping (hub.py stop) or Ctrl+C, then close it: what Hub.close() answered.
    The hub is closed however this ends. ServeError when the hub was started or closed already.

    The wait is a timed one in a loop: on Windows a wait with no time limit is not interrupted
    by Ctrl+C. Each turn of it looks at hub.stopping (set: a 'note' line, STOP_ASKED, and the
    close), then calls hub.watch(), which logs a git sync failure that is new. No signal
    handler is set, so only Ctrl+C (KeyboardInterrupt), `stop` and the stop route are a clean
    close, and the stop route's is the same close as Ctrl+C's. A
    second Ctrl+C, during the close, is Hub.close's to answer (module docstring, "The clean
    close"): with a git sync started it ends the wait for git and this answers False; with
    none, or when it arrives in the last two steps of the close, it leaves here as a
    KeyboardInterrupt (the store is closed, unless closing it was the step cut)."""
    stop = stop if stop is not None else threading.Event()
    try:
        if not hub.start():
            raise ServeError('this server was started or closed already')
        if started is not None:
            started(hub)
        while not stop.wait(POLL_S):
            if hub.stopping.is_set():
                hub._say(STOP_ASKED, 'note')
                break
            hub.watch()
    except KeyboardInterrupt:
        pass
    finally:
        clean = hub.close()
    return clean
