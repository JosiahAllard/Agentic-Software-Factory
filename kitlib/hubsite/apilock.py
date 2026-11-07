"""kitlib.hubsite.apilock: the api's machine-lock routes ("Machine lock" below): the reads and
writes of the lock door, each a handler of kitlib.hubsite.api's ROUTES. kitlib.hubsite.api
re-exports every name here.

Machine lock. By the owner's decision the hub reads and changes the machine lock in-process,
through `lock`: a door object (kitlib.hubsite.lockdoor.LockDoor on the machine's server; this
module never imports it) with
  revision()                     the lock store's revision
  view(remote=False)             {lock_revision, view, writer}: kitlib.lockui.model to_json's view,
                                 and 'owner' or 'agent': who the lock takes this hub for
  stats(days, since, until)      kitlib.lock.api stats
  reserve, hold, edit, end_hold, end_run, move_to_top, game_mode, games, division
                                 the writes (lockdoor's docstring), each one lock api call, each
                                 taking remote; a refusal is a HubError
Every lock route is closed to an agent (`forbidden`, field role), on every listener: an agent
reads the lock through its own MCP server and changes it with the lock's tools. The reads (GET
lock/view, lock/stats, lock/wait) are OWNER's. The writes, by the owner's decision: on this
machine the owner's login is enough, over the tunnel every lock write needs the strong owner. A
write the decision does not name (a reservation: any hold write; the division) is the strong
owner's on every listener, APPROVER; the others (end a run, move one to the top, game mode, the
games list) are LOCK_WRITE: OWNER with strong, or OWNER on a listener that is not the tunnel
(via is not TUNNEL_VIA), refused with NOT_LOCK_WRITER. A future relay that reaches the local
listener for a remote browser must close that local owner path for its callers.
  POST lock/holds            APPROVER  {kind: reserve, scope: whole|half, start, end (ISO with
                                       offset), note} or {kind: hold, scope, start, end, note,
                                       cpu, gpu} (the Hold dialog's text, read in the PC's zone)
  POST lock/holds/{id}/edit  APPROVER  {start, end, note}: the Edit dialog's text; no note keeps it
  POST lock/holds/{id}/end   APPROVER  {}
  POST lock/division         APPROVER  {fields: {key: text}} or {defaults: true}
  POST lock/runs/{id}/end    LOCK_WRITE {confirm}: confirm repeats the run id
  POST lock/runs/{id}/top    LOCK_WRITE {}
  POST lock/game_mode        LOCK_WRITE {enabled}
  POST lock/games            LOCK_WRITE {text}: the Games dialog's text
A write answers {ok, result} plus the view after it (as GET lock/view; view null when the lock
cannot be read then). The page makes owner holds only; there is no `owner` field. A busy lock is
`conflict` (409). The view answers of the lock routes carry `writes`: {lock, strong, why}, what
this caller may change (lock: the LOCK_WRITE routes, strong: the APPROVER ones too) and, when
one is false, why (WRITES_WHY); a hub the lock takes for an agent may change nothing.
GET lock/view gives the view; `remote` is true only for the tunnel listener's calls (via
TUNNEL_VIA), whose warnings and refusals that can name a file are fixed phrases
(model.REMOTE_SAYS, lockdoor.REMOTE_SAYS); every listener gets the same allow-listed keys. The view
carries `queue_items` (the line behind the runs, with each one's position and why it waits) and its
finished runs carry `slug`; the action hub page's Schedule view (web/index.html) draws them. GET
lock/stats takes days (1 to 366), since and until (ISO times with their offset). GET lock/wait
takes since (a lock revision; default the one now) and timeout (seconds, at most WAIT_MAX_S) and
answers the view plus `changed` once the lock's revision differs from since, or at the timeout
with changed false. The lock store has no wake of its own, so the wait looks at its revision
every LOCK_POLL_S, sleeping in Store.wait so that a close of the hub store ends it at once
(`store`). It is a wait for the listener (WAIT_HANDLERS): it takes one of the listener's wait
slots. With no door every route is `unavailable`, field lock; a door that raises anything but a
HubError is `unavailable` with only the exception's class name (_reason), never its text.
"""
import time

from .apibase import (
    LOCK_DAYS_MAX, OWNER, TUNNEL_VIA, WAIT_MAX_S, WRITES_WHY, _fields, _flag, _invalid, _q, _q_int,
    _reason, _text, _timeout, _typed)
from .store import HubError


def _remote(req):
    return req.via == TUNNEL_VIA


def _lock(api, read, *args, **kw):
    """read(door, ...) on the Api's lock door ("Machine lock"), a read or a write. No door, or a door that raises
    anything but a HubError, is `unavailable` (field lock), named by the exception's class
    only. HomeFenced and other BaseExceptions pass."""
    door = api.lock
    if door is None:
        raise HubError('unavailable', 'this server has no machine lock', 'lock')
    try:
        return read(door, *args, **kw)
    except HubError:
        raise
    except Exception as e:          # noqa: BLE001 - any lock failure is the lock being unavailable
        raise HubError('unavailable', 'the machine lock could not be reached (%s)' % _reason(e),
                       'lock')


