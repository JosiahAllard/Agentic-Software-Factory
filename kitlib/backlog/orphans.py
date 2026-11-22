"""`backlog.py list --orphans` (docs/backlog.md): each pulled record, whether any agent works its
branch, and the route that stops its pull (plan_claim.stop_pull's rule, read here, never changed).

State per record, from the branch set and the worktree list, never from the probe's text: `no
branch name` (empty or invalid `pulled_by`); `no branch`; `remote only` (only `<remote>/<b>`: a
quiet release needs the local branch); `main checkout is on it`; `NO WORKTREE` (a local branch no
worktree is on: the probe calls that live, so only a quiet release stops it); `NO LIVE AGENT` (a
worktree the probe finds dead); `IDLE <h> h` (a live one the quiet probe finds quiet: no
commit, reflog entry or file change for kitlib/worktree/quiet.py QUIET_AFTER_S, no process, lock
or queued verdict); `live: <why>`; `unreadable: <why>`."""
import collections

from kitlib.git import cmd, refs
from kitlib.git import repo as gitrepo

from . import plan, plan_claim

NO_NAME, NO_BRANCH, REMOTE, MAIN, NO_TREE, DEAD = (
    'no branch name', 'no branch', 'remote only', 'main checkout is on it', 'NO WORKTREE',
    'NO LIVE AGENT')
IDLE = 'IDLE'

# What one run read: `names` every local and remote branch, `local` the local ones, `on` the
# branches with a linked worktree, `main_on` the main checkout's branch (each None when unread, with
# `why` saying so), `bad` the invalid `pulled_by` values, `liveness(branch) -> (why live, tip)`,
# `idle(branch) -> (why not quiet, None) or (None, seconds quiet)` (ctx.quiet; none: not quiet),
# `info(branch, local) -> (last commit date, commits ahead)`, `quiet(rec)` the caller's session key
# when it may release `rec`'s pull as quiet (plan_claim.quiet_key_why), else None.
View = collections.namedtuple('View', 'names local on main_on bad liveness idle info why quiet')


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
    why, _tip = v.liveness(branch)
    if why is None:
        return DEAD
    _not, quiet_s = v.idle(branch)
    return 'live: %s' % why if quiet_s is None else '%s %.1f h' % (IDLE, quiet_s / 3600.0)


def route(st, rid, key):
    """What stops the pull, as the `backlog.py` call (`--session` is a top-level option): `release`
    by anyone, a quiet release (`key`, the caller's `--session`, is the pull's or a successor's;
    every live row gets it, since the probe's text does not tell what `quiet_hold` will refuse),
    or the PM."""
    if st in (NO_BRANCH, DEAD):
        return 'backlog.py -C <checkout> release %s --reason "<why>"' % rid
    if key and (st == NO_TREE or _working(st)):
        return ('backlog.py -C <checkout> --session %s release %s --reason "<why>" (refused unless '
                'quiet: 4 h, no process, lock or queued verdict)' % (key, rid))
    return 'ask the PM'


def _working(st):
    """Whether `st` is a branch with a worktree some probe calls live (`live:` or `IDLE`)."""
    return st.startswith('live: ') or st.startswith(IDLE + ' ')


def render(recs, v):
    """One line per pulled record; the last commit date and commits ahead are read for a branch
    that exists."""
    out = []
    for r in recs:
        branch, session = r.get('pulled_by'), r.get('pulled_session')
        st = state(branch, v)
        seen = ''
        if st in (REMOTE, MAIN, NO_TREE, DEAD) or _working(st):
            date, ahead = v.info(branch, branch in v.local)
            seen = '  last %s  ahead %s' % (date, ahead)
        out.append('%s  %s%s  session=%s  %s  -> %s'
                   % (r.id, branch or '-', seen, session or 'none', st,
                      route(st, r.id, v.quiet(r))))
    return out


def lines(ctx, snap):
    """The lines of `list --orphans` for `snap`'s pulled records."""
    recs = [r for r in snap.readable if r.get('state') == 'pulled']
    if not recs:
        return []
    names, why = ctx.live_branches(snap)
    trees, twhy = gitrepo.worktrees(ctx.root)
    # Only the pulled branches are checked; a git that cannot answer leaves a branch non-local
    # (`remote only`, whose route is the PM).
    local = {b for b in {r.get('pulled_by') for r in recs} & (names or set())
             if refs.ref_commit('refs/heads/%s' % b, ctx.root)[0]}
    on = None if trees is None else {t.branch for t in trees[1:] if t.branch}
    base = refs.default_base(ctx.root, ctx.code_remote, ctx.code_main)[0]
    bad = {r.get('pulled_by') for r in recs
           if r.get('pulled_by') and plan.branch_name_error(r.get('pulled_by'), ctx.root)}

    def info(branch, is_local):
        ref = ('refs/heads/%s' if is_local else 'refs/remotes/%s/%%s' % ctx.remote) % branch
        date, dwhy = cmd.run_text(['log', '-1', '--format=%cs', ref, '--'], ctx.root)
        ahead, awhy = cmd.run_text(['rev-list', '--count', '%s..%s' % (base, ref)], ctx.root)
        return date or '? (%s)' % dwhy, ahead or '? (%s)' % awhy

    def no_probe(_branch):          # opener.open_store gives a Context no probe in some modes
        return 'no liveness probe on this Context', None

    main = []                           # the code main's commit, read on the first idle ask

    def idle(branch):
        if ctx.quiet is None:
            return 'no quiet probe on this Context', None
        if not main:
            main.append(plan_claim._main_commit(ctx))
        return ctx.quiet(branch, main[0])

    def quiet(rec):
        return ctx.session if plan_claim.quiet_key_why(ctx, rec) is None else None
    v = View(names, local, on, trees[0].branch if trees else None, bad, ctx.liveness or no_probe,
             idle, info, why or twhy, quiet)
    return render(recs, v)
