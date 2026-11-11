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
"""
import kitlib.platform
from kitlib.platform import jobparts
from kitlib.platform.mask import cores_text  # noqa: F401  (job.cores_text stays a name)
from kitlib.platform.windows import job as _win, win32

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


# On Linux the Job is containment's (kitlib.platform.linux.contain): the same methods and
# meanings on a cgroup scope, or on the weak tier it reports. Windows keeps the Job above.
if kitlib.platform.NAME == "linux":
    from kitlib.platform.linux import contain as _contain
    Job = _contain.Job      # noqa: F811