def _writes(req, view):
    """What this caller may change from the page ("Machine lock"): {lock, strong, why}. lock: the
    LOCK_WRITE routes; strong: the APPROVER ones as well; why: the reason one is false, or ''."""
    if not isinstance(view, dict) or view.get('writer') != 'owner':
        return {'lock': False, 'strong': False, 'why': WRITES_WHY['agent']}
    strong = req.role == OWNER and req.strong is True
    lock = strong or (req.role == OWNER and not _remote(req))
    why = '' if strong else WRITES_WHY['strong'] if lock else WRITES_WHY['tunnel']
    return {'lock': lock, 'strong': strong, 'why': why}


def _viewed(api, req):
    """The lock door's view for this caller, with `writes`."""
    out = dict(_lock(api, lambda door: door.view(remote=_remote(req))))
    out['writes'] = _writes(req, out)
    return out


def lock_view(api, req):
    return _viewed(api, req)


def _wrote(api, req, write):
    """write(door, remote) -> its result; the answer {ok, result} plus the view after it (view
    null, writes all false, when the lock cannot be read after the write)."""
    remote = _remote(req)
    result = _lock(api, write, remote)
    try:
        out = _viewed(api, req)
    except HubError:
        out = {'lock_revision': None, 'view': None, 'writer': None,
               'writes': {'lock': False, 'strong': False, 'why': ''}}
    out.update(ok=True, result=result)
    return out


def _confirmed(req, b):
    if b.get('confirm') != req.params['id']:
        raise _invalid('confirm must repeat the run id %s' % req.params['id'], 'confirm')


def _strs(b, names):
    return [_text(b, n) for n in names]


def lock_hold_add(api, req):
    b = _fields(req, ('kind', 'scope', 'start', 'end', 'note', 'cpu', 'gpu'), ('kind', 'scope'))
    kind = _text(b, 'kind')
    if kind == 'reserve':
        scope, start, end, note = _strs(b, ('scope', 'start', 'end', 'note'))
        if b.get('cpu') or b.get('gpu'):
            raise _invalid('a reserve takes no slots: it is a whole or half hold', 'cpu')
        return _wrote(api, req, lambda door, remote: door.reserve(scope, start, end, note,
                                                                  remote=remote))
    if kind == 'hold':
        scope, start, end, note, cpu, gpu = _strs(b, ('scope', 'start', 'end', 'note', 'cpu',
                                                      'gpu'))
        return _wrote(api, req, lambda door, remote: door.hold(scope, start, end, note, cpu, gpu,
                                                               remote=remote))
    raise _invalid('kind is reserve or hold, not %r' % (kind,), 'kind')


def lock_hold_edit(api, req):
    b = _fields(req, ('start', 'end', 'note'))
    start, end, note = _strs(b, ('start', 'end', 'note'))
    hid = req.params['id']
    return _wrote(api, req, lambda door, remote: door.edit(hid, start, end, note, remote=remote))


def lock_hold_end(api, req):
    _fields(req, ())
    hid = req.params['id']
    return _wrote(api, req, lambda door, remote: door.end_hold(hid, remote=remote))


def lock_division(api, req):
    b = _fields(req, ('fields', 'defaults'), flags=('defaults',))
    defaults = _flag(b, 'defaults', False)
    fields = _typed(b, 'fields', (dict,), 'an object')
    if defaults == (fields is not None):
        raise _invalid('give the division fields, or defaults true, not both', 'fields')
    return _wrote(api, req, lambda door, remote: door.division(None if defaults else fields,
                                                               remote=remote))


def lock_run_end(api, req):
    b = _fields(req, ('confirm',), ('confirm',))
    _confirmed(req, b)
    rid = req.params['id']
    return _wrote(api, req, lambda door, remote: door.end_run(rid, remote=remote))


def lock_run_top(api, req):
    _fields(req, ())
    rid = req.params['id']
    return _wrote(api, req, lambda door, remote: door.move_to_top(rid, remote=remote))


def lock_game_mode(api, req):
    b = _fields(req, ('enabled',), ('enabled',), flags=('enabled',))
    enabled = b['enabled']
    return _wrote(api, req, lambda door, remote: door.game_mode(enabled, remote=remote))


def lock_games(api, req):
    b = _fields(req, ('text',), blank=('text',))
    text = _text(b, 'text') or ''
    return _wrote(api, req, lambda door, remote: door.games(text, remote=remote))


def lock_stats(api, req):
    days = _q_int(req, 'days', 1, LOCK_DAYS_MAX)
    since, until = _q(req, 'since') or None, _q(req, 'until') or None
    return _lock(api, lambda door: door.stats(days=days, since=since, until=until))


def lock_wait(api, req):
    since = _q_int(req, 'since', 0, 10 ** 12)
    timeout = min(_timeout(req), float(WAIT_MAX_S))
    api.store.check_open()
    if since is None:
        since = _lock(api, lambda door: door.revision())
    end = time.monotonic() + timeout
    # the lock store wakes nobody: look at its revision every lock_poll_s, sleeping in the hub
    # store's wait so that closing the hub ends this wait at once
    while _lock(api, lambda door: door.revision()) == since:
        left = end - time.monotonic()
        if left <= 0:
            break
        api.store.wait(api.store.revision, min(api.lock_poll_s, left))
        api.store.check_open()
    out = _viewed(api, req)
    out['changed'] = out.get('lock_revision') != since
    return out
