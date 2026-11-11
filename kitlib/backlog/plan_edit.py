"""The plans that write a record's fields: `add`, `set` and `clear-review` (kitlib/backlog/plan.py
has the shared Context and checks), and the keys only a verb writes (WRITTEN_BY), which `set`
refuses and the Action Hub shows.
"""
import re

from kitlib import kitroot

from . import offer as omod
from . import plan_narrow as narrow
from . import policy
from . import record as rmod
from . import scope as smod
from .errors import Failure, Refusal, Usage
from .plan import (_text, branch_name_error, reason_error, refuse_machine_paths, refuse_top,
                   require, shared_of)
from .store import Change, id_refusal

RULED_PRICE_BY = ('owner', 'pm')
ADD_BODY = ('## Why this is here\n\nRecorded by `backlog.py add`. Unprioritised on purpose: '
            'prioritising it is a separate act by somebody not mid-discovery.\n')
# The longest `--summary` the CLI's `add` takes: the summary is the record's title, one line a
# list shows whole; the detail goes in the body (`--body-file`). The Action Hub's add is not held
# to it (opener.planner passes it; recordwrite.add does not).
SUMMARY_MAX = 120
# Keys only a verb writes, each with the verb that does.
WRITTEN_BY = {
    'id': 'nothing - the filename is the id; add the record under its new id and retire this one',
    'displaced': '`pull`, which computes it from the offer',
    'preempt': '`pull --reason`', 'preempt_by': '`pull --reason`', 'preempt_at': '`pull --reason`',
    'pulled_at': '`pull`', 'pulled_by': '`pull`',
    'pulled_session': '`pull` (the caller\'s `--session`)',
    'q': '`set-status parked [--q Q-nnn]`', 'parked_at': '`set-status parked [--q Q-nnn]`',
    'review': '`add` from the Action Hub (the library\'s `review=`); a coordinator clears it with '
              '`clear-review ID --reason T`',
    'answer': 'the Action Hub, filing the owner\'s answer to a question bound to the record '
              '(the library\'s `plan_answer`)',
    'until': '`block ID --on M --until "<condition>"`; `unblock --on M` drops it',
}
# Keys no verb writes any more: an owner gate is a role (kitlib/backlog/roles.py), not a cited
# id. A record that carries one keeps it until `set ID --unset K` clears it (a price move drops
# `priority_ruling`, which named the old price; `uncite` drops `priority_ruling` and
# `hold_ruling`; `priority_per` is kept). In WRITTEN_BY too, so the Action Hub shows them as no
# edit's to make.
UNWRITTEN = ('priority_ruling', 'hold_ruling', 'resume', 'priority_per')
WRITTEN_BY.update((k, 'no verb: an owner gate is a role now (kitlib/backlog/roles.py); '
                      '`set ID --unset %s` clears it' % k)
                  for k in UNWRITTEN)


def read_body_file(path):
    """The body `--body-file` would write: UTF-8, a BOM dropped, never empty."""
    try:
        with open(path, 'r', encoding='utf-8-sig') as fh:
            text = fh.read()
    except OSError as exc:
        raise Usage('cannot read `--body-file %s`: %s' % (path, exc))
    except UnicodeDecodeError as exc:
        raise Usage('`--body-file %s` is not UTF-8: %s' % (path, exc))
    if not text.strip():
        raise Usage('`--body-file %s` is empty; an empty body is refused rather than written' % path)
    return text.replace('\r\n', '\n')


# --- add ------------------------------------------------------------------------------------------

def _checked_uses(ctx, names):
    """`names`, each once, or Usage: every one must be a resource name `.kit.json` declares (a
    write may add no `check` error, and fail_findings reads no config, so the plan refuses it)."""
    for name in names:
        bad = rmod._uses(name)
        if bad:
            raise Usage('bad `--uses %s`: %s; `set ID --uses -` clears the list' % (name, bad))
    names = list(dict.fromkeys(names))
    unknown = [n for n in names if n not in ctx.resources]
    if unknown:
        raise Usage('`--uses %s`: .kit.json "backlog".resources does not declare it (declared: %s)'
                    % (', '.join(unknown), ', '.join(sorted(ctx.resources)) or 'none'))
    return names


