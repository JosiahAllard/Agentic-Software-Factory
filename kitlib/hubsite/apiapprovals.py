"""kitlib.hubsite.apiapprovals: the api's approval routes: add, decide, consume, withdraw, supersede
and verify (kitlib.ownerfiles.approvals holds the rules). kitlib.hubsite.api re-exports every name
here.

Approvals (kitlib.ownerfiles.approvals holds the rules; these routes only carry them). POST approvals
(AGENT) adds a pending request: the base of each file is read with git from this machine's
checkout of the request's repo (Api's checkout; no checkout is `invalid`, field repo, and a base
that is not the file at head is `invalid`, field files), and the unified diff lines the page
shows are stored in the document as `diff`. POST approvals/{id}/decide (APPROVER) writes the
owner's decision, signed with Api's approval_key; POST approvals/{id}/consume (AGENT) marks an
approval consumed by a commit. Both take the `if_version` the caller read and are pinned to it,
and an ApprovalError is answered as a HubError of its code and field. POST approvals/{id}/delete (AGENT) withdraws a request still undecided (pending, or
expired undecided: a decided one is `conflict`, field status): the body gives `from` (the
requester's claim, `pm` as approvals store none) and `if_version`, as a question's withdrawal
does. A file request may give `record` {repo, id} (checked as a question's, _bound): the record
waits on the owner until the approval is decided (park_item, `until: owner approval:A-n
decided`), and the withdrawal drops that line (unpark_item); an op request takes none. POST
approvals/{id}/supersede (AGENT) marks a decided approval nobody consumed superseded
(approvals.supersede_fields): the body gives `from` (`pm`, or a coordinator session of the
approval's repo, Api.coordinator_keys: a claim, as for questions done), `by` (the approval that replaces it,
which must exist, or a commit this machine's checkout of the repo has on main as last fetched,
approvals.on_main) and `if_version`; it leaves pending and is never consumed. GET approvals/{id}/verify
(AGENT, query repo, head, files as JSON {path: blob} and an optional commit) answers
{ok, valid, why} and never the key or the sig; an unknown id is valid false, approvals.WHY_UNKNOWN.
An approval of kind `op` (one run of a delegated operation, docs/delegations.md "Hub approvals")
is added with kind, op, repo, line, title and optional reason and what, and no checkout is read;
any other kind is `invalid`, field kind. It is consumed with args_sha256 in place of commit, and
verified with the query kind=op, op, repo and args (the line's sha256), which answers its version
too, the pin of the consume that follows.
GET snapshot carries `strong`: true only on the strong owner's call (Request.strong), so the page
shows Approve and Deny as usable; the decide route checks the role itself all the same.
"""
import json

from . import schema
from .apibase import AGENT_FROM, _fields, _invalid, _pin, _q, _text, _version, _via, _when
from .apipark import _park_now, _unpark_now
from .apirecord import _bound, _creator, _no_record, _withdrawn
from .store import HubError


# approvals: imported in each route, not at the top (no cycle needs it now; kept)
def _approval(make, *args):
    from kitlib.ownerfiles import approvals as ap
    try:
        return make(*args)
    except ap.ApprovalError as e:
        raise HubError(e.code, e.message, e.field)


def approvals_add(api, req):
    """A new pending request. A file request may give `record` {repo, id} (_bound, checked
    last): the record waits on the owner until the approval is decided (park_item); an op
    request takes none."""
    from kitlib.ownerfiles import approvals as ap
    kind = req.body.get('kind') if isinstance(req.body, dict) else None
    if kind is not None:
        if kind != ap.KIND_OP:
            raise _invalid('kind is %r or absent, not %r' % (ap.KIND_OP, kind), 'kind')
        _no_record(req, 'an op approval takes no record: only a file approval parks one')
        b = _fields(req, ('kind', 'op', 'repo', 'line', 'title', 'reason', 'what'))
        doc = _approval(ap.new_op_doc, b.get('op'), b.get('repo'), b.get('line'), b.get('title'),
                        _when(api.clock(), ''), b.get('reason'), b.get('what'))
        return api.add(ap.COLLECTION, doc, req)
    b = _fields(req, ('repo', 'title', 'head', 'files', 'reason', 'record'))
    doc = _approval(ap.new_doc, b.get('repo'), b.get('title'), b.get('head'), b.get('files'),
                    _when(api.clock(), ''), b.get('reason'))
    root = api.checkout(doc['repo'])
    if not root:
        raise _invalid('this machine has no checkout of %s' % doc['repo'], 'repo')
    lines = []
    for f in doc['files']:
        base, why = ap.read_base(root, doc['head'], f['path'], f['base_blob'])
        if why:
            raise _invalid('%s: %s' % (f['path'], why), 'files')
        lines.extend(ap.diff(f['path'], base, f['content']))
    doc['diff'] = lines
    if 'record' in b:               # checked last: the one part that reads a backlog
        doc['record'] = _bound(api, b['record'], 'approval')
    out = api.add(ap.COLLECTION, doc, req)
    if 'record' in doc:
        return _park_now(api, out['id'], out, _via(req))
    return out


