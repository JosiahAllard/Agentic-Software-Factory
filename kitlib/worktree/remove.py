"""Remove a workstream's worktree only when its work has landed and nothing holds it
(docs/worktree-remove.md). tools/worktree_remove.py is the entry point; the checks are
kitlib.worktree.remove_checks, abandoning unlanded work under a tag is
kitlib.worktree.remove_abandon.

`remove(top, argument)` and `remove_branch(top, argument)` act on the MAIN checkout of the
repository `top` is in (the main checkout or any linked worktree) and return Outcome(code, lines).
Nothing here prints. Once every check (kitlib.worktree.remove_checks) has passed, the
`disposable_untracked` files are deleted, the status is read once more (`last_look`, inside
`git_remove`: every caller gets it), `git worktree remove` runs (never forced, never retried) and
the branch goes with `git branch -d` (never -D).

`remove_branch` deletes a LANDED local branch that no worktree has checked out and no backlog record
still pulls, with `git branch -d` (never -D). `prune_orphan` drops the registration of a worktree
whose folder is already gone, keeping its branch, when its admin folder keeps no commit no ref
reaches.
"""
import os

from kitlib.backlog import store as backlog_store
from kitlib.backlog.errors import Failure as BacklogFailure
from kitlib.config import ConfigError
from kitlib.git import refs

from . import holders
from .holders import canon
from .remove_checks import (ARCHIVE, base_commit, dirty, dirty_refusal, exemption, failure, git,
                            GitReadError, handle, ignored_reason, is_workstream, kept_reflog,
                            merge_check, OK, open_ctx, REFUSED, registered, registered_target,
                            registrations, same_name, unmoved, worktree_entries, worktree_listing)


def delete_branch(ctx, branch, keep, removed=True):
    """After a removal (`removed`) or on its own: `git branch -d`, never -D. Returns the exit
    status."""
    if branch is None:
        ctx.say('branch    none: it was a detached HEAD')
        return OK
    if keep:
        ctx.say('kept      branch %s (--keep-branch)' % branch)
        return OK
    rc, out, err = git(ctx.main, 'branch', '-d', branch)
    if rc == 0:
        ctx.say('deleted   branch %s (%s)' % (branch, out or 'git branch -d'))
        return OK
    ctx.say('failed    git branch -d %s exited %s: %s' % (branch, rc, err or out),
            '  kept     branch %s is still there; %s' % (
                branch, 'the worktree removal above stands and is not undone.' if removed
                else 'nothing was changed.'),
            '           `-d` checks the main checkout\'s HEAD, not %s: if that checkout is'
            % ctx.s['upstream'],
            '           behind, pull it, then `git branch -d %s`. Never -D.' % branch)
    return REFUSED


def unsaved_count(ctx, commits, also_saved=()):
    """How many commits reached from `commits` no branch, remote-tracking branch, tag or
    `also_saved` commit reaches: `(n, None)`, or `(None, why)`."""
    args = ('rev-list', '--count', *commits, '--not', *also_saved, '--branches', '--remotes',
            '--tags')
    rc, count, err = git(ctx.main, *args)
    if rc != 0 or not count.isdigit():
        return None, failure(args[:2], rc, err)
    return int(count), None


# The pseudo-refs a worktree's admin folder may hold besides its reflogs and per-worktree refs.
ADMIN_HEADS = ('HEAD', 'ORIG_HEAD', 'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD')
HEX = frozenset('0123456789abcdef')


def _oid(word):
    return len(word) in (40, 64) and set(word) <= HEX


def admin_dir(ctx, path):
    """`(the admin folder <git common dir>/worktrees/<id> whose gitdir file names `path`, None)`,
    or `(None, why)`: none or more than one does, or a read failed."""
    args = ('rev-parse', '--path-format=absolute', '--git-common-dir')
    rc, common, err = git(ctx.main, *args)
    if rc != 0 or not common:
        return None, failure(args, rc, err)
    top = os.path.join(common, 'worktrees')
    try:
        names = sorted(os.listdir(top))
    except OSError as e:
        return None, 'the admin folders in %s could not be listed (%s)' % (top, e)
    found, key = [], canon(path)
    for name in names:
        gitdir = os.path.join(top, name, 'gitdir')
        try:
            with open(gitdir, encoding='utf-8', errors='replace') as fh:
                named = fh.read().strip()
        except FileNotFoundError:
            continue
        except OSError as e:
            return None, '%s could not be read (%s)' % (gitdir, e)
        if named and canon(os.path.dirname(named)) == key:
            found.append(os.path.join(top, name))
    if len(found) != 1:
        return None, ('%s admin folder in %s names it%s' % (
            'more than one' if found else 'no', top, ' (%s)' % ', '.join(found) if found else ''))
    return found[0], None


