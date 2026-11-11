"""Where the plugin's export is and what marks one (docs/plugin-source.md): the machine config's
`plugin` settings, the export root, the marker in each export folder, the live pointer file and
the marketplace file's path, and the kit checkout a printed command names.

  default_export_dir()             the export root with no plugin.export_dir: %LOCALAPPDATA%\\pm-kit\\
                                   plugin, on Linux <profile>/.local/share/pm-kit/plugin
  settings(obj) / load()           the checked Settings of the machine config's `plugin` object
  live_pointer_path() / read_live_pointer(path)
                                   plugin-live.json beside the default export root: the live folder
                                   for a reader with no kitlib (docs/plugin-source.md)
  read_marker(folder)              an export folder's marker
  export_checkout(folder)          the main checkout its marker names, while it still is one, or None
  current_folder(root)             the folder <root>/.claude-plugin/marketplace.json names
  live_root() / refresh_cmd()      the kit a printed command names, and the refresh command

RefreshError and StateError are the package's errors. It reads only.
"""
import collections
import datetime
import json
import os
import posixpath
import re
import shutil
import stat
import sys

from kitlib import kitroot
from kitlib import platform as kitplatform
from kitlib.git import refs
from kitlib.host import registry


MAIN = 'main'
DEFAULT_REMOTE = 'origin'
MARKER = '.pm-kit-export'            # in every export folder: this tool made it (prune reads it)
LIVE_POINTER = 'plugin-live.json'    # beside the default export root: names the live export folder
MANIFEST = '.claude-plugin/plugin.json'
MARKETPLACE = '.claude-plugin/marketplace.json'
CONFIG_KEY = 'plugin'
_SHA12 = re.compile(r'^[0-9a-f]{12}$')


class RefreshError(Exception):
    """A refusal: the run stops, with this reason, and changes nothing more."""


class StateError(Exception):
    """state.json exists but cannot be read as a state."""


# ---------------------------------------------------------------- settings

Settings = collections.namedtuple('Settings', 'export_dir auto_refresh remote live_pointer',
                                  defaults=(None,))      # live_pointer None: write no pointer


def _norm(path):
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _under(path, top):
    """True when `path` is `top` or inside it."""
    p, t = _norm(path), _norm(top)
    return p == t or p.startswith(t.rstrip(os.sep) + os.sep)


def placement_errors(root, kit=None, sweep_dir=None):
    """Errors for an export root inside the kit checkout (the export would then be part of the
    tree it exports, and a worktree clean could take it) or under the agent temp sweep root
    (agent_temp_sweep.dir, whose sweep removes unused entries)."""
    errs = []
    if kit and _under(root, kit):
        errs.append('plugin.export_dir %s is inside the kit checkout %s: put it outside' % (root, kit))
    if isinstance(sweep_dir, str) and sweep_dir.strip() and os.path.isabs(sweep_dir) \
            and _under(root, sweep_dir):
        errs.append('plugin.export_dir %s is under the agent temp sweep root %s (agent_temp_sweep.dir), '
                    'which removes unused entries: put it elsewhere' % (root, sweep_dir))
    return errs


def check(obj, kit=None, sweep_dir=None):
    """Errors in the machine config's `plugin` object (kitlib.config.user registers the key as an
    object; its inner keys are checked here). With `kit` or `sweep_dir`, an export_dir inside
    either is an error too (placement_errors)."""
    errs = []
    if obj is None:
        return errs
    if not isinstance(obj, dict):
        return ['plugin must be an object']
    for k in sorted(obj):
        if k.startswith('_'):
            continue
        v = obj[k]
        if k == 'export_dir':
            if not isinstance(v, str) or not v.strip() or not os.path.isabs(v):
                errs.append('plugin.export_dir must be an absolute path, not %r' % (v,))
            else:
                errs.extend(placement_errors(v, kit, sweep_dir))
        elif k == 'auto_refresh':
            if not isinstance(v, bool):
                errs.append('plugin.auto_refresh must be true or false, not %r' % (v,))
        elif k == 'remote':
            if not isinstance(v, str) or refs.bad_rev(v) or not v.strip():
                errs.append('plugin.remote must be a git remote name, not %r' % (v,))
        else:
            errs.append('plugin.%s is not a known key (export_dir, auto_refresh, remote)' % k)
    return errs


LINUX_EXPORT = ('.local', 'share', 'pm-kit', 'plugin')    # under the Linux profile folder


