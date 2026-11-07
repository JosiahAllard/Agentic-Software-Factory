"""kitlib.hubsite.apiask: the question handlers that list, ask, edit, withdraw and bind a question
to a record. The filing handlers (answer, file, log, done) stay in kitlib.hubsite.api, which
re-exports every name here."""
import collections
import re

from kitlib import hubvalidate

from .apibase import (AGENT_FROM, AGENT_SAYS, OWNER, _fields, _flag, _invalid, _pin, _reason,
                      _session_from, _text, _typed, _via)
from .apipark import _park_now, _unpark_now
from .apirecord import _bound, _creator, _still_open, _withdrawn
from .store import HubError


def questions_list(api, req):
    return {'items': api.items('questions', req, since=True)}


# The class a question bound to no record declares (docs/hub-questions.md "A question's class"):
# a standing rule (a delegation run line too), the machine, work across repositories, or a
# one-off go (a landing, an archive). A bound question's answer is filed into its record instead.
QUESTION_CLASSES = ('rule', 'machine', 'cross-repo', 'go')


def _class(b, bound):
    """Check b's `class` for a question that is `bound` to a record or not, once the fields it
    ends with are known: refused `invalid`, field `class`, for a class on a bound question, a
    class not in QUESTION_CLASSES, and no class on an unbound one."""
    given = b.get('class')
    if bound:
        if given is not None:
            raise _invalid('a question bound to a record takes no class: its answer is filed '
                           'into the record', 'class')
        return
    if given is None:
        raise _invalid('a question needs --record REPO:ID or --class %s (one bound to no '
                       'record says what kind it is)' % '|'.join(QUESTION_CLASSES), 'class')
    if given not in QUESTION_CLASSES:
        raise _invalid('class is one of %s, not %s' % (', '.join(QUESTION_CLASSES),
                                                      ascii(given)), 'class')


def questions_add(api, req):
    """A new open question: `record` (the backlog record its answer is filed into) or `class`
    (_class) is required. A bound question parks its record (park_item: blocked on the owner
    until it is answered), off the request when the server runs it later. A class-only question
    whose `refs` name a record pulled in a configured repository is stored, with a warning that
    the record goes on unparked (_pulled_refs)."""
    b = _fields(req, ('repo', 'from', 'title', 'context', 'options', 'recommended', 'refs',
                      'urgent', 'story', 'record', 'class'), required=('title',))
    for name in ('repo', 'from', 'title', 'context', 'story'):
        _text(b, name)
    # the session the answer goes back to: a launcher session key, read now, or the PM's
    b['from'] = _session_from(api, (b.get('from') or '').strip() or AGENT_FROM, 'a question')
    _typed(b, 'options', (list,), 'a list of {label, description}')
    _class(b, 'record' in b)
    if 'record' in b:               # checked last: the one part that reads a backlog
        b['record'] = _bound(api, b['record'])
    b.setdefault('options', [])
    b.update(status='open', asked_at=api.clock())
    out = api.add('questions', b, req)
    if 'record' in b:
        return _park_now(api, out['id'], out, _via(req))
    for rid in _pulled_refs(api, b.get('refs')):
        out['warnings'].append({
            'level': hubvalidate.WARN, 'rel': 'questions/%s' % out['id'], 'field': 'refs',
            'code': 'refs-pulled-record',
            'message': 'refs name pulled record %s: ask with --record so the hub parks it' % rid})
    return out

def _pulled_refs(api, refs):
    """The refs (`ID` or `repo:ID`) of a class-only question that name a record pulled in a
    configured repository, read from the backlog view's held answer: [record id]. Empty when the
    view is missing or fails: a warning only, never a refusal."""
    names = [r.strip() for r in (refs if isinstance(refs, list) else [])
             if isinstance(r, str) and r.strip()]
    if not names:
        return []
    try:
        entries = api._view('backlog')()
    except Exception:               # noqa: BLE001 - no backlog read: no warning
        return []
    pulled = set()
    for e in entries if isinstance(entries, list) else []:
        repo = str(e.get('repo') or '').lower() if isinstance(e, dict) else ''
        for i in (e.get('items') or []) if isinstance(e, dict) else []:
            if isinstance(i, dict) and i.get('s') == 'pulled' and isinstance(i.get('id'), str):
                pulled.add(i['id'])
                pulled.add('%s:%s' % (repo, i['id'].lower()))
    out = []
    for r in names:
        rid = r.rpartition(':')[2]
        if (r in pulled or r.lower() in pulled) and rid not in out:
            out.append(rid)
    return out