def admin_commits(ctx, admin):
    """`([every commit id the admin folder `admin` alone may keep], None)` or `(None, why)`: its
    HEAD and pseudo-refs (ADMIN_HEADS) when they hold an id, every id in its reflogs (`logs/`)
    and the tips of its per-worktree refs (`refs/`). `git worktree remove` deletes them all. Read
    from the files only for the files ref format; any other format, and a line that is not an id,
    a symbolic ref or a reflog entry, refuses."""
    args = ('rev-parse', '--show-ref-format')
    rc, out, err = git(ctx.main, *args)
    if rc != 0 or not out:
        return None, failure(args, rc, err)
    if out != 'files':
        return None, 'its refs are stored as %s, whose admin folder this verb cannot read' % out
    files, errors = [os.path.join(admin, n) for n in ADMIN_HEADS], []
    for sub in ('refs', 'logs'):
        root = os.path.join(admin, sub)
        if os.path.isdir(root):
            for folder, _, names in os.walk(root, onerror=errors.append):
                files.extend(os.path.join(folder, n) for n in sorted(names))
        elif os.path.lexists(root):
            errors.append('%s is not a folder' % root)
    if errors:
        return None, 'its admin folder could not be read in full (%s)' % errors[0]
    oids = []
    for name in files:
        try:
            with open(name, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
        except FileNotFoundError:
            continue
        except OSError as e:
            return None, '%s could not be read (%s)' % (name, e)
        log = os.path.relpath(name, admin).split(os.sep)[0] == 'logs'
        for line in text.splitlines():
            if not line.strip():
                continue
            words = line.split('\t', 1)[0].split(' ')[:2] if log else \
                [] if line.startswith('ref: ') else line.split()[:1]
            if (log and len(words) != 2) or not all(_oid(w) for w in words):
                return None, '%s has a line this verb cannot read: %r' % (name, line)
            oids.extend(w for w in words if w.strip('0') and w not in oids)
    return oids, None


def orphan_unsaved(ctx, path):
    """`(how many commits only the admin folder of the missing tree at `path` keeps and no branch,
    remote-tracking branch or tag reaches, None)`, or `(None, why)`. Reads only."""
    admin, why = admin_dir(ctx, path)
    if admin is None:
        return None, why
    oids, why = admin_commits(ctx, admin)
    if oids is None:
        return None, why
    return unsaved_count(ctx, oids) if oids else (0, None)


def orphan_outside(ctx, path, environ=None):
    """Why the stale registration at `path` is reported only, or None when it is the kit's to
    drop: in a worktree folder (is_workstream) or under a scratch root (kitlib.worktree.scratch
    roots, from `environ`). A folder anywhere else may be on a drive that is not mounted now or
    was remapped: gone from this view only."""
    if is_workstream(ctx, path):
        return None
    from . import scratch           # scratch imports this module
    rts, _ = scratch.roots(environ, ctx.main)
    if any(scratch._strictly_under(path, r) for r in rts):
        return None
    return ('it is outside every worktree folder and scratch root (a drive not mounted now, or'
            ' remapped, looks the same): reported only')


def prune_orphan(ctx, path, environ=None):
    """Drop the registration of the worktree at `path` whose folder is already gone (`--classify`
    class O), keeping its branch: `git worktree remove <the registered path>`, never forced, never
    `git worktree prune` (which drops every such registration). Refused, nothing changed: a path
    that exists, a path outside the worktree folders and scratch roots (orphan_outside; `environ`
    names the roots), an exempt folder, a locked tree, the main checkout, and a registration whose
    admin folder keeps a commit no ref reaches (its HEAD reflog, a per-worktree ref or a
    pseudo-ref: the owner's call) or cannot be read. The folder and the admin folder are read once
    more just before git; a folder made inside that window is the window this narrows, not
    closes. A path no longer registered is `nothing` (OK)."""
    path = os.path.normpath(path)
    nothing = 'nothing was removed'
    if os.path.lexists(path):
        ctx.say('refused   %s exists: its registration is not stale; %s' % (path, nothing))
        return REFUSED
    outside = orphan_outside(ctx, path, environ)
    if outside:
        ctx.say('refused   %s: %s; %s' % (path, outside, nothing))
        return REFUSED
    ruling = exemption(ctx.s, path)
    if ruling:
        ctx.say('refused   %s is exempt (%s); %s' % (path, ruling, nothing))
        return REFUSED
    try:
        entries = worktree_entries(worktree_listing(ctx.main))
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s); %s' % (path, e, nothing))
        return REFUSED
    key = canon(path)
    entry = next((e for e in entries if canon(e['path']) == key), None)
    if entry is None:
        ctx.say('nothing   %s is not registered' % path)
        return OK
    if key == canon(ctx.main):
        ctx.say('refused   %s is the main checkout; %s' % (path, nothing))
        return REFUSED
    if entry['locked'] is not None:
        ctx.say('refused   %s is locked (%s); %s' % (path, entry['locked'] or 'no reason', nothing))
        return REFUSED
    admin, why = admin_dir(ctx, path)
    oids, why = admin_commits(ctx, admin) if admin else (None, why)
    lost, why = (None, why) if oids is None else unsaved_count(ctx, oids) if oids else (0, None)
    if lost is None:
        ctx.say('refused   %s: whether its registration keeps a commit no ref reaches is unknown'
                ' (%s); %s' % (path, why, nothing))
        return REFUSED
    if lost:
        ctx.say('refused   %s: its folder is gone, but its registration (%s) keeps %d commit(s)'
                ' no branch, remote-tracking branch or tag reaches (a HEAD reflog, per-worktree'
                ' ref or pseudo-ref); whether to drop them is the owner\'s call; %s'
                % (path, admin, lost, nothing))
        return REFUSED
    again, why = admin_commits(ctx, admin)
    moved = ('its folder appeared after it was checked' if os.path.lexists(path) else
             'its admin folder could not be read again (%s)' % why if again is None else
             'its admin folder changed after it was checked' if again != oids else None)
    if moved:
        ctx.say('refused   %s: %s; %s' % (path, moved, nothing))
        return REFUSED
    rc, out, err = git(ctx.main, 'worktree', 'remove', entry['path'])
    if rc != 0:
        ctx.say('failed    git worktree remove exited %s: %s' % (rc, err or out))
        return REFUSED
    try:
        still = key in registered(ctx.main)
    except GitReadError as e:
        ctx.say('failed    %s: whether its registration is gone is unknown: the worktree list'
                ' could not be read again (%s)' % (path, e))
        return REFUSED
    if still:
        ctx.say('failed    %s is still registered after git worktree remove exited 0' % path)
        return REFUSED
    ctx.say('removed   %s: its stale registration (the folder was already gone)%s'
            % (path, '; branch %s kept' % entry['branch'] if entry['branch'] else ''))
    return OK


