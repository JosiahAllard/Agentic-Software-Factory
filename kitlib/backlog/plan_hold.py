"""The plans that hold a record or wait on the owner: `block`, `unblock` and its `--until`
condition, `set-status` (parking), `unpark`, and filing the owner's answer (plan_answer).
kitlib/backlog/plan.py has the shared Context and checks.
"""
import datetime
import re

from kitlib import kitroot

from . import conditions as condmod
from . import offer as omod
from . import plan_claim as claim
from . import policy
from . import record as rmod
from .errors import Failure, Refusal, Usage
from .plan import (_append_line, _text, clears_owner_blocker, reason_error, refuse_machine_paths,
                   refuse_top, require)
from .store import Change


# --- block / unblock ------------------------------------------------------------------------------

def _hand_owner_hold(rec):
    """True when `rec` is held on the owner with no `until: owner` line: a hold set by hand,
    which keeps its own end (the hub's park leaves it alone too)."""
    return 'owner' in rec.all('blocked_on') and \
        not any(condmod.split(v)[0] == 'owner' for v in rec.all('until'))


def plan_block(snap, ctx, rid, members, owner_ask=None, reason=None, until=None,
               role_checked=False):
    """`block` ADDS `members` to `blocked_on`, in order, after the blockers already there (one
    already present is not repeated); it never removes one, so it never releases the owner's hold:
    removing a blocker is `unblock --on`'s job.
    `until`, when given, is the end condition of the one member (exactly one `--on`, a member with
    no space in it), written as `until: <member> <condition>` and replacing that member's line
    (an `owner` line is added beside the owner's others instead: one per ask, read as AND, so the
    hold ends when the last is met; the same line twice is written once);
    kitlib/backlog/conditions.py names the conditions the Action Hub checks, and an `owner` member
    takes only an owner event (a to-do, a question or an approval). An `until` on `owner`
    schedules the owner's hold to end: unless it is one of the three forms a lead may set
    (policy.lead_until), it is the `unblock-owner` row (policy.sort), and without
    `role_checked` it refuses (refuse_top). One of those three forms on a record already held on
    the owner by hand (no `until: owner` line) is refused too unless the role check ran and the
    session's role may take `unblock-owner` (policy.may: the owner or the PM): only the owner or
    the PM schedules the end of a hold they set.
    `reason`, when given, goes into the commit subject and onto one line appended to the body
    (`Blocked on M (date): T`), so `show` says why the record is held; no header key is written
    for it. A PULLED record is blocked only by whoever may stop its pull (stop_pull), and only
    with a `reason`: the one commit also clears the pull and pre-emption keys, which frees its
    scope, and a body line `Stopped on branch B at <tip>, branch kept.` goes before the `Blocked
    on` line while that branch exists. The branch is never touched."""
    refuse_machine_paths('--owner-ask', owner_ask)
    if reason is not None:
        refuse_machine_paths('--reason', reason)
        bad = reason_error(reason, '--reason')
        if bad:
            raise Usage(bad)
        reason = ' '.join(reason.split())
    rec = require(snap, rid)
    if not members:
        raise Usage('`block` requires at least one `--on <member>`')
    for m in members:
        if m == '-':
            raise Usage('`--on -` is not a blocker; `unblock` writes `-`')
        err = rmod.blocked_on_error(m, snap.rules)
        if err:
            raise Usage('bad `--on %s`: %s' % (m, err))
    if 'item:%s' % rid in members:
        raise Usage('`%s` cannot be blocked on itself' % rid)
    if 'owner' in members and not owner_ask and not rec.has('owner_ask'):
        raise Usage('blocking on the owner needs `--owner-ask` citing the Action Hub ask it '
                    'waits on: %s' % rmod.owner_ask_fix(rid, kitroot.command('hub')))
    if until is not None:
        until = ' '.join(until.split())
        refuse_machine_paths('--until', until)
        if not until:
            raise Usage('`--until` needs the condition that ends the hold')
        if len(members) != 1:
            raise Usage('`--until` is the end condition of one blocker: give exactly one `--on`')
        if len(members[0].split()) != 1:
            raise Usage('`--until` needs a one-word member, and `--on %s` has a space in it'
                        % members[0])
        why = condmod.owner_error(members[0], until)
        if why:
            raise Usage('refused `--until %s`: %s' % (until, why))
        if condmod.kind_of(until) == 'resource':
            why = condmod.resource_error(until, ctx.resources) if condmod.parse(until) else \
                'not the form `resource:<name> free` (<name>: lowercase letters, digits, `-`)'
            if why:
                raise Usage('refused `--until %s`: %s' % (until, why))
        if members[0] == 'owner' and not role_checked and not policy.lead_until(until):
            refuse_top(rid, 'block', ['unblock-owner'])
        if members[0] == 'owner' and _hand_owner_hold(rec) and \
                not (role_checked and policy.may('unblock-owner')):
            # A lead's owner-event `--until` (policy.lead_until) ends only a hold it adds, or
            # one already ending on the owner's asks: a hold set by hand keeps its own end, as
            # the hub's park leaves it (kitlib/hubsite/recordwrite.py `park`).
            raise Refusal('`%s` is held on the owner by hand (`blocked_on: owner`, no `until: '
                          'owner` line): a lead\'s `--until` would schedule that hold to end. '
                          'The owner or the PM sets it (%s)'
                          % (rid, policy.row_text('unblock-owner')))
    if 'owner' in members:
        # Every caller passes here (tools/backlog.py, the hub's park): an owner hold cites the
        # hub ask that ends it, never only itself or another item. A hub id given now is enough
        # beside an older stored ask (record.owner_citation_error).
        bad = rmod.owner_citation_error(rid, reason, owner_ask, hub=kitroot.command('hub'),
                                        stored=None if owner_ask else rec.get('owner_ask'))
        if bad:
            raise Usage(bad)
    stop = None
    if rec.get('state') == 'pulled':
        if reason is None:
            raise Usage('blocking a pulled record frees its scope: it needs `--reason T`, '
                        'written to the body')
        holder = rec.get('pulled_by')
        if not holder:
            raise Failure('`%s` is pulled with no `pulled_by`; `check` names it' % rid)

        def live(why):
            return ('`%s` is in flight (pulled by %s), and %s. Its holder blocks or releases it: %s'
                    % (rid, holder, 'its tree is live: %s' % why if why else
                       'a branch or worktree by that name exists',
                       claim.holder_form(rec, holder,
                                         'block %s --on <member> --reason "<why>"' % rid)))
        stop = claim.stop_pull(ctx, rid, holder, live)
    old =[m for m in rec.all('blocked_on') if m != '-']
    after = list(old)
    for m in members:
        if m not in after:
            after.append(m)
    changes = [('state', ['blocked']), ('blocked_on', after)]
    if owner_ask:
        changes.append(('owner_ask', [owner_ask]))
    # A member this block adds gets no `until` line it did not give: a line an older plugin left
    # behind on a member it cleared would otherwise come live again.
    # `owner` keeps a line per ask (conditions.SEVERAL, read as AND): its `--until` adds a line;
    # any other member's replaces its one line.
    fresh = [m for m in members if m not in old]
    several = until is not None and members[0] == condmod.SEVERAL
    kept = [v for v in rec.all('until') if condmod.split(v)[0] not in fresh
            and (until is None or several or condmod.split(v)[0] != members[0])]
    if until is not None and '%s %s' % (members[0], until) not in kept:
        kept.append('%s %s' % (members[0], until))
    if kept != rec.all('until'):
        changes.append(('until', kept))
    if stop is not None:
        changes += [(k, []) for k in rmod.PULL_CLEARS + rmod.PREEMPT_KEYS]
    pairs = rmod.apply_changes(rec.pairs(), changes)
    lines = ['BLOCKED %s on %s' % (rid, ', '.join(members))]
    if old:
        lines.append('  blocked_on: %s -> %s' % (', '.join(old), ', '.join(after)))
    if until is not None:
        lines.append('  until: %s %s%s' % (members[0], until, '' if condmod.parse(until) else
                                           ' (free text: no program checks it; clear it with '
                                           '`unblock --on %s`)' % members[0]))
    if stop is not None:
        lines += stop[1]
    subject = 'backlog: block %s on %s' % (rid, ', '.join(members))
    body = rec.body
    if reason is not None:
        subject += ': %s' % reason
        note = '%sBlocked on %s (%s): %s' % (stop[0] if stop else '', ', '.join(members),
                                             ctx.date(), reason)
        body = (body or '').rstrip('\n')
        body = (body + '\n\n' if body.strip() else '') + note + '\n'
        lines.append('  reason (in the body): %s' % reason)
    text = _text(pairs, body)
    if text == rec.text:
        return Change({}, subject, lines + ['  (no change)'])
    return Change({rid: text}, subject, lines)