def _asked(text):
    """The number of a question id (`Q-12`, `Q-012`), or None."""
    m = re.match(r'Q-(\d+)\Z', text or '')
    return int(m.group(1)) if m else None


def _cited(api, repo, item):
    """([question numbers a record's hold cites, in order, once each], why it is not one to read
    or None) for an open item of the backlog view: a parked record's `q:` line, an owner-blocked
    record's `owner_ask`, `q`, `blocked_on` members and `Blocked on` body lines (the record
    view, read now). (None, None) for any other record."""
    from kitlib.backlog import record as brecord
    texts = []
    if item.get('s') == 'parked':
        texts.append(item.get('q') or '')
    elif item.get('s') == 'blocked':
        try:
            got = api._view('record')(repo, item.get('id'))
        except Exception as e:      # noqa: BLE001 - a view is another module's code
            return None, 'the record could not be read (%s)' % _reason(e)
        got = got if isinstance(got, dict) else {}
        if not any(b.get('kind') == 'owner' for b in got.get('blockers') or []
                   if isinstance(b, dict)):
            return None, None
        hold = got.get('hold') if isinstance(got.get('hold'), dict) else {}
        texts += [hold.get('owner_ask') or '', hold.get('q') or '']
        texts += [b.get('value') or '' for b in got.get('blockers') or [] if isinstance(b, dict)]
        texts += [l for l in (got.get('body') or '').split('\n') if l.startswith(brecord.BLOCK_NOTE)]
    else:
        return None, None
    nums = []
    for text in texts:
        for c in brecord.cites(text):
            n = _asked(c)
            if n is not None and n not in nums:
                nums.append(n)
    return nums, None

# the statuses questions bind binds a question from: still open, or answered and not yet handled
BINDS_FROM = ('open', 'answered')


def questions_bind(api, req):
    """POST questions/bind, the PM's alone (`from` pm, as questions_done checks a claim): pair
    each parked record's `q` and each owner-blocked record's cited question with an open hub
    question of the same number, and with `apply` true bind them (set `record`). The backlog is
    read fresh with `apply` (a fetch round) and from the held snapshot without, every configured
    repository. A pair whose record fails its check is in `failed` only. Nothing but an open or
    answered, unbound question is written (BINDS_FROM); a filed, logged or done one is a miss. An
    answered one is filed into its record at once (file_answer), its pair carrying `filing`
    {filed, code, message}. Answers {ok, applied, pairs: [{question, record, bound}],
    misses: [{record, why}], multi: [{record, questions}], skipped: [{question, why}],
    failed: [{question, code, message}]}: `multi` are records citing several questions, left
    alone; `skipped` a question already bound elsewhere or wanted by two records."""
    b = _fields(req, ('from', 'apply'), flags=('apply',))
    _text(b, 'from')
    claimed = (b.get('from') or '').strip() or AGENT_FROM
    if claimed != AGENT_FROM:
        raise HubError('forbidden', 'questions bind is the PM\'s: from is %s (%s was given)'
                       % (AGENT_FROM, ascii(claimed)), 'from')
    apply = _flag(b, 'apply', False)
    docs = api.store.docs('questions')
    numbered = {}
    for qid in docs:
        if _asked(qid) is not None:
            numbered[_asked(qid)] = qid
    found = []                      # (record, the one question number it cites)
    cites = collections.Counter()   # records citing each question number
    misses, multi = [], []
    for entry in api.shown('backlog', repo='', state='', fresh=apply):
        repo = entry.get('repo')
        if entry.get('error'):
            misses.append({'record': {'repo': repo, 'id': None},
                           'why': 'the repository could not be read'})
            continue
        for item in entry.get('items') or []:
            rec = {'repo': repo, 'id': item.get('id')}
            nums, why = _cited(api, repo, item)
            cites.update(nums or ())
            if why:
                misses.append({'record': rec, 'why': why})
            elif nums is None:
                continue
            elif not nums:
                if item.get('s') == 'parked':
                    misses.append({'record': rec, 'why': 'cites no question'})
            elif len(nums) > 1:
                multi.append({'record': rec, 'questions': [numbered.get(n, 'Q-%d' % n)
                                                           for n in nums]})
            else:
                found.append((rec, nums[0]))
    pairs, skipped = [], []
    for rec, n in found:
        qid = numbered.get(n)
        d = docs.get(qid) if qid else None
        if d is None:
            misses.append({'record': rec, 'why': 'Q-%d is not a hub question' % n})
        elif d.get('status') not in BINDS_FROM:
            misses.append({'record': rec, 'why': '%s is %s, not open or answered'
                           % (qid, d.get('status'))})
        elif cites[n] > 1:
            skipped.append({'question': qid, 'why': 'cited by more than one record'})
        elif d.get('record') == rec:
            skipped.append({'question': qid, 'why': 'already bound to this record'})
        elif d.get('record'):
            skipped.append({'question': qid, 'why': 'bound to another record'})
        else:
            pairs.append({'question': qid, 'record': rec, 'bound': False})
    failed = []
    if apply:
        for p in pairs:
            d = api.store.docs('questions').get(p['question'])
            try:
                if d is None or d.get('status') not in BINDS_FROM or d.get('record'):
                    raise HubError('conflict', '%s moved meanwhile' % p['question'], 'id')
                bound = _bound(api, p['record'])
                api.change('questions', p['question'], d, {'record': bound, 'class': None}, req)
                p['bound'] = True
                if d.get('status') == 'answered':
                    # an answer the PM has not handled yet: filed now, as a bound answer is
                    from . import api as hubapi    # at call: api imports this module
                    got = hubapi.file_answer(api, p['question'], _via(req), full=req.full)
                    if got is None:         # no longer answered when the filing read it
                        p['filing'] = {'filed': False, 'unanswered': True}
                    else:
                        p['filing'] = {k: got[k] for k in ('filed', 'code', 'message', 'moved')
                                       if k in got}
            except HubError as e:
                failed.append({'question': p['question'], 'code': e.code,
                               'message': e.message if req.full else AGENT_SAYS.get(
                                   e.code, e.message)})
        lost = set(f['question'] for f in failed)
        pairs = [p for p in pairs if p['question'] not in lost]
    return {'ok': True, 'applied': apply, 'pairs': pairs, 'misses': misses, 'multi': multi,
            'skipped': skipped, 'failed': failed}