def clear_disposable(ctx, path):
    """Delete the untracked files `disposable_untracked` names, the verbs' last step before
    `git_remove`. True when none is left; False after saying which could not go, or that ignored
    files appeared since."""
    _, disposable, ignored, why = dirty(ctx, path)
    if disposable is None:
        ctx.say('failed    %s: its status could not be read again (%s); git was NOT run'
                % (path, why))
        return False
    if ignored:
        ctx.say('failed    %s now has %s; git was NOT run' % (path, ignored_reason(ignored, path)))
        return False
    for rel in disposable:
        try:
            os.remove(os.path.join(path, rel))
        except OSError as e:
            ctx.say('failed    could not delete %s (%s); git was NOT run' % (rel, e))
            return False
    if disposable:
        ctx.say('cleared   %d untracked file(s) that are not work (.kit.json worktree_remove.'
                'disposable_untracked): %s' % (len(disposable), '; '.join(disposable[:5])))
    return True


def last_look(ctx, path):
    """The status read `git_remove` makes just before git. True when `dirty` lists nothing at all;
    False after saying what appeared since the caller's checks, or that the status cannot be read.
    git's own refusal names modified and untracked files and never an ignored one, so this read is
    all that keeps an ignored file written late. It narrows that window and does not close it: an
    ignored file written after this returns is deleted with the tree."""
    work, disposable, ignored, why = dirty(ctx, path)
    if work is None:
        ctx.say('failed    %s: its status could not be read again (%s); git was NOT run'
                % (path, why))
        return False
    # disposable_untracked counts here: the callers that clear such files have done it already.
    lines = work + ['?? ' + rel for rel in disposable]
    if lines:
        reason = 'uncommitted or untracked files (%d): %s%s' % (
            len(lines), '; '.join(lines[:5]), ' ...' if len(lines) > 5 else '')
    elif ignored:
        reason = ignored_reason(ignored, path)
    else:
        return True
    ctx.say('failed    %s changed after it was checked - %s; git was NOT run' % (path, reason))
    return False


