"""kitlib.hubsite.apiskills: the api's skill-edit routes ("Skill edits" in the api module
docstring): the owner's edits of a kit skill, from draft to committed. kitlib.hubsite.api
re-exports every name here."""
from . import skilledits
from .apibase import (
    SEGMENT_SAYS, _SEGMENT, _fields, _flag, _invalid, _pin, _record_view, _text)
from .store import HubError

# ---- skill edits (module docstring "Skill edits")
SKILL_EDIT_CHANGE = ('draft', 'superseded', 'failed')     # the owner may change the text
SKILL_EDIT_WITHDRAW = ('draft', 'submitted', 'superseded', 'failed')


def _skill_name(b):
    v = _text(b, 'skill')
    if not isinstance(v, str) or not _SEGMENT.match(v) or '..' in v:
        raise HubError('invalid', SEGMENT_SAYS['name'], 'skill')
    return v


def _skill_text(b, name, source):
    """The body's text, CRLF read as LF, `invalid` (field text) where skilledits.why_not says
    it cannot replace the skill source `source` (the skill_source view's answer)."""
    text = _text(b, 'text')
    text = skilledits.normal(text) if isinstance(text, str) else text
    why = skilledits.why_not(text, source.get('text'), name)
    if why is not None:
        raise _invalid(why, 'text')
    return text


def _skill_source(api, name):
    """The skill_source view's answer for the listed skill `name`; its not_found is the answer
    (a skill that is not listed), anything else it raises is `unavailable`."""
    src = _record_view(api, 'skill_source', name)
    if not isinstance(src, dict) or not skilledits.is_blob(src.get('blob')):
        raise HubError('unavailable', 'the skill_source view gave no blob', 'views')
    return src


def _blob_field(b, name):
    v = _text(b, name)
    if v is not None and not skilledits.is_blob(v):
        raise _invalid('%s is a git blob id: 40 lowercase hex digits' % name, name)
    return v


def skill_edits_list(api, req):
    return {'items': api.items('skill_edits', req)}


def _source_at(api, d):
    """{text, blob}: the source text at a superseded or failed edit's source_blob, read with
    kit_text, or None (another status, no source_blob, or kit_text cannot read it)."""
    blob = d.get('source_blob')
    if d.get('status') not in ('superseded', 'failed') or not skilledits.is_blob(blob):
        return None
    try:
        text = api.kit_text(blob)
    except Exception:               # noqa: BLE001 - git could not be asked: not read
        text = None
    return {'text': text, 'blob': blob} if isinstance(text, str) else None


def _main_blob(api, name):
    """The blob of skill `name`'s source on main in the kit checkout (kit_blob), or None."""
    try:
        got = api.kit_blob('main', skilledits.source_path(name))
    except Exception:               # noqa: BLE001 - git could not be asked: not read
        return None
    return got if skilledits.is_blob(got) else None


def skill_edits_get(api, req):
    doc_id = req.params['id']
    d = api.doc('skill_edits', doc_id)
    d['id'] = doc_id
    return {'item': d, 'source': _source_at(api, d)}


def skill_edits_add(api, req):
    b = _fields(req, ('skill', 'text', 'submit'), required=('skill', 'text'), flags=('submit',))
    name = _skill_name(b)
    src = _skill_source(api, name)
    text = _skill_text(b, name, src)
    now = api.clock()
    fields = {'status': 'draft', 'skill': name, 'text': text, 'base_blob': src['blob'],
              'created_at': now}
    if _flag(b, 'submit', False):
        fields.update(status='submitted', submitted_at=now)
    return api.add('skill_edits', fields, req, {'status': fields['status']})


