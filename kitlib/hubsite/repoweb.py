"""kitlib.hubsite.repoweb: where a repository's files are on the web, so the page can link a repo path.

  web_url(remote)  -> "https://host/owner/repo" | None   a GitHub or GitLab origin, as a web address
  Webs(pairs)      -> .annotate(repos)                    adds web, branch and top to repo entries

Only github.com, gitlab.com and gitlab.* hosts count (their `<web>/blob/<branch>/<path>` shape);
any other host, a local path or an unreadable remote gives no web address and the page keeps the
path as text. The address carries no credentials, port, query or fragment. Nothing is served: the
page links to the host. Git runs per repository once, and again only when .git/config changes or
after REFRESH_S.
"""
import os
import re
import time
from urllib.parse import urlsplit

from kitlib import hubbacklog
from kitlib.git import cmd

REFRESH_S = 600
MAX_TOP = 64
GIT_S = 5               # each git call here; the library default is 30 s

_SCP = re.compile(r'^(?:[^@/\s]+@)?([^:/\s]+):(?!//)([^\s]+)$')
_PART = re.compile(r'^[A-Za-z0-9._-]+$')


def _host_ok(host):
    return host in ('github.com', 'gitlab.com') or host.startswith('gitlab.')


def web_url(remote):
    """The https web address of the git remote URL `remote` (https, ssh:// or scp-like
    git@host:owner/repo), or None: another host, a local path, a path of other than owner/repo
    parts (GitLab groups may nest), or text that is no URL."""
    if not isinstance(remote, str):
        return None
    text = remote.strip()
    if '://' in text:
        try:
            u = urlsplit(text)
            host, path, scheme = (u.hostname or '').lower(), u.path, u.scheme.lower()
        except ValueError:
            return None
        if scheme not in ('https', 'http', 'ssh', 'git'):
            return None
    else:
        m = _SCP.match(text)
        if not m:
            return None
        host, path = m.group(1).lower(), '/' + m.group(2).lstrip('/')
    path = path.strip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = path.split('/')
    if host.startswith('gitlab.') or host == 'gitlab.com':
        if '-' in parts:                    # GitLab's own routes start at /-/: not part of the project
            parts = parts[:parts.index('-')]
        deep = True
    else:
        deep = False
    if (not _host_ok(host) or len(parts) < 2 or (not deep and len(parts) != 2)
            or not all(_PART.match(p) and p not in ('.', '..', '-') for p in parts)):
        return None
    return 'https://%s/%s' % (host, '/'.join(parts))


def _main_branch(root):
    try:
        return hubbacklog.main_branch(root, hubbacklog.kit_json(root))
    except RuntimeError:
        return None


def _read(root):
    """{web, branch, top} of the checkout at `root`, or {}: no origin, not a web address of a
    known host, no main branch, or git failed."""
    url, _ = cmd.run_text(['remote', 'get-url', 'origin'], root, timeout=GIT_S)
    web = web_url(url) if url else None
    branch = _main_branch(root) if web else None
    if not branch:
        return {}
    out, _ = cmd.run_text(['ls-tree', '--name-only', branch], root, timeout=GIT_S)
    top = sorted({n.strip('"') for n in (out or '').split('\n') if n})[:MAX_TOP]
    return {'web': web, 'branch': branch, 'top': top}


def _config_path(root):
    """The config file the origin is read from: <root>/.git/config, or for a linked worktree
    (where .git is a file) the common git folder's, which git names."""
    dot = os.path.join(root, '.git')
    if os.path.isdir(dot):
        return os.path.join(dot, 'config')
    out, _ = cmd.run_text(['rev-parse', '--git-common-dir'], root, timeout=GIT_S)
    return os.path.join(root, out, 'config') if out else None


def _stamp(path):
    try:
        return os.stat(path).st_mtime_ns if path else None
    except OSError:
        return None


class Webs(object):
    """Per-root cache of _read. `pairs` is a callable giving (name, root) pairs."""

    def __init__(self, pairs, clock=time.monotonic, read=_read):
        self._pairs, self._clock, self._read, self._kept = pairs, clock, read, {}

    def _info(self, root):
        now, kept = self._clock(), self._kept.get(root)
        path = kept[3] if kept and kept[3] else _config_path(root)   # git is asked once per root, again if it failed
        stamp = _stamp(path)
        if kept and kept[0] == stamp and now - kept[1] < REFRESH_S:
            return kept[2]
        info = self._read(root)
        self._kept[root] = (stamp, now, info, path)
        return info

    def annotate(self, repos):
        """`repos` ([{name, aliases}]) with web, branch and top added to those that have them;
        the rest are unchanged. Never raises."""
        try:
            roots = {str(n).lower(): r for n, r in self._pairs() if r}
        except Exception:               # noqa: BLE001 - no list: no links
            return repos
        for r in repos:
            root = roots.get(r['name'].lower())
            try:
                info = self._info(root) if root else {}
            except Exception:           # noqa: BLE001 - git failed: this repo has no links
                info = {}
            r.update(info)
        return repos
