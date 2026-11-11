"""kitlib.hubsite.cli.approvals: the approvals verb of tools/hub.py.

  hub.py approvals [--status S] [--repo R]          `A-1 pending [repo] title` for each request
                                                     for the owner's approval, its status read
                                                     now (an expired one says expired;
                                                     kitlib.ownerfiles.approvals status_at)
  hub.py approvals show A-1                         its status, repo, head, the branch it is
                                                     committed on, title and reason, then
                                                     `<path> <base_blob> -> <blob>` per file
  hub.py approvals add --title T --file PATH CONTENT_FILE [--file ...] [--checkout DIR] [--repo R]
        [--head SHA] [--reason TEXT] [--record REPO:ID]
                                                     `REQUESTED A-001`: asks the owner to approve
                                                     writing each PATH (relative to the repo top)
                                                     as CONTENT_FILE's exact UTF-8 bytes; its base
                                                     is PATH's blob at --head (default: HEAD of
                                                     the checkout DIR, default .), none for a new
                                                     file; --repo defaults to DIR's origin. It only
                                                     asks: no delete, no --from (kitlib.ownerfiles.request);
                                                     --record: the record waits on the owner until
                                                     the request is decided (the hub parks it)
  hub.py approvals apply A-1 [--if-version V]       `APPLIED A-1 <commit>`, or `UNAPPLIED A-1:
                                                     <why>` and exit 1: the owner has the hub
                                                     apply an approved change again, on the
                                                     request's branch in its linked worktree
                                                     (docs/hub-approvals.md "Applying"; an
                                                     approve applies it the first time)
  hub.py approvals withdraw A-1 --from KEY --if-version V    `WITHDRAWN A-1`
                                                     the requester takes back a request still
                                                     undecided, expired or not (KEY is pm:
                                                     approvals store no from); a decided one is
                                                     refused; its record's line is dropped
  hub.py approvals supersede A-1 --by A-2|SHA --from KEY --if-version V
                                                    `SUPERSEDED A-1 by A-2`: the PM (KEY pm) or
                                                     a coordinator session of its repo (any
                                                     sessions.json key whose agent is set and
                                                     whose repo is the approval's) marks a decided
                                                     approval nobody consumed superseded by
                                                     approval A-2 or commit SHA (40 hex, on main
                                                     as last fetched); it leaves pending, keeps
                                                     the owner's decision and is never consumed
"""
import datetime

from kitlib.hubclient import client
from kitlib.hubclient import errors as huberrors
from kitlib.hubsite import api
from kitlib.hubsite.cli import common
from kitlib.ownerfiles import approvals as ownerapprovals


def approvals(a, say, stop=None):
    """The requests from GET /snapshot (the api has no route that lists approvals), each with
    its status read now: approvals.status_at applies the expiry, `malformed` for a bad document.
    add posts one request (kitlib.ownerfiles.request): it never decides or consumes one. apply is
    the owner's (approvals.apply_approved)."""
    if a.sub == 'apply':
        got = ownerapprovals.apply_approved(common.hub(a), a.id, a.if_version)
        if got.get('applied') is not True:
            raise huberrors.ServeError('UNAPPLIED %s: %s' % (a.id, got.get('why')))
        return common.wrote(a, say, got, 'APPLIED %s %s' % (a.id, got.get('commit')))
    if a.sub == 'withdraw':
        got = common.hub(a).post(common.one('approvals', a) + '/delete', client.AGENT,
                                 common.edits(a, {}))
        return common.wrote(a, say, got, 'WITHDRAWN %s' % got.get('id'))
    if a.sub == 'supersede':
        got = common.hub(a).post(common.one('approvals', a) + '/supersede', client.AGENT,
                                 common.edits(a, {'by': a.by}))
        return common.wrote(a, say, got, 'SUPERSEDED %s by %s' % (got.get('id'), a.by))
    if a.sub == 'add':
        from kitlib.ownerfiles import request as ownerrequest  # here: 150 ms the other verbs do not pay
        try:
            body = ownerrequest.build(a.checkout, a.title, [tuple(f) for f in a.files],
                                    repo=getattr(a, 'repo', None), head=a.head, reason=a.reason,
                                    record=a.record)
        except ownerrequest.RequestError as e:
            raise huberrors.ServeError(e.message)
        got = ownerrequest.request(common.hub(a), body)
        return common.wrote(a, say, got, 'REQUESTED %s' % got.get('id'))
    docs = (common.hub(a).get('/snapshot', client.ANY, {'collections': 'approvals'})
            .get('collections') or {}).get('approvals') or {}
    now = datetime.datetime.now(datetime.timezone.utc)
    items = [dict(docs[k], id=k, status=ownerapprovals.status_at(docs[k], now) or 'malformed')
             if isinstance(docs[k], dict) else {'id': k, 'status': 'malformed'}
             for k in sorted(docs, key=api._natural)]
    if a.sub == 'show':
        d = next((d for d in items if d['id'] == a.id), None)
        if d is None:
            raise huberrors.ServeError('no approval %s' % a.id)
        lines = [common.item(d), 'head %s' % d.get('head')] + (
            ['branch %s' % d['branch']] if d.get('branch') else []) + (
            ['reason %s' % d['reason']] if d.get('reason') else [])
        note = d.get('superseded')
        if isinstance(note, dict):
            lines.append('superseded by %s from %s at %s'
                         % (note.get('by'), note.get('from'), note.get('at')))
        lines += ['%s %s -> %s' % (f.get('path'), f.get('base_blob') or 'new',
                                   f.get('blob') or 'deleted')
                  for f in d.get('files') or [] if isinstance(f, dict)]
        return common.show(a, say, d, lines)
    status, repo = getattr(a, 'status', 'all'), getattr(a, 'repo', '').strip().lower()
    items = [d for d in items if status in ('all', d['status'])
             and (not repo or str(d.get('repo') or '').strip().lower() == repo)]
    return common.show(a, say, {'items': items}, [common.item(d) for d in items])
