"""`backlog.py list --orphans` (docs/backlog.md): each pulled record, whether any agent works its
branch, and the route that stops its pull (plan_claim.stop_pull's rule, read here, never changed).

State per record, from the branch set and the worktree list, never from the probe's text: `no
branch name` (empty or invalid `pulled_by`); `no branch`; `remote only` (only `<remote>/<b>`: a
quiet release needs the local branch); `main checkout is on it`; `NO WORKTREE` (a local branch no
worktree is on: the probe calls that live, so only a quiet release stops it); `NO LIVE AGENT` (a
worktree the probe finds dead); `live: <why>`; `unreadable: <why>`; `liveness not probed` (no probe
on the Context)."""
import collections

from kitlib.git import cmd, refs
from kitlib.git import repo as gitrepo

from . import plan

NO_NAME, NO_BRANCH, REMOTE, MAIN, NO_TREE, DEAD, UNPROBED = (
    'no branch name', 'no branch', 'remote only', 'main checkout is on it', 'NO WORKTREE',
    'NO LIVE AGENT', 'liveness not probed')

# What one run read: `names` every local and remote branch, `local` the local ones, `on` the
# branches with a linked worktree, `main_on` the main checkout's branch (each None when unread, with
# `why` saying so), `bad` the invalid `pulled_by` values, `liveness(branch) -> (why live, tip)` or
# None, `info(branch, local) -> (last commit date, commits ahead)`.
View = collections.namedtuple('View', 'names local on main_on bad liveness info why')


def state(branch, v):
    """The state of a pull by `branch`, read through the View `v`."""
    if not branch or branch in v.bad:
        return NO_NAME
    if v.names is None or v.on is None:
        return 'unreadable: %s' % v.why
    if branch not in v.names:
        return NO_BRANCH
    if branch not in v.local:
        return REMOTE
    if branch == v.main_on:
        return MAIN
    if branch not in v.on:
        return NO_TREE
    if v.liveness is None:
        return UNPROBED
    why, _tip = v.liveness(branch)
    return DEAD if why is None else 'live: %s' % why


def route(st, rid, session):
    """What stops the pull: `release` by anyone, a quiet release (the caller's `--session`, or a
    successor's, is the pull's: 4 h quiet), or the PM."""
    if st in (NO_BRANCH, DEAD):
        return 'release %s --reason' % rid
    if st == NO_TREE and session:
        return 'quiet release if your --session (or its successor\'s) matches (4 h quiet), else ask the PM'
    return 'ask the PM'


def render(recs, v):
    """One line per pulled record; the last commit date and commits ahead are read for a branch
    that exists."""
    out = []
    for r in recs:
        branch, session = r.get('pulled_by'), r.get('pulled_session')
        st = state(branch, v)
        seen = ''
        if st in (REMOTE, MAIN, NO_TREE, DEAD, UNPROBED) or st.startswith('live: '):
            date, ahead = v.info(branch, branch in v.local)
            seen = '  last %s  ahead %s' % (date, ahead)
        out.append('%s  %s%s  session=%s  %s  -> %s'
                   % (r.id, branch or '-', seen, session or 'none', st,
                      route(st, r.id, session)))
    return out


def lines(ctx, snap):
    """The lines of `list --orphans` for `snap`'s pulled records."""
    recs = [r for r in snap.readable if r.get('state') == 'pulled']
    if not recs:
        return []
    names, why = ctx.live_branches(snap)
    trees, twhy = gitrepo.worktrees(ctx.root)
    local = {b for b in (names or ()) if refs.ref_commit('refs/heads/%s' % b, ctx.root)[0]}
    on = None if trees is None else {t.branch for t in trees[1:] if t.branch}
    base = refs.default_base(ctx.root, ctx.code_remote, ctx.code_main)[0]
    bad = {r.get('pulled_by') for r in recs
           if r.get('pulled_by') and plan.branch_name_error(r.get('pulled_by'), ctx.root)}

    def info(branch, is_local):
        ref = ('refs/heads/%s' if is_local else 'refs/remotes/%s/%%s' % ctx.remote) % branch
        date, dwhy = cmd.run_text(['log', '-1', '--format=%cs', ref, '--'], ctx.root)
        ahead, awhy = cmd.run_text(['rev-list', '--count', '%s..%s' % (base, ref)], ctx.root)
        return date or '? (%s)' % dwhy, ahead or '? (%s)' % awhy
    v = View(names, local, on, trees[0].branch if trees else None, bad, ctx.liveness, info,
             why or twhy)
    return render(recs, v)