def default_export_dir(environ=None):
    """The export root when the machine config sets no plugin.export_dir, the one default every
    reader (settings, so kitlib.gate.pin, kitlib.kitcmd and the installers) gets. Windows (and
    any platform but Linux): %LOCALAPPDATA%\\pm-kit\\plugin, LOCALAPPDATA from `environ`. Linux:
    <profile>/.local/share/pm-kit/plugin, the profile from the password database
    (kitlib.platform.linux.paths.profile), never $HOME or $XDG_DATA_HOME: a cron job, a systemd
    unit and a login shell of the user all get the folder the managed settings name, as they all
    get the same PM-repo pointer. RefreshError when it cannot be told, a Python with no password
    database module (no pwd: Linux patched in on Windows) included, so a caller that skips the
    export on RefreshError (kitlib.guard.rules.advice_kit) skips it there too."""
    if kitplatform.NAME == 'linux':
        from kitlib.platform.linux import paths as linux_paths
        try:
            profile = linux_paths.profile()
        except (OSError, ImportError) as e:
            raise RefreshError('the profile folder cannot be read (%s): set plugin.export_dir in '
                               'the machine config' % e)
        return posixpath.join(profile, *LINUX_EXPORT)
    env = os.environ if environ is None else environ
    base = env.get('LOCALAPPDATA')
    if not base or not os.path.isabs(base):
        raise RefreshError('LOCALAPPDATA is not set: set plugin.export_dir in the machine config')
    return os.path.join(base, 'pm-kit', 'plugin')


def live_pointer_path(environ=None):
    """The one fixed place of the live pointer: LIVE_POINTER beside the default export root, so a
    reader with no kitlib and no machine config finds it, whatever plugin.export_dir says.
    RefreshError as default_export_dir's, and under the kit's tests (the home fence's marker in the
    environment): a test that reaches settings() with the real environment never gets the real
    machine's pointer path."""
    from kitlib.config import user
    if (os.environ if environ is None else environ).get(user.FENCE_ENV):
        raise RefreshError('the live pointer is not placed under the kit\'s tests (%s is set)'
                           % user.FENCE_ENV)
    default = default_export_dir(environ)
    if kitplatform.NAME == 'linux':
        return posixpath.join(posixpath.dirname(default), LIVE_POINTER)
    return os.path.normpath(os.path.join(os.path.dirname(default), LIVE_POINTER))


def settings(obj, environ=None, kit=None, sweep_dir=None):
    """The checked Settings of a `plugin` object (None: every default). The default export_dir is
    default_export_dir's; RefreshError when that is needed and cannot be told, and when the
    export root is inside `kit` or `sweep_dir` (placement_errors)."""
    errs = check(obj)
    if errs:
        raise RefreshError('; '.join(errs))
    obj = obj or {}
    root = obj.get('export_dir') or default_export_dir(environ)
    errs = placement_errors(root, kit, sweep_dir)
    if errs:
        raise RefreshError('; '.join(errs))
    try:
        pointer = live_pointer_path(environ)
    except RefreshError:
        pointer = None                  # the fixed place cannot be told here: no pointer
    return Settings(os.path.normpath(root), obj.get('auto_refresh') is True,
                    obj.get('remote') or DEFAULT_REMOTE, pointer)


def load(home=None, environ=None, kit=None):
    """The machine's plugin Settings (kitlib.config ConfigError for a bad machine config). The
    export root must lie outside `kit` (default: this checkout) and agent_temp_sweep.dir."""
    from kitlib.config import user
    cfg = user.user_config(home)
    sweep = cfg.get('agent_temp_sweep')
    return settings(cfg.get(CONFIG_KEY), environ, kit or kit_root(),
                    sweep.get('dir') if isinstance(sweep, dict) else None)