def skill_edits_edit(api, req):
    b = _fields(req, ('text', 'submit', 'if_version'), required=('if_version',),
                flags=('submit',))
    _pin(b, required=True)
    doc_id = req.params['id']
    d = api.doc('skill_edits', doc_id, b)
    status = d.get('status')
    if status not in SKILL_EDIT_CHANGE:
        raise _invalid('%s is %s: only a draft, superseded or failed edit can change'
                       % (doc_id, status), 'status')
    name = d.get('skill')
    src = _skill_source(api, name)
    if b.get('text') is None:
        b['text'] = d.get('text')
    text = _skill_text(b, name, src)
    # a draft keeps the source it was written on; a superseded or failed edit is put on the
    # source the owner was shown to re-apply onto: its source_blob's text when the hub reads it,
    # else the source as served now
    if status == 'draft':
        base = d.get('base_blob')
    else:
        at = _source_at(api, d)
        base = at['blob'] if at else src['blob']
    changes = {'text': text, 'base_blob': base, 'status': 'draft', 'source_blob': None,
               'why': None}
    if _flag(b, 'submit', False):
        main = _main_blob(api, name) if base != src['blob'] else None
        if base != src['blob'] and base != main:
            # the newest source known: main's when the hub reads it, else the served copy
            changes.update(status='superseded', source_blob=main or src['blob'])
        else:
            changes.update(status='submitted', submitted_at=api.clock())
    return api.change('skill_edits', doc_id, d, changes, req, {'status': changes['status']})


def skill_edits_withdraw(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    d = api.doc('skill_edits', doc_id, b)
    if d.get('status') not in SKILL_EDIT_WITHDRAW:
        raise _invalid('%s is %s: a committed or withdrawn edit cannot be withdrawn'
                       % (doc_id, d.get('status')), 'status')
    return api.change('skill_edits', doc_id, d, {'status': 'withdrawn', 'closed_at': api.clock()},
                      req, {'status': 'withdrawn'})


def _submitted(api, b, req):
    """(id, document) of the submitted skill edit the body's if_version pins; `invalid` (field
    status) for any other status."""
    _pin(b, required=True)
    doc_id = req.params['id']
    d = api.doc('skill_edits', doc_id, b)
    if d.get('status') != 'submitted':
        raise _invalid('%s is %s: only a submitted edit is committed or failed'
                       % (doc_id, d.get('status')), 'status')
    return doc_id, d


def skill_edits_done(api, req):
    b = _fields(req, ('branch', 'blob', 'if_version'), required=('branch', 'blob', 'if_version'))
    branch = _text(b, 'branch')
    if not skilledits.BRANCH.match(branch) or '..' in branch or branch.endswith(('/', '.lock')):
        raise _invalid('branch is a branch name, not %r' % branch, 'branch')
    blob = _blob_field(b, 'blob')
    doc_id, d = _submitted(api, b, req)
    text = d.get('text') if isinstance(d.get('text'), str) else ''
    want = skilledits.git_blob(text.encode('utf-8'))
    if blob != want:
        raise _invalid("blob %s is not the owner's text (%s): commit the text `hub.py skill-edit "
                       "show %s --out FILE` writes, byte for byte" % (blob, want, doc_id), 'blob')
    path = skilledits.source_path(d.get('skill'))
    try:
        got = api.kit_blob(branch, path)
    except Exception:               # noqa: BLE001 - git could not be asked: not verified
        got = None
    if got is None:
        raise _invalid('the hub could not read %s on branch %s in the kit checkout'
                       % (path, branch), 'branch')
    if got != want:
        raise _invalid("branch %s holds %s as blob %s, not the owner's text (%s)"
                       % (branch, path, got, want), 'branch')
    return api.change('skill_edits', doc_id, d, {'status': 'committed', 'branch': branch,
                                                 'blob': blob, 'done_at': api.clock()},
                      req, {'status': 'committed'})


def skill_edits_fail(api, req):
    b = _fields(req, ('why', 'current_blob', 'if_version'), required=('why', 'if_version'))
    why = _text(b, 'why')
    if not why.strip():
        raise _invalid('why is required: what stopped the commit', 'why')
    current = _blob_field(b, 'current_blob')
    doc_id, d = _submitted(api, b, req)
    changes = {'status': 'failed', 'why': why, 'closed_at': api.clock()}
    if current is not None and current != d.get('base_blob'):
        changes.update(status='superseded', source_blob=current)
    return api.change('skill_edits', doc_id, d, changes, req, {'status': changes['status']})
