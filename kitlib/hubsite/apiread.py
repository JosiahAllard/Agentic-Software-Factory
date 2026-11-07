"""kitlib.hubsite.apiread: the api's read routes (the backlog and completed views, the skill and
record pages and the owner's record writes, the listings, pending, wait, snapshot and health).
kitlib.hubsite.api re-exports every name here; its WAIT_HANDLERS names wait."""
import time

from . import apilog, schema
from .apibase import (
    AGENT_FROM, API_VERSION, DAYS_MAX, OWNER, ROUTE_SLACK_S, SEGMENT_SAYS, VIEWS,
    WAIT_MAX_S, _SEGMENT, _fields, _invalid, _natural, _q, _q_flag, _q_int, _reason, _record_view,
    _text, _timeout, _typed)
from .apifindings import _seen, _unpathed, _unresolved_marks
from .apiposts import _unpath_act
from .apiskills import _skill_source
from .store import HubError


def backlog(api, req):
    return {'repos': api.shown('backlog', repo=_q(req, 'repo'), state=_q(req, 'state'),
                               fresh=_q_flag(req, 'fresh'))}


def completed(api, req):
    return {'repos': api.shown('completed', repo=_q(req, 'repo'),
                               days=_q_int(req, 'days', 1, DAYS_MAX))}


def _segment(req, name):
    value = req.params.get(name)
    if not isinstance(value, str) or not _SEGMENT.match(value) or '..' in value:
        raise HubError('invalid', SEGMENT_SAYS[name], name)
    return value


def skill_get(api, req):
    return _record_view(api, 'skill', _segment(req, 'name'))


def skill_source_get(api, req):
    """The skill's whole SKILL.md and its blob, what the owner edits ("Skill edits" in apiskills)."""
    return _skill_source(api, _segment(req, 'name'))


def record(api, req):
    return _record_view(api, 'record', _segment(req, 'repo'), _segment(req, 'id'))


def record_new(api, req):
    return _record_view(api, 'record_template', _segment(req, 'repo'))


# ---- the owner's record writes (docs/hub-views.md "The record page")
def _strings(b, name):
    """b[name] as a list of strings: absent is [], one string is a list of one."""
    v = b.get(name)
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
        raise _invalid('%s must be a list of strings' % name, name)
    return v


def record_add(api, req):
    repo = _segment(req, 'repo')
    b = _fields(req, ('id', 'summary', 'scope', 'effort', 'ref'), ('id', 'summary', 'scope'))
    out = _record_view(api, 'record_add', repo, _text(b, 'id'), _text(b, 'summary'),
                       _strings(b, 'scope'), _text(b, 'effort'), _text(b, 'ref'))
    return dict(out, ok=True)


def record_edit(api, req):
    repo, rid = _segment(req, 'repo'), _segment(req, 'id')
    b = _fields(req, ('version', 'changes', 'unset', 'body'), ('version',))
    changes = _typed(b, 'changes', (dict,), 'an object of field names and values') or {}
    out = _record_view(api, 'record_edit', repo, rid, _text(b, 'version'),
                       [(k, _strings(changes, k)) for k in changes], _strings(b, 'unset'),
                       _text(b, 'body'))
    return dict(out, ok=True)


def record_delete(api, req):
    repo, rid = _segment(req, 'repo'), _segment(req, 'id')
    b = _fields(req, ('why', 'confirm'), ('why', 'confirm'))
    if _text(b, 'confirm') != rid:
        raise _invalid('confirm must repeat the record id to delete it', 'confirm')
    out = _record_view(api, 'record_delete', repo, rid, _text(b, 'why'))
    return dict(out, ok=True)


def record_unblock(api, req):
    repo, rid = _segment(req, 'repo'), _segment(req, 'id')
    b = _fields(req, ('on', 'reason'))
    out = _record_view(api, 'record_unblock', repo, rid, _text(b, 'on'), _text(b, 'reason'))
    return dict(out, ok=True)
# ---- end of the owner's record writes


def _listing(name):
    def handler(api, req):
        return {'items': api.shown(name)}
    handler.__name__ = name
    return handler


def pending(api, req):
    items = api.pending(_q(req, 'for'))
    api.seen(req, 'pulled')
    api.handed(req, items)
    return {'items': _unpath_pending(api, items, req)}


def _unpath_pending(api, items, req):
    """Pending items (fresh dicts from api.pending) with an act note's title, its text's first
    line, cut as _unpathed but for the local owner (Request.full), as GET to_pm cuts its text.
    The note's kind is the one read with the item (its `note`, dropped here for every caller),
    never read again: a note deleted since is still known for what it was. A question filed
    into its record says so: `filed` true."""
    for d in items:
        note = d.pop('note', '')
        if d.get('kind') == 'question' and note == 'filed':
            d['filed'] = True
        if (not req.full and d.get('kind') == 'to_pm' and note == 'act'
                and isinstance(d.get('title'), str)):
            d['title'] = _unpathed(d['title'])
    return items


