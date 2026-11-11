"""Where the live store is (docs/backlog-store.md): the PM data worktree and the store its
`.kit.json` names, the store a linked worktree resolves to, and the refusal of a write to the
default store alone.
"""
import os

from kitlib.git import refs
from kitlib.git import repo as gitrepo

from . import store as smod


def pm_data_live_store(root, *, worktrees=None):
    """`(pm_root, named, None)`: the PM data worktree of `root`'s repository
    (at_landing.pm_data_root) and the "backlog".store its `.kit.json` writes, `named` None when it
    writes none (it then reads the same implicit ref as `root`, refs being shared by every
    worktree). `(None, None, None)` when the repository has no PM data record or `root` is that
    worktree; `(None, None, why)` when the worktree cannot be found; `(pm_root, None, why)` when it
    was found and its `.kit.json` cannot be read (`why` the error). Shared by default_store_refusal and preflight's backlog line (kitlib/preflight/defaultstore.py).
    `worktrees` (a gitrepo.Worktrees) spares git the second ask of the list and the common dir."""
    from . import at_landing                         # at_landing imports this module
    pm_root, why = at_landing.pm_data_root(root, worktrees=worktrees)
    if pm_root is None:
        return None, None, why
    if os.path.normcase(os.path.normpath(pm_root)) == os.path.normcase(os.path.normpath(root)):
        return None, None, None
    from kitlib.config import ConfigError, load as load_config
    try:
        named = load_config(pm_root).written('backlog', 'store')
    except (ConfigError, OSError) as exc:
        return pm_root, None, str(exc) or type(exc).__name__
    return pm_root, named, None


def live_store_root(root, cfg=None):
    """`(path, why)`: the checkout whose configured store holds `root`'s records: `root` when its
    `.kit.json` (`cfg`, default load_config(root)) names a "backlog".store, else its PM data
    worktree when that names one (pm_data_live_store), else `root` (the implicit store). `why` is
    pm_data_live_store's, None when it found nothing wrong; `path` is then `root` only because the
    PM data worktree could not be found or read, which a caller that must not read a wrong store
    refuses on. A `.kit.json` that cannot be read raises ConfigError or OSError."""
    from kitlib.config import load as load_config
    if (load_config(root) if cfg is None else cfg).written('backlog', 'store') is not None:
        return root, None
    pm_root, named, why = pm_data_live_store(root)
    return (pm_root if pm_root and named is not None else root), why


def worktree_live_store(top, cfg):
    """`(pm_root, branch)` when a run from the checkout `top` opens the live store in the PM data
    worktree instead of its own: `top`'s `.kit.json` (`cfg`) names no "backlog".store, `top` is a
    linked worktree of its repository (git's own answer; the PM data worktree itself is excluded
    by pm_data_live_store), that repository's PM data worktree names a store of its own
    (pm_data_live_store, read without error), and `top` has a branch checked out, `branch`, the
    one the run acts as. None otherwise: the main checkout, a detached HEAD, a named store or no
    live store keep `top`'s own (the implicit ref, whose writes default_store_refusal refuses).
    A PM data worktree that cannot be found or read is pm_data_live_store's `why`, so also None:
    a write then refuses naming that `why` (default_store_refusal) and preflight's backlog line
    names it. Nothing is caught here: anything else pm_data_live_store raises is a defect, never
    a quiet fall back to the stale ref. The same resolution `-C <pm data> --as <branch>` spells
    out by hand (docs/backlog.md "The store a worktree opens")."""
    return worktree_store(top, cfg)[0]


def worktree_store(top, cfg, *, place=None, worktrees=None):
    """`(live, why)`: `live` is worktree_live_store's answer; `why`, when `live` is None only
    because the PM data worktree cannot be found or its `.kit.json` read (pm_data_live_store's
    `why`), says so, else None. A reader that must not take `top`'s own store for the live one
    in silence (commit.py's narrowed-record check) reads `why`. `place` is gitrepo.classify(top)
    and `worktrees` a gitrepo.Worktrees, when the caller has them."""
    if cfg.written('backlog', 'store') is not None:
        return None, None
    branch = worktree_branch(top, place=place)
    if branch is None:
        return None, None
    if worktrees is None and place is not None:
        worktrees = gitrepo.Worktrees(common=place.common)
    pm_root, named, why = pm_data_live_store(top, worktrees=worktrees)
    if why:
        return None, (unreadable_kit_json(pm_root, why) if pm_root else why)
    if pm_root is None or not named:
        return None, None
    return (pm_root, branch), None


def worktree_branch(top, *, place=None):
    """The branch checked out at `top` when it is a linked worktree of its repository, else None
    (the main checkout, a detached HEAD): the checkouts worktree_live_store can resolve. `place`
    is gitrepo.classify(top) when the caller has it."""
    if (place or gitrepo.classify(top)).kind != gitrepo.LINKED:
        return None
    return refs.current_branch(top)[0] or None


def unreadable_kit_json(pm_root, why):
    """The clause naming a PM data worktree whose `.kit.json` cannot be read (pm_data_live_store)."""
    return 'the .kit.json of the PM data worktree %s cannot be read: %s' % (
        pm_root.replace('\\', '/'), why)


def default_store_refusal(root):
    """Why a write to the implicit `ref` store is refused, and where to write instead: the PM data
    worktree and its store when the repository has one that names a store (pm_data_live_store),
    else the `.kit.json` setting that opts in. A PM data worktree naming no store reads the same
    implicit ref, so a write there is refused too and it is not offered. Records written there by
    default were lost to the live store once, so a ref store is written only when named."""
    head = ('%s is the default backlog store: this repository\'s .kit.json names no '
            '"backlog".store, so nothing was written' % smod.STORE_REF)
    opt_in = 'set "backlog": {"store": "ref"} in .kit.json to write this store'
    try:
        pm_root, named, why = pm_data_live_store(root)
    except Exception as exc:                         # noqa: BLE001 - the advice is best effort
        pm_root, named, why = None, None, str(exc)
    if pm_root is None:
        where = '; the PM data worktree was not found (%s)' % why if why else ''
        return '%s%s; %s' % (head, where, opt_in)
    if why:
        return '%s; %s; %s' % (head, unreadable_kit_json(pm_root, why), opt_in)
    if named is None:
        return ('%s; the PM data worktree "%s" names no "backlog".store either, so a write there '
                'is refused too; %s' % (head, pm_root.replace('\\', '/'), opt_in))
    return ('%s; write to the live store in the PM data worktree (store %s) with -C "%s", or %s'
            % (head, named, pm_root.replace('\\', '/'), opt_in))
