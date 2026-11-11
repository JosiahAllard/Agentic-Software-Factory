"""The scope and resource rules the write verbs share (kitlib/backlog/plan.py has the rest).

`next`'s offer and its narrowing (offer_for, current_offer, `_narrower`), the live pulls of other
branches and the scopes their diffs touch (`_live_changes`, `_branch_changes`), the fold-in
guard (`_pull_fold_hits`), and the scope, share and resource collisions a pull or a widening
`set` refuses on or records (live_hits, live_shares, resource_hits, `_collisions`).
"""
import os

from kitlib.git import cmd, refs
from kitlib.git import repo as gitrepo

from . import offer as omod
from . import policy
from . import record as rmod
from . import scope as smod
from .errors import Failure, Refusal, Usage
from .plan import shared_of
from .store_folder import MainBackend
from .store_ref import RefBackend


def own_branch(ctx, branch=None):
    """The branch whose own live pulls an offer leaves out, so a record overlapping only them is
    bundled onto it rather than passed over: `branch`, else `--as` (ctx.branch), else the branch
    checked out in ctx.root. None when that cannot be read, HEAD is detached, or it is the main
    branch or the code main: a main bundles nothing, so its pulls hold their scope. Compared
    exactly, as `pulled_by` is written: another letter case or a ref spelling is another branch."""
    if branch is None:
        branch = ctx.branch
    if not branch:
        branch, _why = refs.current_branch(ctx.root)
    if not branch or branch in (ctx.main_branch, ctx.code_main):
        return None
    return branch


def offer_for(snap, ctx, branch=None, cap=omod.DEFAULT_CAP, seeds=(), holding=None,
              narrow=False):
    """`(offers, passed, holding, own)`: the offer `next` prints for `ctx.session`, with the live
    pulls of `own` (own_branch) taking no scope in it; `holding` is still every live pull (read
    here unless the caller passes the live pulls it already read). A record whose scope folds
    into another live pull's branch (`_offer_folds`, the fold-in guard `pull` runs) is passed
    over, not offered. `next` and `pull` both build their offer here, so they cannot disagree on
    it. `narrow` (`next`, and `pull` through current_offer): a record held only by one live
    pull's protected set is offered with the Split that clears it (`_narrower`)."""
    offers, passed, holding, own = _offer(snap, ctx, branch, cap, seeds, holding, narrow=narrow)
    return offers, passed, holding, own


def _offer(snap, ctx, branch, cap, seeds, holding, diffs=None, narrow=False, whys=None):
    """offer_for; `diffs` is the diff cache `_offer_folds` shares with current_offer's `also`,
    `whys` the dict `_narrower` notes why a narrowing failed in."""
    own = own_branch(ctx, branch)
    if holding is None:
        holding, _dead, _why = ctx.in_flight_split(snap)
    taken = [r for r in holding if not (own and r.get('pulled_by') == own)]
    live = _live_reader(ctx, holding, own, diffs)
    folded = _offer_folds(ctx, holding, own, live=live)
    result, refusals, _ranks = omod.next_offer(snap, taken, cap=cap, seeds=seeds,
                                               session=ctx.session, unmet=ctx.unmet(snap),
                                               folded=folded, capacity=ctx.capacity(snap),
                                               branch=own,
                                               narrow=_narrower(ctx, live, whys) if narrow
                                               else None,
                                               session_live=getattr(ctx, 'session_live', None),
                                               shared=shared_of(ctx))
    if refusals:
        raise Failure('`next` cannot compute an offer: %s' % '; '.join(refusals))
    return result[0], result[1], holding, own