def git_remove(ctx, path, registered_path):
    """`last_look`, then `git worktree remove`. True when it removed the tree; False after saying
    why git was not run or what is left (intact, or the entry destroyed) - never retried, never
    forced."""
    if not last_look(ctx, path):
        return False
    rc, out, err = git(ctx.main, 'worktree', 'remove', registered_path)
    if rc == 0:
        ctx.say('removed   %s' % path)
        return True
    ctx.say('failed    git worktree remove exited %s: %s' % (rc, err or out))
    if '--force' in (err or out):
        ctx.say('  NOTE     that message is git\'s, and its advice is a trap here: `--force` is the',
                '           run that destroys the administrative entry when the tree cannot be',
                '           deleted. Commit or clear the modified files instead, then run this again.')
    try:
        still = canon(path) in registered(ctx.main)
    except GitReadError as e:
        ctx.say('  unknown  whether the administrative entry survived: the worktree list could not'
                ' be',
                '           read again (%s). Run `git worktree list` before anything else.' % e)
        return False
    if still:
        ctx.say('  intact   the worktree is still registered; nothing was destroyed')
    else:
        ctx.say('  DESTROYED the administrative entry is gone and the directory is still there.',
                '           A retry cannot work (`not a working tree`); the slug is burned until a',
                '           human clears it. Something took hold between the check and the removal.')
        ctx.describe(path, holders.blockers(path))
    return False


def remove(top, argument, keep_branch=False, cwd=None):
    """The plain verb: remove a LANDED worktree that nothing holds, then `git branch -d` its branch."""
    ctx, refused = open_ctx(top, cwd)
    if refused:
        return refused
    return ctx.outcome(_remove(ctx, argument, keep_branch, branch_ok=True))


def _remove(ctx, argument, keep_branch, branch_ok=False):
    """The plain verb's checks. `branch_ok` (only `remove`) lets a tree on a branch outside the
    worktree folders through `outside_target`; the landing cleanup and classify leave it False."""
    found = registered_target(ctx, argument, branch_ok)
    if not isinstance(found, tuple):
        return found
    path, known = found
    key = canon(path)
    seen = {}
    refusal = merge_check(ctx, key, known[key][0], seen)
    if refusal:
        ctx.say(*refusal)
        if any('UNLANDED' in line for line in refusal):
            # The owner's printed commands are exactly as destructive on failure, so name any holder too.
            ctx.describe(path, holders.blockers(path))
            ctx.say('  note     release every holder first: the owner\'s `git worktree remove` above fails'
                    ' the same destructive way this tool refuses to risk.')
        return REFUSED
    # An outside tree, and a detached one anywhere: a commit made there and left by a checkout
    # lives only in its HEAD reflog, which `git worktree remove` deletes.
    logs = not is_workstream(ctx, path) or known[key][1] == '(detached)'
    if logs:
        # The HEAD merge_check found in the base: the reflog check and `unmoved` hold the tree to
        # it, so a HEAD moved since (to an unlanded tip) is refused, never judged afresh.
        head = seen.get('head')
        if not head:
            ctx.say('refused   %s: git reported no HEAD for it; nothing was removed' % path)
            return REFUSED
        logged = kept_reflog(ctx, path, head, False)
        if logged is None:
            return REFUSED
    why = holders.blockers(path)
    if why:
        ctx.say('refused   %s is held; git was NOT run' % path)
        ctx.describe(path, why)
        ctx.say('  kept     the worktree is still registered and intact, and still removable later')
        return REFUSED
    rerun = '%s %s%s' % (ctx.s['tool'], handle(ctx, known[key][0]),
                         ' --keep-branch' if keep_branch else '')
    if dirty_refusal(ctx, path, 'nothing was removed', rerun):
        return REFUSED
    why = holders.link_reason(path)
    if why:
        ctx.say('refused   %s: %s; nothing was removed' % (path, why))
        return REFUSED
    if not clear_disposable(ctx, path):
        return REFUSED
    if logs and not unmoved(ctx, path, head, logged):
        return REFUSED
    registered_path, branch = known[key]
    if not git_remove(ctx, path, registered_path):
        return REFUSED
    return delete_branch(ctx, None if branch == '(detached)' else branch, keep_branch)


