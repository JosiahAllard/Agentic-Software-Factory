"""A run started again outside its caller (machine_lock.py run|background --detach), with no
console window.

Why WMI: Win32_Process.Create makes the new process a child of WMI's provider host, not of the
caller, so it is in neither the caller's process tree nor its job object (a Claude Code shell's
job ends everything in it when the shell ends) and outlives the shell that started it.

Why the carrier: WMI gives a console program it starts a console of its own, and whether that
console shows a window rests on Win32_ProcessStartup.ShowWindow and on which terminal hosts it.
So the program WMI starts is `pythonw.exe` (no console) running the carrier tools/no_window.py
(kitlib.proc.spawn.carry), which starts the console python.exe running machine_lock.py with
CREATE_NO_WINDOW: a console with no window, which the run's commands share. The carrier waits
for that python and ends with its exit code. Nothing here relies on ShowWindow.

Why the marker: WMI's provider host exits when idle, and its pid may then be gone or reused
while the run goes on, so the process tree alone stops showing that WMI started it. `create`
marks the process it starts (store.DETACHED_ENV, inherited by everything under it), and
store.caller reads a marked process as an agent's. When the environment it is given is marked
already, the launcher is itself inside such a start: the ancestry it recorded in
MACHINE_LOCK_ORIGIN stops at WMI and tells nothing, so `create` drops it, and the new run's role
from its launcher (store.tree_role, over no ancestry) is agent. Both only ever lower a role.

`command` builds the argv, `create` asks WMI, `wrapper_pid` finds the python the carrier
started (the run's wrapper, whose pid the run record holds). Nothing here prints. The carrier's
line when it cannot start its program is kitlib.proc.console.note_carrier_failure, the one
writer of the kit's console incident log.

On Linux each call goes to kitlib.platform.linux.detach (a setsid trampoline, the run in a
systemd user scope of its own where there is one; no carrier, no WMI): `command`, `tool_command`,
`missing`, `create` and `wrapper_pid` keep their meaning, `marked` is shared.
"""
import importlib
import json
import os
import subprocess
import time

import kitlib.platform
from kitlib.lock.run import ORIGIN_ENV
from kitlib.lock.store import DETACHED_ENV
from kitlib.proc import process, spawn


# Reads {"cmd", "cwd", "env"} as JSON on stdin; prints "<ReturnValue> <ProcessId>".
CREATE_PS = ("$d = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
             "$si = New-CimInstance -ClassName Win32_ProcessStartup -Property "
             "@{EnvironmentVariables=[string[]]$d.env} -ClientOnly; "
             "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
             "@{CommandLine=$d.cmd; CurrentDirectory=$d.cwd; ProcessStartupInformation=$si}; "
             "\"$($r.ReturnValue) $($r.ProcessId)\"")
CREATE_TIMEOUT_S = 60
# Win32_Process.Create's documented return values; a refusal names the meaning, not the number.
WMI_CODES = {0: 'successful completion', 2: 'access denied', 3: 'insufficient privilege',
             8: 'unknown failure', 9: 'path not found', 21: 'invalid parameter'}
# Code 8 with no ProcessId (or 0) is asked again, up to CREATE_TRIES starts in all, RETRY_DELAY_S apart (it runs inside a
# land submit, so short). Why that is safe, that a code 8 started nothing:
#   observed: the two land submits refused on it (2026-11-04) got no ProcessId back, and their
#     output logs hold only the submission's header, where a landing that had started would have
#     appended its own output or its untrusted-payload refusal;
#   inferred (not verified here): the WMI provider maps a failed CreateProcess, other than the
#     access, privilege, path and parameter failures, to 8, so no process exists.
# Every other code, a failed or timed-out PowerShell and output that is no code are final.
RETRY_CODE = 8
CREATE_TRIES = 3
RETRY_DELAY_S = 1.0
WRAPPER_WAIT_S = 10.0       # how long wrapper_pid looks for the carrier's python
WRAPPER_POLL_S = 0.05


def _linux():
    """kitlib.platform.linux.detach on Linux (imported at call time), else None."""
    if kitlib.platform.NAME == 'linux':
        return importlib.import_module('kitlib.platform.linux.detach')
    return None


def pythons(python):
    """(pythonw.exe, python.exe) in the folder of `python` (either of the two). Neither is checked
    to exist here. Not the hooks' windowless() (kitlib/host/hookmain.py) or machine setup's
    pythonw(): both fall back to a console python where this must refuse (`missing`), and importing
    either puts the guard, the gate and the backlog in the lock tool's import reach, which a release
    may then not change under a live run (kitlib.lock.release.LOCK_PATHS, tests/lock/test_release.py)."""
    python = os.path.abspath(python)
    return spawn.pythonw_beside(python), os.path.join(os.path.dirname(python), 'python.exe')


