"""The plans that end a record: `close`, `retire` and `delete`, and the rewrite of the records
blocked on it (dependent_rewrites). kitlib/backlog/plan.py has the shared Context and checks.
"""
from . import plan_claim as claim
from . import policy
from . import record as rmod
from .errors import Failure, Refusal, Usage
from .plan import _append_line, _text, reason_error, refuse_machine_paths, refuse_top, require
from .store import Change


# --- close / retire -------------------------------------------------------------------------------

def dependent_rewrites(snap, rid, day, gone='closing'):
    """`({stem: text}, lines)`: every record blocked on `item:<rid>`, without that member; one
    left with none takes `blocked_on: -` and, if blocked, `state: open`. One that opens with a
    stopped pull (blocked_pull) gets the body line that ends it (stale_stop_note, dated `day`,
    `gone` saying what became of `rid`), so a later unblock cannot restore it; this never
    restores a pull."""
    target = 'item:%s' % rid
    writes, lines = {}, []
    for other in snap.readable:
        members = other.all('blocked_on')
        if other.id == rid or target not in members:
            continue
        left = [m for m in members if m != target] or ['-']
        opened = left == ['-'] and other.get('state') == 'blocked'
        changes = [('blocked_on', left)] + ([('state', ['open'])] if opened else [])
        note = (claim.stale_stop_note(other, day, 'opened by %s %s' % (target, gone)) if opened
                else None)
        body = _append_line(other.body, note) if note else other.body
        writes[other.stem] = _text(rmod.apply_changes(other.pairs(), changes), body)
        lines.append('UNBLOCKED %s - `%s` removed -> %s%s' % (other.id, target, ', '.join(left),
                                                              '; state: open' if opened else ''))
    return writes, lines


def _claim_subject(verb, rid, reason, flag):
    refuse_machine_paths(flag, reason)
    bad = reason_error(reason, flag)
    if bad:
        raise Usage(bad)
    return '%s %s: %s' % (verb, rid, ' '.join(reason.split()))


def refuse_unchecked_end(rec, verb, role_checked):
    """`close`, `retire` and `delete` of an owner-held record (`close-held`), and `retire` or
    `delete` of one the owner or the PM priced (`retire-priced`), are the owner's and the PM's
    (kitlib/backlog/roles.py): without `role_checked` (a landing's close, a library caller) the
    write refuses (refuse_top)."""
    if not role_checked:
        refuse_top(rec.id, verb, policy.sort(verb, {}, rec))


def plan_close(snap, ctx, rid, reason, role_checked=False):
    """The work is done: delete the record. An owner-held record is the `close-held` row, so a
    close without `role_checked` (opener ran the role check; a landing's close, at_landing, runs
    none) refuses it (refuse_unchecked_end)."""
    rec = require(snap, rid)
    subject = _claim_subject('closes', rid, reason, '--reason')
    refuse_unchecked_end(rec, 'close', role_checked)
    writes, lines = dependent_rewrites(snap, rid, ctx.date())
    writes[rid] = None
    lines.insert(0, 'CLOSED %s' % rid)
    if rec.get('state') != 'pulled':
        lines.append('warning: `%s` was `state: %s`, never pulled; it closes anyway'
                     % (rid, rec.get('state')))
    return Change(writes, subject, lines)


def plan_retire(snap, ctx, rid, why, role_checked=False):
    """The work should not be done: delete the record. An owner-held or owner-priced record is
    the owner's and the PM's (refuse_unchecked_end). A pull whose branch is live refuses, as
    `delete` does (`_refuse_live_pulls`); a dead pull goes with the record."""
    rec = require(snap, rid)
    subject = _claim_subject('retires', rid, why, '--why')
    notes = _refuse_live_pulls(snap, ctx, [rec])
    refuse_unchecked_end(rec, 'retire', role_checked)
    writes, lines = dependent_rewrites(snap, rid, ctx.date(), 'retiring')
    writes[rid] = None
    lines.insert(0, 'RETIRED %s' % rid)
    lines[1:1] = notes
    return Change(writes, subject, lines)


def _refuse_live_pulls(snap, ctx, recs, note='  dead pull on `%s` dropped with the record',
                       retarget=False):
    """The guard `retire`, `delete` and `retarget` share: a pulled record in `recs` whose branch
    is live refuses (its holder releases it first; with `retarget`, every live one is named in
    the one refusal, which also offers retargeting after they land); `note % branch` for each
    dead pull (none when `note` is None). The live pulls are read once, and only when one of
    `recs` is pulled. Failure when they cannot be read: "cannot tell" is never "dead"."""
    pulled = [r for r in recs if r.get('state') == 'pulled']
    if not pulled:
        return []
    holding, _dead, probe = ctx.in_flight_split(snap)
    live = set(r.id for r in holding)
    notes, building = [], []
    for rec in pulled:
        holder = rec.get('pulled_by') or '?'
        if probe:
            raise Failure('cannot tell whether the pull of `%s` by `%s` is live: %s'
                          % (rec.id, holder, probe))
        if rec.id in live:
            building.append((rec.id, holder))
            if not retarget:
                break
        elif note:
            notes.append(note % holder)
    if building:
        raise Refusal(_live_pull_refusal(building, retarget))
    return notes


def _live_pull_refusal(building, retarget):
    """The refusal `_refuse_live_pulls` raises for `building`, [(id, branch)] of live pulls."""
    where = '; '.join('`%s` is being built on `%s`' % pair for pair in building)
    releases = ', '.join('`release %s --reason T`' % rid for rid, _b in building)
    if len(building) == 1:
        how = 'release it first (%s, from that branch)' % releases
        return '%s; %s%s' % (where, 'retarget after it lands, or ' if retarget else '', how)
    return '%s; retarget after they land, or release them first (%s, each from its branch)'         % (where, releases)


def plan_delete(snap, ctx, rid, why, role_checked=False):
    """The record should not exist (the owner's delete from the Action Hub, or a coordinator's):
    delete it, with the subject `deletes ID: <why>`, which history reads as `closes`/`retires`
    are read (`add` again needs `--reopen`; a `requires: item:` on it never lands). The owner
    gates are `retire`'s (refuse_unchecked_end). A pull whose branch is live refuses: its
    holder releases it first. A dead pull goes with the record; a parked record's kept branch is
    left for its owner to remove."""
    rec = require(snap, rid)
    subject = _claim_subject('deletes', rid, why, '--why')
    state = rec.get('state')
    notes = _refuse_live_pulls(snap, ctx, [rec])
    if state == 'parked':
        notes.append('  parked on %s; a branch kept for it is not touched: remove it by hand'
                     % (rec.get('q') or 'no recorded question'))
    refuse_unchecked_end(rec, 'delete', role_checked)
    writes, lines = dependent_rewrites(snap, rid, ctx.date(), 'being deleted')
    writes[rid] = None
    return Change(writes, subject, ['DELETED %s' % rid] + notes + lines)
