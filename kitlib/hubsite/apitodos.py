"""kitlib.hubsite.apitodos: the api's to-do routes (an owner's or an agent's to-do, its steps, its
done mark and its edit and withdrawal). kitlib.hubsite.api re-exports every name here."""
import re

from kitlib import hubvalidate

from kitlib.hubvalidate import todosteps
from .apibase import (
    OWNER, OWNER_FROM, _SURROGATE, _agent_from, _fields, _flag, _invalid, _pin, _text, _typed,
    _via)
from .apipark import _park_now, _unpark_now
from .apirecord import _bound, _creator, _no_record, _still_open, _withdrawn
from .store import HubError

def todos_list(api, req):
    return {'items': api.items('todos', req)}


def todo_steps(b, judge=True):
    """The fields b of a new to-do with its `steps` made into text (kitlib.hubvalidate.todosteps
    todo_fields, which says how), its refusal an `invalid` HubError with the field named."""
    try:
        return todosteps.todo_fields(b, judge)
    except (todosteps.StepError, todosteps.Refused) as e:
        raise _invalid(str(e), e.field)


def todos_add(api, req):
    """A new open to-do. An agent's may give `record` {repo, id} (_bound, checked last): the
    record waits on the owner until the to-do is done (park_item); the owner's takes none."""
    owner = req.role == OWNER
    names = ('title', 'detail', 'commands', 'steps', 'os', 'repo', 'refs', 'urgent')
    if owner:
        _no_record(req, "the owner's to-do takes no record: only an agent's to-do parks one")
    b = _fields(req, names if owner else names + ('from', 'record'), required=('title',),
                flags=('urgent',))
    for name in ('title', 'detail', 'repo', 'from'):
        _text(b, name)
    b = todo_steps(b, judge=not owner)
    if owner:
        b['from'] = OWNER_FROM
    else:
        b['from'] = _agent_from(b.get('from') or '')
    if 'record' in b:               # checked last: the one part that reads a backlog
        b['record'] = _bound(api, b['record'], 'to-do')
    b.update(status='open', added_at=api.clock())
    out = api.add('todos', b, req)
    if not owner:
        field = _asks_report(b)
        if field:
            out['warnings'].append({
                'level': hubvalidate.WARN, 'rel': 'todos/%s' % out['id'], 'field': field,
                'code': 'todo-asks-report', 'message': TODO_ASKS_REPORT})
    if 'record' in b:
        return _park_now(api, out['id'], out, _via(req))
    return out


# An agent's to-do that asks the owner to send a result back: a done mark files nothing into any
# record (the done note reaches only the push), so such a result is lost to the record
# (KIT-HUB-TODO-RESULT-LOST). Matched on phrasings that ask for something back, not on any
# mention of a report; on the live hub (2026-11-05) it matched 2 of 373 to-dos, T-388 and T-389.
_ASKS_REPORT = re.compile(
    r'\breport (?:back|in|which|what|whether)\b'
    r'|\banswer box\b'
    r'|^[ \t]*(?:[-*]|[0-9]+[.)])?[ \t]*report[ \t]*:', re.IGNORECASE | re.MULTILINE)
TODO_ASKS_REPORT = ('the to-do asks the owner to report a result: a done mark files nothing into '
                    'any record (a done note reaches only the push); ask for the result with a '
                    'question bound to the record (hub_ask record, questions add --record) and '
                    'keep the to-do for the steps')


def _asks_report(b):
    """The field ('title' or 'detail') of to-do `b` that asks the owner to report a result, or
    None."""
    for name in ('title', 'detail'):
        if isinstance(b.get(name), str) and _ASKS_REPORT.search(b[name]):
            return name
    return None


