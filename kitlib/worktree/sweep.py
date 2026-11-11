"""The nightly sweep's checks (docs/worktree-remove.md); the sweep itself is
kitlib.worktree.nightly, which acts on classify's list. Here: the run-wide reads it fails closed
on (the landing lock, `landing_hold`; the fetch of the base, `fetch_base`; the machine lock,
`machine_lock_entries`), the reads classify shares (`machine_lock_holder`, `last_activity`), and
the checks of a stale-marked tree before the sweep abandons it (`sweep_skip`, `sweep_status`).
"""
import json
import os
import subprocess
import sys

from kitlib import kitroot
from kitlib.config import ConfigError
from kitlib.proc import spawn
from kitlib.records import landing

from . import holders, marks, remove_checks
from .holders import canon, extended, plain, prune, WALK_MAX
from .remove_checks import failure, git

SWEEP_IDLE_S = 24 * 3600            # idle 24h+
FETCH_TIMEOUT_S = 300


def fetch_args(s):
    """`git fetch` of the base with an explicit refspec, so the tracking ref is updated whatever
    the remote's configured fetch refspec says; `+`: the base reads as it stands on the remote."""
    return ('fetch', '--no-tags', s['remote'],
            '+refs/heads/%s:%s' % (s['base'], remove_checks.merge_base_ref(s)))


def fetch_base(ctx, timeout=FETCH_TIMEOUT_S):
    """Update the base from its remote. None when it worked, else why. Never prompts
    (GIT_TERMINAL_PROMPT=0, GCM_INTERACTIVE=never): a scheduled task has no terminal."""
    args = fetch_args(ctx.s)
    rc, _, err = git(ctx.main, *args, timeout=timeout,
                     env=dict(remove_checks.git_env(), GIT_TERMINAL_PROMPT='0'))
    if rc != 0:
        return failure(args, rc, err)
    return None


def landing_hold(ctx):
    """None when the landing lock is free, else why the sweep defers: held, unreadable, or its
    place unknown. The lock is kitlib.records.landing's (`<common dir>/kit-landing.lock`),
    the one every landing takes."""
    try:
        lock = landing.LandingLock.for_config(ctx.main, ctx.cfg)
    except (ConfigError, landing.LockError) as e:
        return 'the landing lock cannot be placed (%s)' % e
    try:
        held = lock.read()
    except landing.LockError as e:
        return 'the landing lock %s is there, so counted as held (%s)' % (lock.path, e)
    if held is None:
        return None
    return 'the landing lock %s is held (%s)' % (lock.path, landing.describe(held))


def machine_lock_path(kit=None):
    """The kit's own lock tool, `<kit>/tools/machine_lock.py` (kitlib.kitroot.lock_tool; with no
    `kit`, the checkout this kitlib is in)."""
    return kitroot.lock_tool(kit)


