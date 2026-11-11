"""Writing an export of pushed kit main (docs/plugin-source.md).

  remote_main(kit, remote)         the sha the remote's main points at; refuses one not fetched
  export(kit, sha, root)           <root>/<sha12>/: the tracked files at sha but tests/, plugin.json
                                   stamped, each per-platform manifest the running platform's
                                   (platform_manifests), each host's own manifest placed
                                   (host_manifests) and stamped,
                                   the marker naming the kit's main checkout
  write_marketplace(root, folder)  <root>/.claude-plugin/marketplace.json names that folder
  sync_live_pointer(s, folder)     plugin-live.json names that folder

The export holds tracked files only (git ls-tree of the commit, blobs from one cat-file batch),
so no worktree, no .git and nothing untracked reaches a plugin cache. It reads git and writes
only under the export root; it fetches nothing and takes no machine lock.
"""
import json
import os
import re

from kitlib import platform as kitplatform
from kitlib.git import batch, cmd, refs, repo
from kitlib.host import registry
from kitlib.pluginsource import layout as plugin_layout


HOOKS_FILE = 'hooks/hooks.json'
# The manifests a renderer writes once per platform, `<name>.<platform>.json` beside each
# (platform_manifests): the hooks, Codex's MCP servers and Claude's plugin manifest.
PLATFORM_FILES = (HOOKS_FILE, 'mcp.json', plugin_layout.MANIFEST)
# A host's own plugin manifest, rendered at hosts/<host>/shell/plugin.json: the export also writes
# it at .<host>-plugin/plugin.json, stamped, where that host looks (host_manifests; Codex reads
# .codex-plugin/plugin.json; docs/plugin-source.md "Codex"). The host's marketplace file is its
# own copy step's to write; this module names no host.
_HOST_MANIFEST = re.compile(r'^hosts/([a-z][a-z0-9-]*)/shell/plugin\.json$')
LEFT_OUT = 'tests'                   # the top-level folder export leaves out
_RESERVED = frozenset(['CON', 'PRN', 'AUX', 'NUL'] + ['COM%d' % i for i in range(1, 10)]
                      + ['LPT%d' % i for i in range(1, 10)])
_VERSION = re.compile(rb'("version"\s*:\s*")([^"\\]*)(")')
_BOM = b'\xef\xbb\xbf'


# ---------------------------------------------------------------- git side

def remote_main(kit, remote):
    """The full sha `remote`'s main points at (ls-remote: read-only, writes no ref). RefreshError
    when the remote cannot be read, has no main, or the commit is not in the kit's objects."""
    sha, why = refs.ls_remote(remote, 'refs/heads/%s' % plugin_layout.MAIN, kit)
    if sha is None:
        raise plugin_layout.RefreshError('cannot read %s/%s: %s' % (remote, plugin_layout.MAIN, why))
    if not sha:
        raise plugin_layout.RefreshError('%s has no %s branch' % (remote, plugin_layout.MAIN))
    found, why = batch.commits([sha], kit)
    if found is None:
        raise plugin_layout.RefreshError('cannot look for %s in the kit: %s' % (sha[:12], why))
    if not found.get(sha):
        raise plugin_layout.RefreshError('%s/%s is %s, which the kit checkout does not have: fetch the kit first '
                           '(git -C "%s" fetch %s)' % (remote, plugin_layout.MAIN, sha[:12], kit, remote))
    return sha


def _count(kit, sha):
    out, why = cmd.run_text(['rev-list', '--count', sha], kit)
    if out is None or not out.isdigit():
        raise plugin_layout.RefreshError('cannot count the commits of %s: %s' % (sha[:12], why or repr(out)))
    return int(out)


def _tree(kit, sha):
    """[(mode, type, oid, path)] of every entry at sha, recursively."""
    out, why = cmd.run(['ls-tree', '-r', '-z', '--full-tree', sha], kit)
    if out is None:
        raise plugin_layout.RefreshError('cannot list the files of %s: %s' % (sha[:12], why))
    entries = []
    for rec in out.split(b'\0'):
        if not rec:
            continue
        head, _, path = rec.partition(b'\t')
        parts = head.split()
        if len(parts) != 3 or not path:
            raise plugin_layout.RefreshError('cannot read a git ls-tree line: %r' % rec[:200])
        entries.append((parts[0].decode('ascii'), parts[1].decode('ascii'), parts[2].decode('ascii'),
                        path.decode('utf-8')))
    return entries