def plan_add(snap, ctx, rid, summary, scopes, effort=None, ref=None, reopen=False, session=None,
             found_on=None, why_not_folded=None, review=None, body=None, summary_max=None,
             uses=()):
    """One unprioritised record. `priority: -` always: pricing is a separate act. `uses` names
    the resources it needs (docs/backlog.md "Resources").

    `review` (`<source> <YYYY-MM-DD>`, the library's alone: the Action Hub passes it, the CLI has
    no flag) writes the review mark: `pull` refuses the record and `next` never offers it until a
    coordinator runs `clear-review` (plan_clear_review).

    The fold-in guard (docs/backlog.md, "The fold-in guard"): `found_on` is the branch the finding
    was made on, or `none`; `why_not_folded` says why it is not fixed there. A record whose scope
    overlaps that branch's diff from the code main (`ctx.code_main`), or the scope of a record the
    branch holds pulled, is still filed: `add` prints a WARNING and writes the overlap into the
    `## Found` section. `found_on` and `why_not_folded` go into that `## Found` body section, and
    the branch into the commit subject; the summary stays the title.

    `body` (`--body-file`) opens the body, before the `## Found` and ADD_BODY sections.
    `summary_max`: a longer summary is a usage error naming `--body-file` (the CLI passes
    SUMMARY_MAX; None, the Action Hub's add, takes any length)."""
    why = snap.rules.id_error(rid)
    if why is not None:
        raise Usage('cannot add: %s' % why)
    why = id_refusal(snap, rid)
    if why is not None:
        raise Refusal('cannot add: %s' % why)
    if session is not None and rmod._session(session) is not None:
        raise Usage('bad `--session %s`: %s' % (session, rmod._session(session)))
    if not (summary or '').strip():
        raise Usage('`add` requires a non-blank `--summary`')
    refuse_machine_paths('--summary', summary)
    if summary_max is not None and len(summary.strip()) > summary_max:
        raise Usage('`--summary` is %d characters; a summary is the record\'s title, at most %d: '
                    'keep it to one short line and put the detail in the body (`--body-file F`)'
                    % (len(summary.strip()), summary_max))
    refuse_machine_paths('--ref', ref)
    refuse_machine_paths('--body-file', body)
    found_on, not_folded = _fold_arguments(ctx, found_on, why_not_folded)
    if not scopes:
        raise Usage('`add` requires at least one `--scope`; `none` (no tracked files) is written out')
    for s in scopes:
        bad = smod.scope_member_error(s)
        if bad:
            raise Usage('bad `--scope %s`: %s' % (s, bad))
    uses = _checked_uses(ctx, uses)
    if effort is not None and rmod._effort(effort) is not None:
        raise Usage('bad `--effort %s`: %s' % (effort, rmod._effort(effort)))
    if review is not None and rmod._review(review) is not None:
        raise Usage('bad review mark `%s`: %s' % (review, rmod._review(review)))
    prior = ctx.store.history(rid, snap)
    if prior and not reopen:
        raise Refusal('`%s` was already closed, retired or deleted: %s. Re-adding it under the '
                      'same id makes that history read as though it were about this item: pass '
                      '`--reopen` if it is the same work coming back, or pick a new id'
                      % (rid, prior))
    notes, hits = ([], []) if found_on == smod.NONE else _fold_overlap(snap, ctx, rid, found_on,
                                                                       scopes)
    where = 'no branch (or on a branch since landed)' if found_on == smod.NONE else found_on
    found = 'Found on %s; not folded: %s\n' % (where, not_folded)
    if hits:
        notes.append('WARNING: scope overlaps the work of %s (filed anyway; fold it there if it '
                     'belongs there):\n  %s' % (found_on, '\n  '.join(hits)))
        found += '\nScope overlaps the work of %s when filed:\n  %s\n' % (found_on, '\n  '.join(hits))
    sections = [body.rstrip('\n') + '\n'] if (body or '').strip() else []
    sections += ['## Found\n\n' + found, ADD_BODY]
    summary = summary.strip()
    pairs = [('id', rid), ('area', smod.area_from_scope(scopes)), ('priority', '-'),
             ('priority_by', '-'), ('effort', effort or '-'), ('state', 'open'),
             ('blocked_on', '-')]
    if review is not None:
        pairs.append(('review', review))
    pairs += [('scope', s) for s in scopes]
    pairs += [('uses', n) for n in uses]
    if session is not None:
        pairs.append(('session', session))
    if ref:
        pairs.append(('ref', ref))
    pairs.append(('summary', summary))
    named_broad = broad_scope_warning(scopes, summary, body)
    seen = named_broad[0] if named_broad else ''
    shape = smod.broad_scope_warning([m for m in scopes if '`%s`' % m not in seen])
    lines = notes + named_broad + shape + ['ADDED %s, found on %s' % (rid, where)]
    lines += ['  %-11s %s' % (k + ':', v) for k, v in pairs]
    lines.append('Unprioritised: not in `next` until somebody prices it (`list --needs-priority`).')
    if review is not None:
        lines.append('Marked for review: not pullable until a coordinator runs `clear-review %s '
                     '--reason T`.' % rid)
    lines.append('  body:       %s`## Found`, `## Why this is here`'
                 % ('your `--body-file`, ' if len(sections) == 3 else ''))
    return Change({rid: _text(pairs, '\n'.join(sections))},
                  'backlog: add %s, found on %s' % (rid, where),
                  lines)


