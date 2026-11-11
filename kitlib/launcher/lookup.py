"""Finding a running Claude session by its Remote Control name (docs/launcher.md "Lookup").

A launched session is a claude.exe with a command line, not the Store app's (under WindowsApps);
on Linux a process named as the claude host's Linux process_names (claude, node) with one.
Its name is the --remote-control argument (else -n), matched exactly and case-sensitively as a whole token:
"x-a1" never matches "x-a1-r2". Process rows are dicts with Name, CommandLine, ExecutablePath,
ProcessId and ParentProcessId (kitlib.proc.listing.list_processes, or a test's list).
"""
import re

from kitlib import platform, pmstart
from kitlib.host import registry
from kitlib.launcher.settings import LaunchError
from kitlib.proc import listing

_N = re.compile(r"""(?:^|\s)-n\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")


def rc_name(cmd):
    """The session's name: its --remote-control value, else, for a session launched without it
    (launch.py start --no-remote-control), its -n value; None for neither."""
    n = pmstart.rc_name(cmd)
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
    pmstart.runs_cli), so a node process with `-n x` that runs anything else is no session and
    stop never ends it. LaunchError when hosts.json cannot be read."""
    prof = _default_profile()
    return pmstart.runs_cli(cmd, ((prof.cli_program, prof.cli_package),))


def launched(procs):
    """The running session processes of the default host (launched or not by the launcher)."""
    images = _session_images()
    linux = platform.NAME == 'linux'
    return [p for p in procs
            if p.get('Name') in images and p.get('CommandLine') and (not linux or runs_cli(p['CommandLine']))
            and pmstart.STORE_DIR not in (p.get('ExecutablePath') or '')]


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