def kit_version(manifest):
    """The `version` in plugin.json bytes, or RefreshError."""
    try:
        data = json.loads(manifest.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError) as e:
        raise plugin_layout.RefreshError('the kit\'s %s is not JSON: %s' % (plugin_layout.MANIFEST, e))
    v = data.get('version') if isinstance(data, dict) else None
    if not isinstance(v, str) or not v:
        raise plugin_layout.RefreshError('the kit\'s %s has no version' % plugin_layout.MANIFEST)
    return v


def stamped_version(base, count):
    """`<kit version>-main.<commit count>`: rises with every push to main, no commit needed."""
    return '%s-main.%d' % (base, count)


def stamp(manifest, version, name=plugin_layout.MANIFEST):
    """plugin.json bytes with its one `version` value replaced; every other byte (a BOM, the
    layout) kept. RefreshError, naming the manifest `name`, when the file does not hold exactly one."""
    found = _VERSION.findall(manifest)
    if len(found) != 1:
        raise plugin_layout.RefreshError('%s holds %d "version" keys, not one' % (name, len(found)))
    return _VERSION.sub(lambda m: m.group(1) + version.encode('utf-8') + m.group(3), manifest)


def _safe_rel(path):
    """True for a tree path that writes one file inside the export folder on Windows: relative,
    `/`-separated, no `\\` or `:`, no empty, `.` or `..` part, no part ending in a dot or a space
    (Windows drops those), and no reserved device name (CON, NUL, COM1, ...; with any extension)."""
    if '\\' in path or ':' in path or os.path.isabs(path):
        return False
    for p in path.split('/'):
        if p in ('', '.', '..') or p[-1] in '. ':
            return False
        if p.split('.')[0].rstrip(' ').upper() in _RESERVED:
            return False
    return True


def host_manifests(oids):
    """{export path: blob oid} of each host's own plugin manifest: hosts/<host>/shell/plugin.json
    of the tree (`oids`: {path: oid}) placed at .<host>-plugin/plugin.json, where that host reads
    it, unless the tree holds that path itself (Claude's .claude-plugin/plugin.json)."""
    placed = {}
    for path, oid in oids.items():
        m = _HOST_MANIFEST.match(path)
        if m and '.%s-plugin/plugin.json' % m.group(1) not in oids:
            placed['.%s-plugin/plugin.json' % m.group(1)] = oid
    return placed


def platform_manifests(paths, platform):
    """{manifest path: the path whose bytes the export writes there} for `platform`: each
    PLATFORM_FILES manifest among `paths` (`<dir>/hooks/hooks.json`, `<dir>/mcp.json`,
    MANIFEST) with its `<name>.<platform>.json` beside it (the host renderers write one per
    platform; docs/content.md "Manifests"). Windows: none, the files the plugin names are its own."""
    if platform == 'windows':
        return {}
    have = set(paths)
    swap = {}
    for path in have:
        if any(path == name or path.endswith('/' + name) for name in PLATFORM_FILES):
            other = path[:-len('.json')] + '.%s.json' % platform
            if other in have:
                swap[path] = other
    return swap


