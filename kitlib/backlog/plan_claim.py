"""The plans that take and give up a record: `pull` (a takeover among them) and `release`, and
the stopped-pull notes `block`, `set-status` and `unblock` write and read (stop_pull,
restore_pull; docs/backlog.md "Stopping a pull"). kitlib/backlog/plan.py has the shared Context.
"""
import re

from kitlib import kitroot
from kitlib.git import refs

from . import narrowed as nmod
from . import offer as omod
from . import plan_narrow as narrow
from . import policy
from . import record as rmod
from .errors import Failure, Refusal, Usage
from .plan import _text, branch_name_error, reason_error, refuse_machine_paths, require, shared_of
from .store import Change


# --- pull / release -------------------------------------------------------------------------------

def plan_pull(snap, ctx, rid, reason=None, may_collide=False, blocker=None, batch=False):
    """Take an open, priced record and record the branch. One in `next`'s offer pulls plainly;
    one outside it needs `--reason` and records `preempt` and the `displaced` offer, computed
    here. In the offer means in `next`'s offer as the caller's branch prints it (its own pulls
    left out, so a record overlapping only them is offered) or as `next` prints it without a
    branch (current_offer).
    A record held on the owner is `state: blocked`, so it is refused (as tools/backlog.py's role
    check refuses `pull-held` to every role). One whose scope collides with a live pull on another branch (one on the caller's
    branch is no collision) is the `pull-collision` row (kitlib/backlog/roles.py): refused unless
    `may_collide` (the caller's role may take it), and then it needs `--reason`, recorded in
    `preempt`, and the Change names the row (_collision_change). An unmet `requires:` refuses.
    The fold-in guard (`_pull_fold_hits`): one whose scope overlaps a path another live pull's
    branch changed is that row too; `next` passes such a record over, so its refusal comes
    before the not-offered one. A `uses:` resource live pulls on other branches fill
    (resource_hits) is the same row.
    A pulled record refuses, unless its pull is dead and it is this session's: see
    `_plan_takeover`.
    A record batched for another branch (`batch:`, offer.held_for_batch) refuses for every role:
    a lead lifts the hold (`set ID --unset batch`) first. `batch` (a topic session's pull,
    opener.planner): a record batched for this branch is offered with the whole batch first
    (batch_members as current_offer's seeds), so its rank order is no refusal; no `preempt` is
    written.
    A record `next` offers by narrowing (NARROWS, an offer's `split`) is pulled with the split
    current_offer recomputes from git now, so T grown since `next` keeps less (printed), and a
    split that no longer holds refuses as not offered, naming why. The pull takes `split.keep`
    and writes a `narrowed:` line per member given up; when the live pull gives up a member too
    (it has a `## Files`), its record is narrowed in the same store write, which is the
    `pull-collision` row (refused unless `may_collide`; no `--reason` needed, as the offer
    named it). Takeover never narrows (`_plan_takeover`)."""
    refuse_machine_paths('--reason', reason)
    rec = require(snap, rid)
    held = rec.get('batch')
    if held is not None and held != ctx.current_branch():
        raise Refusal('`%s` is held for the batch on %s (`batch: %s`), and only that branch pulls '
                      'it. A coordinator lifts the hold first, in its own step: `set %s --unset '
                      'batch`' % (rid, held, held, rid))
    if reason is not None:
        reason = ' '.join(reason.split())
        if not reason:
            raise Usage('`--reason` was given blank')
    state = rec.get('state')
    if state == 'pulled':
        return _plan_takeover(snap, ctx, rec, reason, may_collide, blocker)
    if state != 'open':
        raise Refusal('`%s` is `state: %s`, and `pull` takes an open record. %s first, in its own '
                      'step' % (rid, state, 'Unblock it' if state == 'blocked' else 'Open it'))
    if rec.has('review'):
        raise Refusal('`%s` is marked for review (review: %s); a coordinator clears it with '
                      '`clear-review %s --reason T` before anyone pulls it'
                      % (rid, rec.get('review'), rid))
    if rec.get('priority') == rmod.RETIRED_PRIORITY:
        # a leftover owner hold: pricing it would lift the hold, so name the hold, not a price
        raise Refusal('`%s` is `priority: -1`, a retired owner hold, never pulled or priced away: '
                      'the owner or the PM moves it to an owner hold, %s'
                      % (rid, rmod.OWNER_HOLD_FORM % {'hub': kitroot.command('hub'), 'id': rid}))
    if not rmod.is_band(rec.get('priority')):
        raise Refusal('`%s` is `priority: %s`, and `pull` takes a priced record (`1`-`5`). Price it '
                      'first: `set %s --priority N --priority-by agent`'
                      % (rid, rec.get('priority'), rid))
    if blocker is not None:
        other = snap.get(blocker)
        if other is None or 'item:%s' % rid not in other.all('blocked_on'):
            raise Usage('`--blocker %s` is not a record blocked on `item:%s`' % (blocker, rid))
    missing = ctx.unmet(snap)(rec)
    if missing:
        raise Refusal('`%s` requires %s, which is unmet: %s' % ((rid,) + missing[0]))
    branch = ctx.current_branch()
    seeds = narrow.batch_members(snap, branch) if batch and held == branch else ()
    whys = {}
    diffs = []
    offers, passed, holding, also, offer_notes = narrow.current_offer(snap, ctx, branch, seeds,
                                                                      whys, diffs)
    offered = rec in [o.rec for o in offers] or rec in also
    split = next((o.split for o in offers if o.rec is rec), None)
    scopes, others = rec.all('scope'), holding
    if split is not None:
        # what the pull takes is what the narrowing leaves it, clear of each live pull it
        # narrowed against by construction
        against = [p.other for p in split.parts]
        scopes, others = split.keep, [r for r in holding if not any(r is o for o in against)]
    shared = shared_of(ctx)
    hits = narrow.live_hits(scopes, others, skip=rid, branch=branch, shared=shared)
    shares = narrow.live_shares(scopes, others, skip=rid, branch=branch, shared=shared)
    full = narrow.resource_hits(rec.all('uses'), holding, ctx.capacity(snap), skip=rid,
                                branch=branch)
    folds, notes = narrow._pull_fold_hits(ctx, scopes, holding, branch, diffs)
    # The fold refusal first: `next` passes a folding record over, so "not in the offer" would
    # hide the reason and the override that this one names.
    override = '%s, with `pull %s --reason "<why>"`' % (policy.row_text('pull-collision'), rid)
    unnarrowed = ('. No narrowing clears it (if `next` showed one, that narrowing no longer '
                  'holds): %s' % whys[rid] if rid in whys and split is None else '')
    if folds and (not may_collide or reason is None):
        raise Refusal(narrow._fold_message(rid, folds, override) + unnarrowed)
    if not offered and reason is None:
        raise Refusal('`%s` is not in `next`\'s offer - %s. An out-of-order pull is a pre-emption, '
                      'the PM\'s decision: a coordinator asks the PM and, on its yes, runs '
                      '`pull %s --reason "<why>"` (recorded); a worker stops and reports%s'
                      % (rid, omod.not_offered_why(rec, passed, ctx.session), rid,
                         '. Its %s with a live pull, so it is %s'
                         % ('scope collides' if hits else 'resource collides', override)
                         if hits or full else '') + unnarrowed)
    if split is not None and split.narrows_other() and not may_collide:
        hit = [p.other for p in split.parts if p.l_changes]
        raise Refusal('`%s` is offered by narrowing the live pull%s %s (NARROWS), and '
                      'writing the narrowing of %s is %s. A worker stops and reports; a lead '
                      'runs `pull %s`' % (rid, '' if len(hit) == 1 else 's',
                                          ', '.join('%s on %s' % (o.id, o.get('pulled_by'))
                                                    for o in hit),
                                          ', '.join(o.id for o in hit),
                                          policy.row_text('pull-collision'), rid))
    if (hits or full) and (not may_collide or reason is None):
        raise Refusal('`%s` collides with a live pull: %s. `--reason` alone never overrides it: '
                      'pulling it anyway is %s' % (rid, '; '.join(hits + full), override))
    lines = offer_notes + notes + ['PULLED %s - state: open -> pulled' % rid, '  pulled_at:  %s' % ctx.date(),
                     '  pulled_by:  %s' % branch] + ['  ' + s for s in shares]
    resume = resume_line(rec.body)
    if resume:
        lines.append(resume)
    today = ctx.date()
    changes = [('state', ['pulled']), ('pulled_at', [today]), ('pulled_by', [branch]),
               ('pulled_session', pull_session(ctx))]
    subject = 'backlog: pull %s onto %s' % (rid, branch)
    if offered and not folds:
        changes += [(k, []) for k in rmod.PREEMPT_KEYS]
    else:
        recorded = reason
        if blocker is not None:
            recorded += ' (unblocks %s)' % blocker
        lines += narrow._collisions(rec, holding, shared)
        lines += ['warning: %s' % f for f in full + folds]
        offers = [o for o in offers if o.rec is not rec]
        displaced = ['%s rank %s priority %s' % (o.rec.id, o.rank, o.rec.get('priority'))
                     for o in offers] or ['-']
        changes += [('preempt', [recorded]), ('preempt_by', [branch]), ('preempt_at', [today]),
                    ('displaced', displaced)]
        lines.append('  preempt:    %s' % recorded)
        lines += ['  displaced:  %s' % d for d in displaced]
        subject += ', pre-empting %d offered item%s' % (len(offers), '' if len(offers) == 1 else 's')
    writes = {}
    if split is not None:
        more, subject = _apply_split(split, rec, branch, changes, writes, subject)
        lines += more
    pairs = rmod.apply_changes(rec.pairs(), changes)
    writes[rid] = _text(pairs, rec.body)
    change = Change(writes, subject, lines)
    if hits or full or folds or (split is not None and split.narrows_other()):
        narrow._collision_change(change, 'pull')
    return change


