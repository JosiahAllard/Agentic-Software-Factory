"""Abandon a workstream's unlanded work under a tag (docs/worktree-remove.md), with the checks of
kitlib.worktree.remove_checks and the removal of kitlib.worktree.remove.

`abandon(top, argument, why)` tags the tip `archive/<slug>` (annotated, `abandoned: <why>`, local),
every refusal running before the tag, then removes the tree and deletes the branch with
`git update-ref -d <ref> <tip>` while the tag still holds that tip. Backlog records still pulled by
the branch are reported. On a LANDED tree holding uncommitted work it saves that work under the tag
instead, then removes the tree (`_abandon_landed_dirty`); on a landed clean tree it refuses.
`abandon_branch` does the same for a local branch no worktree has checked out. Each returns
Outcome(code, lines); nothing here prints.
"""
import datetime
import os
import re
import shutil

from kitlib.config import user as kuser
from kitlib.git import refs

from . import holders
from .holders import canon
from .remove_checks import (ARCHIVE, base_commit, checked_out, dirty, dirty_refusal, failure, git,
                            git_env, GitReadError, handle, ignored_reason, is_workstream,
                            kept_reflog, OK, open_ctx, REFUSED, registered_target, registrations,
                            same_name, unmoved)
from .remove import (branch_slug, clear_disposable, delete_branch, git_remove, local_branch,
                     report_pulls)


# --- abandon ------------------------------------------------------------------------------------

def tag_tip(main, tag):
    """`(commit, None)` `refs/tags/<tag>` peels to, `('', None)` when there is no such tag, or
    `(None, why)` when git cannot tell.

    GOTCHA: this asked `rev-parse -q --verify`, whose "no such tag" is exit
    1 with no output, a killed git's shape (refs.KILLED_SHAPE): a kill read as a free tag name.
    refs.resolve(known=True) tells the two apart."""
    return refs.resolve('refs/tags/%s' % tag, main, known=True)


def make_tag(main, tag, tip, message):
    """An annotated, unsigned, local `tag` on `tip`, `message` verbatim. (rc, stderr)."""
    rc, out, err = git(main, '-c', 'tag.gpgSign=false', 'tag', '-a', '--cleanup=verbatim', '-F', '-',
                       tag, tip, stdin=message.encode('utf-8'))
    return rc, err or out


def tag_name_refusal(main, tag):
    """None when `tag` (an --abandon --tag) is an archive/ tag name git accepts, else why not."""
    if not tag.startswith(ARCHIVE) or len(tag) == len(ARCHIVE):
        return '--tag %s does not start with %s and name a tag after it' % (tag, ARCHIVE)
    args = ('check-ref-format', 'refs/tags/' + tag)
    rc, _, err = git(main, *args)
    if rc != 0:
        return '--tag %s is not a tag name git accepts (%s)' % (tag, failure(args, rc, err))
    return None


def pick_tag(ctx, slug, tip, same=None, named=None):
    """`(tag, commit it holds or '', None)`: archive/<slug>; with archive_next_free the first of
    archive/<slug>, -2, -3, ... that is free or already on `tip` (or on a commit `same` accepts).
    `named` (--tag) is taken as it is, never the next free one: the caller refuses it when it
    holds another commit. `(tag, None, why)` when git cannot tell what `tag` holds: never taken
    for a free name."""
    if named:
        held, why = tag_tip(ctx.main, named)
        return named, held, why
    tag = ARCHIVE + slug
    held, why = tag_tip(ctx.main, tag)
    if held is None or not ctx.s['archive_next_free']:
        return tag, held, why
    n = 1
    while held and held != tip and not (same and same(held)):
        n += 1
        tag = '%s%s-%d' % (ARCHIVE, slug, n)
        held, why = tag_tip(ctx.main, tag)
    return tag, held, why


