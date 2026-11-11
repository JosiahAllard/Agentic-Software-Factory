"""preflight's `leftovers` lines: registered worktrees whose own work has already landed in
`<remote>/<main_branch>`, shown to every session at its start so each is driven to an end (ported
from the PM kit's preflight `leftover_warn`; docs/preflight.md).

kitlib.worktree.remove_checks's own check (`merged_worktrees`), imported, not shelled out to: a fresh
worktree with no commit of its own is not listed, the `"worktree_remove".exempt` folders are not,
and neither is the worktree this run stands in. The base is read as it stands, never fetched.
Nothing here raises: a failure is a `not checked` line naming it.
"""
import os

from kitlib.config import ConfigError, load
from kitlib.git import repo
from kitlib.worktree import remove_checks
from kitlib.worktree.holders import canon

LABEL = 'leftovers '
INDENT = ' ' * 12


def _open(top, place, wts):
    """`(Ctx, None, listing)` like remove_checks.open_ctx, from the Place and the worktree list the report
    already has (no second `rev-parse --git-common-dir`, no second `git worktree list`); listing is
    None when remove must ask git for it. Anything the report's answers cannot vouch for falls back
    to remove_checks.open_ctx."""
    if place is None or place.kind not in (repo.MAIN, repo.LINKED) or not wts \
            or repo.real(os.path.join(wts[0].path, '.git')) != place.common:
        return (*remove_checks.open_ctx(top, cwd=top), None)
    main = os.path.realpath(os.path.abspath(wts[0].path))
    try:
        ctx = remove_checks.Ctx(main, load(main), top)
    except ConfigError as e:
        return None, remove_checks.Outcome(remove_checks.CONFIG, ['%s: refused: kit configuration: %s'
                                                    % (remove_checks.TOOL, e)]), None
    return ctx, None, _listing(wts)


def _listing(wts):
    """`git worktree list --porcelain`'s text, rebuilt from the parsed worktrees."""
    out = []
    for w in wts:
        out.append('worktree %s' % w.path)
        if w.head:
            out.append('HEAD %s' % w.head)
        if w.branch:
            out.append('branch refs/heads/%s' % w.branch)
        if w.locked is not None:
            out.append(('locked %s' % w.locked).rstrip())
        out.append('')
    return '\n'.join(out)


def lines(top, place=None, wts=None):
    """The lines for the checkout whose top level is `top`. `place` (repo.classify(top)) and `wts`
    (repo.worktrees(top)'s list) are the answers the report already took."""
    try:
        ctx, refused, listing = _open(top, place, wts)
        if refused:
            return [LABEL + 'WARN not checked: %s' % ' '.join(' '.join(refused.lines).split())]
        if listing is None:
            listing = remove_checks.worktree_listing(ctx.main)
        own = [top] if canon(top) != canon(ctx.main) else []
        merged, why = remove_checks.merged_worktrees(ctx, listing, skip=own)
    except Exception as e:               # noqa: BLE001 - a WARN line must never become the failure
        return [LABEL + 'WARN not checked: %s: %s' % (type(e).__name__, e)]
    s = ctx.s
    where = s['worktrees_dir'].replace('\\', '/')
    if merged is None:
        return [LABEL + 'WARN merged worktrees not checked: %s' % why]
    if not merged:
        return [LABEL + 'none under %s' % where]
    out = [LABEL + 'WARN %d under %s - drive each to an end:' % (len(merged), where)]
    for path, branch in merged:
        slug = os.path.basename(path)
        out.append(INDENT + "%s [%s]  landed (its own commits are in %s's %s): %s %s"
                   % (slug, branch or 'detached', s['remote'], s['base'], s['tool'], slug))
    return out
