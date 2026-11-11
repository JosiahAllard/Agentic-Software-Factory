"""kitlib.hubsite.cli.server: the server verbs of tools/hub.py.

  hub.py serve [--port N] [--store PATH]
      serves the hub on the loopback address until Ctrl+C or hub.py stop, which close it
      cleanly: the last changes are committed and the store lock is released. Prints one start
      line and, after a
      clean close, one closing line; notes (NOTE: ...) and errors (ERROR: ...) go to stderr: the
      start-up notes, a git sync pass that failed with git's reason, the wait for git at the
      close before it begins (it is given no time limit) and each step of the close that failed
      or ran out of time. Ctrl+C again ends that wait: an error line then says that a git
      command may have been left half-way and what to check, and the exit code is 1. Where
      the close has no git to wait for (--store, a sync refused at start-up, or its last two
      steps: the store and the handler threads), Ctrl+C again ends the close itself: one line
      says that the close was interrupted and that the store lock is released, and the exit
      code is 1. No line of these holds the user or password of a URL, and neither does a
      `REFUSED:` or a `machine config:` line of any verb. A console that was closed while the
      server served loses the lines, never the exit code.
      The port is hub.port of the machine config (default 8790); --port 0 takes a free one,
      named on the start line. The store is hub.store, else hub/ in the PM data worktree.
      --store PATH serves that folder instead, as a scratch copy: git sync is off for that run,
      so nothing in it is committed or pushed, here or at the close, and no push notification
      is sent. An empty PATH is a usage error, never the configured store. A store another
      server holds is refused.
      Push notifications (docs/hub-push.md "Push notifications") run when the host adapter
      has push() and launcher.state_dir is set; otherwise a start-up note says why they are off.
      With hub.access set in the machine config the server also listens on hub.tunnel_port
      (default 8791, loopback too) for a tunnel program, which this tool never runs: every
      request there needs a Cloudflare Access token (kitlib.hubsite.tunnel). A start-up note
      says that it is on, or why it is off (settings that cannot serve, a port that is taken,
      a listener that could not be made); the hub then serves on its own port alone. A request
      it refuses is a note or an error line, at most one a reason a minute, with the reason and
      never the token. A write made through it is recorded with the source `tunnel`.
  hub.py status [--port N]
      asks the running server for its health and prints the revision, the store, the git sync
      (with git's reason when its last pass failed: the owner's token gets it), each
      repository's backlog fetch age and one line for the tunnel listener: `tunnel: on` once
      the Access keys were fetched (the first request with a token fetches them), `tunnel:
      bound, keys not fetched` until then (with the reason as one fixed word in brackets when
      a fetch failed), or `tunnel: off: ` and why.

  hub.py open [--port N] [--print]
      opens the hub page in the browser, signed in as the owner through a one-time pairing link
      the running server gives for the owner's token. --print prints the link instead. A paired
      browser stays paired across a restart of the server; its session (auth.SESSION_TTL_S, 30
      days) is renewed when it is used with less than half of that left
      (kitlib.hubsite.auth, "Sessions").
  hub.py unpair [--port N]
      ends the session of every browser paired with the server on the port, and every pairing
      link not yet used; the owner's token only. With no server running there it empties the
      sessions file that server reads at its start (in the token folder) itself and says so.
      A new owner token also unpairs every browser.
  hub.py stop [--port N] [--timeout S]
      asks the server on the port to close as Ctrl+C closes it (kitlib.hubsite.control), then
      waits until nothing listens on the port and its process has ended: `STOPPED: ...`. The
      owner's token only; a browser session never stops the server. With nothing answering
      there: `nothing to stop: ...`, exit 0; a server stopped a moment ago may still be closing
      then, as it frees its port before its last git pass and the store's close. --timeout S
      (default 300, approximate: one request in flight may run past it): exit 4 with
      `TIMED OUT: ...` when it has not closed by then; nothing is ever killed, the server goes
      on closing.
  hub.py restart [--port N] [--timeout S]
      stops the server on the port (default hub.port) as stop does, then starts the scheduled
      task kitlib.hubsite.autostart.TASK_NAME again and waits until the new server answers on
      hub.port: `RESTARTED: ...`. It reads the task's state first and starts the task only when
      the task ran the server it stopped, or nothing ran and the task is ready: no task, a
      disabled one or a server started by hand starts nothing, says why and prints the start
      command, exit 3. A task whose state cannot be read or that cannot be started after the
      stop: the STOPPED line, why, and the start command, exit 1. --timeout S covers the whole
      restart: exit 4 as for stop (the console advice only when the new server never answered).
"""
import os

