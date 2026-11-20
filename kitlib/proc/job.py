"""Job objects: the one place a process tree is started pinned, measured and ended as a tree, on
the running platform. On Windows the Job is kitlib.platform.windows.job's (its docstring says why
a job never allows breakaway and how a mask past 64 logical processors is set), with what every
kit start gets added here; on Linux it is containment's (the end of this file).

No console window from a parent without a console: Job.start passes its flags through
no_window_flags. A console program started by a process with no console at all (the guard hook
under pythonw.exe) gets a new console, and Windows shows a window for it; CREATE_NO_WINDOW gives it
a console with no window. A parent that has a console, shown or hidden, keeps its children on that
console as before. The test is "attached to a console" (GetConsoleProcessList), not
GetConsoleWindow: measured, a python started with CREATE_NO_WINDOW, and an agent's
shell, have a console whose GetConsoleWindow() is 0. A caller that wants a visible window passes
CREATE_NEW_CONSOLE. A start that needs no job gets the same flags through kitlib.proc.spawn (run,
call, popen); tests/proc/test_spawn_sites.py holds every start in the kit to one of the two.
has_console lives in kitlib.proc.console, whose process-wide CreateProcess hook is the backstop
for a start that goes through neither.

Ending a tree and waiting for it: end_and_wait. Terminating a job only starts each process's end
(TerminateJobObject on Windows, the cgroup or member kill on Linux), and the job lists none of
them at once, so a caller that waits for its direct child alone can go on while a grandchild is
still ending. end_and_wait holds each process first and returns once all have ended. A brief
command run to its end, its whole tree ended on a timeout: run_short.
"""
import os
import select
import subprocess
import time

import kitlib.platform
from kitlib.platform import jobparts
from kitlib.platform.mask import cores_text  # noqa: F401  (job.cores_text stays a name)
from kitlib.platform.windows import job as _win, process as _winproc, win32

from . import bash
from .console import CONSOLE_CHOICE, has_console  # noqa: F401  (job.has_console stays a name)

JobError = jobparts.JobError


def no_window_flags(flags, console=None):
    """Popen creationflags for a child of this process: `flags` plus CREATE_NO_WINDOW when this
    process has no console (`console` False; None asks has_console()) and `flags` makes no console
    choice of its own (CREATE_NEW_CONSOLE, DETACHED_PROCESS or CREATE_NO_WINDOW). Otherwise
    `flags` unchanged, so a child of a console parent shares that console as before. Off Windows
    `flags` unchanged whatever `console` says: there is no console window to choose, and POSIX
    refuses any creationflags but 0."""
    if kitlib.platform.NAME != 'windows' or flags & CONSOLE_CHOICE:
        return flags
    if console is None:
        console = has_console()
    return flags if console else flags | win32.CREATE_NO_WINDOW


class Job(_win.Job):
    """kitlib.platform.windows.job.Job, whose start resolves bash and adds no_window_flags."""

    def start(self, cmd, before_resume=None, **kw):
        """kitlib.platform.windows.job.Job.start, with a bare leading bash run as Git Bash, never
        WSL's (kitlib.proc.bash; BareBashError, an OSError, when there is none), and
        `creationflags` through no_window_flags: from a parent with no console, no console
        window."""
        cmd = bash.resolve(cmd, kw.get("shell", False), kw.get("env"))
        kw["creationflags"] = no_window_flags(kw.pop("creationflags", 0))
        return super().start(cmd, before_resume, **kw)


GONE_WAIT = 5.0     # seconds a terminated job's processes have, together, to end
_POLL = 0.25


def end_and_wait(job):
    """Terminate `job` and return once each process it held has ended, GONE_WAIT seconds at most
    (read at each call). Each is held before the terminate: a terminated job lists none at once,
    while each end is only started (one with 1.5 GB to tear down was signalled ~45 ms later,
    2026-10-10). Held is a SYNCHRONIZE handle on Windows, a pidfd on Linux (it names the process,
    not its pid, and is ready once it has exited, a zombie included). A job that cannot be asked
    which processes it holds is terminated with nothing waited on; a process that ended before it
    was held is skipped. The terminate's OSError is raised; the handles are freed on every exit,
    Ctrl+C in the wait included. The wait runs in _POLL slices, so Ctrl+C reaches it."""
    try:
        pids = job.pids()
    except OSError:
        pids = []
    held = []
    try:
        for pid in pids:
            h = _hold_one(pid)
            if h is not None:
                held.append(h)
        job.terminate()
        end = time.monotonic() + GONE_WAIT
        for h in held:
            while True:
                left = end - time.monotonic()
                if left <= 0 or _ended(h, min(_POLL, left)):
                    break
    finally:
        for h in held:
            _release(h)


def run_short(argv, timeout, grace, **popen_kw):
    """`(returncode, stdout, stderr)` of argv run to its end in a Job(short=True) of its own,
    kill-on-close, with `popen_kw` given to its start. Every process argv starts is in that job
    from birth, its parent gone or not, so nothing it started outlives the call. After `timeout`
    seconds the job is ended (end_and_wait), its pipes get `grace` seconds to close, and
    subprocess.TimeoutExpired is raised; the closed job still ends the tree when end_and_wait
    fails. Raises OSError when argv cannot start."""
    j = Job(short=True)
    try:
        p = j.start(argv, **popen_kw)
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                end_and_wait(j)
            except OSError:
                pass
            try:
                p.communicate(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
            raise
        return p.returncode, out, err
    finally:
        j.close()


def _hold_one(pid):
    """A handle that is signalled when process pid ends (Linux: a pidfd), or None."""
    if kitlib.platform.NAME == 'linux':
        try:
            return os.pidfd_open(pid)
        except OSError:
            return None
    return _winproc.open_process(pid, _winproc.SYNCHRONIZE) or None


def _release(h):
    if kitlib.platform.NAME == 'linux':
        try:
            os.close(h)
        except OSError:
            pass
        return
    _winproc.close(h)


def _ended(h, seconds):
    """True when the process behind handle h has ended, waiting `seconds` at most."""
    if kitlib.platform.NAME == 'linux':
        poller = select.poll()
        poller.register(h, select.POLLIN)
        return bool(poller.poll(int(seconds * 1000)))
    return _winproc.K32.WaitForSingleObject(h, int(seconds * 1000)) != _winproc.WAIT_TIMEOUT


# On Linux the Job is containment's (kitlib.platform.linux.contain): the same methods and
# meanings on a cgroup scope, or on the weak tier it reports. Windows keeps the Job above.
if kitlib.platform.NAME == "linux":
    from kitlib.platform.linux import contain as _contain
    Job = _contain.Job      # noqa: F811
