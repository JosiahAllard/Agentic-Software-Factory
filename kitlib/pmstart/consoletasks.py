"""pm_start check: a registered kit scheduled task whose program is a console program. Read only.

A task whose program is python.exe or powershell.exe (any console program) is started by a
process with no console, so Windows gives it a new console and shows its window on the desktop
(docs/daily-full.md "The schedule"). The kit's own task lines run pythonw.exe with the carrier
tools/no_window.py as its first argument (kitlib.proc.spawn.CARRIER), which opens none; a task
registered by hand, or before the carrier, may not.

A kit task is one whose name is a name the kit registers (`kit_names`) or one with an action that
names a path inside a kit folder (`kit_roots`: this kit, the live checkout an export runs for
(KIT_LIVE_ROOT, else the main checkout the export's marker records), and the folder of every
export of the plugin when this kit is one). A file named like a kit tool in
another folder (another repository's tools\\gate.py) is not the kit's. Its program is judged by
file name only: a console program in `CONSOLE` is flagged; pythonw.exe and anything else is not.
The tasks are read by kitlib.schedtask.read.read_tasks; `judge` is pure and is what the tests
drive.

On Linux a job opens no console window, so `check` says nothing.
"""
import os
import re

from kitlib import platform
from kitlib.launcher import reboot
from kitlib.machinesetup import agenttemp
from kitlib.proc import spawn
from kitlib.schedtask import read as taskread
from kitlib.schedtask.read import words

CONSOLE = frozenset(('python.exe', 'py.exe', 'powershell.exe', 'pwsh.exe', 'cmd.exe', 'bash.exe'))
NAME_SUFFIXES = ('-worktree-sweep', '-daily-full')    # docs/worktree-remove.md; docs/daily-full.md
KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ABSOLUTE = re.compile(r'^(?:[A-Za-z]:\\|\\\\)')


def kit_names():
    """The task names the kit registers itself: the launcher's retired sign-in task and the agent
    temp sweep. `NAME_SUFFIXES` covers the per-repository ones."""
    return {reboot.REBOOT_TASK, agenttemp.TASK}


def _norm(path):
    return os.path.normcase(os.path.normpath(path.replace('/', '\\'))).rstrip('\\')


def kit_roots(kit=KIT, environ=None):
    """The folders a kit task's action may name, normalised: `kit`; the live kit checkout when
    `kit` is an export (kitlib.pluginsource.layout.live_root: KIT_LIVE_ROOT, else the main checkout its
    marker records when that holds kit.json and a tools folder); and, when `kit`
    is a plugin export (it holds pluginsource.layout.MARKER), the folder that holds it, so a task line
    naming an export of another version is the kit's too."""
    from kitlib.pluginsource import layout as plugin_layout
    roots = {_norm(kit), _norm(plugin_layout.live_root(kit, environ))}
    if os.path.isfile(os.path.join(kit, plugin_layout.MARKER)):
        roots.add(_norm(os.path.dirname(os.path.abspath(kit))))
    return roots


def program(exe):
    """The file name of a task's program, lower case, `.exe` added when it has no extension
    (`python` -> `python.exe`)."""
    name = (exe or '').strip().strip('"').replace('/', '\\').split('\\')[-1].lower()
    return name if os.path.splitext(name)[1] else name + '.exe'


def _inside(word, roots):
    path = word.replace('/', '\\')
    if not ABSOLUTE.match(path):
        return False                    # a relative word or an option: no folder it is inside
    p = _norm(path)
    return any(p == r or p.startswith(r + '\\') for r in roots)


def is_kit(task, roots):
    """Whether `task` ({"path", "actions": [{"exe", "args"}]}) is one of the kit's: by its name, or
    by a word of an action (its program, or an argument, quoted or not) that is an absolute path
    inside one of `roots` (normalised, as `kit_roots` gives them)."""
    name = task['path'].rsplit('\\', 1)[-1]
    if name in kit_names() or name.lower().endswith(NAME_SUFFIXES):
        return True
    for a in task['actions']:
        text = '"%s" %s' % ((a.get('exe') or '').strip().strip('"'), a.get('args') or '')
        for word in words(text):
            if _inside(word, roots):
                return True
    return False


def judge(tasks, roots=None):
    """One warning line per action of a kit task in `tasks` whose program is a console program.
    `roots`: the kit folders (`kit_roots()` by default)."""
    roots = kit_roots() if roots is None else {_norm(r) for r in roots}
    lines = []
    for task in sorted(tasks, key=lambda t: t['path'].lower()):
        if not is_kit(task, roots):
            continue
        for a in task['actions']:
            prog = program(a.get('exe'))
            if prog in CONSOLE:
                lines.append(
                    "scheduled task %s starts %s, a console program: a console window opens each time "
                    "it runs. Its program should be pythonw.exe with the carrier tools\\%s as its first "
                    "argument (docs/daily-full.md \"The schedule\": Set-ScheduledTask, the owner's act)"
                    % (task['path'], prog, spawn.CARRIER))
    return lines


def check(cfg, read=None):
    """Warning lines for kit tasks that start a console program (`judge`); [] when there are none.
    Always on: no config key. `cfg` is unused, kept for the checks pattern. On Linux: []."""
    if platform.NAME == 'linux':
        return []
    return judge((read or taskread.read_tasks)())
