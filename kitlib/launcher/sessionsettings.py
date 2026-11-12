"""The settings file a launched session starts with: `claude --settings <file>` (docs/launcher.md
"Session settings").

The host's renderer writes one file into the kit (for Claude, hosts/claude/shell/session-settings.json:
the tools and MCP servers every launched session starts without, as `permissions.deny`, beside other
settings keys, which every file plan() gives keeps as they are). kitlib never
imports a host, so tools/launch.py hands its kit-relative path, the deny entry of each MCP server and
the stable kit in as a Spec, on the launcher Host (`session_spec`); a Host without one starts sessions
with no --settings.

  resolve(spec)        (path, None) of the rendered file under the stable kit, or (None, the line to
                       say) when the file is not there: the session then starts with no --settings
  plan(spec, path, mcp, folder, name, stamp)
                       the file the session gets and the JSON to write there: the rendered file itself
                       with no `mcp`; else <folder>/launcher/settings-<name>-<stamp>.json (stamp: the
                       launch's epoch ms), the rendered JSON less the deny entries of the `mcp` servers
                       (written at launch, never into the kit). The stamp makes the file this launch's
                       own: a `consultant --replace` beside a live session of the same name never
                       overwrites, or on a failed launch removes, the running session's file
  write(path, data)    write `data` through a temp file, then os.replace: a reader sees the whole file
  remove(path)         remove a file write() wrote (a failed launch), quietly

The stable kit is the kit checkout: a plugin export is replaced at every plugin refresh, so its file
would vanish under a running session (kitlib.pluginsource.layout.default_kit). The checkout and the
export can be at different kit versions; the file used is the checkout's. Deny lists add up across
settings files and a deny beats any allow, so `--mcp` cannot be a second --settings: it is a file of
its own, written for that session only. A successor started by a hand-off gets the rendered file:
`--mcp` is never carried.
"""
import collections
import json
import os

from kitlib.launcher.settings import LaunchError

# rel: the file's kit-relative path; mcp_deny {server: its deny entry}; kit: the stable kit, None unknown
Spec = collections.namedtuple('Spec', 'rel mcp_deny kit')
FOLDER = 'launcher'
NAME = 'settings-%s-%d.json'
MISSING = 'settings: none (missing %s)'


def resolve(spec):
    """(the rendered file's path under the stable kit, None), or (None, MISSING line) when the
    stable kit is unknown or holds no such file."""
    path = os.path.join(spec.kit, *spec.rel.split('/')) if spec.kit else 'the kit checkout: ' + spec.rel
    if not spec.kit or not os.path.isfile(path):
        return None, MISSING % path
    return path, None


def plan(spec, path, mcp, folder, name, stamp):
    """(the file the session starts with, the JSON to write there or None). LaunchError for a
    server the spec has no deny entry for, a rendered file that is not a settings object, or a
    per-session file already there (never another launch's to overwrite)."""
    mcp = tuple(mcp or ())
    unknown = [m for m in mcp if m not in spec.mcp_deny]
    if unknown:
        raise LaunchError('--mcp %s: no such MCP server (known: %s)'
                          % (', '.join(unknown), ', '.join(sorted(spec.mcp_deny))))
    if not mcp:
        return path, None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        deny = data['permissions']['deny']
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise LaunchError('%s is not a settings file with permissions.deny: %s' % (path, e)) from None
    own = os.path.join(folder, FOLDER, NAME % (name, stamp))
    if os.path.exists(own):
        raise LaunchError('%s is already there: another launch of %s wrote it; start again' % (own, name))
    keep = {spec.mcp_deny[m] for m in mcp}
    data['permissions']['deny'] = [e for e in deny if e not in keep]
    return own, data


def write(path, data):
    """Write `data` as JSON at `path` through `<path>.tmp` and os.replace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, indent=2)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        remove(tmp)
        raise


def remove(path):
    """Remove `path`; a file already gone is no fault."""
    try:
        os.remove(path)
    except OSError:
        pass