def export(kit, sha, root, now=None, made_by='tools/plugin_refresh.py', platform=None):
    """<root>/<sha12>: the tracked files at `sha` but the top-level tests/ (LEFT_OUT: no tool reads
    the kit's tests from an export, and they were half its size), plugin.json's version stamped,
    each per-platform manifest holding the running `platform`'s file (platform_manifests; a
    swapped plugin.json is stamped too), each host's own manifest written where that host looks
    and stamped (host_manifests), and the marker (`made_by`: the tool that wrote it;
    `checkout`: the main checkout of `kit`'s repository, left out when git cannot place it). Written into <sha12>.tmp and renamed, so a folder by the
    final name is always whole. An existing folder whose marker names `sha` is kept as it is.
    A 100755 blob is chmodded 0o755 unless the running host is Windows (kitplatform.NAME, not
    the `platform` argument, which picks only the manifests), so git runs the kit githooks.
    Returns (folder, version)."""
    name = plugin_layout.folder_name(sha)
    dest = os.path.join(root, name)
    have = plugin_layout.read_marker(dest)
    if have and have.get('sha') == sha:
        return dest, have.get('version')
    if os.path.exists(dest):
        raise plugin_layout.RefreshError('%s exists and is not an export of %s; move it away first' % (dest, sha[:12]))
    entries = [e for e in _tree(kit, sha) if e[3].split('/')[0] != LEFT_OUT]
    blobs = [e for e in entries if e[1] == 'blob' and e[0] != '120000']
    bad = [e[3] for e in blobs if not _safe_rel(e[3])]
    if bad:
        raise plugin_layout.RefreshError('the tree of %s holds a path that cannot be written safely: %r' % (sha[:12], bad[0]))
    data, why = batch.cat_file(sorted({e[2] for e in blobs}), kit)
    if data is None:
        raise plugin_layout.RefreshError('cannot read the files of %s: %s' % (sha[:12], why))
    manifest = next((data.get(e[2]) for e in blobs if e[3] == plugin_layout.MANIFEST), None)
    if not manifest:
        raise plugin_layout.RefreshError('%s has no %s' % (sha[:12], plugin_layout.MANIFEST))
    version = stamped_version(kit_version(manifest[1]), _count(kit, sha))
    oids = {e[3]: e[2] for e in blobs}
    modes = {e[3]: e[0] for e in blobs}
    swap = platform_manifests(oids, kitplatform.NAME if platform is None else platform)
    placed = host_manifests(oids)
    tmp = dest + '.tmp'
    if os.path.lexists(tmp):
        plugin_layout._rmtree(tmp)
    os.makedirs(tmp)
    for path, oid in [(e[3], e[2]) for e in blobs] + sorted(placed.items()):
        oid = oids[swap[path]] if path in swap else oid
        got = data.get(oid)
        if got is None:
            raise plugin_layout.RefreshError('git has no blob %s for %s' % (oid[:12], path))
        body = got[1]
        if path == plugin_layout.MANIFEST or path in placed:
            body = stamp(body, version, path)
        full = os.path.join(tmp, *path.split('/'))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as fh:
            fh.write(body)
        if modes.get(path) == '100755' and kitplatform.NAME != 'windows':
            os.chmod(full, 0o755)  # git skips a non-executable hook (githooks/pre-push)
    skipped = [e[3] for e in entries if e not in blobs]
    marker = {'sha': sha, 'version': version, 'exported_at': plugin_layout._stamp(now),
              'skipped': skipped, 'made_by': made_by}
    checkout = repo.main_checkout_toplevel(kit)
    if checkout:
        marker['checkout'] = checkout
    with open(os.path.join(tmp, plugin_layout.MARKER), 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(marker, fh, indent=2)
        fh.write('\n')
    os.rename(tmp, dest)
    return dest, version


def write_marketplace(root, folder):
    """<root>/.claude-plugin/marketplace.json: the marketplace `pm-kit` whose plugin `pm-kit` is
    `./<folder>`. The names never change, so agent names (pm-kit:<agent>) and allow entries do not.
    A host with a marketplace file of its own writes it in its copy step (docs/plugin-source.md
    "Codex")."""
    plugin_layout._write_json(plugin_layout.marketplace_path(root), {
        'name': registry.PLUGIN_NAME,
        'description': 'The PM kit\'s plugin, exported from pushed kit main (docs/plugin-source.md).',
        'owner': {'name': 'PM kit'},
        'plugins': [{'name': registry.PLUGIN_NAME, 'source': './%s' % folder,
                     'description': 'The PM kit: agents, skills and the machine lock and hub MCP servers.'}],
    })


def _pointer_body(s, name):
    """What the live pointer should hold for export folder `name`, from that folder's marker; None
    when there is no pointer setting or the folder has no marker (it is not named)."""
    if not s.live_pointer:
        return None
    folder = os.path.join(s.export_dir, name)
    marker = plugin_layout.read_marker(folder)
    if not marker:
        return None
    return {'schema': 1, 'root': s.export_dir, 'folder': name, 'path': folder,
            'sha': marker.get('sha'), 'version': marker.get('version'),
            'exported_at': marker.get('exported_at')}


def sync_live_pointer(s, name, out=None):
    """Make the live pointer (s.live_pointer; None: nothing) name export folder `name` of s.export_dir,
    from that folder's marker (so a folder with none is not named). It is written only when missing
    or different, through a temp file and os.replace. A failed write is a WARNING line, never an
    error: the export stands without it."""
    body = _pointer_body(s, name)
    if body is None or plugin_layout.read_live_pointer(s.live_pointer) == body:
        return
    try:
        plugin_layout._write_json(s.live_pointer, body)
    except OSError as e:
        (out or (lambda line: None))('WARNING: live pointer %s not written: %s' % (s.live_pointer, e))
