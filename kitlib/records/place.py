"""Where the kit's records go: the machine's kit folders and their per-repository keys, and the
gate's log folders (docs/gate-records.md).

`<dev_drive>/pm-kit/<name>` (kit_folder) is one of the machine's kit folders: kit-gate-result
(gate.result), kit-gate-ok (records.gate_ok), kit-gate-budget (gate.budget) and the others, each
holding one folder per repository, named by repo_key: one name for every worktree of a
repository, another for every other one. With no `dev_drive` set, or a machine config that
cannot be read, there is no kit folder (NoKitFolder).

The gate's logs go under log_base: `.kit.json` "landing".log_dir, else `<kit temp>/<repo
name>-gate`, with fallback_log_base when the machine config cannot say; one branch's under
log_folder of it. One rule for the gate that writes the logs and every tool that reads them.
"""
import hashlib
import os
import re

from kitlib.config import user as kuser
from kitlib.config.types import ConfigError

KIT_DIR = 'pm-kit'      # <dev_drive>/pm-kit/kit-gate-result: the machine's kit folder
KEY_NAME_MAX = 40       # the readable part of a repo key; its hash keeps keys apart
NO_DEV_FOR = ('the machine config sets no dev_drive (the Dev Drive\'s root folder), so there is no '
              '%s (<dev_drive>\\pm-kit\\%s); the owner sets it (docs/kit-json.md "Machine config '
              'keys")')
SAFE = frozenset('abcdefghijklmnopqrstuvwxyz0123456789._-')


class NoKitFolder(Exception):
    """There is no kit folder: the machine config sets no dev_drive, or cannot be read."""


def kit_folder(name, what):
    """`<dev_drive>/pm-kit/<name>`: one of the machine's kit folders, each holding one folder per
    repository (repo_key): kit-gate-result, records.gate_ok's kit-gate-ok and
    gate.budget's kit-gate-budget. NoKitFolder when the machine config cannot be read or
    sets no dev_drive; `what` names the folder in it ("result folder")."""
    try:
        cfg = kuser.user_config()
    except Exception as e:      # noqa: BLE001 - ConfigError, OSError: no folder, and why
        raise NoKitFolder('the machine config cannot be read, so there is no %s: %s' % (what, e))
    dev = kuser.dev_drive(cfg)
    if not dev:
        raise NoKitFolder(NO_DEV_FOR % (what, name))
    return os.path.join(os.path.abspath(dev), KIT_DIR, name)


def repo_key(common):
    """The result folder name of the repository whose git common dir is `common`: its folder's
    name (the common dir's parent, for a `.git`), lower case, [a-z0-9_-] only and at most
    KEY_NAME_MAX long, then `-` and 12 hex of the sha256 of the common dir's real path
    (normcase): one name for every worktree of a repository, another for every other one."""
    real = os.path.normcase(os.path.realpath(common)).rstrip('\\/')
    head, base = os.path.split(real)
    name = os.path.basename(head) if base == '.git' else base
    slug = ''.join(c for c in name.lower() if c in SAFE and c != '.')[:KEY_NAME_MAX] or 'repo'
    return '%s-%s' % (slug, hashlib.sha256(real.encode('utf-8')).hexdigest()[:12])


def repo_name(cfg, root):
    return cfg.repo.get('name') or os.path.basename(os.path.normpath(root))


def slug_of(text):
    s = re.sub(r'[^A-Za-z0-9._-]+', '-', text or '').strip('-.')
    return s[:60] or 'gate'


def log_base(cfg, root, name, note):
    """Where gate runs of the repository at `root` keep their logs: `.kit.json` "landing".log_dir
    (repo-relative), else `<kit temp>/<repo name>-gate` (kitlib.config.user.work_temp of `root`:
    the checkout's drive, else %LOCALAPPDATA%/Temp on Windows; never an inherited TEMP). One rule
    for the gate that writes the logs and every tool that reads them (landed.py, which also reads
    fallback_log_base). A machine config work_temp refuses (ConfigError) gives fallback_log_base,
    and note(<why>)."""
    configured = cfg.section('landing').get('log_dir')
    if configured:
        return os.path.join(root, configured)
    try:
        kit = kuser.work_temp(root)
    except ConfigError as e:
        base = fallback_log_base(name)
        note('%s: the machine config could not be read (%s)' % (base, e))
        return base
    return os.path.join(kit, '%s-gate' % slug_of(name))


def fallback_log_base(name):
    """`<shared temp>/<repo name>-gate`: where a gate run's logs go when the default log_base
    cannot be made, and where every default log went before work_temp."""
    return os.path.join(kuser.shared_temp(), '%s-gate' % slug_of(name))


def log_folder(branch):
    """A branch's folder name under log_base."""
    return slug_of(branch)