def plan_unblock(snap, ctx, rid, on=None, reason=None, role_checked=False):
    """Clear one blocker. A record with one blocker opens, `on` and `reason` optional; one with
    several needs `on` (the member to clear) and `reason`, and keeps the rest blocked. The reason
    goes into the commit subject, as `release`'s does. Clearing the owner is the role check's
    `unblock-owner` row (kitlib/backlog/roles.py): without `role_checked` it refuses
    (refuse_top)."""
    rec = require(snap, rid)
    if rec.get('state') != 'blocked':
        raise Usage('`%s` is `state: %s`, not `blocked`: there is nothing to unblock'
                    % (rid, rec.get('state')))
    members = rec.all('blocked_on')
    several = len(members) > 1
    if on is None and several:
        raise Refusal('`%s` is blocked on %s; `unblock` clears one blocker, named, with a reason:\n%s'
                      % (rid, ', '.join(members), '\n'.join(
                          '  %s -C <worktree> unblock %s --on %s --reason "<why>"'
                          % (kitroot.command('backlog'), rid, m) for m in members)))
    if on is not None and on not in members:
        raise Usage('`--on %s` is not a blocker of `%s`; it is blocked on %s'
                    % (on, rid, ', '.join(members)))
    if reason is not None or several:
        refuse_machine_paths('--reason', reason)
        bad = reason_error(reason, '--reason')
        if bad:
            raise Usage(bad)
    left = [m for m in members if m != on] if several else []
    unheld = clears_owner_blocker(rec, left)
    if unheld and not role_checked:
        refuse_top(rid, 'unblock', ['unblock-owner'])
    pairs = [(k, v) for k, v in rec.pairs() if not (unheld and k == 'owner_ask')]
    restored, restore_lines, note = ([], [], '') if left else claim.restore_pull(snap, ctx, rec)
    changes = [('blocked_on', left)] if left else [('state', ['open']), ('blocked_on', ['-'])]
    pairs = rmod.apply_changes(pairs, changes + restored)
    if left:
        lines = ['UNBLOCKED %s - `%s` removed -> %s' % (rid, on, ', '.join(left))]
    else:
        lines = ['UNBLOCKED %s - state: %s, blocked_on: -' % (rid, 'pulled' if restored else 'open')]
    lines += restore_lines
    if unheld and rec.has('owner_ask'):
        lines.append('  owner_ask dropped: nothing waits on the owner now. It was: %s' % rec.get('owner_ask'))
    subject = 'backlog: unblock %s' % rid
    if reason is not None:
        named = next((m for m in ([on] if on else members) if m != '-'), None)
        subject += '%s: %s' % (' from %s' % named if named else '', ' '.join(reason.split()))
    body = _append_line(rec.body, note) if note else rec.body
    return Change({rid: _text(pairs, body)}, subject, lines)


