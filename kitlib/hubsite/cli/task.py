"""kitlib.hubsite.cli.task: the scheduled task (Windows) or the systemd job or XDG autostart
entry (Linux) that starts the hub server, as setup and restart say it and the commands they print.
"""
import os

from kitlib import platform
from kitlib.hubsite import autostart as hub_autostart
from kitlib.hubsite import control
from kitlib.hubsite.cli import common


# restart that starts nothing: why, by control's outcome
NOT_STARTED = {
    control.NO_TASK: "there is no scheduled task '%s' (hub.py setup --autostart makes it), so "
                     "nothing was started" % hub_autostart.TASK_NAME,
    control.DISABLED: "the scheduled task '%s' is disabled, so nothing was started"
                      % hub_autostart.TASK_NAME,
    control.HAND_STARTED: "the server was started by hand, not by the scheduled task '%s', so "
                          "nothing was started" % hub_autostart.TASK_NAME,
}


TASK = "-TaskName '%s'" % hub_autostart.TASK_NAME


START_TASK = 'Start-ScheduledTask %s' % TASK


def linux():
    return platform.NAME == 'linux'


def xdg():
    """On Linux, the hub job is an XDG autostart entry: systemctl cannot start it."""
    return linux() and hub_autostart.linux_is_autostart()


def start_command():
    """The command that starts the task (Windows) or the systemd user service (Linux)."""
    if linux():
        return 'systemctl --user start %s' % hub_autostart.linux_unit()
    return START_TASK


def job():
    """What starts the server, in words: the scheduled task (Windows), the Linux systemd job or
    its XDG autostart entry."""
    if xdg():
        return "the XDG autostart entry '%s'" % os.path.basename(hub_autostart.linux_desktop_path())
    if linux():
        return "the job '%s'" % hub_autostart.linux_unit()
    return "the scheduled task '%s'" % hub_autostart.TASK_NAME


def not_started(outcome):
    """Why a restart started nothing, by control's outcome; the Linux job's words on Linux."""
    if not linux():
        return NOT_STARTED[outcome]
    unit = hub_autostart.linux_unit()
    if xdg():
        entry = os.path.basename(hub_autostart.linux_desktop_path())
        if outcome == control.DISABLED:
            return ("the XDG autostart entry '%s' is disabled (Hidden=true), so nothing was "
                    "started" % entry)
        return ("the job is the XDG autostart entry '%s': hub.py restart cannot start one and "
                "systemctl does not manage it, so nothing was started" % entry)
    return {
        control.NO_TASK: "there is no job '%s' for systemctl --user (hub.py setup --autostart "
                         "makes it), so nothing was started" % unit,
        control.DISABLED: "the job '%s' is disabled in systemctl --user, so nothing was started"
                          % unit,
        control.HAND_STARTED: "the server was not started by the job '%s' (systemctl --user), so "
                              "nothing was started" % unit,
    }[outcome]


def task_advice(outcome):
    """The last line when a restart started nothing, by control's outcome (None: the task could
    not be started). `serve` is not it: a server started in a console ends with that shell; the
    scheduled task runs it detached and windowless. On Linux the job is a systemd user service."""
    if xdg():
        if outcome == control.DISABLED:
            return ('enable the XDG autostart entry by removing the Hidden=true line from '
                    '%s (it then starts at your next sign-in to the desktop), or start the '
                    'server now with hub.py serve in a console: %s'
                    % (hub_autostart.linux_desktop_path(), common.START))
        return ('an XDG autostart entry starts the server at your next sign-in to the desktop; '
                'to start it now, run hub.py serve in a console: %s' % common.START)
    if outcome == control.NO_TASK:
        return 'register the %s, then start it: python "%s" setup --autostart; %s' % (
            'job' if linux() else 'task', common.TOOL, start_command())
    if outcome == control.DISABLED:
        if linux():
            return 'enable the job, then start it: systemctl --user enable %s; %s' % (
                hub_autostart.linux_unit(), start_command())
        return 'enable the task, then start it: Enable-ScheduledTask %s; %s' % (TASK, START_TASK)
    return 'start it with: %s' % start_command()


# a server the task starts has no console: what it says at start-up goes nowhere
NO_CONSOLE = ('a server the scheduled task starts has no console, so its start-up lines are not '
              'shown: run hub.py serve in a console to see why it does not answer: %s'
              % common.START)


NO_CONSOLE_LINUX = NO_CONSOLE.replace('the scheduled task', 'the job')