def wait(api, req):
    since = _q_int(req, 'since', 0, 10 ** 12)
    if since is None:
        since = api.store.revision
    timeout = _timeout(req)
    want = _q_flag(req, 'pending')
    for_key = _q(req, 'for')
    if for_key and not want:
        raise _invalid('for picks among the pending items: give pending=1 with it', 'for')
    skip = {s.strip() for s in _q(req, 'except').split(',') if s.strip()}
    if skip and not want:
        raise _invalid('except leaves out pending items: give pending=1 with it', 'except')
    filtered = bool(for_key or skip)

    def mine():
        return [p for p in api.pending(for_key) if p['id'] not in skip]
    # pending is read whether asked for or not: it is the read that refuses a closed store,
    # which Store.wait answers at once and with nothing (a client would loop on it)
    found = mine()
    items = found if want else []
    if want:
        api.seen(req, 'pulled')
    if items:
        api.seen(req, 'waited')
        api.listen(req)
        ch = api.store.changes_since(since)
    else:
        key = object()
        api.seen(req, 'waited', opened=key)
        api.listen(req, opened=key)
        try:
            if filtered:
                ch = _wait_for(api, since, min(timeout, WAIT_MAX_S), mine,
                               for_key == AGENT_FROM)
            else:
                ch = api.store.wait(since, min(timeout, WAIT_MAX_S))
        finally:
            api.seen(req, 'waited', closed=key)
            api.listen(req, closed=key)
        found = mine()
        items = found if want else []
    api.handed(req, items)
    # a filtered wait ends on its own items, a reset or the timeout, never on other writes
    quiet = not ch.reset and not items and (filtered or not ch.events)
    return {'revision': ch.revision, 'reset': ch.reset,
            'timed_out': quiet,
            'changes': [{'collection': e.collection, 'id': e.id, 'op': e.op, 'status': e.status}
                        for e in ch.events],
            'pending': _unpath_pending(api, items, req)}


def _wait_for(api, since, timeout, mine, routes=False):
    """Store.wait until mine() (the pending items this wait is for: its `for` key, less its
    `except` ids) is not empty, the store resets, or timeout seconds pass; a write that leaves
    it empty waits on. With routes (a wait for the PM) it also looks again when an item's route
    next turns to the PM, which no write announces. The changes since `since`, all of them."""
    end = time.monotonic() + timeout
    at = since
    while True:
        left = max(0.0, end - time.monotonic())
        if routes:
            flip = api.route_change_in()
            if flip is not None:
                left = min(left, flip + ROUTE_SLACK_S)
        ch = api.store.wait(at, left)
        if ch.reset:
            return ch
        if mine() or time.monotonic() >= end:
            break
        at = ch.revision
    return api.store.changes_since(since)


# never copied for GET snapshot: a page body is read on its own (GET pages/{id}, "Pages"), the
# hand-offs with GET handoffs, newest per session ("Hand-offs" in apilog)
SNAPSHOT_SKIP = ('page_html', 'handoffs')


def _snapshot_colls(req):
    """The collections a GET snapshot's `collections` query names (comma-separated), or None
    when it names none; a name that is no collection the snapshot gives is `invalid`."""
    raw = _q(req, 'collections')
    names = [n.strip() for n in raw.split(',') if n.strip()]
    if not names:
        return None
    for n in names:
        if n not in schema.COLLECTIONS or n in SNAPSHOT_SKIP:
            raise _invalid('collections names collections of the snapshot, not %s' % ascii(n),
                           'collections')
    return tuple(dict.fromkeys(names))


def snapshot(api, req):
    colls = _snapshot_colls(req)
    if _q(req, 'bare') and _q_flag(req, 'bare'):
        # the snapshot less its documents: what can move with no store write (the views, the
        # pending routes, the status). The page reads it after a wait that timed out, which no
        # write ended, keeping the documents it holds for that revision (module docstring)
        if colls is not None:
            raise _invalid('bare leaves every collection out: give collections or bare, not both',
                           'bare')
        revision, docs = api.store.revision, {}
    else:
        revision, docs = api.store.snapshot(colls, skip=SNAPSHOT_SKIP)
    if docs.get('landings'):        # the newest FEED_MAX: every event is kept ("Landings" in apilog)
        feed = docs['landings']
        docs['landings'] = {k: feed[k] for k in sorted(feed, key=_natural)[-apilog.FEED_MAX:]}
    if not req.full:                # the paths as GET to_pm and GET finding_marks cut them
        for d in (docs.get('to_pm') or {}).values():
            _unpath_act(d)
        marks = docs.get('finding_marks') or {}
        for doc_id, m in list(marks.items()):
            if isinstance(m, dict):
                marks[doc_id] = _seen(m, req)
    if docs.get('finding_marks'):   # the marks GET finding_marks gives: resolved ones hidden
        docs['finding_marks'] = _unresolved_marks(api, docs['finding_marks'])
    if colls is not None:           # those collections alone: no views, pending or status
        return {'revision': revision, 'collections': docs}
    views, missing = {}, {}
    for name in VIEWS:
        try:
            views[name] = api.view(name)
        except Exception as e:      # noqa: BLE001 - the first paint shows what there is
            views[name] = None
            missing[name] = _reason(e)
    return {'revision': revision, 'collections': docs,
            'pending': _unpath_pending(api, api.pending(), req),
            'views': views, 'unavailable': missing, 'pm_seen': api.pm_seen(),
            'push': api.push_seen(), 'watcher': api.watcher_seen(), 'code': api.code_seen(),
            'strong': req.strong is True}


def health(api, req):
    out, missing = {'git': None, 'backlog': None}, {}
    try:
        extra = api.view('health')
    except Exception as e:          # noqa: BLE001 - health answers whatever a view does
        extra, missing = None, {'health': _reason(e)}
    if isinstance(extra, dict):
        out.update(extra)
    out.pop('store', None)
    out.update(ok=True, version=API_VERSION, revision=api.store.revision, unavailable=missing,
               watcher=api.watcher_seen(), push=api.push_seen(), code=api.code_seen(),
               jobs=api.jobs_seen())
    if req.role == OWNER:           # where the store is on this machine is the owner's to know
        out['store'] = api.store.root
    return out