def questions_edit(api, req):
    names = ('title', 'context', 'options', 'recommended', 'refs', 'urgent', 'record', 'class')
    # record null or "" clears the binding
    b = _fields(req, names + ('from', 'if_version'), required=('from',), flags=('urgent',),
                blank=('record',))
    _pin(b, required=True)
    for name in ('from', 'title', 'context'):
        _text(b, name)
    _typed(b, 'options', (list,), 'a list of {label, description}')
    changes = {k: v for k, v in b.items() if k not in ('from', 'if_version')}
    if not changes:
        raise _invalid('give at least one of %s to change' % ', '.join(names), 'body')
    if 'title' in changes and not changes['title'].strip():
        raise _invalid('title cannot be made empty', 'title')
    doc_id = req.params['id']
    d = api.doc('questions', doc_id, b)
    _creator(b, doc_id, d, 'edit')
    _still_open(doc_id, d, 'edited')
    if changes.get('record') == '':
        changes['record'] = None
    if 'record' in changes or 'class' in changes:
        # what the question ends with: bound and classless, or unbound with a class (_class);
        # an edit of neither leaves a question asked before classes as it is
        record = changes['record'] if 'record' in changes else d.get('record')
        if record:
            _class(changes, True)
            changes['class'] = None
        else:
            _class({'class': changes['class'] if 'class' in changes else d.get('class')}, False)
    if changes.get('record'):       # only once the asker and the status passed: no git before
        changes['record'] = _bound(api, changes['record'])
    old = d.get('record') if isinstance(d.get('record'), dict) else None
    moved = 'record' in changes and changes['record'] != old
    if moved and d.get('park') is not None:
        changes['park'] = None      # parked afresh on the record it is bound to now
    out = api.change('questions', doc_id, d, changes, req, {'status': 'open'})
    if moved:
        # the old record's line goes (the last one unblocks it: `ask withdrawn`), the new one's
        # park follows; each reads the question as it is when it runs
        _unpark_now(api, doc_id, old, out)
        if changes['record']:
            _park_now(api, doc_id, out, _via(req))
    return out


def questions_delete(api, req):
    owner = req.role == OWNER
    b = _fields(req, ('if_version',) if owner else ('from', 'if_version'),
                required=() if owner else ('from',))
    _pin(b, required=not owner)
    _text(b, 'from')
    doc_id = req.params['id']
    d = api.doc('questions', doc_id, b)
    if not owner:
        _creator(b, doc_id, d, 'withdraw')
    _still_open(doc_id, d, 'withdrawn')
    out = _withdrawn(api, 'questions', doc_id, d)
    # the ask is gone, so the record it parked no longer waits on it (`ask withdrawn` when it was
    # the last); an expiry, were there one, would keep the block (there is none for questions)
    return _unpark_now(api, doc_id, d.get('record'), out)