def _narrower(ctx, live, whys=None):
    """The `narrow(rec, others)` an offer (`omod.next_offer`) narrows with: the Split that clears
    `rec` of every live pull of `others` (omod.split_all), or None. Each `other`'s protected
    set T is the paths
    its branch changed since its merge base with the code main (`live`, the `_live_reader` the
    fold check shares, so each branch is diffed once per offer), the dirty paths of its one
    worktree (`_dirty_paths`) and the paths its `## Files` names (omod.files_section), over the
    tracked tree at the code main (`_tracked_tree`). Fails closed, for every live pull if for
    one: when any part of any T or the tree cannot be read, or the split fails against any of
    them, None, and `rec` is passed over as today. A split that narrows any `other`
    too is None when the store is not shared (`_shared_store`): `pull` could not write `other`'s
    narrowing where its branch reads it, so `next` does not offer it either. Each worktree's
    status and the tree are read from git on first use only. `whys` (a dict) gets
    `{rec.id: why}` for each None."""
    cache = {}

    def read(key, fn):
        if key not in cache:
            try:
                cache[key] = (fn(), None)
            except (Failure, Usage) as exc:
                cache[key] = (None, str(exc))
        return cache[key]

    def failed(rec, why):
        if whys is not None:
            whys[rec.id] = why
        return None

    def protected_set(other):
        """`(T, plan, None)` of the live pull `other`, or `(None, None, why)`."""
        name = other.get('pulled_by') or ''
        files, why = omod.files_section(other.body)
        if not name:
            return None, None, '%s names no branch' % other.id
        if files is None:
            return None, None, 'the `## Files` of %s cannot be read: %s' % (other.id, why)
        changes, _notes, unknown = live()
        tips = changes.get(name)
        if name in unknown or not tips:
            return None, None, 'the diff of %s cannot be read' % name
        dirty, why = read(('dirty', name), lambda: _dirty_paths(ctx, name))
        if dirty is None:
            return None, None, why
        protected = set(files) | set(dirty)
        for _label, paths in tips:
            protected.update(paths)
        return sorted(protected), bool(files), None

    def narrow(rec, others):
        parts = []
        for other in others:
            protected, plan, why = protected_set(other)
            if protected is None:
                return failed(rec, why)
            parts.append((other, protected, plan))
        tree, why = read('tree', lambda: _tracked_tree(ctx))
        if not tree:
            return failed(rec, why or 'the code main tree is empty')
        out, why = omod.split_all(rec.all('scope'), parts, tree, shared=shared_of(ctx))
        if out is None:
            return failed(rec, why)
        if out.narrows_other() and not _shared_store(ctx):
            hit = [p for p in out.parts if p.l_changes]
            return failed(rec, 'the backlog store here is this checkout\'s own copy, so a '
                               'narrowing of %s would not reach %s'
                          % (', '.join(p.other.id for p in hit),
                             ', '.join(p.other.get('pulled_by') or '?' for p in hit)))
        return out
    return narrow


def _shared_store(ctx):
    """True when a write to `ctx.store` reaches every branch: a ref store, or a folder store in
    the PM data worktree (its write commits on the PM data branch). A folder store in any other
    checkout is that checkout's own copy (a branch carries its records), so another branch's
    record narrowed there never reaches that branch; a store that cannot be told is not shared."""
    backend = getattr(getattr(ctx, 'store', None), 'backend', None)
    if isinstance(backend, RefBackend):
        return True
    if isinstance(backend, MainBackend):
        try:
            return backend.commit_branch() is not None
        except Failure:
            return False
    return False


def _dirty_paths(ctx, branch):
    """The paths `git status` shows as changed, deleted or untracked in the one worktree that
    has `branch` checked out (spelled exactly as `pulled_by` is); a rename counts as its two
    paths (refs.status, renames off). Failure when there is no such worktree, more than one, it
    is prunable or gone, or git cannot read its status."""
    wts, why = gitrepo.worktrees(ctx.root)
    if wts is None:
        raise Failure('cannot list the worktrees: %s' % why)
    mine = [w for w in wts if w.branch == branch]
    if len(mine) != 1:
        raise Failure('%d worktrees have %s checked out' % (len(mine), branch))
    w = mine[0]
    if w.prunable or not os.path.isdir(w.path):
        raise Failure('the worktree of %s at %s is gone' % (branch, w.path))
    entries, why = refs.status(w.path, renames=False)
    if entries is None:
        raise Failure('cannot read the status of %s: %s' % (w.path, why))
    return sorted(set(p for _xy, p in entries))


