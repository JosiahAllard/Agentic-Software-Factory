"""kitlib.dailyfull.promote: the staging branch's promotion (docs/daily-full.md "Promotion").

With `.kit.json` "staging".branch set on `<remote>/<main>`, a green daily run at a commit of the
staging branch fast-forwards the main branch to exactly that commit: a plain
`git push <remote> <sha>:refs/heads/<main>`, never forced, never past the repository's pre-push
hook. What the push did is read back from the remote (`ls-remote`), never inferred from git's
exit code. Nothing here moves the main checkout's local main or its working tree.

Every local git read goes through the caller's `git(cwd, *args) -> (rc, out, err)`
(kitlib.dailyfull.git), so a test can stand in for it; the push and the remote read are
parameters too. Nothing here prints or exits.
"""
import copy
import os

import kitlib.config as kconfig
from kitlib.config.load import CONFIG_NAME
from kitlib.config.types import ConfigError
from kitlib.git import cmd as gitcmd
from kitlib.git import refs

DUE_COUNT = 4            # landings on staging not yet tested: an --if-due run is due at this many
DUE_HOURS = 6.0          # or when the oldest of them is this many hours old
PUSH_TIMEOUT = 600       # seconds; the landing's own push timeout (kitlib.landing.merge)
OK = ('promoted', 'current')     # the statuses that exit 0; every other one exits 4
# A `show` of a path the commit does not hold (kitlib.config.load._git_show's words): no .kit.json
MISSING = ('does not exist in', 'exists on disk, but not in')


def main_of(cfg):
    """The main branch itself, never the work base: the branch a promotion moves and the one
    `<remote>/<main>` the staging setting is read from (docs/gate-landing.md "Staging branch").
    The one read of it in kitlib.dailyfull."""
    return cfg.main_branch


def staging_read(main, cfg, git):
    """`(cfg2, None)`: `cfg` with the "staging" section `<remote>/<main>` as fetched writes
    (kitlib.config.overlay_staging, on a copy; `cfg` is unchanged). `(None, None)` when that
    cannot be read - the ref does not resolve, or git cannot show its `.kit.json` - so promotion
    is off and the run is as it was (fail closed). `(None, why)` when the overlay itself fails:
    a section there that is not well formed, or its own read of the file failing after this one
    succeeded; that refuses the run."""
    rc, sha, _ = git(main, 'rev-parse', '--verify', '-q',
                     'refs/remotes/%s/%s^{commit}' % (cfg.remote, main_of(cfg)))
    if rc != 0 or not sha:
        return None, None
    rc, _, err = git(main, 'show', '%s:%s' % (sha, CONFIG_NAME))
    if rc != 0 and not any(m in (err or '') for m in MISSING):
        return None, None
    two = copy.copy(cfg)
    try:
        kconfig.overlay_staging(two, sha)
    except ConfigError as e:
        return None, 'the staging setting on %s/%s: %s' % (cfg.remote, main_of(cfg), e)
    return two, None


def last_tested(results):
    """The sha of the latest run that tested a commit (a skip, or a result with no sha, is not
    one), or None. `results`: kitlib.dailyfull.results' list, oldest first."""
    for r in reversed(results):
        if r.get('verdict') != 'skipped' and r.get('sha'):
            return r['sha']
    return None


def untested(main, cfg, staging, results, git):
    """`((count, oldest), None)`: the first-parent commits of `<remote>/<staging>` past the last
    tested commit (or past `<remote>/<main>` when there is none, or it is not an ancestor of the
    staging tip), and the committer time (epoch seconds) of the oldest of them, None when there
    are none. `(None, why)` when git cannot say."""
    tip = '%s/%s' % (cfg.remote, staging)
    base = last_tested(results)
    if base is not None and git(main, 'merge-base', '--is-ancestor', base, tip)[0] != 0:
        base = None
    base = base or '%s/%s' % (cfg.remote, main_of(cfg))
    rc, out, err = git(main, 'log', '--first-parent', '--format=%ct', '%s..%s' % (base, tip))
    if rc != 0:
        return None, 'git log %s..%s failed: %s' % (base, tip, err or 'exit %d' % rc)
    times = [int(t) for t in out.split()]
    return (len(times), min(times) if times else None), None


def due(count, oldest, now):
    """Whether an --if-due run is due: DUE_COUNT untested commits, or the oldest of them
    DUE_HOURS old (`oldest` and `now` in epoch seconds). Pure."""
    if count >= DUE_COUNT:
        return True
    return bool(count) and oldest is not None and (now - oldest) / 3600.0 >= DUE_HOURS


def push_args(remote, sha, main_b):
    """The one push a promotion makes: no force, no lease, no --no-verify, no delete."""
    return ['push', '--quiet', remote, '%s:refs/heads/%s' % (sha, main_b)]


def _push(args, cwd):
    """`(rc, err)` of the push: the landing's timeout, and NETWORK_ENV so a credential prompt
    fails instead of waiting. rc None: git did not start or did not answer in time."""
    env = dict(gitcmd.scrubbed_env(), **gitcmd.NETWORK_ENV)
    r = gitcmd.call(args, cwd, timeout=PUSH_TIMEOUT, env=env)
    return r.rc, r.err or ''


