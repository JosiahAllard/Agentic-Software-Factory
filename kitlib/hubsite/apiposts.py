"""kitlib.hubsite.apiposts: the api's routes for the owner's notes to the PM (to_pm): list, add,
edit, delete, send and the PM's reply. kitlib.hubsite.api re-exports every name here."""
from . import schema
from .apibase import _fields, _invalid, _natural, _one_of, _pin, _text, _typed, _version, _via
from .apifindings import _act_note, _unpathed
from .store import HubError

def to_pm_list(api, req):
    """The notes; an act note's text and refs with no absolute path but for the local owner
    (Request.full), as for its finding (_seen)."""
    items = api.items('to_pm', req)
    if not req.full:
        for d in items:
            _unpath_act(d)
    return {'items': items}


def _unpath_act(d):
    """Note d (a copy) with an act note's text and refs cut as _unpathed; any other note as
    it is: the owner's own words are the PM's to read whole."""
    if isinstance(d, dict) and d.get('kind') == 'act':
        if isinstance(d.get('text'), str):
            d['text'] = _unpathed(d['text'])
        if isinstance(d.get('refs'), list):
            d['refs'] = [_unpathed(r) if isinstance(r, str) else r for r in d['refs']]


def to_pm_add(api, req):
    b = _fields(req, ('kind', 'repo', 'urgent', 'text', 'refs', 'finding'))
    for name in ('kind', 'repo', 'text'):
        _text(b, name)
    b.setdefault('kind', 'question')
    _one_of(b, 'kind', schema.KINDS['to_pm'])
    refs = _typed(b, 'refs', (list,), 'a list of strings')
    if refs is not None and not all(isinstance(r, str) and r.strip() for r in refs):
        raise _invalid('refs is a list of non-empty strings', 'refs')
    if b['kind'] == 'act':
        _act_note(api, b, req)
    elif 'finding' in b:
        raise _invalid('finding is for an act note (kind act), not a %s one' % b['kind'],
                       'finding')
    elif not (b.get('text') or '').strip():
        raise _invalid('text is required', 'text')
    b.update(status='queued', created_at=api.clock(), sent_at=None, reply=None, done_at=None)
    return api.add('to_pm', b, req, {'status': 'queued'})


def _queued(doc_id, d, what):
    if d.get('status') != 'queued':
        raise HubError('conflict', '%s is %s: only a queued note, one not yet sent to the PM, '
                       'can be %s' % (doc_id, d.get('status'), what), 'status')


def to_pm_edit(api, req):
    b = _fields(req, ('kind', 'repo', 'urgent', 'text', 'if_version'), flags=('urgent',),
                blank=('repo',))
    _pin(b)
    for name in ('kind', 'repo', 'text'):
        _text(b, name)
    changes = {k: v for k, v in b.items() if k != 'if_version'}
    if not changes:
        raise _invalid('give at least one of kind, repo, urgent, text to change', 'body')
    if 'text' in changes and not changes['text'].strip():
        raise _invalid('text cannot be made empty', 'text')
    doc_id = req.params['id']
    d = api.doc('to_pm', doc_id, b)
    _queued(doc_id, d, 'edited')
    kind = changes.get('kind', d.get('kind'))
    if kind != d.get('kind') and 'act' in (kind, d.get('kind')):
        raise _invalid('an act note stays an act note and no other note becomes one: %s is %s'
                       % (doc_id, d.get('kind')), 'kind')
    return api.change('to_pm', doc_id, d, changes, req, {'status': 'queued'})


def to_pm_delete(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    d = api.doc('to_pm', doc_id, b)
    _queued(doc_id, d, 'deleted')
    r = api.store.delete('to_pm', doc_id, if_version=_version(d))
    return {'ok': True, 'id': doc_id, 'deleted': True, 'revision': r.revision}


def _refusal(docs, coll, doc_id, changes):
    """Why the store would refuse to send a queued note: to merge changes (the status `sent`,
    its time, via) into coll/doc_id of docs (a snapshot's collections), as a HubError, or None.
    It is the schema half of the store's rule (Store._validate), asked before anything is
    written. The other half, a new hubvalidate error or REFUSED finding, cannot arise here: of
    a to_pm note hubvalidate judges the status (`sent` is one), its refs and its repo (warnings
    both), and the changes touch none of the last two."""
    new = dict(docs[coll][doc_id], **changes)
    errors = schema.check(coll, new, doc_id)
    if errors:
        return HubError('invalid', '%s/%s: %s' % (coll, doc_id,
                                                  '; '.join(f.message for f in errors)),
                        errors[0].field)
    return None


def to_pm_send(api, req):
    _fields(req, ())
    at = api.clock()
    changes = {'status': 'sent', 'sent_at': at, 'via': _via(req)}
    _, docs = api.store.snapshot(('to_pm',))
    queued = sorted((i for i, d in docs['to_pm'].items() if d.get('status') == 'queued'),
                    key=_natural)
    for doc_id in queued:           # every one is checked before any is sent: a bad one sends none
        why = _refusal(docs, 'to_pm', doc_id, changes)
        if why:
            raise why
    out = {'ok': True, 'sent': queued, 'sent_at': at if queued else None}
    for doc_id in queued:
        r = api.store.update('to_pm', doc_id, changes,
                             if_version=_version(docs['to_pm'][doc_id]))
        out['revision'] = r.revision
    return out


def to_pm_reply(api, req):
    b = _fields(req, ('reply', 'if_version'))
    _pin(b)
    changes = {'status': 'done', 'done_at': api.clock(), 'reply': _text(b, 'reply')}
    doc_id = req.params['id']
    d = api.doc('to_pm', doc_id, b)
    if d.get('status') != 'sent':
        raise _invalid('%s is %s: only a sent item is replied to' % (doc_id, d.get('status')),
                       'status')
    return api.change('to_pm', doc_id, d, changes, req, {'status': 'done'})
