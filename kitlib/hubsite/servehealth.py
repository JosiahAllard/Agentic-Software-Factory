"""kitlib.hubsite.servehealth: GET health of the hub server's listeners, and what `hub status`
prints of it (kitlib.hubsite.serve gives each listener its health_route).

  health_route(api, views, tunnel='', fixed=False)
                                  a listener's route for GET health (below)
  status_lines(health, now=None)  what `hub status` prints for a GET health answer
  TUNNEL_*, WORD_*                the words health's `tunnel` and the tunnel's answer say

GET health and the sync's reason (health_route). The Api's health carries no `detail` for
anyone. The route adds `git.detail`, the reason of a failing sync with scrub() applied, for the
owner role only: an agent and a caller with no role get the Api's answer as it is. `detail` may
name a remote URL or a folder of this machine, which is why it is the owner's alone.
The route also adds `tunnel` to every 200 answer, so for the owner and the agent alike and on
both listeners; `hub status` prints it (status_lines) and `Hub.tunnel` is the local listener's:
  TUNNEL_OFF with why the tunnel listener did not start, in kitlib.hubsite.tunnel's fixed
      phrases, TUNNEL_BIND or TUNNEL_FAILED;
  TUNNEL_ON when the listener is bound and its gate has the team's keys to check a token with
      (tunnel.Gate.keys: a good fetch no older than access.STALE_LIMIT_S);
  TUNNEL_BOUND when it is bound and has no such keys. On the local listener, and there only,
      TUNNEL_BOUND_WHY names the reason the last fetch failed, one word of access.REASONS
      (`keys-fetch`, ...), never an exception's text.
It never holds a value of the settings (no team name, audience tag or email) and never the
system's words for a failed bind: those are in the console note. Health asks the gate what it
holds: it starts no key fetch and waits for none, and the gate fetches keys only when a request
brings a token. So a server just started says TUNNEL_BOUND until the first request with a token
made a good fetch, and `on` does not say that a tunnel program reaches the listener. A key
source with no ready() never says it has keys (tunnel.Gate.keys), so it is TUNNEL_BOUND too.
After Hub.close() was called, `Hub.tunnel` of a hub that had a tunnel listener is TUNNEL_CLOSED,
not TUNNEL_BOUND: the gate is stopped and its keys are no longer asked for. Only `Hub.tunnel`
says that word; no GET health answer does (a request the close finds in flight on the local
listener is answered from the stopped gate, so with TUNNEL_BOUND).

GET health through the tunnel (health_route with fixed=True, _fixed_health). The tunnel
listener's answer holds fixed words and no other value: {ok: true, version, store: WORD_OK,
git: WORD_OK | WORD_FAILED | WORD_OFF | WORD_UNKNOWN, tunnel: TUNNEL_ON | TUNNEL_BOUND}. No
store folder, no git branch, step or reason, no backlog, no revision. `store` is WORD_OK
whenever the Api answered at all; `git` is WORD_OFF with no sync, WORD_FAILED when its last
pass failed, WORD_UNKNOWN when the Api gave no git status. An answer of the Api that is no 200
leaves as its status and error code with the message HEALTH_REFUSED, never the Api's own
message.
"""
import datetime

from kitlib.hubclient import codestale

from . import api as _api
from . import access, gitsync, httpd, tunnel

TUNNEL_ON = 'on'                # GET health's `tunnel`, and `Hub.tunnel`
TUNNEL_BOUND = 'bound, keys not fetched'
TUNNEL_BOUND_WHY = 'bound, keys not fetched (%s)'   # the local listener's: one of access.REASONS
TUNNEL_OFF = 'off: %s'
TUNNEL_CLOSED = 'closed'        # `Hub.tunnel` once close() was called; no GET health says it
TUNNEL_BIND = 'the tunnel port could not be bound: the server console says why'
TUNNEL_FAILED = 'the tunnel listener could not be made: the server console says why'
# GET health through the tunnel listener: these words and no other value
WORD_OK, WORD_FAILED, WORD_OFF, WORD_UNKNOWN = 'ok', 'failed', 'off', 'unknown'
HEALTH_REFUSED = 'health could not be answered'


