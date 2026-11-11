"""The backlog's probes of a pulled branch's worktree (docs/modules/worktree-design.md "Contract"):
branch liveness (classify.branch_liveness) and the quiet hold of a quiet release (quiet_hold).
The backlog imports nothing above itself, so a caller that opens a store hands them in:
`kitlib.backlog.opener.open_store(root, ..., probes=PROBES)` binds them to the store's repository
as plan.Context's `liveness` and `quiet` (docs/backlog.md "Stopping a pull"). Read-only.
"""
import json
import os
import re
import time

from kitlib.git import repo as gitrepo
from kitlib.records import verdict_entry

from . import classify, holders, remove_checks, sweep


# A quiet release (plan_release): the holder's tree shows no activity for this long.
QUIET_AFTER_S = 4 * 3600
_REFLOG_STAMP_RE = re.compile(r'@\{(\d+)\}$')


def quiet_hold(root, branch, now=None, machine=None, procs=None, main=None):
    """`(why it is not quiet, None)` or `(None, seconds quiet)`: whether the pull on `branch` may be
    released as quiet (plan_release; docs/backlog.md "Stopping a pull"). Quiet means: no process
    has its current directory in a worktree on `branch` (and, when some process could not be
    read, every folder of it opens for delete: holders.held_folder), no such tree is exempt
    (remove_checks.exemption) or locked with `git worktree lock`, no machine lock record names
    it (sweep.machine_lock_holder), the landing lock does not name `branch`, no verdict-queue
    entry for `branch` waits (kitlib.records.verdict_entry: one that has not expired with `main`, the code
    main's commit, None: every entry waits), and the newest activity is QUIET_AFTER_S old or more.
    Activity is the tip's committer date, the branch's reflog entry times and the mtimes in the
    worktree's tree, its top-level `.git` left out: never the git dir's own files
    (sweep.last_activity reads those, and a status rewrites them). A branch the main checkout is
    on, no local branch, and anything that cannot be read are not quiet. `machine` and `procs`
    (None: sweep.machine_lock_entries, classify.process_cwds) are the run-wide reads
    classify.run_probes takes. Read-only."""
    wctx, refused = remove_checks.open_ctx(root)
    if refused:
        return 'the worktrees cannot be read (%s)' % ' '.join(refused.lines), None
    try:
        listing = remove_checks.worktree_listing(wctx.main)
    except remove_checks.GitReadError as e:
        return 'the worktrees cannot be read (%s)' % e, None
    entries = remove_checks.worktree_entries(listing)
    if entries and entries[0]['branch'] == branch:
        return 'the main checkout is on %s' % branch, None
    on = [e for e in classify.trees_besides_main(wctx.main, listing) if e['branch'] == branch]
    probes, _ = classify.run_probes(wctx, machine or sweep.machine_lock_entries,
                                    procs or classify.process_cwds)
    run_wide, landing, held, cwds = probes
    if run_wide:
        return 'unreadable: %s' % run_wide, None
    if branch in landing:
        return 'the landing lock names %s' % branch, None
    for entry in on:
        path = entry['path']
        # classify.live_reason's own holds on a tree: a worktree_remove exemption ruling and
        # `git worktree lock` (an entry that cannot say is taken as locked: fail closed).
        ruling = remove_checks.exemption(wctx.s, path)
        if ruling:
            return 'the tree %s is exempt (%s)' % (os.path.normpath(path), ruling), None
        if entry.get('locked', '') is not None:
            return ('the tree %s is locked with `git worktree lock`%s'
                    % (os.path.normpath(path),
                       ': ' + entry['locked'] if entry.get('locked') else '')), None
        holder = sweep.machine_lock_holder(held, path, branch)
        if holder:
            return 'machine lock: %s' % holder, None
        target = (entry.get('canon') or holders.canon(path)).rstrip('\\/')
        for pid, name, forms in cwds[0]:
            if any(f == target or f.startswith(target + os.sep) for f in forms):
                return ('process pid %d %s has its current directory in %s'
                        % (pid, name, os.path.normpath(path))), None
        if cwds[1] and holders.held_folder(path) is not None:
            return ('%d process(es) could not be read, and not every folder of %s opens for '
                    'delete: one may hold it' % (cwds[1], os.path.normpath(path))), None
    if not on:
        holder = sweep.machine_lock_holder(held, branch, branch)
        if holder:
            return 'machine lock: %s' % holder, None
    why = _queued_verdict(verdict_entry, wctx.main, branch, main)
    if why:
        return why, None
    newest, why = _quiet_activity(remove_checks, wctx.main, branch, on)
    if newest is None:
        return why, None
    now = time.time() if now is None else now
    quiet = now - newest[0]
    if quiet < QUIET_AFTER_S:
        return ('active %.1f h ago (newest: %s), under the %d h quiet bar'
                % (max(quiet, 0) / 3600.0, newest[1], QUIET_AFTER_S // 3600)), None
    return None, quiet


def _queued_verdict(verdict_entry, main_tree, branch, main):
    """Why a verdict-queue entry for `branch` holds the pull, or None: there is none, or it has
    expired (landed or superseded). An entry that cannot be read holds it."""
    common, why = gitrepo.common_dir(main_tree)
    if common is None:
        return 'the verdict queue cannot be placed (%s)' % why
    try:
        path = verdict_entry.entry_path(common, branch)
    except verdict_entry.VerdictError as e:
        return 'the verdict queue entry for %s cannot be named (%s)' % (branch, e)
    try:
        with open(path, encoding='utf-8') as f:
            text = f.read()
    except FileNotFoundError:
        return None
    except OSError as e:
        return 'the verdict queue entry for %s cannot be read (%s)' % (branch, e)
    try:
        entry = json.loads(text)
    except ValueError as e:
        return 'the verdict queue entry for %s is not JSON (%s)' % (branch, e)
    bad = verdict_entry.check(entry)
    if bad:
        return 'the verdict queue entry for %s is malformed (%s)' % (branch, bad)
    if verdict_entry.expired(common, entry, main):
        return None
    return 'a verdict for %s waits in the landing queue (%s at %s)' % (
        branch, entry.get('state'), str(entry.get('tip'))[:10])


def _quiet_activity(remove_checks, main_tree, branch, on):
    """`((newest time, what), None)` or `(None, why)`: quiet_hold's activity reads."""
    ref = 'refs/heads/%s' % branch
    args = ('log', '-1', '--format=%ct', ref, '--')
    rc, stamp, err = remove_checks.git(main_tree, *args)
    if rc != 0 or not stamp.isdigit():
        return None, 'no local branch %s to read (%s)' % (branch, remove_checks.failure(args, rc, err))
    found = [(float(stamp), 'the commit date of %s' % branch)]
    args = ('reflog', 'show', '--date=unix', '--format=%gd', ref, '--')
    rc, out, err = remove_checks.git(main_tree, *args)
    if rc != 0:
        return None, 'its reflog cannot be read (%s)' % remove_checks.failure(args, rc, err)
    for line in out.splitlines():
        got = _REFLOG_STAMP_RE.search(line.strip())
        if got:
            found.append((float(got.group(1)), 'a reflog entry of %s' % branch))
    for entry in on:
        # The --classify walk with the top-level `.git` (a linked tree's file, or a git dir) left
        # out: the git dir's mtimes are not work.
        got, why = sweep.newest(entry['path'], 'the tree', skip=('.git',))
        if got is None:
            return None, why
        found.append(got)
    return max(found, key=lambda pair: pair[0]), None


# What opener.open_store takes as `probes`: (liveness(top, branch), quiet(top, branch, main=)).
PROBES = (classify.branch_liveness, quiet_hold)
