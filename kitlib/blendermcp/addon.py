"""Blender and the Blender Lab MCP add-on on this machine (docs/blender-mcp.md "Setup"): which
blender to start, and whether the add-on it would load is the one that was reviewed.

The add-on is GPL and is never copied into the kit: the bridge starts the owner's own install and
checks it first. The pin is the add-on's tree hash (tree_hash): SHA-256 over each file's path
relative to the add-on folder ('/' separated), a NUL, the file's own SHA-256 in hex and a newline,
in sorted walk order, __pycache__ skipped. An add-on whose hash differs (another version, or an
edited copy) is refused: an update is reviewed and pinned again here.
"""
import glob
import hashlib
import os
import re
import shutil

import kitlib.platform

ADDON_ID = 'mcp'
ADDON_VERSION = '1.0.3'
# The reviewed add-on: Blender Lab MCP v1.0.3, the 9 files of its zip (zip SHA-256
# A7A9DA816192502E5A0A202A396444E266B47D8FC4F74AD4698048BD43040707).
TREE_SHA256 = '98960c572d06ebce315192b2bc3d7eb4b279090eb3610cc276452dca86c832de'
MIN_VERSION = (5, 1)                    # the add-on's blender_version_min
# The add-on's CLI command, and the module whose stop() ends its server (and so the background
# Blender) cleanly.
COMMAND = 'blender_mcp'
SERVER_MODULE = 'bl_ext.user_default.mcp.mcp_to_blender_server'
BLENDER_ENV = 'PM_KIT_BLENDER'          # a blender executable to use instead of the search
_VERSION_RE = re.compile(r'Blender (\d+)\.(\d+)')


class SetupError(Exception):
    """Blender or the add-on is missing, too old, or not the reviewed one; the message says what
    to do."""


def tree_hash(folder):
    """The add-on tree hash of `folder` (module docstring)."""
    h = hashlib.sha256()
    for d, dirs, files in os.walk(folder):
        dirs[:] = sorted(x for x in dirs if x != '__pycache__')
        for name in sorted(files):
            path = os.path.join(d, name)
            rel = os.path.relpath(path, folder).replace(os.sep, '/')
            with open(path, 'rb') as f:
                digest = hashlib.sha256(f.read()).hexdigest()
            h.update(rel.encode('utf-8') + b'\0' + digest.encode('ascii') + b'\n')
    return h.hexdigest()


def extensions_root(version, env=None, platform=None):
    """The user extensions folder Blender `version` ('5.2') reads: BLENDER_USER_EXTENSIONS when
    set, else %APPDATA%/Blender Foundation/Blender/<version>/extensions on Windows and
    $XDG_CONFIG_HOME (default ~/.config)/blender/<version>/extensions on Linux."""
    env = os.environ if env is None else env
    platform = platform or kitlib.platform.NAME
    if env.get('BLENDER_USER_EXTENSIONS'):
        return env['BLENDER_USER_EXTENSIONS']
    if platform == 'windows':
        if not env.get('APPDATA'):
            raise SetupError('APPDATA is not set, so the Blender add-on folder cannot be found')
        return os.path.join(env['APPDATA'], 'Blender Foundation', 'Blender', version, 'extensions')
    base = env.get('XDG_CONFIG_HOME') or os.path.join(env.get('HOME') or os.path.expanduser('~'),
                                                      '.config')
    return os.path.join(base, 'blender', version, 'extensions')


def addon_dir(version, env=None, platform=None):
    """Where Blender `version` loads the add-on from: its user_default repository's `mcp`."""
    return os.path.join(extensions_root(version, env, platform), 'user_default', ADDON_ID)


def check(folder):
    """Refuses (SetupError) an add-on folder that is missing or whose tree hash is not the pin."""
    if not os.path.isdir(folder):
        raise SetupError('the Blender MCP add-on is not installed at %s: install Blender Lab MCP '
                         'v%s for this Blender (docs/blender-mcp.md "Setup")' % (folder, ADDON_VERSION))
    got = tree_hash(folder)
    if got != TREE_SHA256:
        raise SetupError('the Blender MCP add-on at %s is not the reviewed v%s (its tree hash is %s, '
                         'the pin is %s): reinstall v%s, or have the update reviewed and pinned in '
                         'kitlib/blendermcp/addon.py' % (folder, ADDON_VERSION, got, TREE_SHA256,
                                                         ADDON_VERSION))