from kitlib.hubclient import client, localask, settings
from kitlib.hubclient import errors as huberrors
from kitlib.hubclient import token as hubtoken
from kitlib.hubsite import api, auth, control, gitsync, httpd, servehealth
from kitlib.hubsite import serve as hubserve
from kitlib.hubsite.cli import common, task
from kitlib.launcher import pushdelivery
from kitlib.proc import process


HEALTH = api.BASE + '/health'


LEVELS = {'error': 'ERROR', 'note': 'NOTE'}     # the log levels that are printed, and as what


CLOSED = 'hub closed: the server ended cleanly and the store lock is released'


# Ctrl+C left the close itself (kitlib.hubsite.serve, "The clean close": a second one with no
# git sync to wait for, or one in the close's last two steps). The lock: step 5 released it, or
# the system does when the process ends.
CLOSE_CUT = ('interrupted during the close: the hub server did not close cleanly. The store '
             'lock is released, at the latest when this process ends')


UNCLEAN = 'the hub server did not close cleanly: see the lines above'


PRIORITY_FAILED = ('the hub server could not raise itself to normal priority: it serves below '
                   'normal and can starve while runs keep every core busy')


def log(say, level, text):
    """The server's error and note lines, to stderr; a request line is not printed. Every line
    goes through gitsync.scrub: a listener's error line may quote a remote URL as git gave it."""
    word = LEVELS.get(level)
    if word is None:
        return
    say('%s: %s' % (word, gitsync.scrub(text)), err=True)   # closed console: a handler goes on


def push_delivery(say, adapter_module):
    """(delivery, folder) for push notifications (docs/hub-push.md "Push notifications"): the
    host adapter's push() and the launcher's state folder, or (None, None) with one note line
    saying why there are none; any fault is such a note, the hub serves without push
    notifications. `adapter_module`: the tool's import of the host adapter (tools/_host.py). A
    scratch store (`serve --store`) never asks."""
    return pushdelivery.push_delivery(adapter_module, note=lambda text: log(say, 'note', text))


def serve(a, say, stop=None, *, machine_sources, adapter_module):
    """`machine_sources(sync=...)`: the machine's Sources (kitlib.hubjobs, which the tool
    binds); `adapter_module`: as for push_delivery."""
    # First, so every git call it starts runs at normal too: the autostart task's default
    # priority is below normal, and a server below the agents' runs starved under load.
    if process.raise_to_normal() == 'failed':
        log(say, 'note', PRIORITY_FAILED)
    s = settings.load()
    if a.port is not None:
        s = s._replace(port=a.port)
    scratch = a.store is not None       # never the truth of the text: '' is refused by the parser
    if scratch:
        s = s._replace(store=os.path.abspath(a.store), push=False)
    sources = machine_sources(sync=not scratch)
    if not scratch:
        sources.push, sources.push_dir = push_delivery(say, adapter_module)
    hub = hubserve.build(s, sources, log=lambda level, text: log(say, level, text))

    def started(h):
        for note in h.notes:
            log(say, 'note', note)
        say('hub serving %s on %s (Ctrl+C stops it); to pair a browser run: %s'
            % (h.store.root, h.url, common.OPEN))

    try:
        clean = hubserve.run(hub, started, stop)
    except KeyboardInterrupt:           # run() lets one through only from the close
        say(CLOSE_CUT, err=True)
        return 1
    if not clean:
        say(UNCLEAN, err=True)
        return 1
    say(CLOSED)                        # the owner sees that the server ended on purpose
    return 0


def status(a, say, stop=None):
    port = a.port or settings.load().port
    folder = hubtoken.folder()
    token = (hubtoken.read_token(folder, hubtoken.OWNER)
             or hubtoken.read_token(folder, hubtoken.AGENT))
    status, obj = localask.ask(port, 'GET', HEALTH, token)
    if status != 200 or obj.get('ok') is not True:
        return common.refused(say, port, status, obj)
    say('hub server on http://%s:%d' % (httpd.LOOPBACK, port))
    for line in servehealth.status_lines(obj):
        say(line)
    return 0


