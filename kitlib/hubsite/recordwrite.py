"""kitlib.hubsite.recordwrite: the Action Hub's writes to a repository's backlog store, the owner's
add, edit, delete and unblock from the record page (docs/hub-views.md "The record page").

  add(root, rid, summary, scopes, effort=None, ref=None)        -> {"lines"}
  edit(root, rid, version, changes, unset=(), body=None)        -> {"lines"}
  delete(root, rid, why)                                        -> {"lines"}
  unblock(root, rid, on=None, reason=None)                      -> {"lines"}
  answer(root, rid, qid, at, title, label, note=None, via=None, other=False, waiting=())
                                                                -> {"lines"}
  condition_met(root, rid, member, cond)                        -> {"lines", "unblocked", "dropped"}
  park(root, rid, item_id, cond, asker)                         -> {"lines", "park"}
  withdraw(root, rid, cond)                                     -> {"lines", "unblocked", "dropped"}

condition_met is not an owner route: the hub's unblock-conditions job (hubjobs
.unblock_conditions) calls it when a record's `until` condition is met, and it commits under
Session: hub-condition (CONDITION_SESSION), clearing that one member (plan.plan_condition_met:
an `owner` member only on a to-do, a question or an approval, the owner's own act).

park and withdraw are no route either: the hub calls them itself when an ask bound to a record
is posted, a question, an agent's to-do or a file approval (park: the record waits on the owner,
until the answer, the to-do done or the approval decided) and when it is withdrawn, deleted or
unbound (withdraw), committing under Session: hub-park (PARK_SESSION) as the owner's proxy
(kitlib.hubsite.api park_item, unpark_item).

Each opens the store `.kit.json` in `root` names (kitlib.backlog.opener open_store, fetched first,
with opener.NO_PROBES: a park stops a pull as its holder would, so no write here stops another
branch's pull) and writes through Store.write with the plan functions backlog.py runs
(kitlib.backlog.plan plan_add, plan_set, plan_delete, plan_unblock, plan_answer), so every store
check applies.
"lines" is what the store said about the write, its notes included.

answer files the owner's answer to hub question `qid` (answered at `at`) into the record the
question is bound to (docs/hub-questions.md "A question bound to a record"): the hub calls it after
the answer is stored, and again on a retry, so the same `qid` and `at` changes nothing and an
older `at` never overwrites a newer answer (plan_answer). `waiting` is the hub's other questions
on the record still open. It runs with the role check made (role_checked): the hub files only the
answer the owner gave, whoever's call starts the filing.

add writes the review mark `owner-hub <date>`, found on no branch: pull and next refuse the record
until a coordinator clears it. edit is pinned to the record text the page read: `version` is
Views.record's `<commit>:<sha1 of the text>`, and a record whose text no longer has that sha1 is a
`conflict`, checked again on every re-plan. These are owner routes, so the plans run with the
role check already made (role_checked); no ruling id is taken or written. unblock clears
one blocker as the backlog tool's unblock does: a record blocked on several needs `on` (the one
to clear) and a `reason`, and with no `on` it is refused here as `invalid` on `on`, since the
store's own refusal names the backlog tool's path.

What is raised is a HubError: NotFound `not_found`; Usage and any other refusal `invalid`, with the
store's own text; a Failure (git, a publish, a folder store outside a linked worktree) `store`,
whose text the Api gives the local owner only (kitlib.hubsite.api "What an agent is told"). An
IndexStale is not raised: its write is committed, so the route returns it as written, the
WARNING line last in "lines" (condition_met's "unblocked" true). The
implicit `ref` store (no "backlog".store in `.kit.json`) is refused as `store` before any write,
since its refusal names the PM data worktree's path; answer refuses it as `no_store` instead, a
code the hub's filing alone reads (kitlib.hubsite.api file_answer, which leaves that answer to
the PM). Anything else is raised as it is.
"""
import hashlib

from kitlib.backlog import opener
from kitlib.backlog import plan as pmod
from kitlib.backlog import scope as bscope
from kitlib.backlog import store as bstore
from kitlib.backlog.errors import Failure, IndexStale, NotFound, Refusal

from .store import HubError

SESSION = 'owner-hub'               # the commit's Session: line and the review mark's source
# The Session: line of condition_met's commit: the hub's timed job, not the owner (an audit
# by-design writer, kitlib.backlog.audit_check.BY_DESIGN).
CONDITION_SESSION = 'hub-condition'
NOT_FOLDED = 'added by the owner from the Action Hub'
STALE = 'the record changed since this page read it: read it again'


def _open(root, no_store='store', session=SESSION):
    try:
        ctx = opener.open_store(root, fetch=True, session=session, probes=opener.NO_PROBES)
    except Failure as e:
        raise HubError('store', str(e), 'repo')
    if getattr(ctx.store.backend, 'implicit', False):
        raise HubError(no_store, bstore.default_store_refusal(ctx.root), 'repo')
    return ctx