def _tracked_tree(ctx):
    """Every path tracked at the code main (`_code_main`). Failure when git cannot list it."""
    _base, sha = _code_main(ctx)
    if not sha:
        raise Failure('the code main does not resolve here')
    data, why = cmd.run(['ls-tree', '-r', '--name-only', '-z', sha], ctx.root)
    if data is None:
        raise Failure('cannot list the code main tree: %s' % why)
    return [p for p in cmd.text(data).split('\0') if p]


def batch_members(snap, branch):
    """The ids of the open records batched for `branch` (`batch: <branch>`), in rank order: the
    seeds of a topic session's pull (plan_pull)."""
    mine = [r for r in snap.readable if r.get('batch') == branch and r.get('state') == 'open']
    for r in mine:
        if not rmod.is_band(r.get('priority')):
            # rank_key reads a price; refuse as a plain pull of an unpriced record does
            raise Refusal('`%s` is batched for %s but is `priority: %s`, and `pull` takes a priced '
                          'record (`1`-`5`). Price it first: `set %s --priority N --priority-by '
                          'agent`' % (r.id, branch, r.get('priority'), r.id))
    return [r.id for r in sorted(mine, key=omod.rank_key)]


def current_offer(snap, ctx, branch=None, seeds=(), whys=None, diffs=None):
    """`(offers, passed, holding, also, notes)` for a pull onto `branch`: the offer `next` prints
    as that branch (offer_for, narrowing as `next` does, so an offer's `split` is the one `next`
    shows, recomputed from git now; `whys` gets why a narrowing failed, `_narrower`), and `also`,
    the records `next` offers without it (as the main checkout prints it, never narrowed), []
    when the two are the same. A pull takes a record either one offers, so a coordinator's
    `next` and a worker's `pull` never disagree on what is in order.
    `also` only widens what a pull takes: when it cannot be computed (one of the branch's own
    pulls has a scope that cannot be compared, which the branch's offer never reads) it is [],
    so a record `next` offered as the branch still pulls plainly and nothing else is let in;
    `notes` then holds one NOTE naming why, for the pull to print.
    `seeds` (a topic session's batch, batch_members) go first in the branch's offer, as
    `next --with` puts them, and the cap grows to hold them all.
    `diffs`, a list the caller keeps, is the diff cache the offers fill; `pull` hands it on to
    `_pull_fold_hits` so each live branch is diffed once."""
    diffs = [] if diffs is None else diffs
    cap = ctx.cap
    if seeds and cap is not None:
        cap = max(cap, len(seeds))
    offers, passed, holding, own = _offer(snap, ctx, branch, cap, tuple(seeds), None, diffs,
                                          narrow=True, whys=whys)
    also, notes = [], []
    if own is not None and any(r.get('pulled_by') == own for r in holding):
        # The fold check `next` runs in the main checkout (no own branch, so the branch's own
        # diff counts too): `also` must be exactly that offer, or a record the main checkout
        # offers because a higher one folds into this branch would be refused here. The diffs
        # are read once for both offers (`diffs`).
        result, refusals, _ranks = omod.next_offer(snap, holding, cap=ctx.cap,
                                                   session=ctx.session,
                                                   unmet=ctx.unmet(snap),
                                                   folded=_offer_folds(ctx, holding, None, diffs),
                                                   capacity=ctx.capacity(snap),
                                                   session_live=getattr(ctx, 'session_live',
                                                                        None),
                                                   shared=shared_of(ctx))
        if refusals:
            notes.append('NOTE: the offer without branch %s cannot be computed (%s); only the '
                         'offer as %s was checked' % (own, '; '.join(refusals), own))
        else:
            also = [o.rec for o in result[0]]
    return offers, passed, holding, also, notes


