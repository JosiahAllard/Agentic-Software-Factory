"""The procwatch scheduled task (docs/machine-setup.md "Extras", "Procwatch task"): every 15 minutes
`optional/procwatch/procwatch.py --log <agent temp>\\procwatch.log`, registered by `--procwatch` as
the registering user. Opt-in, and report only: `--procwatch-kill` adds `--kill --only SAFE`, so
killing is a second, separate choice and reaches only the rules in SAFE. The task's program is the
base `pythonw.exe` (kitlib.schedtask.pythonw.base_pythonw, a literal path, never a PATH lookup) with
the carrier `tools/no_window.py` first, as the agent temp sweep task does; the script is the kit's
own file, not a link in the home folder, and the log goes in the agents' temp folder
(kitlib.machinesetup.agenttemp.choose), not the home folder. A task already registered is
re-registered (`-Force`) so a task written by hand, or before this, takes the kit's definition.
On Linux the same run is the pm-kit job `procwatch` (linux_job, planned through linuxjobs.plan_job:
every 15 minutes, ended after 10, `python optional/procwatch/procwatch.py --log <log>` in the kit),
written, or written again when its argv differs (the kill flag), by the same flags.
Nothing here prints or exits.
"""
import os

from kitlib import platform
from kitlib.proc.spawn import CARRIER
from kitlib.schedtask.line import NOT_THE_STORE_ALIAS, PYTHONW_IS, quoted
from kitlib.schedtask.pythonw import base_pythonw

from . import agenttemp
from .settings import host_name

LINUX_JOB = 'procwatch'       # the Linux job's name: a space is not allowed in one
TASK = host_name().capitalize() + ' procwatch'    # "Claude procwatch": the name the task already has
EVERY_MINUTES = 15
SCRIPT = os.path.join('optional', 'procwatch', 'procwatch.py')
LOG_NAME = 'procwatch.log'
# The rules --kill may act on from the task: the kill-allowlisted rules of procwatch.RULES (a test
# keeps the two the same).
SAFE = ('msys-follow', 'msys-filter', 'msys-sleep', 'root-search', 'msbuild-node', 'test-host')


def argument(kit, log, kill=False):
    """The task's argument string: the carrier, `python`, the script, `--log <log>`, and with `kill`
    `--kill --only <SAFE>`."""
    kit = os.path.normpath(kit)
    arg = '"%s" python "%s" --log "%s"' % (os.path.join(kit, 'tools', CARRIER),
                                           os.path.join(kit, SCRIPT), os.path.normpath(log))
    return arg + ' --kill --only %s' % ','.join(SAFE) if kill else arg


def task_line(kit, log, kill=False, pythonw=None):
    """The one PowerShell line that registers TASK (-Force: replaces one already there), every
    EVERY_MINUTES minutes as the registering user (-RunLevel Limited), one run at a time, ended
    after 10 minutes. The program is `pythonw` (a literal path, default the base install's) running
    the carrier, which starts the python.exe beside it with no window. The line throws, registering
    nothing, when that pythonw is not a file or is under WindowsApps."""
    kit = os.path.normpath(kit)
    pythonw = pythonw or base_pythonw()
    return ("%s; %s; %s; Register-ScheduledTask -Force -TaskName %s"
            " -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date)"
            " -RepetitionInterval (New-TimeSpan -Minutes %d) -RepetitionDuration (New-TimeSpan -Days 3650))"
            " -Action (New-ScheduledTaskAction -Execute $py -Argument %s -WorkingDirectory %s)"
            " -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew"
            " -ExecutionTimeLimit (New-TimeSpan -Minutes 10))"
            " -User $env:USERNAME -RunLevel Limited -Description %s"
            % (PYTHONW_IS % quoted(pythonw), NOT_THE_STORE_ALIAS, agenttemp.NOT_A_FILE, quoted(TASK),
               EVERY_MINUTES, quoted(argument(kit, log, kill)), quoted(kit),
               quoted('procwatch: reports stray tool processes' + (' and ends the SAFE ones' if kill else ''))))


def linux_job(kit, log, kill=False, python=None):
    """The Linux job: `python <kit>/SCRIPT --log <log>` (with `kill`, `--kill --only <SAFE>`) in the
    kit, every EVERY_MINUTES minutes, ended after 10 (python: default schedule.python())."""
    from kitlib.platform.linux import schedule
    kit = os.path.normpath(kit)
    argv = [python or schedule.python(), os.path.join(kit, SCRIPT), '--log', log]
    if kill:
        argv += ['--kill', '--only', ','.join(SAFE)]
    return schedule.Job(LINUX_JOB, argv, kit, ('every', EVERY_MINUTES),
                        'procwatch: reports stray tool processes' + (' and ends the SAFE ones' if kill else ''),
                        10)


def _linux_step(plan, ctx, log, kill):
    """task_step on Linux: the job (linuxjobs.plan_job) when it is not written as planned."""
    from kitlib.platform.linux import schedule
    from . import linuxjobs, worktreesweep
    try:
        have = worktreesweep.find(ctx.host.task_list(), LINUX_JOB)
    except (OSError, RuntimeError, ValueError, SyntaxError) as e:
        plan.note('extras', 'could not tell whether the pm-kit job %s is written (%s)' % (LINUX_JOB, e))
        return
    job = linux_job(ctx.kit, log, kill)
    if not schedule.same(have, job):
        linuxjobs.plan_job(plan, ctx, job, 'update' if have else 'write')


def task_step(plan, ctx, folder):
    """Plan the task when ctx.procwatch: `folder` is the agents' temp folder survey chose (None when
    there is none: a note, nothing planned). Called from extras.survey."""
    if not getattr(ctx, 'procwatch', False):
        return
    kill = bool(getattr(ctx, 'procwatch_kill', False))
    if not folder:
        plan.note('extras', 'procwatch task skipped: no agent temp folder to hold its log (pass '
                            '--agent-temp)')
        return
    log = os.path.join(folder, LOG_NAME)
    if platform.NAME == 'linux':
        _linux_step(plan, ctx, log, kill)
        return
    line = task_line(ctx.kit, log, kill)
    try:
        there = ctx.host.task_exists(TASK)
    except (OSError, RuntimeError) as e:
        plan.note('extras', 'could not tell whether the scheduled task %s is registered (%s)' % (TASK, e))
        return
    what = 'ends the SAFE rules\' processes (--kill --only %s)' % ','.join(SAFE) if kill \
        else 'report only, kills nothing (--procwatch-kill adds the kill)'
    plan.add('extras', '%s the scheduled task %s (every %d min, as you, not elevated; log %s; %s): %s'
             % ('update' if there else 'register', TASK, EVERY_MINUTES, log, what, line),
             lambda: ctx.host.task_register(line),
             undo='Unregister-ScheduledTask -TaskName "%s" -Confirm:$false%s'
                  % (TASK, ' (the task there now is replaced: Export-ScheduledTask it first to keep it)'
                     if there else ''))