def _change(root, plan, no_store='store', session=SESSION):
    """The Change Store.write(plan(snapshot, ctx)) made on the store at root, its errors as
    HubErrors; the implicit store is refused with the code `no_store`. An IndexStale is a write
    that is committed (errors.IndexStale), so it returns the change it planned, its WARNING line
    added, as tools/backlog.py reports it, never a `store` error."""
    ctx = _open(root, no_store, session)
    made = []

    def planned(s):
        made[:] = [plan(s, ctx)]
        return made[0]
    try:
        change, _tip = ctx.store.write(planned)
    except IndexStale as e:
        if not made:
            raise HubError('store', str(e), '')
        made[0].lines = list(made[0].lines) + ['WARNING: %s' % ' '.join(str(e).split())]
        return made[0]
    except NotFound as e:
        raise HubError('not_found', str(e), 'id')
    except Failure as e:
        raise HubError('store', str(e), '')
    except Refusal as e:
        raise HubError('invalid', str(e), '')
    return change


def _write(root, plan, no_store='store'):
    return {'lines': list(_change(root, plan, no_store).lines)}


def add(root, rid, summary, scopes, effort=None, ref=None):
    def plan(s, ctx):
        return pmod.plan_add(s, ctx, rid, summary, list(scopes), effort, ref, session=None,
                             found_on=bscope.NONE, why_not_folded=NOT_FOLDED,
                             review='%s %s' % (SESSION, ctx.date()))
    return _write(root, plan)


def edit(root, rid, version, changes, unset=(), body=None):
    want = str(version).rsplit(':', 1)[-1]

    def plan(s, ctx):
        rec = s.get(rid)
        if rec is not None:
            now = s.raw.get(rid)
            if now is None:
                now = rec.text.encode('utf-8')
            if hashlib.sha1(now).hexdigest() != want:
                raise HubError('conflict', STALE, 'version')
        # an OWNER route (kitlib/hubsite/api.py), so the role check is made
        return pmod.plan_set(s, ctx, rid, list(changes), list(unset), body=body,
                             role_checked=True, may_collide=True)
    return _write(root, plan)


def delete(root, rid, why):
    return _write(root, lambda s, ctx: pmod.plan_delete(s, ctx, rid, why,
                                                        role_checked=True))   # an OWNER route


def unblock(root, rid, on=None, reason=None):
    def plan(s, ctx):
        rec = s.get(rid)
        if rec is not None and on is None and rec.get('state') == 'blocked':
            members = rec.all('blocked_on')
            if len(members) > 1:
                raise HubError('invalid', '%s is blocked on %s: name the one to clear (on) and '
                               'give a reason' % (rid, ', '.join(members)), 'on')
        return pmod.plan_unblock(s, ctx, rid, on, reason, role_checked=True)   # an OWNER route
    return _write(root, plan)


def answer(root, rid, qid, at, title, label, note=None, via=None, other=False, waiting=()):
    # the owner's stored answer (module docstring), so the unblock-owner row's check is made
    return _write(root, lambda s, ctx: pmod.plan_answer(
        s, ctx, rid, qid, at, title, label, note or None, via or None, other, list(waiting),
        role_checked=True), no_store='no_store')


def condition_met(root, rid, member, cond):
    """Clear blocker `member` of `rid`, whose `until` condition `cond` the caller found met
    (hubjobs.unblock_conditions), in one store commit under Session: hub-condition
    (plan.plan_condition_met). {"lines", "unblocked", "dropped"}: dropped when the line was
    dropped, unblocked when that cleared the member (an `owner` member with other lines left
    keeps its hold); both False when the record no longer holds that condition, and nothing was
    written."""
    change = _change(root, lambda s, ctx: pmod.plan_condition_met(s, ctx, rid, member, cond),
                     session=CONDITION_SESSION)
    wrote = bool(change.writes)
    return {'lines': list(change.lines), 'dropped': wrote,
            'unblocked': wrote and getattr(change, 'cleared', True)}


# --- the hub as the owner's proxy: a record waits on the question bound to it --------------------

# The Session: line of park's and withdraw's commits: the hub blocking a record on the owner as
# the owner's proxy, never an agent (an audit by-design writer, audit_check.BY_DESIGN; its
# policy.by_design_refusal keeps any agent's --session off it).
PARK_SESSION = 'hub-park'
PARK_REASON = 'asked as %s on the Action Hub by %s'
PARK_ASK = '%s on the Action Hub (asked by %s)'
WITHDRAWN = 'ask withdrawn'
# What park found; `blocked` is the one that writes.
PARK_OUTCOMES = ('blocked', 'already', 'held', 'parked', 'gone')