def _apply_split(split, rec, branch, changes, writes, subject):
    """The narrowing `pull` of `rec` onto `branch` writes (an offer's Split, omod.split_all):
    `rec` takes `split.keep` and a `narrowed:` line per (member it gave up, live pull it
    collided with) (added to `changes`); each live pull of `split.parts` that gives up a member
    too (it has a `## Files`) takes its `other_keep` and its own lines, in `writes`, so every
    record goes in one store write. A record narrowed again keeps one line per (member, other
    id) (narrowed.merge_lines). `(lines to print, subject)`."""
    def said(rid, whom, side):
        return 'NARROWED %s for %s: %s' % (rid, whom, omod.side_text(side))
    lines = []
    new = [(m, p.other.id, p.other.get('pulled_by')) for p in split.parts for m in p.c_members]
    changes.append(('scope', list(split.keep)))
    if new:
        changes.append(('narrowed', nmod.merge_lines(rec.all('narrowed'), new)))
        lines.append(said(rec.id, ', '.join('%s on %s' % (p.other.id, p.other.get('pulled_by'))
                                             for p in split.parts if p.c_members),
                          split.c_changes))
    lines += ['  scope:      %s' % m for m in split.keep]
    lines += ['  narrowed:   %s %s %s' % n for n in new]
    narrowed = []
    for p in split.parts:
        other = p.other
        if not p.l_changes:
            lines.append('%s %s' % (other.id, omod.side_text([], p.plan)))
            continue
        new = [(m, rec.id, branch) for m, _d, _k in p.l_changes]
        pairs = rmod.apply_changes(other.pairs(), [
            ('scope', list(p.other_keep)),
            ('narrowed', nmod.merge_lines(other.all('narrowed'), new))])
        writes[other.stem] = _text(pairs, other.body)
        lines.append(said(other.id, '%s on %s' % (rec.id, branch), p.l_changes))
        lines += ['  scope:      %s' % m for m in p.other_keep]
        lines += ['  narrowed:   %s %s %s' % n for n in new]
        narrowed.append(other.id)
    return lines, subject + (', narrowing %s' % ', '.join(narrowed) if narrowed else '')