def branch_tip(main, branch):
    """`(commit, None)` of `refs/heads/<branch>`, `('', None)` when there is no such branch, or
    `(None, why)` when git cannot tell (a kill is never "no branch": tag_tip's GOTCHA)."""
    return refs.resolve('refs/heads/' + branch, main, known=True)


def _shown(answer, none):
    """A tag_tip or branch_tip answer for a line: the short hash, `none` for no such ref, or that
    git could not tell."""
    sha, why = answer
    if sha is None:
        return '(unknown: git could not tell: %s)' % why
    return sha[:10] if sha else none


def left_after(ctx, path, branch, tag):
    now, held = branch_tip(ctx.main, branch), tag_tip(ctx.main, tag)
    ctx.say('  left     %s; branch %s kept at %s; tag %s holds %s. Nothing was'
            ' forced.' % ('worktree %s is gone' % path if path else 'there was no worktree',
                          branch, _shown(now, '(none)'), tag, _shown(held, '(nothing)')),
            '  next     tell the coordinator; do not run git branch -D')


def drop_branch(ctx, path, branch, tip, tag, keep):
    """Delete `branch` only while it still holds `tip` and `tag` still holds that tip (`update-ref
    -d <ref> <old>` is atomic). Never `branch -D`. Returns the exit status."""
    main = ctx.main
    if branch is None:
        ctx.say('branch    none: it was a detached HEAD')
        return OK
    if keep:
        ctx.say('kept      branch %s (--keep-branch); its commits are also under tag %s'
                % (branch, tag))
        return OK
    held, why = tag_tip(main, tag)
    if held != tip:
        ctx.say('failed    tag %s %s, so branch %s was not deleted'
                % (tag, 'no longer holds %s' % tip[:10] if held is not None else
                   'could not be read (%s)' % why, branch))
        left_after(ctx, path, branch, tag)
        return REFUSED
    rc, _, err = git(main, 'update-ref', '-d', 'refs/heads/' + branch, tip)
    if rc != 0:
        ctx.say('failed    could not delete branch %s (the delete holds it to the tagged %s): %s'
                % (branch, tip[:10], err or '?'))
        left_after(ctx, path, branch, tag)
        return REFUSED
    section = 'branch.' + branch
    if git(main, 'config', '--get-regexp', '^' + re.escape(section) + r'\.')[0] == 0:
        git(main, 'config', '--remove-section', section)
    ctx.say('deleted   branch %s (was %s); its commits are kept by tag %s' % (branch, tip[:10], tag))
    return OK


def abandon(top, argument, why, keep_branch=False, cwd=None, today=None, tag=None):
    """`--abandon <why>`: drop UNLANDED work, keeping it as the annotated tag archive/<slug>, or
    as `tag` (--tag archive/<name>, the tag a ruling names): made when absent, reused when it
    already holds the work, refused on any other commit."""
    ctx, refused = open_ctx(top, cwd)
    if refused:
        return refused
    if tag is not None:
        bad = tag_name_refusal(ctx.main, tag)
        if bad:
            ctx.say('refused   %s; nothing was tagged or removed' % bad)
            return ctx.outcome(REFUSED)
    return ctx.outcome(_abandon(ctx, argument, why, keep_branch, today, tag))