def _branch_tips(ctx, branch, names):
    """`(tips, note)`: `tips` is `[(label, commit)]` to diff for `branch`, shared by `add` and
    `pull`: its local tip, else the tip of each remote that has it as last fetched (each distinct
    commit once, labelled `<remote>/<branch>`), with a NOTE naming those remotes; `[]` and no
    note when neither has it. `names` is `_ref_names`. Failure when git cannot read a ref."""
    tip, why = refs.resolve(_spelling(names, 'refs/heads/%s' % branch), ctx.root, known=True)
    if tip is None:
        raise Failure('cannot read branch %s: %s' % (branch, why))
    if tip:
        return [(branch, tip)], None
    remotes = _remotes_with(ctx, branch, names)
    tips = []
    for _remote, ref, rtip in remotes:
        if rtip not in [t for _label, t in tips]:
            tips.append((ref[len('refs/remotes/'):], rtip))
    note = remotes and ('NOTE: branch %s is not a local branch; its diff was compared as last '
                        'fetched from %s' % (branch, ', '.join(r for r, _ref, _t in remotes)))
    return tips, note or None


def _remotes_with(ctx, branch, names=None):
    """`[(remote, ref, tip)]`: each remote whose `branch` git holds a remote-tracking ref for (as
    last fetched; no network), the ref as git spells it (`_spelling`; `names` is `_ref_names`,
    None: asked here) and its commit. Every remote is asked, not `ctx.remote` alone: a store's
    checkout can name another remote than the one the code is pushed to. Failure when git cannot
    list the remotes or read a ref: "cannot tell" is never "no such branch"."""
    out, why = cmd.run_text(['remote'], ctx.root)
    if out is None:
        raise Failure('cannot list the remotes to look for branch %s: %s' % (branch, why))
    if names is None:
        names = _ref_names(ctx, branch)
    found = []
    for remote in out.splitlines():
        remote = remote.strip()
        if not remote:
            continue
        ref = _spelling(names, 'refs/remotes/%s/%s' % (remote, branch))
        tip, why = refs.resolve(ref, ctx.root, known=True)
        if tip is None:
            raise Failure('cannot read %s: %s' % (ref, why))
        if tip:
            found.append((remote, ref, tip))
    return found


def _ref_names(ctx, branch):
    """Every branch and remote-tracking ref name git lists (`for-each-ref refs/heads/
    refs/remotes/`), to look `branch` up in. Asking git for the list, not for the one name, is
    what makes the letter case of a lookup consistent: on a case-insensitive file system a loose
    ref's file opens under any case, a packed-refs line only under its own. Failure when git
    cannot list them."""
    out, why = cmd.run_text(['for-each-ref', '--format=%(refname)', 'refs/heads/',
                             'refs/remotes/'], ctx.root)
    if out is None:
        raise Failure('cannot list the refs to look for branch %s: %s' % (branch, why))
    return [n.strip() for n in out.splitlines() if n.strip()]


def _spelling(names, ref):
    """`ref` as git spells it in `names` (`_ref_names`): the one name equal to it ignoring case,
    else `ref` unchanged (git then answers for it: a broken loose ref for-each-ref skips is no
    answer, not "no such ref"). Usage when several listed names equal it ignoring case, even
    one exactly: on a case-insensitive file system `rev-parse` of the packed one can open the
    other's loose file, so neither can be read for certain."""
    near = [n for n in names if n.casefold() == ref.casefold()]
    if len(near) > 1:
        raise Usage('`%s` matches %s, which differ only in letter case and cannot be told apart '
                    'on this file system: rename one' % (ref, ', '.join(sorted(near))))
    return near[0] if near else ref


def _overlaps(scopes, others, source, shared=()):
    """An overlap line per (member of `scopes`, member or path of `others`) that collide; a pair
    either side of which is one of `shared` (scope.members_collide) is none."""
    out = []
    for s in scopes:
        for o in others:
            try:
                hit = smod.members_collide(s, o, shared)
            except smod.UndecidableScope as exc:
                out.append('%s vs %s %s - cannot be compared, so it counts: %s'
                           % (s, o, source, exc))
                continue
            if hit:
                out.append('%s vs %s %s' % (s, o, source))
    return out