def _version_key(path):
    m = _VERSION_RE.search(os.path.basename(os.path.dirname(path)))
    return (int(m.group(1)), int(m.group(2))) if m else (-1, -1)


def find_blender(env=None, which=shutil.which, globber=glob.glob, platform=None):
    """The blender executable: PM_KIT_BLENDER when set (it must be a file), else `blender` on
    PATH, else (Windows) the newest %ProgramFiles%/Blender Foundation/Blender <x.y>/blender.exe.
    SetupError when there is none."""
    env = os.environ if env is None else env
    platform = platform or kitlib.platform.NAME
    named = env.get(BLENDER_ENV)
    if named:
        if not os.path.isfile(named):
            raise SetupError('%s names %s, which is not a file' % (BLENDER_ENV, named))
        return named
    found = which('blender')
    if found:
        return found
    if platform == 'windows':
        roots = [env.get(k) for k in ('ProgramFiles', 'ProgramW6432') if env.get(k)]
        hits = []
        for root in roots:
            hits += globber(os.path.join(root, 'Blender Foundation', 'Blender *', 'blender.exe'))
        hits = [h for h in hits if _version_key(h) != (-1, -1)]
        if hits:
            return max(hits, key=_version_key)
    raise SetupError('no Blender found: put blender on PATH, or set %s to its executable '
                     '(Blender %d.%d or newer)' % (BLENDER_ENV, MIN_VERSION[0], MIN_VERSION[1]))


def version_of(exe, run=None):
    """(major, minor) of the blender at `exe`: from its folder's name ('Blender 5.2') when it has
    one, else from `blender --version` (run: kitlib.proc.spawn.run by default). SetupError when
    neither tells."""
    key = _version_key(exe)
    if key != (-1, -1):
        return key
    if run is None:
        from kitlib.proc import spawn
        run = spawn.run
    import subprocess
    try:
        r = run([exe, '--version'], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                errors='replace', timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise SetupError('could not ask %s its version: %s' % (exe, e))
    m = _VERSION_RE.search(r.stdout or '')
    if not m:
        raise SetupError('%s --version did not name a Blender version' % exe)
    return int(m.group(1)), int(m.group(2))


def ready(env=None, which=shutil.which, globber=glob.glob, run=None, platform=None):
    """The blender executable to start, once Blender is new enough and its add-on is the pinned
    one. SetupError otherwise."""
    exe = find_blender(env, which, globber, platform)
    version = version_of(exe, run)
    if version < MIN_VERSION:
        raise SetupError('Blender %d.%d at %s is older than the add-on needs (%d.%d)'
                         % (version + (exe,) + MIN_VERSION))
    check(addon_dir('%d.%d' % version, env, platform))
    return exe


def launch_argv(exe, port):
    """The command that starts a background Blender serving the add-on on 127.0.0.1:port. The
    add-on needs Blender's --online-mode even on loopback. --command hands the rest of the line
    to the add-on's own parser, which refuses any option but --host and --port, so Blender's
    options come before it and --command comes last. --python-use-system-env lets Blender's
    Python read the environment (launch_env), which it ignores by default."""
    return [exe, '--background', '--python-use-system-env', '--online-mode', '--command', COMMAND,
            '--host', '127.0.0.1', '--port', str(port)]


def launch_env(env, pycache):
    """The environment a Blender is started with: `env` without any PYTHON* variable, so the
    caller's PYTHONPATH or PYTHONHOME never reaches Blender's Python, plus PYTHONPYCACHEPREFIX
    `pycache` (a fresh empty folder), PYTHONDONTWRITEBYTECODE and PYTHONNOUSERSITE. Python then
    reads byte code only under that prefix, never from a __pycache__ beside a source, so a .pyc
    planted in the add-on's own __pycache__ (which the tree hash skips) is never run; it writes
    none, and the user site-packages stay off, as Blender has them by default."""
    out = {k: v for k, v in env.items() if not k.upper().startswith('PYTHON')}
    out.update(PYTHONPYCACHEPREFIX=pycache, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    return out
