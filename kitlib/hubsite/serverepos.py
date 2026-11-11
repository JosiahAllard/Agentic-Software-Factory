"""kitlib.hubsite.serverepos: the repositories the hub server's Api asks about: the kit
checkout's skill sources at a branch (kit_checkout, _kit_key, _kit_blob, _kit_text: the Api's
"Skill edits") and a configured repository by its origin remote (_checkout, _repo_name over
_RemoteIds: an approval's repo_id and its coordinator). kitlib.hubsite.serve hands them to the
Api; every one is a read, and none writes a repository.
"""
import os
import threading
import time

from kitlib import pmstart

from . import skilledits


def _checkout(backlog_views):
    """Api's checkout(repo_id) (kitlib.hubsite.api, "Approvals"): the root of the configured
    repository (backlog_views.repos(), its (name, root) pairs) whose origin remote is that
    repo_id (approvals.remote_id), read with git on each call; None when none is, when there is
    no backlog, or when the repositories cannot be listed, so a request fails closed."""
    def checkout(repo_id):
        found = _configured(backlog_views, repo_id)
        return found[0][1] if found else None
    return checkout


def kit_checkout(kit_root=None):
    """The kit's main checkout: kit_root (default pmstart.KIT_ROOT) when it is a checkout, else
    the checkout its plugin export marker names (kitlib.pluginsource.default_kit); None when there
    is none."""
    from kitlib import pluginsource
    return pluginsource.default_kit(kit_root or pmstart.KIT_ROOT)[0]


def _kit_key(repo_name, remote_ids, kit_root=None):
    """Api's kit_key() (kitlib.hubsite.api, "Skill edits"): the name repo_name gives the kit
    checkout's origin remote (`remote_ids`, a _RemoteIds), the session key of the kit's
    coordinator; '' when there is no checkout, no remote or no configured name, so a submitted
    skill edit is the PM's."""
    def kit_key():
        checkout = kit_checkout(kit_root)
        repo_id = remote_ids(checkout)[0] if checkout else None
        name = repo_name(repo_id) if repo_id else None
        return name if isinstance(name, str) else ''
    return kit_key


def _kit_blob(kit_root=None, run=None):
    """Api's kit_blob(branch, path) (kitlib.hubsite.api, "Skill edits"): the blob id of `path`
    on `branch` in the kit checkout, read with `git rev-parse --verify` (`run`, default
    kitlib.git.cmd.run_text; a read: the hub never writes a repository), or None when there is no
    checkout or git cannot read it."""
    def kit_blob(branch, path):
        from kitlib.git import cmd
        checkout = kit_checkout(kit_root)
        if not checkout:
            return None
        out, _why = (run or cmd.run_text)(['rev-parse', '--verify', '--quiet',
                                           '%s:%s' % (branch, path)], checkout)
        return out if skilledits.is_blob(out) else None
    return kit_blob


def _kit_text(kit_root=None, run=None):
    """Api's kit_text(blob) (kitlib.hubsite.api, "Skill edits"): the text of git blob `blob` in
    the kit checkout, read with `git cat-file blob` (`run`, default kitlib.git.cmd.run, giving
    bytes; a read: the hub never writes a repository), CRLF read as LF; None when there is no
    checkout, `blob` is not a blob id, git cannot read it, the bytes are not that blob or are not
    UTF-8."""
    def kit_text(blob):
        from kitlib.git import cmd
        checkout = kit_checkout(kit_root)
        if not checkout or not skilledits.is_blob(blob):
            return None
        out, _why = (run or cmd.run)(['cat-file', 'blob', blob], checkout)
        if not isinstance(out, bytes) or skilledits.git_blob(out) != blob:
            return None
        try:
            return skilledits.normal(out.decode('utf-8'))
        except UnicodeDecodeError:
            return None
    return kit_text


REMOTE_TTL_S = 60.0     # the longest a remembered origin remote answers (_RemoteIds)
REMOTE_MISS_TTL_S = 5.0     # the longest a remembered failure to read one answers