def _plan_takeover(snap, ctx, rec, reason, may_collide, blocker):
    """A pull of a pulled record. It refuses `already in flight`, unless the record's `session:` is
    the caller's session and its pull is dead by `next`'s test (omod.in_flight_split: git
    answered and no branch, remote-tracking branch or worktree has its `pulled_by`); then the
    pull moves to this branch, `pulled_at` restamped, any pre-emption lines kept as the first pull
    wrote them, with the scope-collision warnings a pre-empting pull prints. A takeover whose scope
    collides with a live pull on another branch, or folds into one's diff (`_pull_fold_hits`),
    is the `pull-collision` row, as a pull is: refused unless `may_collide`, and the Change names
    the row (_collision_change). It takes no flag. A dead pull marked for review refuses, as `pull` of a marked record does,
    until `clear-review`. A failed git probe
    holds the pull (fail closed). A `pulled_by` that landed on the code main (a `Merge <branch>`
    merge on its first-parent line, committed on or after the `pulled_at` day; one brought in
    inside another branch does not count) is not dead but unclosed:
    that refuses, and a git that cannot say fails."""
    rid, old = rec.id, rec.get('pulled_by')
    busy = Refusal('`%s` is already in flight - pulled %s by %s'
                   % (rid, rec.get('pulled_at'), old))
    if not ctx.session or rec.get('session') != ctx.session:
        raise busy
    holding, dead, _why = ctx.in_flight_split(snap)
    if rec not in dead:
        raise busy
    if rec.has('review'):
        raise Refusal('`%s` is a dead pull of this session, and it is marked for review (review: '
                      '%s); a coordinator clears it with `clear-review %s --reason T` before '
                      'anyone takes it over' % (rid, rec.get('review'), rid))
    hits = narrow.live_hits(rec.all('scope'), holding, skip=rid, branch=ctx.current_branch(),
                            shared=shared_of(ctx))
    full = narrow.resource_hits(rec.all('uses'), holding, ctx.capacity(snap), skip=rid,
                                branch=ctx.current_branch())
    folds, notes = narrow._pull_fold_hits(ctx, rec.all('scope'), holding, ctx.current_branch())
    given = [f for f, v in (('--reason', reason), ('--blocker', blocker)) if v is not None]
    if given:
        raise Usage('`%s` is a dead pull of this session (pulled_by %s has no branch): `pull %s` '
                    'takes it over as it stands, with no %s' % (rid, old, rid, ', '.join(given)))
    base, sha = narrow._code_main(ctx)
    if not sha:
        raise Failure('the code main `%s` does not resolve here, so whether %s landed cannot be '
                      'told: set `backlog.code_main` in .kit.json to the branch code lands on'
                      % (base, old))
    merge = narrow.landing_merge(ctx.root, ctx.code_main, old, since=rec.get('pulled_at'), rev=sha)
    if merge:
        raise Refusal('`%s` was pulled by %s, which has landed on %s (%s): close the record, do '
                      'not take it over' % (rid, old, ctx.code_main, merge))
    override = '%s, with `pull %s`' % (policy.row_text('pull-collision'), rid)
    if (hits or full) and not may_collide:
        raise Refusal('`%s` is a dead pull of this session, and its %s with a live pull: %s. '
                      'Taking it over anyway is %s'
                      % (rid, 'scope collides' if hits else 'resource collides',
                         '; '.join(hits + full), override))
    if folds and not may_collide:
        raise Refusal(narrow._fold_message(rid, folds, override))
    branch = ctx.current_branch()
    today = ctx.date()
    pairs = rmod.apply_changes(rec.pairs(), [('pulled_at', [today]), ('pulled_by', [branch]),
                                             ('pulled_session', pull_session(ctx))])
    lines = notes + ['PULLED %s - took over a dead pull from %s (it has no branch)' % (rid, old),
                     '  pulled_at:  %s -> %s' % (rec.get('pulled_at'), today),
                     '  pulled_by:  %s -> %s' % (old, branch)]
    resume = resume_line(rec.body)
    if resume:
        lines.append(resume)
    lines += narrow._collisions(rec, holding, shared_of(ctx))
    lines += ['warning: %s' % f for f in full + folds]
    change = Change({rid: _text(pairs, rec.body)},
                    'backlog: pull %s onto %s, taking over a dead pull from %s' % (rid, branch, old),
                    lines)
    if hits or full or folds:
        narrow._collision_change(change, 'pull')
    return change


