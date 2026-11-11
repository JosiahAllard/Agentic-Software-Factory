"""kitlib.hubsite.apilog: the api's log routes: the inbox ("Inbox"), the landing feed ("Landings")
and the hand-off records ("Hand-offs"). kitlib.hubsite.api re-exports every name here.

Landings (docs/hub-approvals.md "landings/L-NNN"). POST landings (AGENT) adds one event of the
landing feed, which tools/land.py posts for each branch it landed (kitlib.landing.feed): the
fields of schema.FIELDS['landings'] (repo, branch, tip and main required; records a list of
record ids; approvals, when given, {A-<n>: commit sha} of the hub approvals the landing put on
main, approvals.landed), and the hub sets posted_at. Each consumed file approval an event names
whose repo is the event's (repo_name of its remote) is marked spent (that sha) and spent_at,
once: display only, for a later page (none reads it yet); its status stays, any other is
skipped, and the answer's `spent` lists the ids marked. GET landings (ANY)
lists them newest first, filtered by `repo`, at most `limit` (default FEED_MAX); GET snapshot
carries the newest FEED_MAX. Every event is kept; none is ever pending or pushed, and no route
changes or deletes one.

Hand-offs (docs/hub-approvals.md "handoffs/C-NNN"). POST handoffs (AGENT) records that a session
checkpointed and stands down, beside its ready reply to the PM (skill session-handoff): {from,
kind, state_path}, all required. from is a session key checked as a question's is (a launcher
session key, read now, or `pm`); kind is one of HANDOFF_KINDS (handoff, restart, stop: the word
of the reply; checkin: a successor that has checked in, kitlib.launcher.handoff); state_path is
the absolute path of the state note, on one line. The hub sets `at`.
GET handoffs (ANY) gives the newest record of each session (newest_handoffs: the highest id per
`from`), sorted by key, or with `from=<key>` that key's alone; over the tunnel only, the state
path is cut to `.../<its file name>` (ntpath.basename). Every record is kept; none is pending or pushed, no route changes or
deletes one, and GET snapshot leaves them out.
"""
import ntpath
import os
import re

from kitlib import hubvalidate

from . import schema
from .apibase import (
    FEED_MAX, LIMIT_DIGITS, _SURROGATE, _agent_from, _fields, _flag, _invalid, _natural, _one_of,
    _pin, _q, _session_from, _text, _typed, _version, _via)
from .apilock import _remote
from .store import HubError


def inbox_add(api, req):
    b = _fields(req, ('ref', 'asked', 'text', 'title', 'repo', 'from', 'refs'),
                required=('text',))
    for name in ('ref', 'asked', 'text', 'title', 'repo', 'from'):
        _text(b, name)
    if 'from' in b:                 # the route is an agent's: the same rule as a to-do's from
        b['from'] = _agent_from(b['from'])
    b.update(at=api.clock(), read=False)
    return api.add('inbox', b, req)


def inbox_read(api, req):
    b = _fields(req, ('read', 'if_version'), flags=('read',))
    _pin(b)
    read = _flag(b, 'read', True)
    doc_id = req.params['id']
    d = api.doc('inbox', doc_id, b)
    changes = {'read': read, 'read_at': api.clock() if read else None}
    return api.change('inbox', doc_id, d, changes, req, changes)


# landings ("Landings"): the feed tools/land.py posts, one event per landed branch
LANDING_FIELDS = tuple(f for f in sorted(schema.FIELDS['landings'][1]) if f != 'posted_at')


def _newest(docs, n):
    """The `n` documents of `docs` (a list, each with its `id`) with the highest ids, newest
    (highest) first."""
    return sorted(docs, key=lambda d: _natural(d['id']), reverse=True)[:n]


def landings_list(api, req):
    """The events, newest first: at most `limit` (a whole number of at most LIMIT_DIGITS
    digits, default FEED_MAX) of them, filtered by repo as every listing is."""
    text = _q(req, 'limit')
    if text and not (text.isdigit() and text.isascii() and len(text) <= LIMIT_DIGITS
                     and int(text) > 0):
        raise _invalid('limit is a whole number from 1 to %s, not %r'
                       % ('9' * LIMIT_DIGITS, text[:40]), 'limit')
    return {'items': _newest(api.items('landings', req), int(text) if text else FEED_MAX)}


