"""Opening a store (docs/backlog-store.md): the checkout's `.kit.json` and rules, the store
`-C` resolves to, and the backend `.kit.json` "backlog".store names.
"""
from kitlib.git import repo as gitrepo

from . import record as rmod
from . import store as smod
from . import store_folder as sfolder
from . import store_live as slive
from . import store_ref as sref
from .errors import Failure


def open_repo(root):
    """`(top, cfg, rules)` of the checkout `root` is in; Failure when `root` is no repository or
    its `.kit.json` cannot be read (the ConfigError's text). opener.open_repo."""
    return _open_repo(root)[:3]


def _open_repo(root):
    """open_repo's answer and, as a fourth item, the gitrepo.Place git gave for `root`."""
    from kitlib.config import ConfigError, load as load_config
    place, why = gitrepo.toplevel_place(root)
    if not place:
        raise Failure(why)
    try:
        cfg = load_config(place.top)
    except ConfigError as exc:
        raise Failure(str(exc))
    section = cfg.section('backlog')
    return place.top, cfg, rmod.Rules(section['id_grammar'], section['phases']), place


def resolve_store(root, *, worktrees=None):
    """`(top, cfg, rules, branch, why)`: the checkout whose store a run from `root` opens and
    its configuration (open_repo's, Failure as there); `branch`, the branch the run acts as when
    that is the PM data worktree's live store (worktree_live_store), else None; `why`, when the
    run keeps `root`'s own store only because the PM data worktree cannot be found or read
    (worktree_store), else None. The one lookup opener.open_store and commit.py's
    narrowed-record check share (docs/backlog.md "The store a worktree opens"). `worktrees` is a
    gitrepo.Worktrees the caller keeps to reuse the list and the common dir afterwards; git is
    asked each of its questions of `root` once."""
    top, cfg, rules, place = _open_repo(root)
    if worktrees is not None and worktrees.common is None:
        worktrees.common = place.common
    live, why = slive.worktree_store(top, cfg, place=place, worktrees=worktrees)
    if live is None:
        return top, cfg, rules, None, why
    pm_root, branch = live
    top, cfg, rules = open_repo(pm_root)
    return top, cfg, rules, branch, None


def open_backend(root, cfg, fetch=True, clean=False):
    """The backend `.kit.json` "backlog".store names (absent: the schema default, `ref`): `ref`
    the hidden ref, `branch` the `backlog` branch, `main` docs/backlog in `root`, any other path
    that folder in `root`. `clean`: a folder store's MainBackend writes over no uncommitted change
    (Dirty); a ref store ignores it. The `ref` store reached by the default alone (`.kit.json`
    does not name it) is read-only: its publish refuses (default_store_refusal)."""
    implicit = cfg.written('backlog', 'store') is None
    store = cfg.section('backlog')['store']
    remote = cfg.remote
    if store == 'ref':
        return sref.RefBackend(root, remote, smod.STORE_REF, smod.STORE_REF, fetch=fetch,
                               implicit=implicit)
    if store == 'branch':
        return sref.RefBackend(root, remote, 'refs/heads/' + smod.BRANCH,
                               'refs/remotes/%s/%s' % (remote, smod.BRANCH),
                               no_remote_ref='refs/heads/' + smod.BRANCH, fetch=fetch)
    return sfolder.MainBackend(root, 'docs/backlog' if store == 'main' else store, clean=clean)
