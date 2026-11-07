"""kitlib.hubsite.apipark: the hub as the owner's proxy: a bound ask (a question, a to-do, an
approval) parks the record it is bound to, and the sweep drops the lines of asks that are gone.
kitlib.hubsite.api re-exports every name here."""
from kitlib import hubvalidate

from .apibase import AGENT_FROM, AGENT_SAYS, _version, _when
from .store import HubError

# --- the hub as the owner's proxy: a bound ask parks its record -----------------------------------

# The asks that park the record they are bound to: {collection: (noun, the `until` condition the
# record waits on, % the item's id; what ends it)}. A question is bound when it is asked or
# edited; an agent's to-do (todos_add) and a file approval (approvals_add) only when added. The
# owner's act ends each: an answer (filed into the record), a to-do marked done or an approval
# decided (either way: denied counts), which the hub's unblock-conditions job reads
# (kitlib.hubjobs.unblock_conditions); an approval that expires undecided keeps its line, and that
# job pushes the PM `approval-expired`.
ASKS = {
    'questions': ('question', 'question:%s answered', 'answered'),
    'todos': ('to-do', 'todo:%s done', 'done'),
    'approvals': ('approval', 'approval:%s decided', 'decided'),
}

# what an ask's `park` says, by park's outcome (recordwrite.PARK_OUTCOMES) or the code its write
# failed with: fixed phrases, as FILING_SAYS, the ask's noun and end filled in (_park_says)
_PARK_SAYS = {
    'blocked': 'the record waits on this %(noun)s: blocked on the owner until it is %(end)s',
    'already': 'the record waits on this %(noun)s',
    'held': 'the record was held on the owner already: that hold keeps its own end',
    'parked': 'the record is parked: the hub leaves a park as it is',
    'gone': 'the record is completed or gone: nothing waits on this %(noun)s',
    'not_found': 'the record is completed or gone: nothing waits on this %(noun)s',
    'invalid': 'the backlog store refused to block the record: the PM handles it',
    'store': 'the backlog store could not be written now: the hub tries again',
    'unavailable': 'this hub cannot write a backlog store: the hub tries again',
    'no_store': 'this repository names no backlog store the hub may write (.kit.json '
                '"backlog".store): the PM handles it',
}
PARK_SAYS = {k: v % {'noun': 'question', 'end': 'answered'} for k, v in _PARK_SAYS.items()}
# the codes a later park tries again (POST questions/file; the hub runs it at start)
PARK_RETRY = ('store', 'unavailable', 'internal')
_UNPARK_FAILED = ('the record still waits on this %s, which is gone or bound elsewhere: the hub '
                  'tries again at its next start or questions file call')
UNPARK_FAILED = _UNPARK_FAILED % 'question'


def _parks(api):
    """Whether this server parks a bound ask's record: it has the record_park view."""
    return api.views is not None and callable(getattr(api.views, 'record_park', None))


def _ask_coll(doc_id):
    """The collection of ASKS whose id doc_id is (by its prefix), or None."""
    prefix = doc_id.split('-', 1)[0].upper() if isinstance(doc_id, str) else ''
    coll = hubvalidate.REF_COLLECTIONS.get(prefix)
    return coll if coll in ASKS else None


def _until(doc_id):
    """The `until` condition a record parked on the ask doc_id waits on."""
    return ASKS[_ask_coll(doc_id) or 'questions'][1] % doc_id


def _park_says(coll, code):
    noun, _, end = ASKS[coll]
    said = _PARK_SAYS.get(code)
    return (said % {'noun': noun, 'end': end} if said
            else 'the record could not be parked (%s)' % code)


def _asking(api, coll, d):
    """Whether item d still asks the owner: an open question or to-do, an approval pending and
    not expired (an expired one is never parked afresh; the line it parked stays)."""
    if coll == 'approvals':
        from . import approvals as ap
        return ap.status_at(d, _when(api.clock(), '')) == ap.PENDING
    return d.get('status') == 'open'


def park_item(api, doc_id, via='hub'):
    """Block the record the ask doc_id (a question, to-do or approval: ASKS) is bound to on the
    owner, until the owner's act ends it, through the `record_park` view
    (kitlib.hubsite.recordwrite park, Session: hub-park: the hub as the owner's proxy), then mark
    the ask `park` {at, code, message} (code park's outcome, recordwrite.PARK_OUTCOMES, or the
    failure's; message a fixed phrase, _park_says). The asker is the ask's `from` (`pm` when it
    has none, as an approval never does). None for an ask that is gone, no longer asking
    (_asking), bound to no record, or parked already (a `park` whose code is not one to retry,
    PARK_RETRY); else {id, code, message, version, revision}, or {id, moved: true} when the ask
    changed before its mark. Under Api.record_lock: an ask withdrawn or ended meanwhile is read
    as it is now, so a late park never blocks a record on an ask that is gone. The record write
    is idempotent, so a retry is safe."""
    coll = _ask_coll(doc_id)
    if coll is None:
        return None
    with api.record_lock:
        d = api.store.get(coll, doc_id)
        if d is None or not _asking(api, coll, d) or not isinstance(d.get('record'), dict):
            return None
        was = d.get('park') if isinstance(d.get('park'), dict) else None
        if was is not None and was.get('code') not in PARK_RETRY:
            return None
        record = d['record']
        try:
            got = api._view('record_park')(record.get('repo'), record.get('id'), doc_id,
                                           _until(doc_id), d.get('from') or AGENT_FROM)
        except HubError as e:
            code = e.code
        except Exception:           # noqa: BLE001 - a view is another module's code
            code = 'internal'
        else:
            code = got.get('park') if isinstance(got, dict) and got.get('park') else 'blocked'
        message = _park_says(coll, code)
        if was is not None and (was.get('code'), was.get('message')) == (code, message):
            return {'id': doc_id, 'code': code, 'message': message, 'version': _version(d),
                    'revision': api.store.revision}
        try:
            r = api.store.update(coll, doc_id, {
                'park': {'at': api.clock(), 'code': code, 'message': message}, 'via': via},
                if_version=_version(d))
        except HubError as e:
            if e.code == 'conflict':
                return {'id': doc_id, 'moved': True}
            return {'id': doc_id, 'code': e.code, 'message': AGENT_SAYS.get(e.code, e.message)}
        return {'id': doc_id, 'code': code, 'message': message, 'version': r.version,
                'revision': r.revision}