CONDITION_MET = 'condition met: '      # the unblock reason plan_condition_met writes


def plan_condition_met(snap, ctx, rid, member, cond):
    """Clear blocker `member` of `rid` because its end condition `cond` is met: plan_unblock with
    the reason `condition met: <cond>` (the commit subject). The caller checked the condition
    (kitlib.hubjobs.unblock_conditions); this checks the record still holds it: `rid` blocked on
    `member`, with the line `until: <member> <cond>`. When it does not (an unblock or a re-block
    got there first), nothing is written, so a second run changes nothing. A condition that is
    not one of conditions.py's forms is refused, and so is any but an owner event on `owner`:
    only those clear the owner's hold (role_checked, the unblock-owner row: the owner's own act
    ended it)."""
    if condmod.parse(cond) is None:
        raise Refusal('`%s`: `until: %s %s` is free text, which no program acts on; it is cleared '
                      'by hand with `unblock --on %s`' % (rid, member, cond, member))
    why = condmod.owner_error(member, cond)
    if why:
        raise Refusal('`%s`: %s' % (rid, why))
    return plan_drop_until(snap, ctx, rid, member, cond, CONDITION_MET + cond,
                           role_checked=member == 'owner')


def plan_drop_until(snap, ctx, rid, member, cond, reason, role_checked=False):
    """Drop the line `until: <member> <cond>` of `rid` for `reason` (its condition is met, or
    the ask it waits on was withdrawn). The member's last line clears the member, as plan_unblock
    with `reason` (the commit subject); an `owner` member with other lines left
    (conditions.SEVERAL, read as AND) stays, losing only that line: the body is not touched, so a
    pull a block stopped still waits (blocked_pull). Either is the `unblock-owner` row on
    `owner`: without `role_checked` it refuses (refuse_top). When the record no longer holds that
    line (an unblock or a re-block got there first) nothing is written, so a second run changes
    nothing. The Change's `cleared` says whether the member was cleared."""
    rec = require(snap, rid)
    line = '%s %s' % (member, cond)
    reason = ' '.join(reason.split())
    subject = 'backlog: unblock %s from %s: %s' % (rid, member, reason)
    if (rec.get('state') != 'blocked' or member not in rec.all('blocked_on')
            or line not in rec.all('until')):
        change = Change({}, subject, ['%s no longer waits on `until: %s`: nothing to clear'
                                      % (rid, line), '  (no change)'])
        change.cleared = False
        return change
    others = [v for v in rec.all('until') if v != line]
    left = [condmod.split(v)[1] for v in others if condmod.split(v)[0] == member]
    if not left:
        change = plan_unblock(snap, ctx, rid, on=member, reason=reason, role_checked=role_checked)
        change.cleared = True
        return change
    if member == 'owner' and not role_checked:
        refuse_top(rid, 'unblock', ['unblock-owner'])
    pairs = rmod.apply_changes(rec.pairs(), [('until', others)])
    change = Change({rid: _text(pairs, rec.body)},
                    '%s (still held on %s: %d more)' % (subject, member, len(left)),
                    ['UNTIL %s - `until: %s` dropped (%s); still blocked on %s until %s'
                     % (rid, line, reason, member, '; '.join(left))])
    change.cleared = False
    return change


