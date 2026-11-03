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
from kitlib.git import cmd, refs

REFRESH_S = 600
MAX_TOP = 64

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
    if not _host_ok(host) or len(parts) < 2 or not all(_PART.match(p) and p not in ('.', '..')
                                                       for p in parts):
        return None
    return 'https://%s/%s' % (host, '/'.join(parts))


def _main_branch(root):
    named = (hubbacklog.kit_json(root).get('repo') or {}).get('main_branch')
    for b in ([named] if named else ['main', 'master']):
        commit, _ = refs.ref_commit('refs/heads/%s' % b, root)
        if commit:
            return b
    return None


def _read(root):
    """{web, branch, top} of the checkout at `root`, or {}: no origin, not a web address of a
    known host, no main branch, or git failed."""
    url, _ = cmd.run_text(['remote', 'get-url', 'origin'], root)
    web = web_url(url) if url else None
    branch = _main_branch(root) if web else None
    if not branch:
        return {}
    out, _ = cmd.run_text(['ls-tree', '--name-only', branch], root)
    top = sorted({n.strip('"') for n in (out or '').split('\n') if n})[:MAX_TOP]
    return {'web': web, 'branch': branch, 'top': top}


def _stamp(root):
    try:
        return os.stat(os.path.join(root, '.git', 'config')).st_mtime_ns
    except OSError:
        return None


class Webs(object):
    """Per-root cache of _read. `pairs` is a callable giving (name, root) pairs."""

    def __init__(self, pairs, clock=time.monotonic, read=_read):
        self._pairs, self._clock, self._read, self._kept = pairs, clock, read, {}

    def _info(self, root):
        stamp, now = _stamp(root), self._clock()
        kept = self._kept.get(root)
        if kept and kept[0] == stamp and now - kept[1] < REFRESH_S:
            return kept[2]
        info = self._read(root)
        self._kept[root] = (stamp, now, info)
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