def _diff_hits(ctx, branch, tip, scopes, base=None, mb=None):
    """An overlap line for each member of `scopes` that overlaps a path `branch` (at `tip`)
    changed since its merge base with the code main; one that cannot be compared counts."""
    hits = []
    for path in _branch_changes(ctx, branch, tip, base, mb):
        hits += _overlaps(scopes, [path], '(changed on %s)' % branch)
    return hits


def _code_main(ctx):
    """`(rev, sha)` of the code main as `refs.default_base` picks it on the code remote
    (`<code_remote>/<code_main>` as last fetched, else the local branch); `sha` is '' when it does
    not resolve. Failure when git cannot read it."""
    base, _note = refs.default_base(ctx.root, ctx.code_remote, ctx.code_main)
    sha, why = refs.resolve(base, ctx.root, known=True)
    if sha is None:
        raise Failure('cannot read the code main `%s`: %s' % (base, why))
    return base, sha


def _live_changes(ctx, holding, branch):
    """`(changes, notes, unknown)` for the fold-in guard (docs/backlog.md, "The fold-in guard"):
    `changes` is `{name: [(label, paths)]}`, the paths each live pull's branch in `holding`
    changed since its merge base with the code main, the caller's `branch` (any letter case) and
    its own pulls aside; each branch is diffed once. A code main that does not resolve here (a
    store whose git is not the code's) skips every diff with a NOTE, and so does a live branch
    that shares no history with it (the code main is not that branch's history:
    backlog.code_main unset). A live branch only remotes have is diffed at each remote's tip as
    last fetched, with a NOTE, as `add` does (`_branch_tips`); one neither here nor on a remote
    is noted, not compared. `unknown` is `{name: error}` for each branch git could not answer
    for (a ref it cannot read, a merge base it cannot find, a diff it cannot run: the Failure,
    or the Usage of `_spelling`); nothing is raised, so `pull` raises it and `next` passes over."""
    names = []
    for other in holding:
        name = other.get('pulled_by') or ''
        if name and name.lower() != (branch or '').lower() and name not in names:
            names.append(name)
    changes, notes, unknown = {}, [], {}
    if not names:
        return changes, notes, unknown
    try:
        base, sha = _code_main(ctx)
        if not sha:
            return changes, ['NOTE: code main %s does not resolve here; branch diffs not '
                             'compared' % base], unknown
        refnames = _ref_names(ctx, ', '.join(sorted(names)))
    except (Failure, Usage) as exc:
        return changes, notes, dict((name, exc) for name in names)
    for name in sorted(names):
        try:
            changes[name] = _one_branch(ctx, name, refnames, base, sha, notes)
        except (Failure, Usage) as exc:
            unknown[name] = exc
    return changes, notes, unknown


def live_dirty(ctx, holding, changes):
    """`{branch: (paths, why)}` for the narrowing hint of `next` (omod.narrowings): the
    uncommitted paths of each live pull's one worktree (`_dirty_paths`) for each branch in
    `changes`, or `(None, why)` when they cannot be read. Read only."""
    out = {}
    for other in holding:
        branch = other.get('pulled_by')
        if branch in changes and branch not in out:
            try:
                out[branch] = (_dirty_paths(ctx, branch), None)
            except (Failure, Usage) as exc:
                out[branch] = (None, 'its uncommitted files cannot be read (%s)' % exc)
    return out


def _one_branch(ctx, name, refnames, base, sha, notes):
    """`[(label, paths)]` for one live branch of `_live_changes`, its NOTEs added to `notes`."""
    out = []
    tips, note = _branch_tips(ctx, name, refnames)
    if note:
        notes.append(note)
    if not tips:
        notes.append('NOTE: branch %s is neither a local branch nor on a remote; its diff was '
                     'not compared' % name)
    for label, tip in tips:
        mb, why = refs.merge_base(sha, tip, ctx.root)
        if mb is None:
            raise Failure('cannot find the merge base of %s and %s: %s' % (label, base, why))
        if not mb:
            notes.append('NOTE: code main %s shares no history with %s; set '
                         'backlog.code_main; branch diffs not compared' % (base, label))
            continue
        out.append((label, _branch_changes(ctx, label, tip, (base, sha), mb)))
    return out