def _abandon(ctx, argument, why, keep_branch, today, named=None, expect=None):
    """The abandon of one registered tree. `expect`, when given, is the commit the caller checked
    (the sweep's stale mark): a tree at any other commit, or one whose work is already in the base
    with no uncommitted work left, is refused, never tagged; a landed tree that holds uncommitted
    work has it saved (`_abandon_landed_dirty`), as without `expect`."""
    found = registered_target(ctx, argument)
    if not isinstance(found, tuple):
        return found
    path, known = found
    main, s = ctx.main, ctx.s
    key = canon(path)
    registered_path = known[key][0]
    # The name git registered, not the one typed: the tag is the one the UNLANDED hint names.
    slug = os.path.basename(os.path.normpath(registered_path))
    up = s['upstream']
    try:
        branch, tip = checked_out(main, key)
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s); nothing was tagged or'
                ' removed' % (path, e))
        return REFUSED
    if not tip:
        ctx.say('refused   %s: git reported no HEAD for it; nothing was tagged or removed' % path)
        return REFUSED
    if expect is not None and tip != expect:
        ctx.say('refused   %s: it is at %s, not the %s it was checked at; nothing was tagged or'
                ' removed' % (path, tip[:10], expect[:10]))
        return REFUSED
    name = branch or ('detached HEAD %s' % tip[:10])
    others = sorted(os.path.normpath(p) for k, (p, b) in known.items()
                    if branch and b == branch and k != key)
    if others:
        ctx.say('refused   branch %s is also checked out at %s; nothing was tagged or removed'
                % (branch, ', '.join(others)))
        return REFUSED
    base, err = base_commit(ctx)
    if base is None:
        ctx.say('refused   %s: cannot read %s (%s); nothing was tagged or removed' % (path, up, err))
        return REFUSED
    merged, why_not = refs.is_ancestor(tip, base, main)
    if merged:
        work = dirty(ctx, path)[0]
        if work:
            return _abandon_landed_dirty(ctx, path, registered_path, slug, branch, tip, name,
                                         len(work), why, keep_branch, today, named)
        if expect is not None:
            ctx.say('refused   %s: %s is already in %s %s and holds no uncommitted work, so it is'
                    ' no longer stale work; nothing was tagged or removed'
                    % (path, name, up, base[:10]))
            return REFUSED
        ctx.say('refused   %s: %s is already in %s %s - nothing to abandon. Remove it'
                ' with `%s %s`; nothing was tagged or removed'
                % (path, name, up, base[:10], s['tool'], handle(ctx, registered_path)))
        return REFUSED
    if merged is None:
        ctx.say('refused   %s: the merge check failed (%s); nothing was tagged or removed'
                % (path, why_not))
        return REFUSED
    # An outside tree, and a detached one anywhere (the plain verb's rule).
    logs = not is_workstream(ctx, path) or not branch
    logged = kept_reflog(ctx, path, tip, True) if logs else None
    if logs and logged is None:
        return REFUSED
    rerun = '%s %s --abandon "<why>"%s%s' % (s['tool'], handle(ctx, registered_path),
                                             ' --tag %s' % named if named else '',
                                             ' --keep-branch' if keep_branch else '')
    if dirty_refusal(ctx, path, 'nothing was tagged or removed', rerun):
        return REFUSED
    why_link = holders.link_reason(path)
    if why_link:
        ctx.say('refused   %s: %s; nothing was tagged or removed' % (path, why_link))
        return REFUSED
    held = holders.blockers(path)
    if held:
        ctx.say('refused   %s is held; nothing was tagged and git was NOT run' % path)
        ctx.describe(path, held)
        return REFUSED
    tag, existing, why_tag = pick_tag(ctx, slug, tip, named=named)
    if existing is None:
        ctx.say('refused   %s: cannot read tag %s (%s); nothing was tagged or removed'
                % (path, tag, why_tag))
        return REFUSED
    if existing and existing != tip:
        ctx.say('refused   tag %s already exists on %s, not on %s (%s); nothing was tagged or'
                ' removed' % (tag, existing[:10], tip[:10], name))
        return REFUSED
    if existing:
        ctx.say('reused    tag %s already holds %s (%s)'
                % (tag, tip[:10], 'the tag --tag names' if named else 'a previous run made it'))
    else:
        message = 'abandoned: %s\n\n%s at %s\nworktree %s\ndate %s\n' % (
            why, ('branch ' + branch) if branch else 'detached HEAD', tip,
            os.path.normpath(registered_path), (today or datetime.date.today()).isoformat())
        rc, err = make_tag(main, tag, tip, message)
        if rc != 0:
            ctx.say('refused   could not create tag %s: git tag exited %s: %s; nothing was removed'
                    % (tag, rc, err or '?'))
            return REFUSED
        held, why_tag = tag_tip(main, tag)
        if held != tip:
            ctx.say('refused   tag %s %s after it was made; nothing was removed'
                    % (tag, 'does not hold %s' % tip[:10] if held is not None else
                       'could not be read (%s)' % why_tag))
            return REFUSED
        ctx.say('tagged    %s -> %s (%s): abandoned: %s' % (tag, tip[:10], name, why))
    if (not clear_disposable(ctx, path)
            or (logs and not unmoved(ctx, path, tip, logged))
            or not git_remove(ctx, path, registered_path)):
        ctx.say('  kept     tag %s stays on %s%s. Once the cause is cleared, the same command'
                ' reruns and reuses the tag.' % (tag, tip[:10],
                                                 ', and branch %s' % branch if branch else ''))
        return REFUSED
    status = drop_branch(ctx, path, branch, tip, tag, keep_branch)
    if branch:
        report_pulls(ctx, base, tip, branch)
    return status