def _anc(git, cwd, a, b):
    return git(cwd, 'merge-base', '--is-ancestor', a, b)[0] == 0


def _known(git, cwd, sha):
    return bool(sha) and git(cwd, 'cat-file', '-e', '%s^{commit}' % sha)[0] == 0


def promote(wt, cfg, sha, staging, git, push=None, ls_remote=None):
    """Fast-forward `<remote>/<main>` to `sha`, a commit a green daily run tested, read in the
    daily worktree `wt`. A dict `{status, main, from, to, why}`; status is one of:
      promoted     the remote's main is `sha` after the push
      current      the remote's main already holds `sha`: nothing to do (no push)
      off-staging  `sha` is not on `<remote>/<staging>`: never pushed
      diverged     the remote's main has commits that are not on `sha`: never pushed
      not-promoted the remote's main is still behind `sha` after the push (git's first line)
      unknown      the remote's main cannot be read or placed after the push
    """
    push, ls_remote = push or _push, ls_remote or refs.ls_remote
    remote, main_b = cfg.remote, main_of(cfg)
    got = {'status': None, 'main': main_b, 'from': None, 'to': sha, 'why': None}

    def done(status, why=None):
        got['status'], got['why'] = status, why
        return got
    rc, _, err = git(wt, 'fetch', remote, '--quiet')
    if rc != 0:
        return done('not-promoted', 'git fetch %s failed: %s' % (remote, err or 'exit %d' % rc))
    on = '%s/%s' % (remote, staging)
    if not _anc(git, wt, sha, on):
        return done('off-staging', '%s is not on %s' % (sha[:10], on))
    rc, old, err = git(wt, 'rev-parse', '--verify', '-q', 'refs/remotes/%s/%s' % (remote, main_b))
    if rc != 0 or not old:
        return done('unknown', '%s/%s cannot be read here' % (remote, main_b))
    got['from'] = old
    if _anc(git, wt, sha, old):
        return done('current', '%s/%s already holds %s' % (remote, main_b, sha[:10]))
    if not _anc(git, wt, old, sha):
        return done('diverged', _diverged(git, wt, old, sha, remote, main_b, staging))
    prc, perr = push(push_args(remote, sha, main_b), wt)
    said = next((l.strip() for l in perr.splitlines() if l.strip()), None)
    said = said or ('exit %s' % prc if prc else 'git exited 0')
    now, why = ls_remote(remote, 'refs/heads/%s' % main_b, wt)
    if now is None:
        return done('unknown', 'the push said "%s", and %s/%s cannot be read: %s' % (
            said, remote, main_b, why))
    if now == sha:
        return done('promoted')
    if not _known(git, wt, now):
        return done('unknown', 'the push said "%s", and %s/%s is %s, which this checkout cannot '
                    'place' % (said, remote, main_b, now[:10] or 'absent'))
    if _anc(git, wt, sha, now):
        return done('current', '%s/%s moved on to %s, which holds %s' % (
            remote, main_b, now[:10], sha[:10]))
    if _anc(git, wt, now, sha):
        return done('not-promoted', 'the push said "%s"; %s/%s is still %s' % (
            said, remote, main_b, now[:10]))
    return done('diverged', _diverged(git, wt, now, sha, remote, main_b, staging))


def _diverged(git, wt, main_at, sha, remote, main_b, staging):
    rc, n, _ = git(wt, 'rev-list', '--count', '%s..%s' % (sha, main_at))
    return '%s/%s (%s) has %s commit(s) that are not on %s (%s)' % (
        remote, main_b, main_at[:10], n if rc == 0 else 'some', staging, sha[:10])


def line(p):
    """The run's line for promotion dict `p`."""
    if p['status'] == 'promoted':
        return 'daily: PROMOTED %s %s..%s' % (p['main'], (p['from'] or '?')[:10], p['to'][:10])
    if p['status'] == 'current':
        return 'daily: promotion: nothing to do - %s' % p['why']
    return 'daily: PROMOTION %s - %s' % (p['status'].upper(), p['why'])


def local_behind(main, cfg, git):
    """The read-only status line when staging is on at `<remote>/<main>` and the main checkout's
    local main is behind `<remote>/<main>` (nothing moves it in staging mode, and tools that read
    the main checkout's `.kit.json` see a promoted change only once it is merged there). None
    otherwise, a git that cannot say included."""
    two, _ = staging_read(main, cfg, git)
    if two is None or not kconfig.staging_on(two):
        return None
    m = main_of(cfg)
    r = '%s/%s' % (cfg.remote, m)
    rc, n, _ = git(main, 'rev-list', '--count', 'refs/heads/%s..refs/remotes/%s' % (m, r))
    if rc != 0 or not n.isdigit() or int(n) == 0:
        return None
    return 'daily     local %s is %s behind %s: git merge --ff-only %s' % (m, n, r, r)