def _change_hits(scopes, changes, shared=()):
    """An overlap line for each member of `scopes` that overlaps a path in `changes`
    (`_live_changes`), branch by branch in name order; one that cannot be compared counts. A
    shared path (`shared`) on either side overlaps nothing."""
    hits = []
    for name in sorted(changes):
        for label, paths in changes[name]:
            for path in paths:
                hits += _overlaps(scopes, [path], '(changed on %s)' % label, shared)
    return hits


def _pull_fold_hits(ctx, scopes, holding, branch, diffs=None):
    """The fold-in guard on `pull`: `(hits, notes)`, the overlaps of `scopes` with what the other
    live pulls' branches changed (`_live_changes`). Failure (or Usage) when git cannot answer for
    one of them: on `pull`, "cannot tell" is never "no overlap". A filled `diffs` (the cache
    current_offer left) is read instead of diffing again, unless it carries a NOTE: the cache
    was read with the caller's own branch in, so its notes may be about that branch, which this
    guard never reports; that case diffs again, as before."""
    if diffs and not diffs[0][1]:
        changes, notes, unknown = _live_reader(ctx, holding, branch, diffs)()
    else:
        changes, notes, unknown = _live_changes(ctx, holding, branch)
    for name in sorted(unknown):
        raise unknown[name]
    return _change_hits(scopes, changes, shared_of(ctx)), notes


def _live_reader(ctx, holding, own, diffs=None):
    """`live()` -> `(changes, notes, unknown)` of `_live_changes` for the live pulls of another
    branch than `own` (any letter case), read from git on the first call only. `diffs`, a list
    two checks share (current_offer), caches every live branch's diff, `own`'s too, and each
    reader leaves its own branch out of it; without it only the other branches are diffed."""
    seen = [] if diffs is None else diffs

    def live():
        if not seen:
            seen.append(_live_changes(ctx, holding, own if diffs is None else None))
        changes, notes, unknown = seen[0]
        if diffs is not None and own:
            mine = own.lower()
            changes = dict((k, v) for k, v in changes.items() if k.lower() != mine)
            unknown = dict((k, v) for k, v in unknown.items() if k.lower() != mine)
        return changes, notes, unknown
    return live


def _offer_folds(ctx, holding, own, diffs=None, live=None):
    """The `folded(rec, scopes=None)` check an offer (`omod.next_offer`) passes over with: the
    reason `rec`'s scope (`scopes` when given: what a narrowed record keeps) folds into another
    live pull's branch than `own` (any letter case), None when it does not. The branches are
    diffed on the first call only (`_live_reader`, or the `live` reader the caller shares with
    `_narrower`, so each live branch is diffed once), and never when no candidate asks. A
    branch git cannot answer for passes every candidate over with a "cannot tell" note instead
    of failing `next`; `pull` still fails on it (`_pull_fold_hits`)."""
    live = live or _live_reader(ctx, holding, own, diffs)

    def folded(rec, scopes=None):
        changes, _notes, unknown = live()
        hits = _change_hits(scopes or rec.all('scope'), changes, shared_of(ctx))
        if hits:
            where = _fold_where(hits)
            return ('scope overlaps the work of %s (%s); fold it into %s, or pull it after %s '
                    'lands' % (where, hits[0], where, where))
        for name in sorted(unknown):
            return ('cannot tell whether %s folds into %s\'s work; pull after %s lands or check '
                    'git (%s)' % (rec.id, name, name, unknown[name]))
        return None
    return folded


def _fold_where(hits):
    """The branches the overlap lines `hits` name, in order, comma-separated."""
    branches = []
    for h in hits:
        b = h.rpartition('(changed on ')[2].partition(')')[0]
        if b not in branches:
            branches.append(b)
    return ', '.join(branches)