def holder_form(rec, holder, verb):
    """How a live pull's holder stops it, for stop_pull's refusals: from the branch's worktree, or
    from any checkout with `--as <holder>` (a coordinator that pulled with `--as` is the holder;
    a worker on that branch did not pull it). The record's `session:` is named as `--session S`
    when it has one, as pull_branch.refusal does."""
    session = rec.get('session')
    return ('from that branch, or as that branch from any checkout (backlog.py -C <store> '
            '%s--as %s %s)' % ('--session %s ' % session if session else '', holder, verb))


def pull_session(ctx):
    """The `pulled_session` value a pull writes: `[ctx.session]`, or `[]` (none, any old one
    dropped) when no `--session` names the caller or its key is not a session name."""
    key = ctx.session
    return [key] if key and rmod.SESSION_RE.match(key) else []


def quiet_key_why(ctx, rec):
    """Why the caller's session may not release `rec`'s pull as quiet, or None when it may: the
    caller's `--session` is the pull's `pulled_session`, or the two keys have one stem in the
    launcher's sessions.json (omod.launcher_stems: a successor session). A sessions.json that
    cannot be read allows the exact key only."""
    pulled, mine = rec.get('pulled_session'), ctx.session
    if not pulled:
        return 'the pull records no `pulled_session`'
    if not mine:
        return 'no `--session` names the caller (the pull is %s\'s)' % pulled
    if mine == pulled:
        return None
    try:
        stems = omod.launcher_stems()
    except Exception as exc:            # noqa: BLE001 - unread is "not the same stem", never a raise
        return ('session %s is not %s, and sessions.json cannot be read to match their stems (%s)'
                % (mine, pulled, ' '.join(str(exc).split()) or type(exc).__name__))
    a, b = (stems or {}).get(mine), (stems or {}).get(pulled)
    if isinstance(a, str) and a and a == b:
        return None
    return ('session %s is neither %s nor a successor of it (sessions.json stems: %s, %s)'
            % (mine, pulled, a or 'none', b or 'none'))


