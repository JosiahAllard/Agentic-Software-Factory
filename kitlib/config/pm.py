"""kitlib.config.pm: the PM settings (docs/modules/config-design.md): the machine config's
`pm_start` section (load_config), the whole machine config read unchecked (machine_config), and
the repositories the PM knows (known_repos). The session starts, the hub and the launcher read
them here.
"""
import json
import os
import subprocess

from kitlib.config import user as kuser
from kitlib.config.types import ConfigError

KEY = "pm_start"
TIMEOUT = 4           # seconds the git ask of _toplevel waits


def section(key, path=None):
    """The dict at `key` of the machine config: the one reader of a key, with one failure meaning.
    None when the file is missing, the key is missing or its value is not an object ("not
    configured"). kitlib.config.ConfigError, naming the file, when the file is there but cannot be
    used (unreadable, bad JSON, not an object) and, with no `path`, when its location cannot be
    told (kitlib.config.user.locate BAD): a broken file is never read as "no config". A caller in
    an unattended process catches the error where it reports and skips its step."""
    p = os.path.expanduser(path) if path else kuser.readable_file()
    data = kuser._read_object(p)
    if data is not None and not isinstance(data, dict):
        raise ConfigError("%s: the machine config is not a JSON object" % p)
    cfg = (data or {}).get(key)
    return cfg if isinstance(cfg, dict) else None


def load_config(path=None):
    """The "pm_start" dict (section), or None when the file or key is missing."""
    return section(KEY, path)


def _read(path):
    """A JSON file's text; utf-8-sig because PowerShell writes the file with a BOM."""
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def machine_config(path=None):
    """The whole machine config as a dict ({} when the file is missing or is not a JSON object),
    read as load_config reads it: unchecked, so one bad key elsewhere does not stop the hub line.
    With no `path`, a PM repository's local.json is read too (its keys over config.json's: `hub`
    and `launcher` live there, kitlib.config.user.LOCAL_KEYS). ConfigError when its location
    cannot be told (kitlib.config.user.readable_file)."""
    def read(p):
        try:
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    if path:
        return read(os.path.expanduser(path))
    data = read(kuser.readable_file())
    loc = kuser.locate()
    if loc.state == kuser.OK and loc.local:
        data.update(read(loc.local))
    return data


def _toplevel(path):
    """The basename of the git repository holding path, or None outside one."""
    from kitlib.git import cmd as gitcmd     # here, not at the top: only this line needs git
    try:
        r = gitcmd.subprocess_run(["git", "-C", path, "rev-parse", "--show-toplevel"],
                                  capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    top = r.stdout.strip() if r.returncode == 0 else ""
    return os.path.basename(top.rstrip("/")) or None


def known_repos(cfg, read=None, toplevel=_toplevel, kit_dir=None):
    """Every repository the PM knows, sorted: each session's "repo" in sessions_file,
    the PM's own repository (the one holding sessions_file), this kit's repository, and any names
    in the optional "repos" list. The Action Hub builds every repo menu from these; nothing is
    hard-coded, so a new coordinator's repository appears on its own."""
    path = cfg.get("sessions_file")
    if not path:
        raise KeyError("sessions_file not configured")
    if read is None:
        read = _read
    names = {e["repo"] for e in json.loads(read(path)).values()
             if isinstance(e, dict) and isinstance(e.get("repo"), str) and e["repo"]}
    for d in (os.path.dirname(os.path.abspath(path)),
              kit_dir or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))):
        n = toplevel(d)
        if n:
            names.add(n)
    extra = cfg.get("repos") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) and x for x in extra):
        raise ValueError("pm_start.repos must be a list of names")
    names.update(extra)
    folded = {}
    for n in sorted(names):  # one entry per name, whatever its case
        folded.setdefault(n.lower(), n)
    return sorted(folded.values(), key=str.lower)