# --- park / unpark -----------------------------------------------------------------------

def plan_set_status(snap, ctx, rid, status, q=None):
    """Park an open or pulled record, on the owner's question `q` when one is given (`q` is
    optional: a record can wait with no question id). A pull is given up: a parked record holds no
    scope and takes no place in the offer. A parked record is re-pointed to another `q`; with no
    `q` its recorded question is dropped (`parked_at` kept). The same question again, or no `q`
    on a record parked with none, refuses: nothing would change."""
    if status != 'parked':
        raise Usage('`set-status` sets `parked` only; `%s` is reached by its own verb (open: '
                    '`unblock`/`release`/`unpark`, blocked: `block`, pulled: `pull`)' % status)
    if q is not None and rmod._question(q) is not None:
        raise Usage('bad `--q %s`: %s' % (q, rmod._question(q)))
    rec = require(snap, rid)
    state = rec.get('state')
    if state == 'parked':
        was = rec.get('q')
        if was == q:
            raise Refusal('`%s` is already parked on %s' % (rid, q) if q else
                          '`%s` is already parked with no question' % rid)
        # Re-point: only `q` changes; `parked_at` keeps the day the wait began, and the old
        # question stays in the store's log through the commit subject.
        pairs = rmod.apply_changes(rec.pairs(), [('q', [q] if q else [])])
        was = was or 'no recorded question'
        on = 'on %s' % q if q else 'with no question'
        return Change({rid: _text(pairs, rec.body)},
                      'backlog: re-park %s %s (was %s)' % (rid, on, was),
                      ['PARKED %s %s, re-pointed (was %s)' % (rid, on, was)])
    if state not in ('open', 'pulled'):
        raise Refusal('`%s` is `state: %s`; only an open or pulled record is parked. %s first'
                      % (rid, state, 'Unblock it' if state == 'blocked' else 'Open it'))
    if state == 'pulled':
        # Parking gives up the pull, so it takes block's and release's holder check: only the
        # holder, or anybody when the pull or its worktree is dead (stop_pull). Park writes no
        # body line, so stop_pull reads no branch (kept=False).
        holder = rec.get('pulled_by')
        if not holder:
            raise Failure('`%s` is pulled with no `pulled_by`; `check` names it' % rid)

        def live(why):
            return ('`%s` is in flight (pulled by %s), and %s. Its holder parks or releases it: %s'
                    % (rid, holder, 'its tree is live: %s' % why if why else
                       'a branch or worktree by that name exists',
                       claim.holder_form(rec, holder, 'set-status %s parked%s'
                                         % (rid, ' --q %s' % q if q else ''))))
        found = claim.stop_pull(ctx, rid, holder, live, kept=False)[1]
    pairs = [(k, v) for k, v in rec.pairs() if k not in rmod.PULL_CLEARS + rmod.PREEMPT_KEYS]
    pairs = rmod.apply_changes(pairs, [('state', ['parked']), ('q', [q] if q else []),
                                       ('parked_at', [ctx.date()])])
    lines = ['PARKED %s %s' % (rid, 'on %s' % q if q else '(no question)')]
    if state == 'pulled':
        lines.append('  pull by %s given up: its scope is free and it is out of the offer'
                     % rec.get('pulled_by'))
        lines += found
    return Change({rid: _text(pairs, rec.body)},
                  'backlog: park %s%s' % (rid, ' on %s' % q if q else ''), lines)