def snapshot_tree(path):
    """`(tree, None)`: the tree of the worktree at `path` as it stands (tracked changes, staged or
    not, and untracked files that are not ignored), written through a throwaway index so neither
    the files nor the worktree's own index change; `(None, why)` when git fails."""
    folder = kuser.mkdtemp('kit-abandon-')
    env = dict(git_env(), GIT_INDEX_FILE=os.path.join(folder, 'index'))
    try:
        for args in (('read-tree', 'HEAD'), ('add', '-A', '--', '.'), ('write-tree',)):
            rc, out, err = git(path, *args, env=env)
            if rc != 0 or (args == ('write-tree',) and not out):
                return None, failure(args, rc, err)
        return out, None
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def saved_work(main, commit, tree, tip):
    """True when `commit` is exactly `tree` on the one parent `tip`: a previous run's saved work."""
    rc, out, _ = git(main, 'log', '-1', '--format=%T %P', commit)
    return rc == 0 and out == '%s %s' % (tree, tip)


def _abandon_landed_dirty(ctx, path, registered_path, slug, branch, tip, name, count, why,
                          keep_branch, today, named=None):
    """`--abandon` on a LANDED tree that holds uncommitted work (the plain verb refuses the work,
    and the commits are in the base: each verb used to name the other). Every check of the plain
    verb runs first; then the work is saved as a commit on `tip` under the annotated tag
    archive/<slug>, the tree is reset to its HEAD (`reset --hard`, `clean -d`, never -x: build
    output goes with the tree as in the plain verb) only while it still matches the saved commit,
    and removed; the branch goes with `git branch -d`. A file written between that last match and
    the reset is lost: the window is narrowed, not closed."""
    main, s = ctx.main, ctx.s
    nothing = 'nothing was tagged or removed'
    logs = not is_workstream(ctx, path) or not branch
    logged = kept_reflog(ctx, path, tip, False) if logs else None
    if logs and logged is None:
        return REFUSED
    _, _, ignored, why_not = dirty(ctx, path)
    if ignored is None:
        ctx.say('refused   %s: its status could not be read (%s); %s' % (path, why_not, nothing))
        return REFUSED
    if ignored:
        ctx.say('refused   %s has %s. %s.' % (path, ignored_reason(ignored, path),
                                             nothing[0].upper() + nothing[1:]),
                '  rerun    %s %s --abandon "<why>"%s%s' % (
                    s['tool'], handle(ctx, registered_path),
                    ' --tag %s' % named if named else '',
                    ' --keep-branch' if keep_branch else ''))
        return REFUSED
    why_link = holders.link_reason(path)
    if why_link:
        ctx.say('refused   %s: %s; %s' % (path, why_link, nothing))
        return REFUSED
    held = holders.blockers(path)
    if held:
        ctx.say('refused   %s is held; nothing was tagged and git was NOT run' % path)
        ctx.describe(path, held)
        return REFUSED
    if not clear_disposable(ctx, path):
        return REFUSED
    tree, why_tree = snapshot_tree(path)
    if tree is None:
        ctx.say('refused   %s: its uncommitted work could not be read (%s); %s'
                % (path, why_tree, nothing))
        return REFUSED

    def same(commit):
        return saved_work(main, commit, tree, tip)

    tag, existing, why_tag = pick_tag(ctx, slug, None, same, named=named)
    if existing is None:
        ctx.say('refused   %s: cannot read tag %s (%s); %s' % (path, tag, why_tag, nothing))
        return REFUSED
    if existing and not same(existing):
        ctx.say('refused   tag %s already exists on %s, not on the uncommitted work of %s; %s'
                % (tag, existing[:10], name, nothing))
        return REFUSED
    if existing:
        commit = existing
        ctx.say('reused    tag %s already holds this work (%s)'
                % (tag, 'the tag --tag names' if named else 'a previous run made it'))
    else:
        message = 'abandoned: %s\n\nuncommitted work on %s at %s\nworktree %s\ndate %s\n' % (
            why, ('branch ' + branch) if branch else 'detached HEAD', tip,
            os.path.normpath(registered_path), (today or datetime.date.today()).isoformat())
        rc, commit, err = git(main, 'commit-tree', tree, '-p', tip, '-F', '-',
                              stdin=message.encode('utf-8'))
        if rc != 0 or not commit:
            ctx.say('refused   %s: could not commit its uncommitted work (%s); %s'
                    % (path, failure(('commit-tree',), rc, err), nothing))
            return REFUSED
        rc, err = make_tag(main, tag, commit, message)
        if rc != 0:
            ctx.say('refused   could not create tag %s: git tag exited %s: %s; nothing was removed'
                    % (tag, rc, err or '?'))
            return REFUSED
        got, why_tag = tag_tip(main, tag)
        if got != commit:
            ctx.say('refused   tag %s %s after it was made; nothing was removed'
                    % (tag, 'does not hold %s' % commit[:10] if got is not None else
                       'could not be read (%s)' % why_tag))
            return REFUSED
    ctx.say('saved     uncommitted work (%d) of %s as %s under tag %s (on %s, already in %s);'
            ' see it: git diff %s %s' % (count, path, commit[:10], tag, tip[:10], s['upstream'],
                                        tip[:10], tag))
    kept = ('  kept     tag %s holds the saved work%s. Nothing was forced.'
            % (tag, ', and branch %s' % branch if branch else ''))
    if logs and not unmoved(ctx, path, tip, logged):
        ctx.say(kept)
        return REFUSED
    now, why_now = snapshot_tree(path)
    head = git(path, 'rev-parse', 'HEAD')[1]
    if now != tree or head != tip:
        ctx.say('failed    %s changed after its work was saved (%s); it was NOT reset or removed'
                % (path, why_now or ('HEAD is %s' % head[:10] if head != tip else
                                     'the files differ')), kept)
        return REFUSED
    for args in (('reset', '-q', '--hard', 'HEAD'), ('clean', '-q', '-f', '-d')):
        rc, _, err = git(path, *args)
        if rc != 0:
            ctx.say('failed    %s: %s; git worktree remove was NOT run'
                    % (path, failure(args, rc, err)), kept)
            return REFUSED
    ctx.say('reset     %s to %s (its uncommitted work is under tag %s)' % (path, tip[:10], tag))
    if not git_remove(ctx, path, registered_path):
        ctx.say(kept)
        return REFUSED
    return delete_branch(ctx, branch, keep_branch)