def park(root, rid, item_id, cond, asker):
    """Block `rid` on `owner` until `cond` (`question:<item_id> answered`, `todo:<item_id> done`
    or `approval:<item_id> decided`), as the owner's proxy: an ask bound to a record means the
    record waits on the owner (docs/backlog.md "Owner gates").
    One store commit under Session: hub-park, by the record's state:

      open       blocked on owner, `until: owner <cond>`, body line `Blocked on owner (<date>):
                 asked as <item_id> on the Action Hub by <asker>`; `owner_ask` names the ask when
                 the record has none
      pulled     the same, and its pull stopped as its holder would (plan.stop_pull: the branch is
                 kept and named in the body; answering the last ask restores the pull while that
                 branch still holds its tip, plan.restore_pull)
      blocked    owner added beside its blockers, with the line; a record already blocked on
                 owner with lines adds its line (several read as AND)

    Nothing is written for a record blocked on owner by hand with no owner `until` line (`held`:
    that hold keeps its own end, and the answer filed on the record still ends it,
    plan.plan_answer), one parked by hand (`parked`: the hub never touches a park), one with the
    line already (`already`: a retry), or one gone (`gone`: closed). {"lines", "park": one of
    PARK_OUTCOMES}."""
    reason = PARK_REASON % (item_id, asker)
    line = 'owner %s' % cond
    found = {}

    def plan(s, ctx):
        rec = s.get(rid)
        state = rec.get('state') if rec is not None else None
        subject = 'backlog: block %s on owner: %s' % (rid, reason)
        if rec is None:
            found['park'] = 'gone'
            return bstore.Change({}, subject, ['%s is gone: nothing waits on %s' % (rid, item_id)])
        if state == 'parked':
            found['park'] = 'parked'
            return bstore.Change({}, subject, ['%s is parked: the hub leaves a park as it is'
                                               % rid])
        owner_lines = [v for v in rec.all('until') if v.split(' ', 1)[0] == 'owner']
        if state == 'blocked' and line in rec.all('until'):
            found['park'] = 'already'
            return bstore.Change({}, subject, ['%s already waits on `until: %s`' % (rid, line)])
        if state == 'blocked' and 'owner' in rec.all('blocked_on') and not owner_lines:
            found['park'] = 'held'
            return bstore.Change({}, subject, ['%s is held on the owner by hand: that hold keeps '
                                               'its own end' % rid])
        if state == 'pulled':
            # the hub stops the pull as its holder would: the commit names the holder's branch
            ctx.branch = rec.get('pulled_by')
        found['park'] = 'blocked'
        ask = None if rec.has('owner_ask') else PARK_ASK % (item_id, asker)
        return pmod.plan_block(s, ctx, rid, ['owner'], owner_ask=ask, reason=reason, until=cond,
                               role_checked=True)    # the owner's proxy (module docstring)
    change = _change(root, plan, session=PARK_SESSION)
    return {'lines': list(change.lines), 'park': found.get('park')}


def asks(root):
    """The asks the store at `root` waits on: [{id, q, kind, parked}], one per `until: owner
    <condition>` line on an owner event (conditions.OWNER_KINDS: `question:<q> answered`,
    `todo:<q> done`, `approval:<q> decided`; `kind` the condition's) of a record still blocked
    on the owner, `parked` true when the record's body says the hub parked it on q
    (PARK_REASON). Read with no fetch, as the hub's timed jobs read
    (hubjobs.unblock_conditions open_snapshot); the implicit store holds none. A store
    that cannot be read is a HubError `store`. For the hub's sweep of lines whose ask is gone
    (kitlib.hubsite.api questions_file)."""
    from kitlib.backlog import conditions as condmod
    from kitlib.backlog import record as rmod
    try:
        ctx = opener.open_store(root, fetch=False, session=PARK_SESSION, probes=opener.NO_PROBES)
        if getattr(ctx.store.backend, 'implicit', False):
            return []
        snap = ctx.store.load()
    except Failure as e:
        raise HubError('store', str(e), 'repo')
    out = []
    for rec in snap.readable:
        if rec.get('state') != 'blocked' or 'owner' not in rec.all('blocked_on'):
            continue
        for member, text in rmod.until_lines(rec):
            c = condmod.parse(text) if member == 'owner' else None
            if (c is None or c.kind not in condmod.OWNER_KINDS
                    or text != '%s:%s %s' % (c.kind, c.target, c.state)):
                continue
            out.append({'id': rec.id, 'q': c.target, 'kind': c.kind,
                        'parked': ('asked as %s on the Action Hub' % c.target) in rec.body})
    return out


def withdraw(root, rid, cond):
    """The ask behind `until: owner <cond>` on `rid` is gone (withdrawn or deleted, or the
    question no longer bound to it): drop that line under Session: hub-park; the last owner line
    clears the owner with the reason `ask withdrawn` (plan.plan_drop_until; a pull park stopped
    comes back as plan.restore_pull says). Nothing is written when the record no longer holds the
    line, or is gone. {"lines", "dropped", "unblocked"}."""
    found = {}

    def plan(s, ctx):
        if s.get(rid) is None:
            return bstore.Change({}, 'backlog: unblock %s from owner: %s' % (rid, WITHDRAWN),
                                 ['%s is gone: nothing to drop' % rid])
        change = pmod.plan_drop_until(s, ctx, rid, 'owner', cond, WITHDRAWN, role_checked=True)
        found['cleared'] = change.cleared
        return change
    change = _change(root, plan, session=PARK_SESSION)
    wrote = bool(change.writes)
    return {'lines': list(change.lines), 'dropped': wrote,
            'unblocked': wrote and bool(found.get('cleared'))}