def open_page(a, say, stop=None):
    port = a.port or settings.load().port
    folder = hubtoken.folder()
    token = hubtoken.read_token(folder, hubtoken.OWNER)
    if not token:
        raise huberrors.ServeError('no owner token in %s: run hub.py setup' % folder)
    status, obj = localask.ask(port, 'POST', httpd.PAIR_ROUTE, token)
    path = obj.get('path')
    if status != 200 or not isinstance(path, str) or not path.startswith('/'):
        return common.refused(say, port, status, obj)
    url = 'http://%s:%d%s' % (httpd.LOOPBACK, port, path)
    if a.show:
        say(url)
        return 0
    import webbrowser                       # here: 50 ms the other verbs do not pay
    if not webbrowser.open(url):
        raise huberrors.ServeError('no browser could be opened: use hub.py open --print')
    say('opened the hub page in the browser')
    return 0


def unpair(a, say, stop=None):
    port = a.port or settings.load().port
    folder = hubtoken.folder()
    token = hubtoken.read_token(folder, hubtoken.OWNER)
    if not token:
        raise huberrors.ServeError('no owner token in %s: run hub.py setup' % folder)
    try:
        status, obj = localask.ask(port, 'POST', httpd.UNPAIR_ROUTE, token)
    except huberrors.NotRunning as e:
        # no server holds the sessions in memory: the file the next one reads is emptied here
        failed = []
        creds = auth.Credentials(folder, port=port, persist=True,
                                 log=lambda level, text: failed.append(text))
        creds.unpair_all()
        if failed:
            raise huberrors.ServeError(failed[0])
        say('%s: unpaired every browser in %s, the sessions file the server on that port '
            'reads at its start' % (e, creds.path))
        # a server may have bound the port and read the file between the first ask and the write
        try:
            status, obj = localask.ask(port, 'POST', httpd.UNPAIR_ROUTE, token)
        except huberrors.NotRunning:
            return 0
    if status != 200 or obj.get('ok') is not True:
        return common.refused(say, port, status, obj)
    say('unpaired every browser of the hub server on port %d' % port)
    return 0


def stopped(say, port, got):
    if not got.ran:
        say('nothing to stop: nothing answers on port %d; %s' % (port, control.STILL_CLOSING))
    elif got.pid is None:
        say('STOPPED: the hub server on port %d has closed' % port)
    else:
        say('STOPPED: the hub server on port %d (pid %d) has closed and its process has ended'
            % (port, got.pid))


def stop(a, say, stop=None):
    port = a.port or settings.load().port
    token = common.owner_token()
    try:
        got = control.stop(port, token, a.timeout)
    except control.Refused as e:
        return common.refused(say, e.port, e.status, e.answer)
    except control.TimedOut as e:
        say(gitsync.scrub('TIMED OUT: %s' % e), err=True)
        return client.TIMED_OUT
    stopped(say, port, got)
    return 0


def restart(a, say, stop=None):
    new_port = settings.load().port
    port = a.port or new_port
    token = common.owner_token()
    try:
        got = control.restart(port, token, a.timeout, new_port)
    except control.Refused as e:
        return common.refused(say, e.port, e.status, e.answer)
    except control.StartFailed as e:
        # the stop went through: say so, then why nothing was started and how to start it
        stopped(say, port, e.stopped)
        say(gitsync.scrub('%s could not be started: %s' % (
            task.job() if task.linux() else 'the scheduled task', e.reason)), err=True)
        say(task.task_advice(None), err=True)
        return 1
    except control.TimedOut as e:
        say(gitsync.scrub('TIMED OUT: %s' % e), err=True)
        if e.phase == control.ANSWER_PHASE:
            # the task was started and its server never answered: why is in its start-up lines
            say(task.NO_CONSOLE_LINUX if task.linux() else task.NO_CONSOLE, err=True)
        return client.TIMED_OUT
    stopped(say, port, got.stopped)
    if got.outcome != control.RESTARTED:
        say(task.not_started(got.outcome))
        say(task.task_advice(got.outcome))
        return client.NOT_RUNNING
    say("RESTARTED: the hub server answers on port %d (%s started it)" % (got.port, task.job()))
    return 0