def plan_unpark(snap, ctx, rid):
    """Open a parked record once its question is answered: the park keys go and it is `state:
    open` again, an ordinary record in `next`. Who may is the role check's `unpark` row."""
    rec = require(snap, rid)
    if rec.get('state') != 'parked':
        raise Refusal('`%s` is `state: %s`, not `parked`: there is nothing to unpark'
                      % (rid, rec.get('state')))
    pairs = [(k, v) for k, v in rec.pairs() if k not in rmod.PARK_KEYS]
    pairs = rmod.apply_changes(pairs, [('state', ['open'])])
    lines = ['UNPARKED %s - state: open%s' % (rid, ' (was parked on %s)' % rec.get('q')
                                                if rec.get('q') else '')]
    return Change({rid: _text(pairs, rec.body)}, 'backlog: unpark %s' % rid, lines)


# --- the owner's answer ---------------------------------------------------------------------------

ANSWER_REVIEW = omod.ANSWER_REVIEW     # `next` reads it back (offer.review_line)
ASKED_Q = re.compile(r'(?<![A-Za-z0-9-])Q-[0-9]{3,}(?![A-Za-z0-9_])')


def _waits_on(rec):
    """The questions the record's own wait names: `q` on a parked record, the Q-ids `owner_ask`
    cites on one blocked on the owner. Empty: nothing binds the wait to a question."""
    state = rec.get('state')
    if state == 'parked':
        return [rec.get('q')] if rec.get('q') else []
    if state == 'blocked' and 'owner' in rec.all('blocked_on'):
        return ASKED_Q.findall(rec.get('owner_ask') or '')
    return []