def unpark_item(api, doc_id, record):
    """The ask doc_id is gone from `record` ({repo, id}): withdrawn or deleted, or (a question)
    edited to another record or none. Drop the record's `until: owner <condition>` line
    (_until) through the `record_unpark` view (recordwrite withdraw: the last owner line clears
    the owner, reason `ask withdrawn`). Under Api.record_lock, and only while the ask is not
    bound to `record` now (a rebind back, or the answer's filing, is left to its own write).
    None when nothing was asked of the view, else the view's {lines, dropped, unblocked}, or
    {code} when it failed."""
    coll = _ask_coll(doc_id)
    if not isinstance(record, dict) or coll is None:
        return None
    with api.record_lock:
        d = api.store.get(coll, doc_id)
        if d is not None and d.get('record') == record:
            return None
        try:
            return api._view('record_unpark')(record.get('repo'), record.get('id'),
                                              _until(doc_id))
        except HubError as e:
            return {'code': e.code}
        except Exception:           # noqa: BLE001 - a view is another module's code
            return {'code': 'internal'}


def _park_now(api, doc_id, out, via):
    """Park the record the ask doc_id was just bound to: off the request through park_later
    when the server set it, else here, its result on `out` as `park` (with the mark's version
    and revision, the ask's last write)."""
    if not _parks(api):
        return out
    if api.park_later is not None:
        api.park_later(doc_id, lambda: _later_said(park_item(api, doc_id, via), 'parked'))
        return out
    got = park_item(api, doc_id, via)
    if got is not None:
        out['park'] = got
        if 'version' in got:
            out.update(version=got['version'], revision=got['revision'])
    return out


def _park_failed(got):
    """The code of a park that was not made, from park_item's answer: a code to retry
    (PARK_RETRY), or a mark that was not written (an answer with no version, the ask gone
    meanwhile, say). None for a park made or left alone, or an ask that moved."""
    if got is None or got.get('moved'):
        return None
    if got.get('code') in PARK_RETRY or 'version' not in got:
        return got.get('code') or 'internal'
    return None


def _parked(got, doc_id, parked, park_failed):
    """Count park_item's answer for questions_file: into `parked` [ids] or `park_failed`."""
    if got is None or got.get('moved'):
        return
    code = _park_failed(got)
    if code:
        park_failed.append({'id': doc_id, 'code': code, 'message': got.get('message')})
    else:
        parked.append(doc_id)


def _later_said(got, what):
    """For serve's log, what a park (`what` 'parked') or its undoing ('unparked') run off the
    request failed with: 'record not <what>: <code>', or None. An undoing failed when
    unpark_item answers a code; nothing retries it then but the sweep at the next hub start
    (questions_file)."""
    code = _park_failed(got) if what == 'parked' else (got or {}).get('code')
    return 'record not %s: %s' % (what, code) if code else None


def _sweep_asks(api):
    """Drop the `until: owner <condition>` lines the hub wrote for an ask (`parked`: the record's
    body says the hub parked it on that ask: a question, a to-do or an approval) that no owner
    act will ever end: the ask is gone from the hub, or bound now to another record or none
    (unpark_item, reason `ask withdrawn` on the last line). An approval that expired undecided is
    still there and keeps its line (it is withdrawn to drop it). The lines come from the
    `record_asks` view. A line a person wrote is never dropped, so an owner's hold never ends on
    the hub's word: one whose ask is gone is reported in the third list for the PM to clear by
    hand. ([{id, record, unblocked}], [{id, code, message}], [{repo, id, q}])."""
    view = getattr(api.views, 'record_asks', None) if api.views is not None else None
    if not callable(view):
        return [], [], []
    try:
        lines = view() or []
    except Exception:               # noqa: BLE001 - a view is another module's code
        return [], [], []
    done, failed, hand = [], [], []
    for a in lines:
        q, record = a.get('q'), {'repo': a.get('repo'), 'id': a.get('id')}
        coll = _ask_coll(q)
        if coll is None:
            continue
        d = api.store.get(coll, q)
        if not a.get('parked'):
            if d is None:
                hand.append({'repo': record['repo'], 'id': record['id'], 'q': q})
            continue
        if d is not None and d.get('record') == record:
            continue
        got = unpark_item(api, q, record)
        if got is None:
            continue
        if got.get('code'):
            failed.append({'id': q, 'code': got['code'], 'message': _UNPARK_FAILED % ASKS[coll][0]})
        elif got.get('dropped'):
            done.append({'id': q, 'record': record, 'unblocked': bool(got.get('unblocked'))})
    return done, failed, hand


def _unpark_now(api, doc_id, record, out):
    """unpark_item for `record`, off the request through park_later when set."""
    if not _parks(api) or not isinstance(record, dict):
        return out
    if api.park_later is not None:
        api.park_later(doc_id, lambda: _later_said(unpark_item(api, doc_id, record),
                                                   'unparked'))
        return out
    got = unpark_item(api, doc_id, record)
    if got is not None:
        out['unpark'] = got
    return out