NAMED_FILE = re.compile(r'(?<![\w./-])[\w.-]+(?:/[\w.-]+)*/[\w-]+\.[\w-]+(?![\w/-])')


def broad_scope_warning(scopes, summary, body=None):
    """`[one WARNING: line]` for the members of `scopes` that are broader than the change needs,
    else []. `add` prints it and still adds. A member is flagged when it is a one-component
    top-level `<top>/**`, or is broad (`omod.is_broad`) and the summary or body names a file path
    under it (the line suggests those paths)."""
    named = []
    for m in NAMED_FILE.findall((summary or '') + '\n' + (body or '')):
        if m not in named and smod.path_shape_error(m) is None:
            named.append(m)
    parts = []
    for m in scopes:
        if m == smod.NONE or not omod.is_broad(m):
            continue
        under = [p for p in named if m.endswith(smod.GLOB_SUFFIX) and smod.members_collide(m, p)]
        top = m.endswith(smod.GLOB_SUFFIX) and '/' not in m[:-len(smod.GLOB_SUFFIX)]
        if under:
            parts.append('`%s` is broad and the text names %s: scope those paths instead'
                         % (m, ', '.join('`%s`' % p for p in under[:5])))
        elif top:
            parts.append('`%s` is a whole top-level folder: scope what the change touches' % m)
    return ['WARNING: ' + '; '.join(parts)] if parts else []


def _fold_arguments(ctx, found_on, why_not_folded):
    """`(found_on, the reason with its whitespace collapsed)`, or Usage. No git but the
    branch-name check."""
    if found_on is None:
        raise Usage('`add` requires `--found-on BRANCH|none`: the branch the finding was made on, '
                    'or `none` when it was found on no branch, or on a branch that landed and was removed')
    if why_not_folded is None:
        raise Usage('`add` requires `--why-not-folded TEXT`: why the finding is not fixed on the '
                    'branch that found it')
    text = ' '.join(why_not_folded.split())
    bad = reason_error(text, '--why-not-folded')
    if bad:
        raise Usage(bad)
    refuse_machine_paths('--why-not-folded', text)
    if found_on != smod.NONE:
        bad = branch_name_error(found_on, ctx.root)
        if bad:
            raise Usage('bad `--found-on`: %s; write a branch, or `none`' % bad)
        bare = _bare_branch(ctx, found_on)
        if bare.lower() == ctx.code_main.lower():
            raise Usage('`--found-on %s` names the code main: a finding on main has no branch: '
                        'write none' % found_on)
        if bare != found_on:
            raise Usage('`--found-on %s` is a ref, not a branch name: write the local branch '
                        'name `%s`' % (found_on, bare))
    return found_on, text


def _bare_branch(ctx, name):
    """`name` without a `refs/heads/`, `heads/`, `refs/remotes/<remote>/`, `remotes/<remote>/` or
    `<remote>/` prefix, matched in any letter case: the local branch a ref spelling means."""
    for prefix in ('refs/heads/', 'heads/', 'refs/remotes/%s/' % ctx.remote,
                   'remotes/%s/' % ctx.remote, '%s/' % ctx.remote):
        if name.lower().startswith(prefix.lower()) and len(name) > len(prefix):
            return name[len(prefix):]
    return name