TODO_NOTE_MAX = 2000        # characters of the owner's note on marking a to-do done
_NOTE_CONTROL = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _todo_note(b, done):
    """The owner's optional `note` of a done mark, stripped: '' when there is none. Plain text of
    at most TODO_NOTE_MAX characters (`invalid`, field note, otherwise); a note on a reopen is
    refused, since a reopened to-do keeps none."""
    note = (_text(b, 'note') or '').strip()
    if note and not done:
        raise _invalid('a note comes with a done mark, not a reopen', 'note')
    if len(note) > TODO_NOTE_MAX:
        raise _invalid('the note is %d characters: at most %d' % (len(note), TODO_NOTE_MAX), 'note')
    if _NOTE_CONTROL.search(note):
        raise _invalid('the note is plain text: no control characters', 'note')
    if _SURROGATE.search(note):
        raise _invalid('the note holds a lone surrogate, half a character', 'note')
    return note


def todos_done(api, req):
    b = _fields(req, ('done', 'note', 'if_version'), flags=('done',))
    _pin(b)
    done = _flag(b, 'done', True)
    note = _todo_note(b, done)
    doc_id = req.params['id']
    d = api.doc('todos', doc_id, b)
    changes = {'status': 'done' if done else 'open', 'done_at': api.clock() if done else None}
    if note or d.get('done_note'):
        changes['done_note'] = note or None      # a re-done or a reopen drops the old note
    out = api.change('todos', doc_id, d, changes, req, changes)
    # a reopened or re-done to-do is pending afresh: an old hand-over must not outlive it when
    # no read ran in between (_routed forgets only what it sees gone)
    api._handed.pop(('todo', doc_id), None)
    return out


def todos_ack(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    d = api.doc('todos', doc_id, b)
    return api.change('todos', doc_id, d, {'pm_ack': True}, req, {'pm_ack': True})


def _own(doc_id, d, what):
    who = d.get('from')
    if who != OWNER_FROM:           # exactly what todos_add writes for the owner
        raise HubError('forbidden', '%s is from %s: the owner can %s only a to-do the owner '
                       'added (from: %s)' % (doc_id, who or 'nobody', what, OWNER_FROM), 'from')


def todos_edit(api, req):
    owner = req.role == OWNER
    names = ('title', 'detail', 'commands', 'repo', 'refs', 'urgent')
    _no_record(req, "a to-do's record is given only when it is added: withdraw it and add it "
                    "again to bind another")
    b = _fields(req, names + (('if_version',) if owner else ('from', 'if_version')),
                required=() if owner else ('from',), flags=('urgent',),
                blank=('repo', 'detail'))
    _pin(b, required=not owner)
    for name in ('title', 'detail', 'repo', 'from'):
        _text(b, name)
    for name in ('commands', 'refs'):
        _typed(b, name, (list,), 'a list')
    changes = {k: v for k, v in b.items() if k not in ('from', 'if_version')}
    if not changes:
        raise _invalid('give at least one of %s to change' % ', '.join(names), 'body')
    if 'title' in changes and not changes['title'].strip():
        raise _invalid('title cannot be made empty', 'title')
    doc_id = req.params['id']
    d = api.doc('todos', doc_id, b)
    if owner:
        _own(doc_id, d, 'edit')
    else:
        _creator(b, doc_id, d, 'edit')
        _still_open(doc_id, d, 'edited')
        # the text an agent gives is judged by the rules its add was (todo_steps)
        changes = todo_steps(changes, judge=True)
    if d.get('pm_ack'):             # the PM read the old text: the changed one is pending again
        changes['pm_ack'] = False
    return api.change('todos', doc_id, d, changes, req, {'status': d.get('status')})


def todos_delete(api, req):
    owner = req.role == OWNER
    b = _fields(req, ('if_version',) if owner else ('from', 'if_version'),
                required=() if owner else ('from',))
    _pin(b, required=not owner)
    _text(b, 'from')
    doc_id = req.params['id']
    d = api.doc('todos', doc_id, b)
    if not owner:
        _creator(b, doc_id, d, 'withdraw')
        _still_open(doc_id, d, 'withdrawn')
    elif d.get('from') != OWNER_FROM:
        # the owner deletes a to-do of their own at any status, an agent's only while open
        _still_open(doc_id, d, 'deleted')
    out = _withdrawn(api, 'todos', doc_id, d)
    # the ask is gone, so the record it parked no longer waits on it (`ask withdrawn` when it
    # was the last line); a done to-do's line is the unblock-conditions job's to drop
    return _unpark_now(api, doc_id, d.get('record'), out)