def _main_commit(ctx):
    """The code main's commit for the quiet probe's verdict check, None when git cannot read it
    (then every queued verdict holds the pull)."""
    try:
        return narrow._code_main(ctx)[1] or None
    except Failure:
        return None


def _quiet_or_refuse(quiet, refusal):
    """The ` (<note>)` stop_pull prints for a quiet release, or Refusal(refusal) when there is no
    `quiet` probe or it says no."""
    if quiet is None:
        raise Refusal(refusal)
    note, why = quiet()
    if note is None:
        raise Refusal('%s. Not a quiet release either: %s' % (refusal.rstrip('.'), why))
    return ' (%s)' % note


def stop_pull(ctx, rid, holder, live, kept=True, quiet=None):
    """Whether the caller may stop `rid`'s pull by `holder` (block or release it): its holder (the
    caller's branch) may; anybody may when the pull is dead (no local branch, remote-tracking
    branch or worktree by that name: offer.live_branches), or when `ctx.liveness` finds the
    holder's worktree dead. Refusal(live(why)) otherwise, `why` None when there is no probe;
    with `quiet` (release only: `quiet() -> (note, None) or (None, why not)`), a live pull it
    allows is stopped too, and its note goes on the `was pulled_by` line.
    Returns `(stopped, lines)`: `stopped` is the body line `Stopped on branch B at <tip>, branch
    kept.` with its newline (its own line, so the `Blocked on` line after it still starts one:
    record.hold_citations), '' when there is no local branch B; `lines` say what was found.
    `kept` False (a landing's release, whose cleanup deletes the branch) reads no branch and
    gives ''."""
    mine = ctx.branch or refs.current_branch(ctx.root)[0]
    if holder == mine:
        how = ''
    else:
        bad = branch_name_error(holder, ctx.root)
        if bad:
            raise Refusal('%s, so what holds the pull of `%s` cannot be checked' % (bad, rid))
        names, why = omod.live_branches(ctx.root, ctx.remote)
        if names is None:
            raise Failure('cannot tell whether the pull of `%s` by `%s` is dead: %s'
                          % (rid, holder, why))
        if holder not in names:
            how = ' (dead)'
        elif ctx.liveness is None:
            how = _quiet_or_refuse(quiet, live(None))
        else:
            why, _tip = ctx.liveness(holder)
            how = (_quiet_or_refuse(quiet, live(why)) if why is not None else
                   ' (its worktree is dead)')
    lines = ['  was pulled_by: %s%s' % (holder, how)]
    if not kept:
        return '', lines
    tip, why = refs.ref_commit('refs/heads/%s' % holder, ctx.root)
    if tip is None:
        raise Failure('cannot read branch `%s` for the body line: %s' % (holder, why))
    if not tip:
        return '', lines
    lines.append('  branch kept: %s at %s' % (holder, tip[:10]))
    return STOPPED_NOTE % (holder, tip[:10]) + '\n', lines


# The body line stop_pull writes, and the pattern resume_line reads it back with, made from it.
STOPPED_NOTE = 'Stopped on branch %s at %s, branch kept.'
STOPPED_RE = re.compile('^%s$' % r'(\S+)'.join(re.escape(p) for p in STOPPED_NOTE.split('%s')),
                        re.M)