def abandon_branch(top, branch, why, cwd=None, today=None, tag=None):
    """`<branch> --branch --abandon <why>`: drop an UNLANDED local branch that has no worktree,
    keeping its commits as the annotated tag archive/<slug>, or as `tag` (--tag, as abandon).
    The remote branch is not touched."""
    ctx, refused = open_ctx(top, cwd)
    if refused:
        return refused
    if tag is not None:
        bad = tag_name_refusal(ctx.main, tag)
        if bad:
            ctx.say('refused   %s; nothing was tagged or deleted' % bad)
            return ctx.outcome(REFUSED)
    return ctx.outcome(_abandon_branch(ctx, branch, why, today, tag))


def _abandon_branch(ctx, branch, why, today, named=None):
    main, s = ctx.main, ctx.s
    up = s['upstream']
    if same_name(branch, s['base']):
        ctx.say('refused   %s is the main branch; nothing was tagged or deleted' % branch)
        return REFUSED
    tip, bad = local_branch(main, branch)
    if bad:
        ctx.say('refused   %s; nothing was tagged or deleted' % bad)
        return REFUSED
    if tip is None:
        ctx.say('refused   there is no local branch %s; nothing was tagged or deleted' % branch)
        return REFUSED
    try:
        known = registrations(main)
    except GitReadError as e:
        ctx.say('refused   %s: the worktree list could not be read (%s), so whether a worktree'
                ' holds it is unknown; nothing was tagged or deleted' % (branch, e))
        return REFUSED
    holding = sorted(os.path.normpath(p) for p, b in known.values() if same_name(b, branch))
    if holding:
        ctx.say('refused   branch %s is checked out at %s: abandon that worktree instead (`%s'
                ' <slug> --abandon "<why>"`); nothing was tagged or deleted'
                % (branch, ', '.join(holding), s['tool']))
        return REFUSED
    base, err = base_commit(ctx)
    if base is None:
        ctx.say('refused   %s: cannot read %s (%s); nothing was tagged or deleted'
                % (branch, up, err))
        return REFUSED
    merged, why_not = refs.is_ancestor(tip, base, main)
    if merged:
        ctx.say('refused   %s is already in %s %s - nothing to abandon. Delete it with `%s %s'
                ' --branch` (or the landing cleanup, land.py --sweep-landed); nothing was tagged'
                ' or deleted' % (branch, up, base[:10], s['tool'], branch))
        return REFUSED
    if merged is None:
        ctx.say('refused   %s: the merge check failed (%s); nothing was tagged or deleted'
                % (branch, why_not))
        return REFUSED
    tag, existing, why_tag = pick_tag(ctx, branch_slug(ctx, branch), tip, named=named)
    if existing is None:
        ctx.say('refused   %s: cannot read tag %s (%s); nothing was tagged or deleted'
                % (branch, tag, why_tag))
        return REFUSED
    if existing and existing != tip:
        ctx.say('refused   tag %s already exists on %s, not on %s (%s); nothing was tagged or'
                ' deleted' % (tag, existing[:10], tip[:10], branch))
        return REFUSED
    if existing:
        ctx.say('reused    tag %s already holds %s (%s)'
                % (tag, tip[:10], 'the tag --tag names' if named else 'a previous run made it'))
    else:
        message = 'abandoned: %s\n\nbranch %s at %s\nno worktree\ndate %s\n' % (
            why, branch, tip, (today or datetime.date.today()).isoformat())
        rc, err = make_tag(main, tag, tip, message)
        if rc != 0:
            ctx.say('refused   could not create tag %s: git tag exited %s: %s; nothing was deleted'
                    % (tag, rc, err or '?'))
            return REFUSED
        held, why_tag = tag_tip(main, tag)
        if held != tip:
            ctx.say('refused   tag %s %s after it was made; nothing was deleted'
                    % (tag, 'does not hold %s' % tip[:10] if held is not None else
                       'could not be read (%s)' % why_tag))
            return REFUSED
        ctx.say('tagged    %s -> %s (%s): abandoned: %s' % (tag, tip[:10], branch, why))
    status = drop_branch(ctx, None, branch, tip, tag, False)
    report_pulls(ctx, base, tip, branch)
    return status