def machine_lock_entries(run=spawn.run, kit=None):
    """`(entries, None)`: every record `machine_lock.py status --json` lists - its `lock.held`,
    `waiting` and `background` lists, or (the kit lock's snapshot) its `runs` and `queue.entries`;
    or `(None, why)` when it cannot be read: the sweep then defers (fail closed)."""
    path = machine_lock_path(kit)
    if not os.path.isfile(path):
        return None, 'the machine lock tool is not at %s' % path
    try:
        r = run([sys.executable, '-B', path, 'status', '--json'], capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, 'machine_lock.py status --json did not run: %s' % e
    if r.returncode != 0:
        return None, 'machine_lock.py status --json exited %d: %s' % (
            r.returncode, (r.stderr or r.stdout or '').strip()[:300] or 'nothing printed')
    try:
        data = json.loads(r.stdout)
    except ValueError as e:
        return None, 'machine_lock.py status --json printed no JSON (%s)' % e
    return entries_of(data)


def entries_of(data):
    """The records of a `status --json` document, either shape; `(None, why)` for any other."""
    lock = data.get('lock') if isinstance(data, dict) else None
    if isinstance(lock, dict) and isinstance(lock.get('held'), list):
        entries = []
        for key in ('held', 'waiting', 'background'):
            value = lock.get(key, [])
            if not isinstance(value, list):
                return None, 'machine_lock.py status --json: `lock.%s` is not a list' % key
            entries += value
        return entries, None
    if isinstance(data, dict) and isinstance(data.get('runs'), list):
        queue = data.get('queue', {})
        waiting = queue.get('entries', []) if isinstance(queue, dict) else None
        if not isinstance(waiting, list):
            return None, 'machine_lock.py status --json: `queue.entries` is not a list'
        return list(data['runs']) + waiting, None
    return None, 'machine_lock.py status --json has neither a `lock.held` nor a `runs` list'


def machine_lock_holder(entries, path, branch):
    """The first machine lock record that names this worktree, as a printable line, or None. Names
    means: any string field contains, ignoring case, the worktree's folder name, its branch, or its
    path (either slash). Over-matching only skips more."""
    flat = os.path.normcase(os.path.normpath(path))
    needles = {os.path.basename(flat).lower(), branch.lower(), flat.lower(),
               flat.lower().replace('\\', '/')}
    needles.discard('')
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for value in entry.values():
            if not isinstance(value, str):
                continue
            text = value.lower()
            if any(n in text or n in text.replace('/', '\\') for n in needles):
                return '%s/%s run %s (%s)' % (entry.get('repo', '?'), entry.get('slug', '?'),
                                              entry.get('id', '?'), entry.get('note') or 'no note')
    return None


def last_activity(main, path, branch):
    """`((newest time, what), None)`, or `(None, why)` when it cannot be read in full: the mtime of
    everything in the tree; everything in the worktree's own git dir except `index`, `index.lock`
    and the dir's own mtime (a status rewrites the index: a read, not work); the branch's reflog;
    the branch tip's committer date. A tree that cannot be read in full is never called idle."""
    best = [0.0, None]

    def see(when, what):
        if when > best[0]:
            best[0], best[1] = when, what

    def walk(root, label, skip=()):
        errors = []
        top = extended(root)
        seen = 0
        for where, dirs, files in os.walk(top, onerror=errors.append):
            seen += len(dirs) + len(files)
            if seen > WALK_MAX:
                return '%s could not be read in full: more than %d entries' % (label, WALK_MAX)
            for name in dirs + files:
                if where == top and name in skip:
                    continue
                full = os.path.join(where, name)
                try:
                    see(os.lstat(full).st_mtime, plain(full))
                except OSError as e:
                    errors.append(e)
            prune(where, dirs)
        try:
            when = os.lstat(top).st_mtime
            if not skip:
                see(when, plain(top))
        except OSError as e:
            errors.append(e)
        if errors:
            return '%s could not be read in full (%d error(s), first: %s)' % (
                label, len(errors), errors[0])
        return None

    why = walk(path, 'the tree')
    if why:
        return None, why
    rc, gitdir, err = git(path, 'rev-parse', '--path-format=absolute', '--git-dir')
    if rc != 0 or not gitdir:
        return None, failure(('rev-parse', '--git-dir'), rc, err)
    why = walk(os.path.normpath(gitdir), 'its git dir', skip=('index', 'index.lock'))
    if why:
        return None, why
    rc, common, err = git(main, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if rc != 0 or not common:
        return None, failure(('rev-parse', '--git-common-dir'), rc, err)
    reflog = os.path.join(os.path.normpath(common), 'logs', 'refs', 'heads', *branch.split('/'))
    try:
        see(os.lstat(reflog).st_mtime, 'the reflog of %s' % branch)
    except FileNotFoundError:
        pass
    except OSError as e:
        return None, 'the reflog %s cannot be read (%s)' % (reflog, e)
    args = ('log', '-1', '--format=%ct', 'refs/heads/' + branch, '--')
    rc, stamp, err = git(main, *args)
    if rc != 0 or not stamp.isdigit():
        return None, failure(args, rc, err)
    see(float(stamp), 'the commit date of %s' % branch)
    return (best[0], best[1]), None


def sweep_skip(ctx, entry, base, ancestors, entries, machine, now, idle_s, mark):
    """Why the UNLANDED, stale-marked tree `entry` is NOT abandoned, or None when every check
    before the holder probe passed. Cheapest checks first; the first that fails is the reason
    printed. `mark` is the stale mark of its branch (kitlib.worktree.marks). A landed tree is
    never here: classify decides it."""
    s, main = ctx.s, ctx.main
    path, branch, head = entry['path'], entry['branch'], entry['head']
    key = canon(path)
    if key == canon(main):
        return 'the main checkout: never touched'
    if not remove_checks.is_workstream(ctx, path):
        return 'not under %s: only workstream worktrees are abandoned' % s['worktrees_dir']
    if remove_checks.exemption(s, path):
        return 'exempt (%s)' % remove_checks.exemption(s, path)
    if branch is None:
        return 'detached HEAD: no branch, never abandoned'
    if not os.path.isdir(path):
        return 'its directory is missing (an orphan: `--check` reports it)'
    if entry['locked'] is not None:
        return 'locked with `git worktree lock`%s' % (
            ': ' + entry['locked'] if entry['locked'] else '')
    if not head:
        return 'git reported no HEAD for it'
    if head in ancestors:
        return 'its tip is in the base: classify decides a landed tree'
    void = marks.void_reason(mark, path, head)
    if void:
        rc, count, _ = git(main, 'rev-list', '--count', '%s..%s' % (base, head))
        return 'UNLANDED: %s has %s commit(s) not in %s %s; its stale mark is void: %s' % (
            branch, count if rc == 0 and count else 'an unknown number of', s['upstream'],
            base[:10], void)
    others = sorted(os.path.normpath(e['path']) for e in entries
                    if e['branch'] == branch and canon(e['path']) != key)
    if others:
        return 'branch %s is also checked out at %s' % (branch, ', '.join(others))
    holder = machine_lock_holder(machine, path, branch)
    if holder:
        return 'machine lock holder: %s' % holder
    ids, why = marks.claims(ctx, base, branch)
    if ids is None:
        return 'the open-claim check failed (%s)' % why
    if ids:
        return 'a backlog record is still pulled by %s (an open claim): %s' % (
            branch, ', '.join(ids))
    got, why = last_activity(main, path, branch)
    if got is None:
        return 'its last activity cannot be read (%s)' % why
    when, what = got
    void = marks.void_reason(mark, path, head, got)
    if void:
        return 'UNLANDED; its stale mark is void: %s' % void
    if now - when < idle_s:
        return 'active %.1f h ago (newest: %s), under the %d h idle bar' % (
            max(now - when, 0) / 3600.0, what, idle_s // 3600)
    return sweep_status(ctx, path)


def sweep_status(ctx, path):
    """Why the status of `path` stops the sweep, or None when it is clean. Read in `sweep_skip`
    and AGAIN just before the abandon."""
    work, disposable, ignored, why = remove_checks.dirty(ctx, path)
    if work is None:
        return 'its status could not be read (%s)' % why
    # disposable_untracked counts here: the sweep deletes nothing but the tree git removes.
    lines = work + ['?? ' + rel for rel in disposable]
    if lines:
        return 'uncommitted or untracked files (%d): %s%s' % (
            len(lines), '; '.join(lines[:5]), ' ...' if len(lines) > 5 else '')
    if ignored:
        return remove_checks.ignored_reason(ignored, path)
    return holders.link_reason(path)
