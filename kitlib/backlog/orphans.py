"""`backlog.py list --orphans` (docs/backlog.md): each pulled record, whether any agent works its
branch, and the route that stops its pull (plan_claim.stop_pull's rule, read here, never changed).

State per record, from the branch set and the worktree list, never from the probe's text: `no
branch`; `NO WORKTREE` (a branch no worktree is on: the probe calls that live, so only a quiet
release stops it); `NO LIVE AGENT` (a worktree the probe finds dead); `live: <why>`; `unreadable`;
`liveness not probed` (no probe on the Context)."""
from kitlib.git import cmd, refs
from kitlib.git import repo as gitrepo

from . import offer as omod

NO_BRANCH, NO_TREE, DEAD, UNREADABLE, UNPROBED = (
    'no branch', 'NO WORKTREE', 'NO LIVE AGENT', 'unreadable', 'liveness not probed')


def state(branch, names, on, liveness):
    """The state of a pull by `branch`: `names` the local and remote branch names (None: unread),
    `on` the branches with a worktree (None: unread), `liveness(branch) -> (why live, tip)` or None."""
    if names is None or on is None:
        return UNREADABLE
    if branch not in names:
        return NO_BRANCH
    if branch not in on:
        return NO_TREE
    if liveness is None:
        return UNPROBED
    why, _tip = liveness(branch)
    return DEAD if why is None else 'live: %s' % why


def route(st, session):
    """What stops the pull: `release` by anyone, a quiet release (the caller's `--session` is the
    pull's, 4 h quiet), or the PM."""
    if st in (NO_BRANCH, DEAD):
        return 'release <ID> --reason'
    if st == NO_TREE and session:
        return 'quiet release if your --session matches (4 h quiet), else ask the PM'
    return 'ask the PM'


def render(recs, names, on, liveness, info):
    """One line per pulled record; `info(branch) -> (last commit date, commits ahead)` is read
    for a branch that exists."""
    out = []
    for r in recs:
        branch, session = r.get('pulled_by'), r.get('pulled_session')
        st = state(branch, names, on, liveness)
        seen = ''
        if names is not None and branch in names:
            date, ahead = info(branch)
            seen = '  last %s  ahead %s' % (date, ahead)
        out.append('%s  %s%s  session=%s  %s  -> %s'
                   % (r.id, branch, seen, session or 'none', st, route(st, session)))
    return out


def lines(ctx, snap):
    """The lines of `list --orphans` for `snap`'s pulled records."""
    recs = [r for r in snap.readable if r.get('state') == 'pulled']
    names, _why = ctx.live_branches(snap) if recs else (set(), None)
    trees, _why = gitrepo.worktrees(ctx.root) if recs else ([], None)
    on = None if trees is None else {t.branch for t in trees[1:] if t.branch}
    base = refs.default_base(ctx.root, ctx.code_remote, ctx.code_main)[0]

    def info(branch):
        ref = 'refs/heads/%s' % branch
        date = cmd.run_text(['log', '-1', '--format=%cs', ref, '--'], ctx.root)[0] or '?'
        ahead = cmd.run_text(['rev-list', '--count', '%s..%s' % (base, ref)], ctx.root)[0] or '?'
        return date, ahead
    return render(recs, names, on, ctx.liveness, info)