def resume_line(body):
    """The line `pull` prints for a record whose body carries stop_pull's note (STOPPED_NOTE),
    naming the branch and tip the work stopped on (the latest note when there are several); None
    when there is none."""
    found = STOPPED_RE.findall(body or '')
    if not found:
        return None
    branch, tip = found[-1]
    return '  resume on %s? Work stopped there at %s, branch kept' % (branch, tip)


# plan_block's `Blocked on M (date): T` line, and the lines that end a stopped pull's wait.
BLOCKED_NOTE_RE = re.compile(r'Blocked on .+? \((\d{4}-\d{2}-\d{2})\): ')
_STOP_ENDED = ('Unblocked (', 'Released (')


def blocked_pull(body):
    """`(branch, tip)` of the pull a `block` stopped and nothing has ended since: the latest
    STOPPED_NOTE is followed straight by plan_block's `Blocked on` line, and no
    `Unblocked (` or `Released (` line comes after it. None otherwise."""
    found = list(STOPPED_RE.finditer(body or ''))
    if not found:
        return None
    rest = body[found[-1].end():].split('\n')[1:]
    blocked = BLOCKED_NOTE_RE.match(rest[0]) if rest else None
    if not blocked or any(line.startswith(_STOP_ENDED) for line in rest[1:]):
        return None
    return found[-1].group(1), found[-1].group(2)


def _git_answer(got, what):
    """`got`'s value, `(value, why)` from a kitlib.git.refs probe; Failure when git cannot tell."""
    value, why = got
    if value is None:
        raise Failure('cannot tell %s: %s' % (what, why))
    return value


def _restore_why_not(snap, ctx, rec, branch, tip):
    """Why the pull `block` stopped on `branch` at `tip` does not come back, None when it does.
    Failure when git cannot tell any of it."""
    what = 'whether the pull of `%s` by %s comes back' % (rec.id, branch)
    names = _git_answer(ctx.live_branches(snap), what)
    if branch not in names:
        return 'no branch or worktree by that name'
    full = _git_answer(refs.resolve(tip, ctx.root, known=True), what)
    if not full:
        return 'its stopped tip %s is gone' % tip
    head = ''
    for ref in ('refs/heads/%s' % branch, 'refs/remotes/%s/%s' % (ctx.remote, branch)):
        head = _git_answer(refs.ref_commit(ref, ctx.root), what)
        if head:
            break
    if not head or not _git_answer(refs.is_ancestor(full, head, ctx.root), what):
        return '%s no longer holds its stopped tip %s' % (branch, tip)
    _base, main = narrow._code_main(ctx)
    # Landed: in main, off its first-parent chain (a landing's --no-ff merge). A branch with no
    # commits of its own stopped at a main commit, on that chain, and comes back.
    if main and _git_answer(refs.is_ancestor(full, main, ctx.root), what) \
            and not _git_answer(refs.on_first_parent(full, main, ctx.root), what):
        return 'its stopped tip %s has landed on %s' % (tip, ctx.code_main)
    holding, _dead, _why = ctx.in_flight_split(snap)
    lead = '; a lead may re-pull it (the pull-collision row)'
    hits = narrow.live_hits(rec.all('scope'), holding, skip=rec.id, branch=branch,
                            shared=shared_of(ctx))
    if hits:
        return hits[0] + lead
    folds, notes = narrow._pull_fold_hits(ctx, rec.all('scope'), holding, branch)
    if folds:
        return 'scope folds into %s (%s)%s' % (narrow._fold_where(folds), folds[0], lead)
    # A branch the fold check could not compare is "cannot tell", never "no overlap" (as
    # _branch_changes refuses to measure it): the record opens for a lead to look at.
    unmeasured = [n for n in notes if 'not compared' in n]
    if unmeasured:
        return ('cannot tell whether its scope folds into another pull\'s work (%s)%s'
                % (unmeasured[0][len('NOTE: '):] if unmeasured[0].startswith('NOTE: ')
                   else unmeasured[0], lead))
    return None