def command(python, tool, head, rid, tail):
    """The detached run's argv: the pythonw beside `python`, the carrier beside `tool`, the
    python.exe beside `python`, then `-B tool` with `head` (the caller's arguments before `--`),
    `--detached-id rid` and `tail` (`--` and the command). Linux: `python -B tool ...`."""
    if _linux():
        return _linux().command(python, tool, head, rid, tail)
    pythonw, console = pythons(python)
    carrier = os.path.join(os.path.dirname(os.path.abspath(tool)), spawn.CARRIER)
    return ([pythonw, carrier, console, '-B', os.path.abspath(tool)] + list(head)
            + ['--detached-id', rid] + list(tail))


def tool_command(python, tool, args):
    """A detached tool's argv (the gate's detached landing): `command`'s shape without the lock's
    --detached-id, so `missing` and `create` take it."""
    if _linux():
        return _linux().tool_command(python, tool, args)
    pythonw, console = pythons(python)
    tool = os.path.abspath(tool)
    carrier = os.path.join(os.path.dirname(tool), spawn.CARRIER)
    return [pythonw, carrier, console, '-B', tool] + list(args)


def missing(argv):
    """Why `command`'s argv cannot start (its pythonw, carrier or python.exe is not a file; Linux:
    its python or tool), or None."""
    if _linux():
        return _linux().missing(argv)
    for what, path in zip(('pythonw.exe', 'the carrier', 'python.exe'), argv[:3]):
        if not os.path.isfile(path):
            return '%s is not a file: %s' % (what, path)
    return None


def marked(env):
    """A copy of `env` (a dict) for a detached start: DETACHED_ENV set, and ORIGIN_ENV dropped
    when `env` was marked already (the launcher is inside a detached start itself)."""
    out = dict(env)
    if out.get(DETACHED_ENV):
        out.pop(ORIGIN_ENV, None)
    out[DETACHED_ENV] = '1'
    return out


def create(argv, cwd, env, run=None, timeout=CREATE_TIMEOUT_S, sleep=None):
    """(pid, None) of the process Win32_Process.Create started for `argv` in `cwd` with `env`
    (a dict, marked as a detached start: `marked`), else (None, why). A code 8 is asked again
    (CREATE_TRIES, RETRY_DELAY_S); a refusal names a code's meaning (WMI_CODES). run: the tests'
    stand-in for spawn.run (looked up at call time); on Linux, for subprocess.Popen (the
    trampoline's start). sleep: the tests' stand-in for time.sleep."""
    env = marked(env)
    if _linux():
        return _linux().create(argv, cwd, env, popen=run, timeout=timeout)
    data = json.dumps({'cmd': subprocess.list2cmdline(argv), 'cwd': cwd,
                       'env': ['%s=%s' % kv for kv in env.items()]})
    tries = 0
    while True:
        tries += 1
        try:
            r = (run or spawn.run)(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                                    CREATE_PS],
                                   input=data, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            return None, 'failed%s: %s' % (' after %d tries' % tries if tries > 1 else '', e)
        parts = (r.stdout or '').split()
        if not r.returncode and len(parts) == 2 and parts[0] == '0' and parts[1].isdigit():
            return int(parts[1]), None
        code = int(parts[0]) if not r.returncode and parts and parts[0].isdigit() else None
        # Only a code 8 with no pid (or pid 0) is asked again: a pid back means a process may
        # have started, and asking again could start a second one.
        pid = parts[1] if len(parts) > 1 and parts[1] != '0' else None
        if (code == RETRY_CODE and parts in (['8'], ['8', '0'])
                and tries < CREATE_TRIES):
            (sleep or time.sleep)(RETRY_DELAY_S)
            continue
        if code is None or code == 0:   # 0 with no pid is output that is no start either
            got = '%r' % (r.stdout or '').strip()
        else:
            got = 'WMI code %d (%s)%s%s' % (code, WMI_CODES.get(code, 'undocumented'),
                                            ' with pid %s' % pid if pid else '',
                                            ' after %d tries' % tries if tries > 1 else '')
        err = (r.stderr or '').strip()[-300:]
        return None, 'got %s (exit %s)%s' % (got, r.returncode, ': %s' % err if err else '')


def wrapper_pid(carrier, wait_s=WRAPPER_WAIT_S, poll_s=WRAPPER_POLL_S, snapshot=process.snapshot,
                ctime=process.proc_ctime, sleep=time.sleep, clock=time.monotonic):
    """The pid of the python.exe the carrier `carrier` started, or None when none was seen within
    `wait_s` seconds (the carrier and its python have ended already, or a snapshot failed). A
    process with the carrier's pid as parent but created before the carrier holds a reused pid and
    is not it. Linux: `carrier` is the run itself."""
    if _linux():
        return _linux().wrapper_pid(carrier)
    start = ctime(carrier)
    if start is None:
        return None
    limit = clock() + wait_s
    while True:
        try:
            for pid, ppid, name in snapshot():
                if ppid == carrier and pid != carrier and name.lower() == 'python.exe':
                    c = ctime(pid)
                    if c is not None and c >= start:
                        return pid
        except OSError:
            return None
        if clock() >= limit or ctime(carrier) != start:
            return None
        sleep(poll_s)