class _RemoteIds(object):
    """approvals.remote_id(root) with what git said remembered per root, for _repo_name: every
    decided approval is routed on each pending read (each GET snapshot, each wait a write wakes),
    and asking git each time cost one `git remote get-url` per configured repository per
    approval (KIT-HUB-CLICK-LAG).

    A remembered answer serves while both hold: the stat (mtime, size) of the repository's
    config is the one it was read under, so `git remote set-url` (which rewrites that file) is
    seen on the next read; and it is younger than REMOTE_TTL_S, or REMOTE_MISS_TTL_S for a failed
    read (a git timeout under load must not send every decision to the PM for long). The config
    is <root>/.git/config, or for a checkout whose .git is a file (a linked worktree) the common
    dir's config, found from its `gitdir:` line and `commondir` file
    (kitlib.git.defaults.common_dir).

    Worst case, a stale answer: when that config cannot be found (an unreadable .git file, say)
    or a remote is set where it is not (a worktree's own config.worktree), a changed remote is
    seen only when the TTL runs out. For up to REMOTE_TTL_S a decided approval can then be routed
    to the coordinator of the repository the remote used to name (the wrong repository), or to
    the PM alone, and so can the decision's one-time push (kitlib.hubsite.push, which names the
    coordinator through the same Api.repo_key). The approval's verify (Api checkout, _checkout)
    is not cached: it reads git on each call.

    `clock` gives monotonic seconds (tests). A lock guards the dict; git runs outside it, so two
    callers that miss at once both run git and the later answer is kept."""

    def __init__(self, clock=time.monotonic, ttl_s=REMOTE_TTL_S, miss_ttl_s=REMOTE_MISS_TTL_S):
        self._clock = clock
        self._ttl_s = ttl_s
        self._miss_ttl_s = miss_ttl_s
        self._got = {}              # root -> (stat key, clock when read, (repo_id, why))
        self._lock = threading.Lock()

    @staticmethod
    def _stat(root):
        from kitlib.git import defaults
        try:
            common = defaults.common_dir(root)
            st = os.stat(os.path.join(common or os.path.join(root, '.git'), 'config'))
        except (OSError, TypeError, ValueError):
            return None
        return (st.st_mtime_ns, st.st_size)

    def __call__(self, root):
        from kitlib.ownerfiles import approvals     # local: no cycle needs it now; kept
        key, now = self._stat(root), self._clock()
        with self._lock:
            got = self._got.get(root)
        if got is not None and got[0] == key and now - got[1] < (
                self._ttl_s if got[2][0] else self._miss_ttl_s):
            return got[2]
        answer = approvals.remote_id(root)
        with self._lock:
            self._got[root] = (key, now, answer)
        return answer


def _repo_name(backlog_views, session_keys=None, remote_ids=None):
    """Api's repo_name(repo_id) (kitlib.hubsite.api, "Routes"): the name of the configured
    repository whose origin remote is that repo_id, found as _checkout finds its root but with
    each root's remote remembered (`remote_ids`, a _RemoteIds; default a new one); None when
    none is, so a decided approval is the PM's alone. When several configured names share that
    remote (the kit's folder name and its coordinator's key, say), the first that is one of
    session_keys() (the launcher's keys, read only then) is named, else the first."""
    remote_ids = remote_ids or _RemoteIds()

    def repo_name(repo_id):
        names = [name for name, _root in _configured(backlog_views, repo_id, remote_ids)]
        if len(names) > 1 and session_keys is not None:
            try:
                keys = session_keys()
            except Exception:       # noqa: BLE001 - no keys: the first name, as with one
                keys = ()
            names = [n for n in names if n in keys] + names
        return names[0] if names else None
    return repo_name


def _configured(backlog_views, repo_id, remote_id=None):
    """Every (name, root) of backlog_views.repos() whose origin remote is repo_id, in its order,
    read with git (`remote_id`, default approvals.remote_id: on each call); [] when none is,
    when there is no backlog, when the repositories cannot be listed, or for a name with no
    checkout on this machine."""
    from kitlib.ownerfiles import approvals         # local: no cycle needs it now; kept
    remote_id = remote_id or approvals.remote_id
    try:
        pairs = backlog_views.repos() if backlog_views is not None else []
    except Exception:               # noqa: BLE001 - no list: no repository
        return []
    # a name with no checkout here has no remote: remote_id(None) would ask git in this
    # process's own folder
    return [(name, root) for name, root in pairs if root and remote_id(root)[0] == repo_id]