def _fold_overlap(snap, ctx, rid, branch, scopes):
    """The fold-in guard's git probe: the overlap lines `hits` for each member of `scopes` that
    overlaps a path `branch` changed since its merge base with the code main, or the scope of a
    record `branch` holds pulled; a member that cannot be compared counts as an overlap. `add`
    warns on them and files the record. Refusal when
    `branch` is neither a local branch, nor a branch of any remote as last fetched, nor a branch
    that landed on the code main (a `Merge <branch>` merge on its first-parent chain,
    `landing_merge`); Failure, not that Refusal, when the code main does not resolve.
    Returns `(notes, hits)`; the NOTE lines: a branch only remotes have has the diff of each remote's tip
    compared; a landed branch has no diff to compare; the pulled scopes are checked either way.
    Every ref name is matched in any letter case, against git's own list of names
    (`_ref_names`), so a packed ref is found as a loose one is. Failure when git cannot answer
    or there is no base to measure from. `backlog.shared_paths` exempts nothing here: a finding
    in a shared file the branch changed still belongs to that branch (pull and next exempt it)."""
    notes, hits = [], []
    tips, note = narrow._branch_tips(ctx, branch, narrow._ref_names(ctx, branch))
    for label, tip in tips:
        hits += narrow._diff_hits(ctx, label, tip, scopes)
    if note:
        notes.append(note)
    if not tips:
        base, sha = narrow._code_main(ctx)
        if not sha:
            raise Failure('the code main `%s` does not resolve here, so whether %s landed cannot '
                          'be told: set `backlog.code_main` in .kit.json to the branch code lands '
                          'on' % (base, branch))
        merge = narrow.landing_merge(ctx.root, ctx.code_main, branch, rev=sha, any_case=True)
        if not merge:
            raise Refusal('cannot add %s: `--found-on %s` names a branch that exists neither '
                          'here nor on a remote (as last fetched): pass an existing branch, or '
                          '`none` when no branch found it\na branch that landed is found by its '
                          '`Merge %s` merge on %s; one removed without landing is written as '
                          '`none`, with the branch named in --why-not-folded'
                          % (rid, branch, branch, ctx.code_main))
        notes.append('NOTE: branch %s landed on %s as %s; its diff was not compared'
                     % (branch, ctx.code_main, merge))
    unread = [r.stem for r in snap.unreadable]
    if unread:
        raise Failure('cannot tell which records %s holds pulled: unreadable record(s): %s'
                      % (branch, ', '.join(unread)))
    for r in snap.readable:
        if r.get('state') == 'pulled' and (r.get('pulled_by') or '').lower() == branch.lower():
            hits += narrow._overlaps(scopes, r.all('scope'), '(scope of %s, pulled by %s)'
                                     % (r.stem, branch))
    return notes, hits


# --- set ------------------------------------------------------------------------------------------

WIDEN_ROW =policy.COLLISION_ROWS['set']


