"""kitlib.hubsite.apipages: the api's page routes ("Pages"): publish, place, read and delete an
HTML page and its card. kitlib.hubsite.api re-exports every name here."""
from kitlib import hubvalidate

from . import pagecheck
from . import schema
from .apibase import (
    _REF_SHAPED, _agent_from, _fields, _invalid, _pin, _text, _typed, _version, _via)
from .store import PAGES_LOCK, HubError


def pages_list(api, req):
    return {'items': api.items('pages', req)}


def pages_publish(api, req):
    b = _fields(req, ('id', 'title', 'html', 'repo', 'from', 'summary', 'refs', 'kind', 'folder',
                      'if_version'), required=('id', 'title', 'html'))
    for name in ('id', 'title', 'html', 'repo', 'from', 'summary', 'kind', 'folder'):
        _text(b, name)
    pin = _pin(b)
    doc_id = b['id']
    why = schema.check_id(doc_id)
    if why:
        raise _invalid(why, 'id')
    shaped = _REF_SHAPED.search(doc_id)
    if shaped:
        raise _invalid('a page id cannot hold a hub item\'s or a ruling\'s id (%s holds %s): a ref '
                       'that names it would be read as that item' % (doc_id, shaped.group()), 'id')
    refs = _typed(b, 'refs', (list,), 'a list of strings')
    if refs is not None and not all(isinstance(r, str) for r in refs):
        raise _invalid('refs must be a list of strings', 'refs')
    why = pagecheck.check(b['html'])
    if why:
        raise _invalid(why, 'html')
    with PAGES_LOCK:
        out = _pages_write(api, req, b, doc_id, pin)
    for said in pagecheck.warnings(b['html']):      # the page is written; the publisher is told
        out['warnings'].append(hubvalidate.Finding(
            hubvalidate.WARN, 'page_html/%s' % doc_id, 'html', 'media-blocked', said)._asdict())
    return out


def _pages_write(api, req, b, doc_id, pin):
    """pages_publish from the reads on, called with PAGES_LOCK held."""
    card, body = api.store.get('pages', doc_id), api.store.get('page_html', doc_id)
    if card is not None and pin is None:
        raise HubError('conflict', 'pages/%s is at version %d: give it as if_version to publish '
                       'over it' % (doc_id, _version(card)), 'if_version')
    if card is not None and pin != _version(card):
        raise HubError('conflict', 'pages/%s is at version %d, not %d'
                       % (doc_id, _version(card), pin), 'if_version')
    if card is None and pin:
        raise HubError('conflict', 'there is no pages/%s at version %d: it is not there'
                       % (doc_id, pin), 'if_version')
    size = len(b['html'].encode('utf-8'))
    fields = {k: b[k] for k in ('title', 'repo', 'summary', 'refs', 'kind', 'folder') if k in b}
    for k in ('kind', 'folder'):        # put replaces the card: keep what place set ("Pages")
        if k not in fields and card is not None and card.get(k) is not None:
            fields[k] = card[k]
    fields.update({'from': _agent_from(b.get('from') or ''), 'at': api.clock(), 'bytes': size,
                   'via': _via(req)})
    # the body first, each write pinned to what was read under the lock: a card never names a
    # body that is not there, and a second publisher reads only after both writes ("Pages")
    wrote = api.store.put('page_html', doc_id, {'html': b['html'], 'via': _via(req)},
                          if_version=_version(body) if body is not None else 0)
    r = api.store.put('pages', doc_id, fields, if_version=_version(card) if card is not None else 0)
    return api.written(r, {'bytes': size, 'html_version': wrote.version})


PAGE_PLACE_FIELDS = ('kind', 'folder', 'repo')


def pages_place(api, req):
    """Sets a card's kind, folder and repo, pinned by if_version; the body is never written
    ("Pages")."""
    b = _fields(req, PAGE_PLACE_FIELDS + ('if_version',), blank=PAGE_PLACE_FIELDS)
    _pin(b, required=True)
    for name in PAGE_PLACE_FIELDS:
        _text(b, name)
    changes = {k: b[k] for k in PAGE_PLACE_FIELDS if k in b}
    if not changes:
        raise _invalid('give at least one of %s to change' % ', '.join(PAGE_PLACE_FIELDS), 'body')
    doc_id = req.params['id']
    with PAGES_LOCK:        # no publish rewrites the card between this read and the write
        d = api.doc('pages', doc_id, b)
        return api.change('pages', doc_id, d, changes, req)


def pages_get(api, req):
    doc_id = req.params['id']
    d = api.doc('page_html', doc_id)
    return {'ok': True, 'id': doc_id, 'html': d.get('html'), 'version': _version(d)}


def pages_delete(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    with PAGES_LOCK:        # no publish runs between the reads and the two deletes
        d = api.doc('pages', doc_id, b)
        r = api.store.delete('pages', doc_id, if_version=_version(d))
        body = api.store.get('page_html', doc_id)   # the card first: it never names no body
        if body is not None:
            # A body delete that fails (the store's own error: under the lock no publish can
            # move its version) leaves an orphan body with no card. The card stays deleted:
            # hubvalidate warns of the orphan, no card offers it, and the next publish of
            # this id writes over it ("Pages").
            r = api.store.delete('page_html', doc_id, if_version=_version(body))
    return {'ok': True, 'id': doc_id, 'deleted': True, 'revision': r.revision}
