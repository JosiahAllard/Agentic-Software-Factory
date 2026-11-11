"""Finding a running Claude session by its Remote Control name (docs/launcher.md "Lookup").

A launched session is a claude.exe with a command line, not the Store app's (under WindowsApps);
on Linux a process named as the claude host's Linux process_names (claude, node) with one.
Its name is the --remote-control argument (else -n), matched exactly and case-sensitively as a whole token:
"x-a1" never matches "x-a1-r2". Process rows are dicts with Name, CommandLine, ExecutablePath,
ProcessId and ParentProcessId (kitlib.proc.listing.list_processes, or a test's list).
"""
import re

from kitlib import platform
from kitlib.host import registry
from kitlib.launcher.settings import LaunchError
from kitlib.proc import listing

STORE_DIR = chr(92) + "WindowsApps" + chr(92)
_RC = re.compile(r"""(?:^|\s)--remote-control(?:\s+|=)(?:"([^"]*)"|'([^']*)'|(\S+))""")


def session_images(path=None):
    """The process images a host's launched session runs as (each hosts.json profile's
    session_process; claude.exe on Claude), matched exactly against a process's Name. On Linux the
    running platform's process_names over every host (registry.process_names): claude, node, codex."""
    if platform.NAME == 'linux':
        return frozenset(registry.process_names(path))
    return frozenset(registry.union('session_process', path))


def remote_control_name(cmd):
    """The --remote-control value of a session's command line, or None (rc_name uses it too)."""
    m = _RC.search(cmd or "")
    return next((g for g in m.groups() if g), None) if m else None


def cli_names(path=None):
    """((cli_program, cli_package), ...) of every host in hosts.json, for runs_host_cli."""
    return tuple((p.cli_program, p.cli_package) for p in registry.profiles(path))


def _in_package(parts, package):
    """True when the path parts hold node_modules followed directly by the scoped package's two
    parts (@scope, name), with the file below them."""
    seg = package.split("/")
    return any(parts[i] == "node_modules" and parts[i + 1:i + 1 + len(seg)] == seg
               for i in range(len(parts) - len(seg) - 1))


def runs_host_cli(cmd, clis):
    """True when a Linux command line (/proc's arguments joined by spaces) runs one of `clis`,
    ((program, scoped npm package), ...): its program, or the first word after it that is not an
    option (node's script), has the program as its last path part or lies in the package
    directly under a node_modules folder. `node app.js --remote-control x` runs none, nor
    `node ~/src/claude-code/x.js`."""
    words = (cmd or "").split()
    script = next((w for w in words[1:] if not w.startswith("-")), "")
    for word in words[:1] + [script]:
        parts = word.split("/")
        if any(parts[-1] == program or _in_package(parts, package) for program, package in clis):
            return True
    return False


def linux_clis(path=None):
    """cli_names() on Linux, for runs_a_cli; None elsewhere (a Windows image is the CLI's own)."""
    return cli_names(path) if platform.NAME == 'linux' else None


def runs_a_cli(row, clis):
    """A process row runs a host's CLI: always when clis is None (linux_clis off Linux)."""
    return clis is None or runs_host_cli(row.get("CommandLine"), clis)


def live_session_names(procs):
    """Remote Control names of launched sessions (session_images); the Store app (under WindowsApps) is not one,
    nor on Linux a process that runs no host's CLI (runs_host_cli)."""
    names = set()
    images = session_images()
    clis = linux_clis()
    for p in procs:
        if (p.get("Name") in images and p.get("CommandLine")
                and STORE_DIR not in (p.get("ExecutablePath") or "") and runs_a_cli(p, clis)):
            n = remote_control_name(p["CommandLine"])
            if n:
                names.add(n)
    return names


def down_entries(sessions, alive):
    """[(key, entry)] of the sessions.json entries that should run and do not: an object with a
    name, `autostart` not false, and no live session of exactly that name in `alive` (exact,
    case-sensitive, as find_exact). The PM checklist's coordinator lines
    and the hub's coordinator restart job (kitlib.hubjobs.coord_restart) both read it."""
    return [(key, e) for key, e in sessions.items()
            if isinstance(e, dict) and e.get("name") and e.get("autostart") is not False
            and e["name"] not in alive]


_N = re.compile(r"""(?:^|\s)-n\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")


def rc_name(cmd):
    """The session's name: its --remote-control value, else, for a session launched without it
    (launch.py start --no-remote-control), its -n value; None for neither."""
    n = remote_control_name(cmd)
    if n:
        return n
    m = _N.search(cmd or '')
    return next((g for g in m.groups() if g), None) if m else None
list_processes = listing.list_processes


def _default_profile():
    try:
        return registry.default_profile()
    except registry.RegistryError as e:
        raise LaunchError('cannot tell a session process on Linux: %s' % e)


def _session_images():
    """The process names a launched session runs as: claude.exe; on Linux the default host's
    (the claude host's) Linux process_names in hosts.json (claude, node). LaunchError when
    hosts.json cannot be read."""
    if platform.NAME != 'linux':
        return ('claude.exe',)
    return tuple(dict(_default_profile().process_names).get('linux') or ())


def runs_cli(cmd):
    """On Linux: `cmd` runs the default host's CLI (hosts.json cli_program and cli_package;
    runs_host_cli), so a node process with `-n x` that runs anything else is no session and
    stop never ends it. LaunchError when hosts.json cannot be read."""
    prof = _default_profile()
    return runs_host_cli(cmd, ((prof.cli_program, prof.cli_package),))


def launched(procs):
    """The running session processes of the default host (launched or not by the launcher)."""
    images = _session_images()
    linux = platform.NAME == 'linux'
    return [p for p in procs
            if p.get('Name') in images and p.get('CommandLine') and (not linux or runs_cli(p['CommandLine']))
            and STORE_DIR not in (p.get('ExecutablePath') or '')]


claude_sessions = launched       # the older name, kept for its callers


def find_exact(sessions, name):
    return [p for p in sessions if rc_name(p.get('CommandLine')) == name]


def of_stem(name, stem):
    """True when `name` is a session of `stem`: the stem itself, or <stem>-a<N>[-r<k>]."""
    return bool(name) and bool(stem) and (
        name == stem or re.match(re.escape(stem) + r'-a\d+(?:-r\d+)?\Z', name) is not None)


def running_stem(procs, stem):
    """True when a launched session is of `stem` (of_stem: any account, any recycle); a stem that
    only prefixes another's name (demo, demo-lane-a1) is not."""
    return any(of_stem(rc_name(p.get('CommandLine')), stem) for p in claude_sessions(procs))


def pids(rows):
    return ', '.join(str(p.get('ProcessId')) for p in rows)