def remove_branch(top, branch, cwd=None):
    """`<branch> --branch`: delete a LANDED local branch that has no worktree, with `git branch
    -d` (never -D). The remote branch is not touched."""
    ctx, refused = open_ctx(top, cwd)
    if refused:
        return refused
    return ctx.outcome(_remove_branch(ctx, branch))


def _remove_branch(ctx, branch):
    """The plain verb's checks for a branch with no tree: not the main branch; present; checked out
    nowhere (a worktree on it is a session's, removed by its slug); in `<remote>/<main_branch>` as
    it stands (never fetched); no backlog record still pulled by it (an open claim, as the sweep
    reads it). Every refusal changes nothing."""
    main, s = ctx.main, ctx.s
    up = s['upstream']
    if same_name(branch, s['base']):
        ctx.say('refused   %s is the main branch; nothing was deleted' % branch)
        return REFUSED
    tip, bad = local_branch(main, branch)
    if bad:
        ctx.say('refused   %s; nothing was deleted' % bad)
        return REFUSED
    if tip is None:
        ctx.say('nothing   there is no local branch %s' % branch)
        return OK
    try:
        known = registrations(main)
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s), so whether a worktree'
                ' holds it is unknown; nothing was deleted' % (branch, e))
        return REFUSED
    holding = sorted(os.path.normpath(p) for p, b in known.values() if same_name(b, branch))
    if holding:
        ctx.say('refused   branch %s is checked out at %s: remove that worktree instead (`%s'
                ' <slug>`); nothing was deleted' % (branch, ', '.join(holding), s['tool']))
        return REFUSED
    base, err = base_commit(ctx)
    if base is None:
        ctx.say('refused   %s: cannot read %s (%s), so whether it has landed is unknown; nothing'
                ' was deleted' % (branch, up, err))
        return REFUSED
    merged, why = refs.is_ancestor(tip, base, main)
    if merged is False:
        rc, count, _ = git(main, 'rev-list', '--count', '%s..%s' % (base, tip))
        ctx.say('refused   branch %s holds UNLANDED work: %s commit(s) not in %s %s; nothing was'
                ' deleted' % (branch, count if rc == 0 and count else 'an unknown number of',
                              up, base[:10]),
                '  abandon  the supported route, run on the owner\'s or the PM\'s ruling: it keeps'
                ' the commits under the tag %s%s (--tag %s<name> for the tag a ruling names),'
                ' then deletes the branch:' % (ARCHIVE, branch_slug(ctx, branch), ARCHIVE),
                '             %s %s --branch --abandon "<why>"' % (s['tool'], branch),
                '  owner    deleting unlanded work outright is the owner\'s own act, in the owner\'s'
                ' own terminal only: the Action Hub refuses it as a to-do, and this verb has no'
                ' override.',
                '  note     %s was read as it stands, not fetched: if the branch has landed'
                ' since, `git fetch %s` and run this again.' % (up, s['remote']))
        return REFUSED
    if merged is None:
        ctx.say('refused   %s: the merge check failed (%s); nothing was deleted' % (branch, why))
        return REFUSED
    ctx.say('merged    %s is in %s %s' % (branch, up, base[:10]))
    ids, why = pulled_by(ctx, base, branch)
    if ids is None:
        ctx.say('refused   %s: the open-claim check failed (%s); nothing was deleted'
                % (branch, why))
        return REFUSED
    if ids:
        ctx.say('refused   a backlog record is still pulled by %s (an open claim): %s; nothing was'
                ' deleted. Tell the coordinator, so it is closed or released first.'
                % (branch, ', '.join(ids)))
        return REFUSED
    return delete_branch(ctx, branch, False, removed=False)


def local_branch(main, name):
    """`(tip, None)` for exactly the local branch `name`, `(None, None)` when there is none, or
    `(None, why)` refusing. `name` must come back unchanged from `git check-ref-format --branch`,
    so revision syntax (`main~1`, `main^`, `main@{1}`, `@{-1}`) is never looked up; the lookup is an
    exact match over `git for-each-ref refs/heads/`. A branch whose name differs from `name` only in
    case is refused, never resolved to: the delete would act on the typed name."""
    args = ('check-ref-format', '--branch', name)
    rc, out, err = git(main, *args)
    if rc != 0 or out != name:
        return None, '%s is not a branch name as typed (%s)' % (
            name, failure(args, rc, err) if rc != 0 else 'git reads it as %s' % (out or '?'))
    args = ('for-each-ref', '--format=%(objectname) %(refname)', 'refs/heads/')
    rc, out, err = git(main, *args)
    if rc != 0:
        return None, 'the local branches could not be read (%s)' % failure(args, rc, err)
    tip, near = None, []
    for line in out.splitlines():
        sha, _, ref = line.partition(' ')
        have = ref[len('refs/heads/'):]
        if have == name:
            tip = sha
        elif same_name(have, name):
            near.append(have)
    if near:
        return None, ('git has %s, whose name and %s differ only in case (one ref on this file'
                      ' system); name the branch exactly as git lists it' % (', '.join(near), name))
    return tip, None