def _fold_message(rid, hits, override, verb='pull'):
    """The fold refusal's text (`verb` `pull`, or `widen` for `set --scope`): the overlapping
    pairs, where the work belongs, and the override (`override`: the row in words, and any form
    it takes). The caller raises it as a Refusal (tests/backlog/test_refusal_kinds.py audits each
    raise by name)."""
    where = _fold_where(hits)
    return ('cannot %s %s: its scope overlaps the work of %s, so it belongs there:\n  %s\n'
            'fold it into %s, or %s it after %s lands. Overriding that is %s'
            % (verb, rid, where, '\n  '.join(hits), where, verb, where, override))


def _collision_change(change, verb):
    """`change` marked as one that took the collision row of `verb` (policy.COLLISION_ROWS): its
    `rows` for the audit log (tools/backlog.py), and the mark on its subject the audit check reads
    back from the store's history."""
    change.rows = [policy.COLLISION_ROWS[verb]]
    change.subject += policy.COLLISION_MARK
    return change


def _branch_changes(ctx, branch, tip, base=None, mb=None):
    """The paths `branch` changed since its merge base with the code main (no rename pairing,
    so a rename names both paths). `base` is `(rev, sha)` when the caller already resolved the
    code main, `mb` the merge base when it already found it."""
    key = 'set `backlog.code_main` in .kit.json to the branch code lands on'
    base, sha = base or _code_main(ctx)
    if not sha:
        raise Failure('the code main `%s` does not resolve here, so the diff of %s cannot be '
                      'measured: %s' % (base, branch, key))
    why = None
    if not mb:
        mb, why = refs.merge_base(sha, tip, ctx.root)
    if mb is None:
        raise Failure('cannot find the merge base of %s and %s: %s' % (branch, base, why))
    if not mb:
        raise Failure('%s shares no history with the code main `%s`, so its diff cannot be '
                      'measured: %s' % (branch, base, key))
    out, why = cmd.run(['diff', '--no-renames', '--name-only', '-z', mb, tip, '--'], ctx.root)
    if out is None:
        raise Failure('cannot diff %s against its merge base %s: %s' % (branch, mb[:10], why))
    return sorted(set(p for p in out.decode('utf-8', errors='replace').split('\0') if p))


def resource_hits(names, holding, capacity, skip=None, branch=None):
    """`resource X at capacity N, taken by ...` for each of `names` the live pulls of `holding`
    fill, bar the record id `skip` and any pull by `branch` (live_hits' rule). An undeclared name
    fills nothing."""
    others = [r for r in holding
              if r.id != skip and not (branch and r.get('pulled_by') == branch)]
    load = omod.resource_load(others, capacity)
    return [omod.capacity_why(n, capacity[n], [omod.live_label(r) for r in load[n]])
            for n in dict.fromkeys(names) if n in capacity and len(load[n]) >= capacity[n]]


def uses_shown(snap, ctx, rec):
    """`show`'s `uses` line for a record that has one, None otherwise; a read, so a config that
    cannot be read is said on the line, never an error."""
    if not rec.has('uses'):
        return None
    try:
        capacity = ctx.resources
    except Failure as exc:
        return 'uses %s (capacity not read: %s)' % (', '.join(omod.uses(rec)), exc)
    holding, _dead, _why = ctx.in_flight_split(snap)
    return omod.uses_line(rec, holding, capacity)


def live_hits(scopes, holding, skip=None, branch=None, shared=()):
    """`scope A collides with B already taken by ID (in flight on BRANCH)` for each collision of
    `scopes` with a live pull in `holding`, bar the record id `skip` and any pull by `branch`: one
    branch holding overlapping records is not a collision. A comparison that cannot be decided is
    a hit, never a clearance. A pair either member of which is one of `shared` is no hit
    (live_shares names it)."""
    hits = []
    for other in holding:
        if other.id == skip or (branch and other.get('pulled_by') == branch):
            continue
        who = '%s (in flight on %s)' % (other.id, other.get('pulled_by') or '?')
        try:
            pairs = smod.colliding_pairs(scopes, other.all('scope'), shared)
        except smod.UndecidableScope as exc:
            hits.append('scope cannot be compared with %s, so it is a collision: %s' % (who, exc))
            continue
        hits += ['scope %s collides with %s already taken by %s' % (a, b, who) for a, b in pairs]
    return hits