def _tunnel_state(listener, why=False):
    """What health says of the bound tunnel `listener`: TUNNEL_ON when its gate has the keys to
    check a token with (tunnel.Gate.keys: it asks the key source and starts no fetch, waits for
    none), else TUNNEL_BOUND. With `why`, and a reason that is one of access.REASONS, it is
    TUNNEL_BOUND_WHY with that word. A gate that cannot say, or that raises, has no keys."""
    try:
        fetched, reason = listener.gate.keys()
    except Exception:               # noqa: BLE001 - health still answers
        fetched, reason = False, ''
    if fetched is True:
        return TUNNEL_ON
    if why and isinstance(reason, str) and reason in access.REASONS:
        return TUNNEL_BOUND_WHY % reason
    return TUNNEL_BOUND


def _fixed_health(status, obj, tunnel):
    """GET health as the tunnel listener answers it, from the Api's (status, obj): fixed words
    and nothing else (module docstring, "GET health through the tunnel")."""
    if status != 200 or not isinstance(obj, dict):
        error = obj.get('error') if isinstance(obj, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if status == 200:           # a 200 that is no object is no answer at all
            status, code = 500, 'internal'
        elif not (isinstance(code, str) and code in _api.STATUS):
            code = 'internal'
        return httpd.refusal(status, code, HEALTH_REFUSED)
    git = obj.get('git')
    if not isinstance(git, dict):
        word = WORD_UNKNOWN
    elif git.get('off'):
        word = WORD_OFF
    else:
        word = WORD_FAILED if git.get('failed') else WORD_OK
    said = {'ok': True, 'version': _api.API_VERSION, 'store': WORD_OK, 'git': word}
    if tunnel:
        said['tunnel'] = tunnel
    return httpd.json_response(200, said)


def health_route(api, views, tunnel='', fixed=False):
    """The route a listener answers GET health with (module docstring, "GET health and the
    sync's reason"): the answer of `api` (an Api), and for the owner role `git.detail` from
    `views` (a SiteViews) when the sync is failing at the step the answer names. `tunnel` is
    added to every 200 answer when given: a text (TUNNEL_OFF with a fixed reason), or a callable
    giving one, asked at each request (_tunnel_state). With `fixed`, the tunnel listener's
    route, the answer is _fixed_health's instead: the Api is asked all the same, and nothing of
    its answer but a fixed word leaves."""
    def route(request):
        # raise_unexpected: as the listener's own Api call, so an error is logged there; and
        # strong, as that call, only for the request its gate marked (httpd.strong_words)
        status, obj = api.call(request.method, request.path, request.role, request.body,
                               request.query, request.via, raise_unexpected=True,
                               **httpd.strong_words(request))
        said = tunnel() if callable(tunnel) else tunnel
        if fixed:
            return _fixed_health(status, obj, said)
        git = obj.get('git') if status == 200 and request.role == _api.OWNER else None
        if isinstance(git, dict):
            failed, detail = views.git_failure()
            if detail and git.get('failed') == failed:
                git['detail'] = detail
        if status == 200 and said and isinstance(obj, dict):
            obj['tunnel'] = said
        return httpd.json_response(status, obj)
    return route


def _age(text, now):
    """`now` less the ISO time `text`, in words, or None when `text` is no time."""
    try:
        at = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.timezone.utc)
    s = max(0, int((now - at).total_seconds()))
    if s < 120:
        return '%d s' % s
    if s < 7200:
        return '%d min' % (s // 60)
    return '%d h' % (s // 3600)


def _git_line(git):
    if not isinstance(git, dict):
        return 'no answer'
    if git.get('off'):
        return str(git['off'])
    parts = ['running' if git.get('running') else 'not running']
    if git.get('branch'):
        parts.append('branch %s' % git['branch'])
    pending = [w for k, w in (('pending_commit', 'a commit'), ('pending_push', 'a push'))
               if git.get(k)]
    parts.append('pending: %s' % ' and '.join(pending) if pending else 'nothing pending')
    if git.get('diverged'):
        parts.append('diverged from the remote: a person joins them')
    if git.get('failed'):
        # `detail` is in the owner's answer only (health_route); scrubbed there, and here again
        why = gitsync.scrub(git['detail']) if git.get('detail') else ''
        parts.append('the last pass failed at %s%s' % (git['failed'], ': %s' % why if why else ''))
    return ', '.join(parts)


JOB_NOTE_MAX = 60               # characters of a job's note or error on the `jobs:` status line


def _jobs_line(blocks):
    """GET health `jobs` in one line: `none` for null, else per job (by name) `<name> runs R,
    failures F` with `, running` while it runs and its error (while failures is not 0) or else
    its last note, scrubbed and cut to JOB_NOTE_MAX characters."""
    if not isinstance(blocks, dict) or not blocks:
        return 'none'
    parts = []
    for name in sorted(blocks):
        b = blocks[name] if isinstance(blocks[name], dict) else {}
        text = '%s runs %s, failures %s' % (name, b.get('runs'), b.get('failures'))
        if b.get('running'):
            text += ', running'
        said = b.get('error') if b.get('failures') else b.get('note')
        if isinstance(said, str) and said:
            said = ' '.join(gitsync.scrub(said).split())     # one line, whatever the job wrote
            text += ': ' + (said if len(said) <= JOB_NOTE_MAX else said[:JOB_NOTE_MAX - 3] + '...')
        parts.append(text)
    return '; '.join(parts)


def status_lines(health, now=None):
    """The lines `hub status` prints for a GET health answer: the revision, the store (the
    owner's answer has it), the git sync, each repository's backlog fetch age, `code: current`
    or `code: stale: ` and the restart (from health's `code`, codestale.notice), `jobs: ` and
    each timed job's runs, failures and last note or error (_jobs_line; `jobs: none` when the
    server runs none) (neither line when the answer lacks the field, as an older server's), the
    tunnel
    listener in one line (`tunnel: on`, `tunnel: bound, keys not fetched` with the reason in
    brackets when health gave one, or `tunnel: off: ` and why; none when the answer has no such
    text, as an older server's) and what is unavailable. `now`: an aware datetime (default the
    UTC clock)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lines = ['revision %s' % health.get('revision')]
    if health.get('store'):
        lines.append('store %s' % health['store'])
    lines.append('git: %s' % _git_line(health.get('git')))
    repos = health.get('backlog')
    if not isinstance(repos, list):
        lines.append('backlog: no source on this server')
    elif not health.get('backlog_at'):
        lines.append('backlog: not read yet')
    else:
        for r in repos:
            if not isinstance(r, dict):
                continue
            age = _age(r.get('fetched_at'), now)
            line = 'backlog %s: %s' % (r.get('repo'), 'fetched %s ago' % age if age
                                       else 'never fetched')
            lines.append(line + ('; %s' % r['error'] if r.get('error') else ''))
    for key, what in (('backlog_error', 'backlog'), ('backlog_timer', 'backlog timer')):
        if health.get(key):
            lines.append('%s: %s' % (what, health[key]))
    if health.get('backlog_timer_running') is False:    # null or absent: no timer to say of
        lines.append('backlog timer: not running')
    code = health.get('code')
    if isinstance(code, dict):
        stale = codestale.notice(code)
        lines.append('code: %s' % ('stale: ' + stale if stale else 'current'))
    if 'jobs' in health:
        lines.append('jobs: %s' % _jobs_line(health['jobs']))
    said = health.get('tunnel')
    if isinstance(said, str) and said:
        lines.append('tunnel: %s' % gitsync.scrub(said))    # fixed words; scrubbed all the same
    missing = health.get('unavailable')
    if isinstance(missing, dict) and missing:
        lines.append('unavailable: %s' % ', '.join('%s (%s)' % (k, missing[k])
                                                   for k in sorted(missing)))
    return lines