def approvals_decide(api, req):
    from kitlib.ownerfiles import approvals as ap
    b = _fields(req, ('decision', 'if_version'))
    _pin(b, required=True)
    doc_id = req.params['id']
    d = api.doc(ap.COLLECTION, doc_id, b)
    changes = _approval(ap.decide_fields, api.approval_key(), doc_id, d, b.get('decision'),
                        _when(api.clock(), ''))
    return api.change(ap.COLLECTION, doc_id, d, changes, req, {'status': changes['status']})


def approvals_consume(api, req):
    from kitlib.ownerfiles import approvals as ap
    b = _fields(req, ('commit', 'args_sha256', 'if_version'))
    _pin(b, required=True)
    if 'commit' in b and 'args_sha256' in b:
        raise _invalid('give commit (a change) or args_sha256 (an operation), not both',
                       'args_sha256')
    doc_id = req.params['id']
    d = api.doc(ap.COLLECTION, doc_id, b)
    if 'args_sha256' in b:
        changes = _approval(ap.consume_op_fields, api.approval_key(), doc_id, d,
                            b['args_sha256'], _when(api.clock(), ''))
    else:
        changes = _approval(ap.consume_fields, api.approval_key(), doc_id, d, b.get('commit'),
                            _when(api.clock(), ''))
    return api.change(ap.COLLECTION, doc_id, d, changes, req, {'status': changes['status']})


def approvals_delete(api, req):
    """The requester takes back an undecided request, pending or expired undecided
    (approvals.withdraw_check): the `from` is a claim checked as for a question's withdrawal (an
    approval has none stored, so it is `pm`). The line the request parked on its record is
    dropped (unpark_item); the record's other lines stay."""
    from kitlib.ownerfiles import approvals as ap
    b = _fields(req, ('from', 'if_version'), required=('from',))
    _pin(b, required=True)
    _text(b, 'from')
    doc_id = req.params['id']
    d = api.doc(ap.COLLECTION, doc_id, b)
    _creator(b, doc_id, d, 'withdraw')
    _approval(ap.withdraw_check, d, _when(api.clock(), ''))
    out = _withdrawn(api, ap.COLLECTION, doc_id, d)
    return _unpark_now(api, doc_id, d.get('record'), out)


def approvals_supersede(api, req):
    """The PM, or a coordinator session of the approval's repository (coordinator_keys), marks
    a decided approval nobody consumed superseded by another approval or a commit on main
    (approvals.supersede_fields, on_main). The `from` is a claim checked as questions_done checks
    one, before anything else about the approval is said; the decision and the stored status
    stay."""
    from kitlib.ownerfiles import approvals as ap
    b = _fields(req, ('from', 'by', 'if_version'), required=('from', 'by'))
    _pin(b, required=True)
    _text(b, 'from')
    doc_id = req.params['id']
    d = api.doc(ap.COLLECTION, doc_id)
    claimed = b['from'].strip()
    allowed = [AGENT_FROM] + [k for k in api.coordinator_keys(d.get('repo')) if k != AGENT_FROM]
    if claimed not in allowed:
        raise HubError('forbidden', '%s is for %s: only they can mark it superseded (%s was '
                       'given)' % (doc_id, ' or '.join(allowed), ascii(claimed)), 'from')
    d = api.doc(ap.COLLECTION, doc_id, b)
    by = b['by']
    changes = _approval(ap.supersede_fields, doc_id, d, by, claimed, _when(api.clock(), ''))
    if ap.APPROVAL_ID.match(by):
        if api.store.get(ap.COLLECTION, by) is None:
            raise _invalid('there is no approval %s' % by, 'by')
    else:
        root = api.checkout(d.get('repo'))
        if not root:
            raise _invalid('this machine has no checkout of %s to find %s in'
                           % (d.get('repo'), by), 'by')
        reached, why = ap.on_main(root, by)
        if not reached:
            raise _invalid('main as last fetched does not reach %s%s'
                           % (by, ': %s' % why if why else ''), 'by')
    return api.change(ap.COLLECTION, doc_id, d, changes, req, {'superseded': changes['superseded']})


def approvals_verify(api, req):
    from kitlib.ownerfiles import approvals as ap
    doc_id = req.params['id']
    d = api.store.get(ap.COLLECTION, doc_id) if schema.check_id(doc_id) is None else None
    if d is None:
        return {'ok': True, 'valid': False, 'why': ap.WHY_UNKNOWN}
    if _q(req, 'kind') == ap.KIND_OP:
        valid, why = ap.check_op(api.approval_key(), doc_id, d, _q(req, 'op'), _q(req, 'repo'),
                                 _q(req, 'args'), _when(api.clock(), ''))
        return {'ok': True, 'valid': valid, 'why': why, 'version': _version(d)}
    try:
        blobs = json.loads(_q(req, 'files'))
    except ValueError:
        blobs = None                # check() answers WHY_FILES
    # a commit given, even empty, is checked: only an absent one leaves it out
    commit = _q(req, 'commit') if 'commit' in (req.query or {}) else None
    valid, why = ap.check(api.approval_key(), doc_id, d, _q(req, 'repo'), _q(req, 'head'), blobs,
                          _when(api.clock(), ''), commit)
    return {'ok': True, 'valid': valid, 'why': why}