def pulled_by(ctx, rev, branch):
    """`(ids, None)`: backlog records `state: pulled` with `pulled_by: <branch>`, matched without
    regard to case (`.lower()`, as backlog.py does), or `(None, why)` (pulled_index, for one
    branch)."""
    index, why = pulled_index(ctx, rev)
    if index is None:
        return None, why
    want = branch.lower()
    return sorted(i for b, ids in index.items() if b.lower() == want for i in ids), None


def pulled_index(ctx, rev):
    """`({branch: sorted ids}, None)` over every backlog record `state: pulled`, keyed by its
    `pulled_by` as written (callers match it case-blind), or `(None, why)`: one store read for every
    branch. A main or folder store is read in the tree of `rev`; a ref or branch store at its
    local ref as it stands (never fetched; `rev` is not used), an absent ref holding no
    records."""
    try:
        backend = backlog_store.open_backend(ctx.main, ctx.cfg, fetch=False)
    except (BacklogFailure, ConfigError, KeyError) as e:
        return None, 'the backlog store cannot be opened (%s)' % e
    if isinstance(backend, backlog_store.MainBackend):
        records, _, why = backlog_store.records_in_tree(ctx.main, rev, backend.rel)
        where = '%s at %s' % (backend.rel, rev[:10])
    else:
        # refs.ref_commit: a git ended from outside is no answer, never "no store ref".
        tip, why = refs.ref_commit(backend.local_ref, ctx.main)
        if tip is None:
            return None, 'cannot read %s (%s)' % (backend.local_ref, why)
        if not tip:
            return {}, None
        records, _, why = backlog_store.records_in_tree(ctx.main, tip, backlog_store.STORE_DIR)
        where = '%s at %s' % (backend.local_ref, tip[:10])
    if records is None:
        return None, 'the backlog records %s cannot be read (%s)' % (where, why)
    unread = [r.stem for r in records if not r.readable]
    if unread:
        return None, 'unreadable backlog record(s) %s: %s' % (where, ', '.join(unread))
    index = {}
    for r in records:
        if r.get('state') == 'pulled' and r.get('pulled_by'):
            index.setdefault(r.get('pulled_by'), []).append(r.id)
    return {branch: sorted(ids) for branch, ids in index.items()}, None


def branch_slug(ctx, branch):
    """The tag name's tail: `branch` with `branch_template`'s prefix and suffix cut off when it
    fits the template, else the whole branch name."""
    for template in ctx.s['branch_template']:
        pre, post = template.split('{slug}', 1)
        if (branch.startswith(pre) and branch.endswith(post)
                and len(branch) > len(pre) + len(post)):
            return branch[len(pre):len(branch) - len(post)]
    return branch


def report_pulls(ctx, base, tip, branch):
    """Backlog records the abandoned branch still pulls: on the base (or the store), a dead pull
    the coordinator releases; on the branch's own tip only (a main or folder store), nothing to do.
    Reported, never refused."""
    up = ctx.s['upstream']
    on_base, why = pulled_by(ctx, base, branch)
    if on_base is None:
        ctx.say('note      backlog records pulled by %s were not checked: %s' % (branch, why))
        return
    for record in on_base:
        ctx.say('warning   backlog record %s on %s is pulled by %s: a dead pull now the branch is'
                ' gone. Tell the coordinator, so it is released in a records commit.'
                % (record, up, branch))
    try:
        backend = backlog_store.open_backend(ctx.main, ctx.cfg, fetch=False)
    except (BacklogFailure, ConfigError, KeyError):
        return
    if not isinstance(backend, backlog_store.MainBackend):
        return
    on_tip, _ = pulled_by(ctx, tip, branch)
    for record in on_tip or []:
        if record not in on_base:
            ctx.say('note      backlog record %s was pulled on %s only: the copy on %s was'
                    ' never pulled, so there is nothing to release' % (record, branch, up))