def plan_answer(snap, ctx, rid, qid, at, title, label, note=None, via=None, other=False,
                waiting=(), role_checked=False):
    """File the owner's answer to question `qid` (answered at `at`, the hub's timestamp) into
    the record it is bound to, in one write: an `answer: <qid> <at>` line (one per question; a
    re-answer replaces its line) and a `## Owner answer (Action Hub, <at>)` body section with
    `title`, the choice `label` and `note` verbatim, and `via`. Only the Action Hub calls it
    (WRITTEN_BY: `set` neither writes nor unsets `answer`).

    The record's wait ends only on the last answer: when `waiting` (the other questions bound to
    the record, still unanswered; the hub knows them) is empty and every question the wait names
    (_waits_on) has an answer line, a parked record is unparked (as plan_unpark) and the `owner`
    blocker is cleared (as plan_unblock: other blockers stay, `owner_ask` goes), which is the
    `unblock-owner` row, so it needs `role_checked` (else a
    Refusal: the owner's or the PM's role clears an owner blocker). An owner hold with `until`
    lines (one per ask: the hub parks a record on each question bound to it) ends on those
    instead: the answer drops its own `owner question:<qid> answered` line, and the hold ends
    when no owner line is left, `waiting` aside (a bound question whose park has not run parks
    the record again itself). A pulled or open record, or a wait no
    question names, only gets the answer.
    `other` (a free-text answer) also writes the review mark `review: owner-answer <date>`
    unless one stands, so `pull` refuses the record and `next` lists it to review until a
    coordinator's `clear-review`. The same `qid` and `at` again changes nothing, and so does an
    `at` older than the record's line for `qid` (a stale re-file never overwrites a newer
    answer). A record that
    is gone is NotFound: the hub leaves the answer to the PM."""
    err = rmod._question(qid) if qid else 'no question id'
    if err:
        raise Usage('bad question `%s`: %s' % (qid, err))
    line = '%s %s' % (qid, at)
    err = rmod._answer(line)
    if err:
        raise Usage('bad answer time: %s' % err)
    if not (title or '').strip() or not (label or '').strip():
        raise Usage('an owner answer needs the question\'s title and the choice\'s label')
    for w in waiting:
        if rmod._question(w):
            raise Usage('bad waiting question `%s`: %s' % (w, rmod._question(w)))
    refuse_machine_paths('answer', title, label, note, via)
    rec = require(snap, rid)
    subject = 'backlog: owner answer %s on %s' % (rid, qid)
    if line in rec.all('answer'):
        return Change({}, subject, ['ANSWERED %s on %s (%s)' % (rid, qid, at), '  (no change)'])
    when = datetime.datetime.fromisoformat(at)
    newer = [v for v in rec.all('answer') if v.partition(' ')[0] == qid
             and rmod._answer(v) is None
             and datetime.datetime.fromisoformat(v.partition(' ')[2]) > when]
    if newer:
        return Change({}, subject, ['ANSWERED %s on %s (%s)' % (rid, qid, at),
                                    '  (no change: the record holds a newer answer, `%s`)'
                                    % newer[0]])
    others = [v for v in rec.all('answer') if v.partition(' ')[0] != qid]
    pairs = rmod.apply_changes(rec.pairs(), [('answer', others + [line])])
    lines = ['ANSWERED %s on %s (%s): %s' % (rid, qid, at, ' '.join(label.split()))]
    done = set(rmod.answered(rec)) | {qid}
    asked = _waits_on(rec)
    state = rec.get('state')
    pending = [q for q in asked if q not in done] + [w for w in waiting if w not in done]
    # An owner hold with `until` lines (one per ask, conditions.SEVERAL) ends on those: this
    # answer drops its own line, and the hold ends when no owner line is left, whatever
    # `owner_ask` cites or `waiting` lists: the unblock-conditions job reads only the lines, so
    # the two writers agree, and a question still open whose park has not run parks the record
    # again with its own line (kitlib.hubsite.api park_item).
    owner_lines = [c for m, c in rmod.until_lines(rec) if m == condmod.SEVERAL]
    if state == 'blocked' and 'owner' in rec.all('blocked_on') and owner_lines:
        mine = 'question:%s answered' % qid
        left = [c for c in owner_lines if c != mine]
        if left != owner_lines:
            pairs = rmod.apply_changes(pairs, [('until', [
                v for v in rec.all('until') if v != '%s %s' % (condmod.SEVERAL, mine)])])
            lines.append('  until: owner %s dropped' % mine)
        asked = [qid]
        pending = ['`owner %s`' % c for c in left]
    stop_note = ''
    if not asked:
        lines.append('  state: %s, unchanged: its wait names no question' % state)
    elif pending:
        lines.append('  state: %s, unchanged: still waits on %s' % (state, ', '.join(pending)))
    elif state == 'parked':
        pairs = [(k, v) for k, v in pairs if k not in rmod.PARK_KEYS]
        pairs = rmod.apply_changes(pairs, [('state', ['open'])])
        lines.append('  UNPARKED - state: open (was parked on %s)' % rec.get('q'))
    else:
        if not role_checked:
            raise Refusal('`%s`: filing the answer to %s clears the record\'s owner blocker, '
                          'which needs the owner\'s or the PM\'s role (`unblock-owner`, '
                          'kitlib/backlog/roles.py), and no role check ran: the Action Hub files '
                          'it from the owner\'s answer route, with role_checked' % (rid, qid))
        left = [m for m in rec.all('blocked_on') if m != 'owner']
        pairs = [(k, v) for k, v in pairs if k != 'owner_ask']
        restored, restore_lines, stop_note = (([], [], '') if left
                                              else claim.restore_pull(snap, ctx, rec))
        changes = [('blocked_on', left)] if left else [('state', ['open']),
                                                         ('blocked_on', ['-'])]
        pairs = rmod.apply_changes(pairs, changes + restored)
        lines.append('  owner blocker cleared - %s' % ('still blocked on %s' % ', '.join(left)
                                                       if left else 'state: %s' % (
                                                           'pulled' if restored else 'open')))
        lines += restore_lines
    marked = other and not rec.has('review')
    if marked:
        mark = '%s %s' % (ANSWER_REVIEW, ctx.date())
        pairs = rmod.apply_changes(pairs, [('review', [mark])])
        lines.append('  review: %s - a free-text answer: `pull` refuses it until a coordinator '
                     'reads it and runs `clear-review %s --reason T`' % (mark, rid))
    section = ['## Owner answer (Action Hub, %s)' % at, '',
               'Question: %s, %s' % (qid, ' '.join(title.split())), 'Answer: %s' % label]
    if note:
        section.append('Note: %s' % note)
    if via:
        section.append('Via: %s' % via)
    if marked:
        section.append('Marked for review: a free-text answer, read by a coordinator before '
                       'anyone pulls the record.')
    body = (rec.body or '').rstrip('\n')
    body = (body + '\n\n' if body.strip() else '') + '\n'.join(section) + '\n'
    if stop_note:
        body = _append_line(body, stop_note)
    lines.append('  body: section `## Owner answer (Action Hub, %s)` appended' % at)
    return Change({rid: _text(pairs, body)}, subject, lines)