def restore_pull(snap, ctx, rec):
    """What opening the blocked `rec` does with the pull its `block` stopped (blocked_pull):
    `(changes, lines, note)`, all empty when there is none. The pull comes back (`state:
    pulled`, `pulled_by` the branch, `pulled_at` today) only while the branch is live by `next`'s
    test (offer.live_branches: a local, remote-tracking or worktree branch by that name), still
    holds the stopped tip, which has not landed on the code main (merged into it, off its
    first-parent chain: a branch with no commits of its own stops on that chain), and its scope
    neither collides with another live pull nor folds
    into one's work (those are a lead's call, the pull-collision row). Otherwise the record opens
    as before. `note` is the body line that says which, and ends the stopped pull's wait. A git
    that cannot say fails (_restore_why_not)."""
    found = blocked_pull(rec.body)
    if found is None:
        return [], [], ''
    branch, tip = found
    why = _restore_why_not(snap, ctx, rec, branch, tip)
    if why:
        return [], ['  pull by %s not restored: %s' % (branch, why)], \
            'Unblocked (%s): pull by %s not restored: %s.' % (ctx.date(), branch, why)
    changes = [('state', ['pulled']), ('pulled_at', [ctx.date()]), ('pulled_by', [branch])]
    lines = ['  pull restored - state: pulled, pulled_by: %s, pulled_at: %s'
             % (branch, ctx.date())]
    return changes, lines, 'Unblocked (%s): pull by %s restored.' % (ctx.date(), branch)


def stale_stop_note(rec, day, why):
    """The body line that ends `rec`'s stopped pull (blocked_pull) when something other than an
    unblock opens it, None when it has none: `why` says what opened it. It never restores."""
    found = blocked_pull(rec.body)
    if found is None:
        return None
    return 'Unblocked (%s): %s; pull by %s not restored.' % (day, why, found[0])


def plan_release(snap, ctx, rid, reason, kept=True):
    """Return a pull to `state: open`: the caller's own pull, or one anybody may stop (stop_pull:
    no branch or worktree by its `pulled_by`, or a worktree `ctx.liveness` finds dead). Pre-emption
    lines go with it, or the released record would keep its pre-emption sort. While the branch
    exists a body line `Stopped on branch B at <tip>, branch kept.` and then `Released (date): T`
    say where the work was left; the branch is never touched. `kept` False (at_landing's release
    of a part landed: the landing's cleanup deletes the branch) writes neither line and reads no
    branch. A live pull of another branch is released too when it is quiet: the caller's
    `--session` is its `pulled_session` or a successor's (quiet_key_why) and `ctx.quiet` (None:
    no probe, so the plain refusal) finds it quiet; `(holder quiet <h> h, released by <key>)`
    then follows the reason in the `Released` line and the subject."""
    refuse_machine_paths('--reason', reason)
    bad = reason_error(reason, '--reason')
    if bad:
        raise Usage(bad)
    rec = require(snap, rid)
    if rec.get('state') != 'pulled':
        raise Usage('`%s` is `state: %s`, not `pulled`: there is no pull to release'
                    % (rid, rec.get('state')))
    holder = rec.get('pulled_by')
    if not holder:
        raise Failure('`%s` is pulled with no `pulled_by`; `check` names it' % rid)
    def live(why):
        return ('the pull of `%s` by `%s` is live (a branch or worktree by that name exists)%s. '
                'Its holder releases it: %s'
                % (rid, holder, '; its tree is live: %s' % why if why else '',
                   holder_form(rec, holder, 'release %s --reason "<why>"' % rid)))
    notes = []

    def quiet():
        why = quiet_key_why(ctx, rec)
        if why is None:
            why, quiet_s = ctx.quiet(holder, _main_commit(ctx))
        if why is not None:
            return None, why
        notes.append('holder quiet %d h, released by %s' % (quiet_s // 3600, ctx.session))
        return notes[-1], None
    # A pull from before `pulled_session` (or from a caller with no --session) names no key a
    # quiet release could be checked against, and a context with no quiet probe cannot tell:
    # either refusal stays the plain one.
    stopped, found = stop_pull(ctx, rid, holder, live, kept,
                               quiet=quiet if rec.get('pulled_session') and ctx.quiet else None)
    changes = [('state', ['open'])] + [(k, []) for k in rmod.PULL_CLEARS + rmod.PREEMPT_KEYS]
    pairs = rmod.apply_changes(rec.pairs(), changes)
    text = ' '.join(reason.split()) + ''.join(' (%s)' % n for n in notes)
    body = rec.body
    if stopped:
        body = (body or '').rstrip('\n')
        body = ((body + '\n\n' if body.strip() else '') + '%sReleased (%s): %s\n'
                % (stopped, ctx.date(), text))
    return Change({rid: _text(pairs, body)},
                  'backlog: release %s from %s: %s' % (rid, holder, text),
                  ['RELEASED %s - state: pulled -> open' % rid,
                   '  was pulled_at: %s' % rec.get('pulled_at')] + found)