def plan_set(snap, ctx, rid, changes, unset=(), *, body=None, role_checked=False,
             may_collide=False):
    """Edit fields and/or the body. `changes` is `[(key, [values])]`.

    - Who may move an owner's or the PM's price, or clear the owner from `blocked_on`, is the
      role check's (kitlib/backlog/roles.py: set-price-owner, unblock-owner). `role_checked`
      says the caller ran it (opener for tools/backlog.py, the hub's owner routes); without it
      such a write refuses (refuse_top).
    - `--priority -1` is refused (record.RETIRED_PRIORITY): the owner hold is `blocked_on:
      owner`, written by `block --on owner`.
    - An owner-held record's priority changes only with `priority_by` owner or pm.
    - A price field moved drops the record's `priority_ruling` (it named the old price); so does
      a result of `priority_by: agent`.
    - No ruling id is taken: an owner gate is a role. Past `unset` every argument is a keyword,
      so a sixth positional (the old ruling slot) is a TypeError.
    - `state: pulled` is entered only by `pull` and left only by `release`; `state: parked` only
      by `set-status parked` and `unpark`.
    - `priority_ruling`, `hold_ruling`, `resume` and `priority_per` (UNWRITTEN) are never
      written; `unset` of
      one clears it. Clearing `resume` is the owner's and the PM's (policy.OWNER_CLEARS, the
      `set-price-owner` row), so without the role check it refuses (refuse_top).
    - On a pulled record, `scope` members no old member covers (widened_members) that collide
      with a live pull, or overlap a path another live pull's branch changed (the fold-in guard,
      `_pull_fold_hits`, the record's own branch aside), take the `set-widen-collision` row:
      refused unless `may_collide` (the caller's role may take it). Taken, the output names each
      collision and the Change names the row (_collision_change). Narrowing, and widening into
      free scope, are free. Each `--scope` value is checked first: a malformed member is USAGE,
      not a collision.
    - `scope` replaces the whole list; the output names each member it removed.
    - `uses` replaces the whole list too, `-` alone clearing it, which frees a pulled record's
      slot as narrowing frees scope. Every name must be declared. On a pulled record, a name it
      did not hold whose resource live pulls fill is the same `set-widen-collision` row.
    """
    for key, vals in changes:
        refuse_machine_paths('--' + key, *vals)
    refuse_machine_paths('--body-file', body)
    rec = require(snap, rid)
    for key in unset:
        if key not in rmod.FIELDS:
            raise Usage('`--unset %s` names no field' % key)
        if key in rmod.REQUIRED:
            raise Usage('`%s` is required in every record, so it cannot be unset' % key)
    if not changes and not unset and body is None:
        raise Usage('`set` was given nothing to change')
    for key, vals in list(changes) + [(k, None) for k in unset]:
        if key not in rmod.FIELDS:
            raise Usage('`%s` is not a field' % key)
        if key in UNWRITTEN:
            if vals is None:
                continue                # clearing one is the write left (the role check's row)
            raise Refusal('`%s` is no longer written: an owner gate is a role now '
                          '(kitlib/backlog/roles.py), not a cited id; `set %s --unset %s` '
                          'clears it' % (key, rid, key))
        if key in WRITTEN_BY:
            raise Refusal('`%s` is written only by %s' % (key, WRITTEN_BY[key]))
        if key in rmod.SINGLE_VALUED and vals is not None and len(vals) > 1:
            raise Usage('`%s` takes one value and was given %d' % (key, len(vals)))
    if rmod.RETIRED_PRIORITY in dict(changes).get('priority', []):
        raise Usage('`priority: -1` is retired: a record awaiting the owner is '
                    + rmod.OWNER_HOLD_FORM % {'hub': kitroot.command('hub'), 'id': rid})
    for s in dict(changes).get('scope', []):
        # Before the widening check, which counts a member it cannot compare as a collision.
        bad = smod.scope_member_error(s)
        if bad:
            raise Usage('bad `--scope %s`: %s; give one `--scope <path>` per path' % (s, bad))
    changes = [(k, ([] if v == ['-'] else _checked_uses(ctx, v)) if k == 'uses' else v)
               for k, v in changes]

    keys = [k for k, _ in changes]
    pairs = [(k, v) for k, v in rec.pairs() if k not in unset]
    pairs = rmod.apply_changes(pairs, changes)
    get = lambda k: rmod.values(pairs, k)  # noqa: E731
    was_pulled, now_pulled = rec.get('state') == 'pulled', get('state') == ['pulled']
    if was_pulled != now_pulled:
        raise Refusal('`state: pulled` is entered only by `pull` and left only by `release`, which '
                      'record who has it; `set` does neither')
    if (rec.get('state') == 'parked') != (get('state') == ['parked']):
        raise Refusal('`state: parked` is entered only by `set-status parked [--q Q-nnn]` and left '
                      'only by `unpark`, which record the wait and its end')
    if not role_checked:
        refuse_top(rid, 'set', policy.sort('set', policy.given_of_set(changes, unset, body), rec))
    if 'owner' in get('blocked_on') and ('blocked_on' in keys or 'owner_ask' in keys) and \
            ('owner' not in rec.all('blocked_on') or get('owner_ask') != rec.all('owner_ask')):
        # The same owner check as `block --on owner` (plan_block): a `set` that adds the owner
        # hold, or rewrites its ask, cites the hub ask that ends it.
        bad = rmod.owner_citation_error(rid, None, ' '.join(get('owner_ask')) or None,
                                        hub=kitroot.command('hub'))
        if bad:
            raise Usage(bad.replace('`block %s --on owner`' % rid,
                                    '`set %s` of an owner hold' % rid))

    new_by = dict(changes).get('priority_by', [None])[0]
    if 'priority' in keys and rmod.is_owner_held(rec) and new_by not in RULED_PRICE_BY:
        raise Refusal('`%s` is owner-held (%s), so its priority changes only with `--priority-by '
                      'owner|pm`' % (rid, rmod.owner_hold_label(rec)))

    moved = [k for k in rmod.PRIORITY_KEYS if rec.all(k) != get(k)]
    priced = [k for k in moved if k != 'priority_ruling']
    widen_hits, folds, notes, added = [], [], [], []
    if 'scope' in keys and was_pulled:
        added = narrow.widened_members(rec.all('scope'), get('scope'))
        if added:
            holding, _dead, _why = ctx.in_flight_split(snap)
            widen_hits = narrow.live_hits(added, holding, skip=rid, branch=rec.get('pulled_by'),
                                          shared=shared_of(ctx))
            # The fold-in guard, as `pull` runs it.
            folds, notes = narrow._pull_fold_hits(ctx, added, holding, rec.get('pulled_by'))
    new_uses = [n for n in get('uses') if n not in rec.all('uses')]
    full = []
    if 'uses' in keys and was_pulled and new_uses:
        holding, _dead, _why = ctx.in_flight_split(snap)
        full = narrow.resource_hits(new_uses, holding, ctx.resources, skip=rid,
                                    branch=rec.get('pulled_by'))
    if (widen_hits or full or folds) and not may_collide:
        override = '%s: `set %s --scope <path> [--scope <path> ...]` (the whole new list)' \
            % (policy.row_text(WIDEN_ROW), rid)
        if widen_hits:
            raise Refusal('`%s` is pulled, and this widens its scope into a live pull: %s. '
                          'Narrowing is free; widening into a live pull is %s'
                          % (rid, '; '.join(widen_hits), override))
        if full:
            raise Refusal('`%s` is pulled, and this adds a resource live pulls fill: %s. Dropping '
                          'a resource is free; taking a full one is %s: `set %s --uses <name> '
                          '[--uses <name> ...]` (the whole new list)'
                          % (rid, '; '.join(full), policy.row_text(WIDEN_ROW), rid))
        raise Refusal(narrow._fold_message(rid, folds, override, 'widen'))
    dropped = dropped_why = None
    if get('priority_ruling') and (priced or get('priority_by') == ['agent']):
        dropped = get('priority_ruling')[0]
        dropped_why = ('`priority_by: agent` carries no ruling' if get('priority_by') == ['agent']
                       else 'it named the old price')
        pairs = [(k, v) for k, v in pairs if k != 'priority_ruling']

    lines = notes + smod.broad_scope_warning(dict(changes).get('scope', [])) + ['SET %s' % rid]
    for key in sorted(set(keys) | set(unset) | ({'priority_ruling'} if dropped else set())):
        lines.append('  %-11s %s -> %s' % (key + ':', ' / '.join(rec.all(key)) or '(absent)',
                                           ' / '.join(rmod.values(pairs, key)) or '(absent)'))
    if body is not None:
        lines.append('  body:       replaced (%d bytes)' % len(body.encode('utf-8')))
    lines += ['  widened into a live pull: %s' % h for h in widen_hits + full]
    lines += ['  folds into the work of %s: %s'
              % (h.rpartition('(changed on ')[2].partition(')')[0], h) for h in folds]
    if 'scope' in keys:
        # `--scope` replaces the whole list; say plainly what that dropped.
        gone = [m for m in rec.all('scope') if m not in get('scope')]
        if gone:
            lines.append('  removed %d scope %s: %s' % (len(gone), 'entry' if len(gone) == 1
                                                         else 'entries', ', '.join(gone)))
    if dropped is not None:
        lines.append('  priority_ruling %s dropped: %s' % (dropped, dropped_why))
    subject = 'backlog: set %s' % rid
    text = _text(pairs, body if body is not None else rec.body)
    if text == rec.text:
        return Change({}, subject, lines + ['  (no change)'])
    change = Change({rid: text}, subject, lines)
    if widen_hits or full or folds:
        narrow._collision_change(change, 'set')
    return change


# --- review mark ----------------------------------------------------------------------------------

def plan_clear_review(snap, ctx, rid, reason):
    """Drop the review mark (`review:`, written by `add` from the Action Hub): a coordinator has
    reviewed the record, which `pull` and `next` then treat as any other. Any state; no ruling,
    and the price is not touched."""
    rec = require(snap, rid)
    refuse_machine_paths('--reason', reason)
    bad = reason_error(reason, '--reason')
    if bad:
        raise Usage(bad)
    if not rec.has('review'):
        raise Refusal('`%s` carries no `review:` mark, so there is nothing to clear' % rid)
    mark = rec.get('review')
    pairs = [(k, v) for k, v in rec.pairs() if k != 'review']
    text = ' '.join(reason.split())
    return Change({rid: _text(pairs, rec.body)}, 'backlog: clear review %s: %s' % (rid, text),
                  ['CLEARED the review mark on %s (was: %s)' % (rid, mark),
                   '  state: %s, priority: %s; `pull` and `next` now treat it as any other record'
                   % (rec.get('state'), rec.get('priority'))])