def landings_add(api, req):
    b = _fields(req, LANDING_FIELDS, required=schema.FIELDS['landings'][0])
    records = _typed(b, 'records', (list,), 'a list')
    if records is not None and not all(isinstance(r, str) and r.strip() for r in records):
        raise _invalid('records is a list of record ids', 'records')
    from kitlib.ownerfiles import approvals as ap
    landed = _typed(b, 'approvals', (dict,), 'an object')
    if landed is not None and not all(isinstance(k, str) and ap.APPROVAL_ID.match(k)
                                      and isinstance(v, str) and ap.SHA.match(v)
                                      for k, v in landed.items()):
        raise _invalid('approvals is an object of approval ids (A-<n>) to commit shas',
                       'approvals')
    b['posted_at'] = api.clock()
    out = api.add('landings', b, req)
    out['spent'] = _mark_spent(api, landed or {}, b['repo'], b['posted_at'], req)
    return out


def _mark_spent(api, landed, repo, now, req):
    """The ids of the file approvals in `landed` ({id: sha}) marked spent now: each consumed
    one, of the event's `repo` (api.repo_name of its remote; none when that is unknown), with no
    spent yet. The event is any agent's: it cannot mark an approval no commit used, or another
    repository's. Display only, for a later page; one that changed meanwhile is left."""
    marked, repo = [], str(repo).strip().lower()
    for doc_id in sorted(landed, key=_natural):
        d = api.store.get('approvals', doc_id)
        if not (isinstance(d, dict) and d.get('kind') is None and d.get('status') == 'consumed'
                and not d.get('spent') and _repo_name_of(api, d.get('repo')) == repo):
            continue
        try:
            api.store.update('approvals', doc_id,
                             {'spent': landed[doc_id], 'spent_at': now, 'via': _via(req)},
                             if_version=_version(d))
        except HubError:
            continue
        marked.append(doc_id)
    return marked


def _repo_name_of(api, repo_id):
    """The configured repository name (lower case) of the remote `repo_id`, or None."""
    try:
        name = api.repo_name(repo_id) if isinstance(repo_id, str) and repo_id else None
    except Exception:           # noqa: BLE001 - no name: nothing is marked
        return None
    return name.strip().lower() if isinstance(name, str) and name.strip() else None


# hand-offs ("Hand-offs"): the record a session writes beside its ready reply to the PM
_CONTROL = re.compile('[\x00-\x1f\x7f]')


def newest_handoffs(docs):
    """{session key: its newest hand-off record} of docs ({id: document} of `handoffs`), each
    with its `id`: the record with the highest id of each `from` (ids only grow). A document
    with no string `from` is left out."""
    out = {}
    for doc_id in sorted(docs, key=_natural):
        d = docs[doc_id]
        if isinstance(d, dict) and isinstance(d.get('from'), str):
            out[d['from']] = dict(d, id=doc_id)
    return out


def handoffs_list(api, req):
    """The newest record of each session, sorted by session key; with `from=<key>` that key's
    alone (none: an empty list). Over the tunnel only, the state path is cut to
    `.../<its file name>`."""
    newest = newest_handoffs(api.store.docs('handoffs'))
    who = _q(req, 'from')
    items = [newest[k] for k in sorted(newest) if not who or k == who]
    if _remote(req):
        for d in items:
            if isinstance(d.get('state_path'), str):
                d['state_path'] = '.../' + ntpath.basename(d['state_path'])
    return {'items': items}


def handoffs_add(api, req):
    """A new record: `from` (a session key, checked as a question's: _session_from), `kind`
    (HANDOFF_KINDS) and `state_path` (an absolute path, on one line), all required; the hub
    sets `at`."""
    b = _fields(req, ('from', 'kind', 'state_path'), required=('from', 'kind', 'state_path'))
    _one_of(b, 'kind', hubvalidate.HANDOFF_KINDS)
    path = _text(b, 'state_path').strip()
    if _CONTROL.search(path) or _SURROGATE.search(path) or not os.path.isabs(path):
        raise _invalid('state_path is the absolute path of your state note, on one line, not %s'
                       % ascii(path[:200]), 'state_path')
    b['from'] = _session_from(api, _text(b, 'from').strip(), 'a hand-off record')
    b.update(state_path=path, at=api.clock())
    return api.add('handoffs', b, req)