def kit_root():
    """The checkout this module is in."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


LIVE_ROOT_ENV = 'KIT_LIVE_ROOT'      # set by kitlib.gate.pin for a tool it runs from an export


def live_root(kit=None, environ=None):
    """The kit checkout a printed command names. A checkout (`kit`, default: this one, holding no
    MARKER) names itself. An export (it holds MARKER) names KIT_LIVE_ROOT when the variable names
    a folder, else the main checkout its marker records (export_checkout) when that holds kit.json
    and a tools folder (a test's live kit fixture makes one), else itself. A tool pinned to an
    export of pushed main (kitlib.gate.pin), or run from the plugin's export with no KIT_LIVE_ROOT
    (the session start hook, pm_start), runs from a folder a later prune may remove."""
    kit = kit or kit_root()
    if not os.path.isfile(os.path.join(kit, MARKER)):
        return kit
    env = os.environ if environ is None else environ
    live = env.get(LIVE_ROOT_ENV)
    if live and os.path.isdir(live):
        return live
    checkout = export_checkout(kit)
    if checkout and os.path.isfile(os.path.join(checkout, 'kit.json')) \
            and os.path.isdir(os.path.join(checkout, 'tools')):
        return os.path.normpath(checkout)
    return kit


def refresh_cmd(migrate=False):
    """`python "<kit>/tools/plugin_refresh.py" [--migrate] --apply`, the kit (live_root) named in
    full: the line reaches a PM whose session runs in another folder, and no repository holds a
    copy of the kit's tools (decision 21 in docs/multi-host-plan.md section 7)."""
    return '%s%s --apply' % (kitroot.command('plugin_refresh', kit=live_root()),
                             ' --migrate' if migrate else '')


def source_kit(kit):
    """The checkout git runs against for `kit`: `kit` itself, or, when `kit` is an export (it holds
    MARKER; the plugin's own code runs from one), the main checkout its marker records
    (export_checkout). RefreshError when an export names no checkout that still exists."""
    if not os.path.isfile(os.path.join(kit, MARKER)):
        return kit
    checkout = export_checkout(kit)
    if not checkout:
        raise RefreshError('%s is a plugin export and its marker names no source checkout that '
                           'still exists, so its refs cannot be read' % kit)
    return checkout


def tracking_main(kit, remote):
    """(sha of the kit's refs/remotes/<remote>/main, None), ('', None) without one, (None, why)."""
    return refs.ref_commit('refs/remotes/%s/%s' % (remote, MAIN), kit)


def _is_link(path):
    """True for a symlink, a junction or any other reparse point (or a path lstat cannot read)."""
    try:
        st = os.lstat(path)
    except OSError:
        return True
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & 0x400)


def _rmtree(path):
    """shutil.rmtree, clearing a read-only bit on the way. A failure other than removing one entry
    (rmtree refusing a link, a listing error) is raised, never passed over."""
    def retry(func, p, exc):
        if func not in (os.unlink, os.rmdir, os.remove):
            raise exc
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=lambda func, p, info: retry(func, p, info[1]))


def folder_name(sha):
    return sha[:12]


def read_marker(folder):
    """The marker of an export folder, or None (no folder, no marker, unreadable)."""
    try:
        with open(os.path.join(folder, MARKER), encoding='utf-8') as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def export_checkout(folder):
    """The kit main checkout an export folder was made from: its marker's `checkout`, when that
    names a folder holding a .git folder (a main checkout; a linked worktree's .git is a file).
    None for a folder with no marker, an older marker without the key, or a checkout that has
    moved or gone. kitlib.gate.pin pins a tool started from the export through it."""
    marker = read_marker(folder)
    checkout = marker.get('checkout') if marker else None
    if not isinstance(checkout, str) or not checkout:
        return None
    if not os.path.isdir(os.path.join(checkout, '.git')):
        return None
    return checkout


def default_kit(folder):
    """(kit checkout, None) or (None, why): tools/plugin_refresh.py's --kit default for a tool in
    `folder`. A checkout is its own kit. An export (it holds MARKER and has no .git) defaults to
    the main checkout its marker records (export_checkout); with none, it is refused, since the
    export cannot read the remote's main."""
    if not os.path.isfile(os.path.join(folder, MARKER)):
        return folder, None
    checkout = export_checkout(folder)
    if checkout:
        return checkout, None
    return None, '%s: this is an export; pass --kit <kit checkout>' % folder


# ---------------------------------------------------------------- the files in the export root

def _write_json(path, data):
    """Write `data` as JSON (no BOM) through a temp file and os.replace: a reader sees the old file
    or the new one, never half of one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
            json.dump(data, fh, indent=2)
            fh.write('\n')
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)              # a failed write leaves no half file behind
        except OSError:
            pass
        raise


def marketplace_path(root):
    return os.path.join(root, *MARKETPLACE.split('/'))


def read_live_pointer(path):
    """The live pointer at `path` as a dict, None when missing, unreadable or another shape."""
    try:
        with open(path, encoding='utf-8-sig') as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _pointed_folder(pointer, root):
    """The export folder name the live pointer names under `root`, else None (no pointer, another
    root, a name that is not a plain <sha12>)."""
    data = read_live_pointer(pointer) if pointer else None
    if not data or _norm(str(data.get('root') or '')) != _norm(root):
        return None
    name = data.get('folder')
    return name if isinstance(name, str) and _SHA12.match(name) else None


def current_folder(root):
    """The folder name marketplace.json names, or None (no file, unreadable, another shape)."""
    try:
        with open(marketplace_path(root), encoding='utf-8-sig') as fh:
            data = json.load(fh)
        for p in data.get('plugins') or []:
            if p.get('name') == registry.PLUGIN_NAME and isinstance(p.get('source'), str):
                src = p['source']
                return src[2:] if src.startswith('./') else src
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    return None


def _stamp(now=None):
    return (now or datetime.datetime.now()).isoformat(timespec='seconds')
