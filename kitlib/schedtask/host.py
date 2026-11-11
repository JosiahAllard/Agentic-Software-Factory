"""The host object that reads, registers and writes scheduled tasks: the real machine's side, which
tests replace with a fake. On Windows a task is registered by running its PowerShell line; on Linux
it is a pm-kit job (kitlib.platform.linux.schedule). A feature's own host object extends it with
the rest of what that feature asks of the machine.
"""
import subprocess

from kitlib.proc.job import no_window_flags

from . import read


class Host(object):
    """The real machine's scheduled tasks: PowerShell and schtasks on Windows, the pm-kit jobs on
    Linux."""

    def _ps(self, script):
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                           capture_output=True, text=True, creationflags=no_window_flags(0))
        if r.returncode != 0:
            raise RuntimeError('powershell exit %d: %s' % (r.returncode, (r.stderr or r.stdout).strip()))
        return r.stdout

    def task_exists(self, name):
        """Whether the scheduled task `name` is registered (read.task_registered: False only when
        schtasks says it does not exist; RuntimeError on a timeout or another failure)."""
        return read.task_registered(name)

    def task_register(self, line):
        """Run the PowerShell line that registers a scheduled task."""
        self._ps(line)

    def task_list(self):
        """Every scheduled task with its actions (read.read_tasks): RuntimeError, OSError or
        ET.ParseError when schtasks cannot be read. On Linux every pm-kit job read back."""
        return read.read_tasks(timeout=read.TASK_TIMEOUT)

    def job_backend(self, job):
        """Linux: the backend the pm-kit job `job` goes to (schedule.choose: ScheduleError when
        there is none)."""
        from kitlib.platform.linux import schedule
        return schedule.choose(job)

    def job_describe(self, job, kind):
        """Linux: the lines saying what job_install(job) writes (schedule.describe)."""
        from kitlib.platform.linux import schedule
        return schedule.describe(job, kind)

    def job_install(self, job):
        """Linux: write and enable the pm-kit job `job` (schedule.install); its lines."""
        from kitlib.platform.linux import schedule
        return schedule.install(job)

    def systemd(self):
        """Linux: whether the user's systemd manager answers."""
        from kitlib.platform.linux import schedule
        return schedule.has_systemd()

    def linger(self):
        """Linux: whether linger is on for this user (True, False, None: unknown)."""
        from kitlib.platform.linux import schedule
        return schedule.linger()