def live_shares(scopes, holding, skip=None, branch=None, shared=()):
    """`shares P with ID (shared path)` for each shared path P (`shared`) on which `scopes` meets
    a live pull ID of `holding`, a pair `shared` exempts (scope.shared_hits); the pulls live_hits
    leaves out are left out here too. A comparison that cannot be decided is live_hits' to
    report, so it adds nothing here."""
    out = []
    for other in holding:
        if other.id == skip or (branch and other.get('pulled_by') == branch):
            continue
        try:
            paths = smod.shared_hits(scopes, other.all('scope'), shared)
        except smod.UndecidableScope:
            continue
        out += [omod.shares_line(p, other.id) for p in paths]
    return out


def widened_members(old, new):
    """The members of `new` that no member of `old` covers: what a `set --scope` adds. An old
    member covers a new one when they are equal (components casefolded, the same `/**`-ness), or
    when the old one is `X/**` and the new one lies under X. `none` covers only `none`, and a new
    `none` adds nothing. A member the grammar cannot read is never covered."""
    def shape(m):
        if m == smod.NONE:
            return None
        try:
            return smod.scope_components(m), m.endswith(smod.GLOB_SUFFIX)
        except smod.UndecidableScope:
            return False
    olds = [s for s in (shape(m) for m in old) if s]
    added = []
    for m in new:
        if m == smod.NONE:
            continue
        s = shape(m)
        if s is False or not any(o == s or (o[1] and s[0][:len(o[0])] == o[0]) for o in olds):
            added.append(m)
    return added


def _collisions(rec, holding, shared=()):
    """A warning line for each in-flight record in `holding` whose scope collides with `rec`'s
    (or cannot be compared with it); a shared path (`shared`) collides with nothing."""
    lines = []
    for other in holding:
        try:
            if smod.scopes_collide(rec.all('scope'), other.all('scope'), shared):
                lines.append('warning: scope collides with `%s`, in flight on %s'
                             % (other.id, other.get('pulled_by')))
        except smod.UndecidableScope as exc:
            lines.append('warning: scope cannot be compared with `%s` (in flight), so it is a '
                         'collision: %s' % (other.id, exc))
    return lines


def landing_merge(root, main_branch, branch, since=None, rev=None, any_case=False):
    """The landing merge of `branch` on `main_branch` (`<short sha> <subject>`, the subject
    `Merge <branch>` or `Merge <branch>: <summary>` as kitlib/landing/merge.py writes it), or None
    when its history has none. Only merges on the first-parent chain count: a `Merge <branch>`
    merge brought in inside another branch was not made on `main_branch`. `since` (a
    `YYYY-MM-DD` day, the record's `pulled_at`) skips a merge committed on an earlier day: an
    older landing of a branch whose name was later reused.
    A merge on that day still counts. `rev` is the history searched (None: the local
    `main_branch`); `any_case` matches the branch name in any letter case. Failure when git
    cannot say."""
    want = 'Merge %s' % branch
    args = ['log', '--merges', '--first-parent', '--format=%h %cs %s', '-F', '--grep', want]
    if any_case:
        args.append('-i')
    out, why = cmd.run_text(args + [rev or 'refs/heads/%s' % main_branch, '--'], root)
    if out is None:
        raise Failure('cannot read the history of %s to see whether %s landed: %s'
                      % (main_branch, branch, why))
    fold = (lambda t: t.casefold()) if any_case else (lambda t: t)
    for line in out.split('\n'):
        sha, _, rest = line.partition(' ')
        day, _, subject = rest.partition(' ')
        if since and day < since:
            continue
        if fold(subject) == fold(want) or fold(subject).startswith(fold(want + ': ')):
            return '%s %s' % (sha, subject)
    return None
