"""A run's command as Windows starts it (kitlib.lock.run, whose docstring gives the whole run):
script_command builds the cmd.exe line a .bat or .cmd needs, alias_check refuses a WindowsApps
App Execution Alias (it would start outside the run's job), path_alias_line is status's warning
for one on PATH, and bare_hint explains a bare name Windows did not look for in the current
folder.

Nothing here prints.
"""
import os
import re
import shutil
import subprocess
import sys

import kitlib.platform
from kitlib.proc import bash


NO_CWD_ENV = "NoDefaultCurrentDirectoryInExePath"


def bare_hint(cmd, err):
    """The text that follows a failed start of a list command whose first item is a bare name
    (no folder part) that is a file in this process's folder while NoDefaultCurrentDirectoryInExePath
    is set: Windows then does not look in the current folder for a bare name (agent hosts set it),
    so the hint names the folder and the .\\<name> form that runs. "" in every other case,
    including a current folder that cannot be read (removed under us)."""
    if not isinstance(err, FileNotFoundError) or isinstance(cmd, str) or not cmd:
        return ""
    name = str(cmd[0])
    if not name or os.path.dirname(name) or not os.environ.get(NO_CWD_ENV):
        return ""
    try:
        here = os.getcwd()
    except OSError:
        return ""
    if not os.path.isfile(os.path.join(here, name)):
        return ""
    return ("; %s is in %s, but %s is set, so Windows does not look in the current folder for a "
            "bare name: run it as .\\%s" % (name, here, NO_CWD_ENV, name))


SCRIPT_EXTS = (".bat", ".cmd")
# What cmd.exe expands or splits even inside double quotes: a .bat or .cmd cannot take it.
CMD_UNSAFE = (("%", "`%`"), ('"', '`"`'), ("\n", "a line break"), ("\r", "a line break"))
# What cmd.exe reads as syntax outside double quotes: an argument holding one is quoted.
CMD_QUOTE = frozenset(" \t\x0b\x0c&|<>^(),;=")


class ScriptArgError(OSError):
    """A command script_command will not start: a .bat or .cmd that cannot start as it would start
    it (an argument it cannot take literally, or no cmd.exe under SystemRoot), or a WindowsApps
    App Execution Alias (AliasStubError); an OSError, so the "could not start the command" path
    reports it."""


class AliasStubError(ScriptArgError):
    """A command whose program is a WindowsApps App Execution Alias (alias_check)."""


# A python interpreter's name, as the WindowsApps aliases spell it: python, python3, python3.12,
# pythonw, pythonw3, pythonw3.12, and the py launcher's py and pyw (a trailing .exe dropped first).
PYTHON_NAME_RE = re.compile(r"^(?:python(?P<w>w?)(?:3(?:\.\d+)?)?|(?P<py>py)(?P<pyw>w?))$",
                            re.IGNORECASE)


def windows_apps_dir():
    """%LOCALAPPDATA%\\Microsoft\\WindowsApps, where the App Execution Aliases live, or None when
    LOCALAPPDATA is unset."""
    local = os.environ.get("LOCALAPPDATA")
    return os.path.join(local, "Microsoft", "WindowsApps") if local else None


def under_windows_apps(path):
    """Is path windows_apps_dir() or inside it (a package's subfolder included)?"""
    apps = windows_apps_dir()
    if not apps or not path:
        return False
    a = os.path.normcase(os.path.abspath(apps)).rstrip(os.sep)
    p = os.path.normcase(os.path.abspath(path))
    return p == a or p.startswith(a + os.sep)


def created_program(name, dirs=None):
    """The file CreateProcess starts for name (an argument list's first item), or None when it
    finds none: name itself when it has an extension, else name.exe (the only one CreateProcess
    appends). A name with a folder part is that file; a bare one is the first found in dirs
    (default: _create_process_dirs(), then this process's PATH)."""
    cand = name if os.path.splitext(name)[1] else name + ".exe"
    if os.path.dirname(cand):
        return os.path.abspath(cand) if os.path.isfile(cand) else None
    if dirs is None:
        dirs = _create_process_dirs() + _path_dirs(os.environ.get("PATH"))
    for d in dirs:
        p = os.path.join(d, cand)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


def alias_instead(cmd, found):
    """What alias_check's refusal of cmd (whose program, found, is an alias) tells the caller to do
    instead. A python name (PYTHON_NAME_RE), or found being this interpreter itself: when
    sys.executable is itself under WindowsApps (a Store python), to run the kit under a python.org
    or Python install manager interpreter, as no path here would do; else to run this
    interpreter (pythonw.exe beside it for a windowed name, when it is there): with cmd's
    arguments, or, for the py launcher, with them left for the caller to adjust (py's own flags
    are not python's). Any other name: to name the program's own path."""
    stem = os.path.basename(str(cmd[0]))
    if stem.lower().endswith(".exe"):
        stem = stem[:-4]
    m = PYTHON_NAME_RE.match(stem)
    me = os.path.abspath(sys.executable) if sys.executable else None
    if (m or (me and os.path.normcase(found) == os.path.normcase(me))) and \
            (not me or under_windows_apps(me)):
        return ("this kit itself runs under a WindowsApps python (%s): run it under a python.org or "
                "Python install manager interpreter instead, not the Store python or its alias"
                % (me or "no sys.executable"))
    if not m:
        return "name the program's own path instead, not the alias"
    exe = me
    if m.group("w") or m.group("pyw"):
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.isfile(w) else exe
    if m.group("py"):
        return ('run "%s" in place of %s instead, with the arguments adjusted for python (py\'s own '
                'flags, such as -3.12, are not python\'s)' % (exe, stem))
    rest = subprocess.list2cmdline([str(a) for a in cmd[1:]])
    return 'run "%s"%s instead' % (exe, " " + rest if rest else "")


def alias_check(cmd):
    """Raises AliasStubError when cmd (an argument list, on Windows) starts a WindowsApps App
    Execution Alias: its first item, or the file CreateProcess finds for it (created_program),
    is under windows_apps_dir(). An alias's launch runs its program outside the job of whoever
    starts it: unpinned, on every core, and alive after the job is ended. Refused, never swapped,
    even when it is this interpreter itself; the refusal says what to do instead (alias_instead).
    A bare bash is left to kitlib.proc.bash (Git Bash), and so is anything else when LOCALAPPDATA
    is unset."""
    if kitlib.platform.NAME != "windows" or not isinstance(cmd, (list, tuple)) or not cmd:
        return
    name = str(cmd[0])
    if name.strip().lower() in bash.NAMES or not windows_apps_dir():
        return
    found = created_program(name)
    if not found or not under_windows_apps(found):
        return
    raise AliasStubError("%s is the WindowsApps App Execution Alias %s: what it starts runs "
                         "outside the run's job, on every core, and outlives a stop; %s"
                         % (name, found, alias_instead(cmd, found)))


def path_alias_line():
    """`status`'s warning when the first python.exe on this process's PATH is a WindowsApps alias,
    else None (and None off Windows): a .bat, cmd or bash the run starts finds python there, and
    the alias starts it outside the run's job (alias_check sees only the run's own command)."""
    if kitlib.platform.NAME != "windows" or not windows_apps_dir():
        return None
    found = created_program("python", dirs=_path_dirs(os.environ.get("PATH")))
    if not found or not under_windows_apps(found):
        return None
    real = (os.path.dirname(os.path.abspath(sys.executable))
            if sys.executable and not under_windows_apps(sys.executable)
            else "the real interpreter's folder")
    return ("python: first on PATH is the WindowsApps alias %s; a non-python child's python "
            "escapes the run's job; put %s ahead of it on the user PATH" % (found, real))


def comspec():
    """The cmd.exe that runs a .bat or .cmd: %SystemRoot%\\System32\\cmd.exe, never ComSpec (another
    shell would read the quoting otherwise) nor a bare cmd.exe (looked for in the current folder
    first). Raises ScriptArgError when SystemRoot is unset or holds no such file."""
    root = os.environ.get("SystemRoot")
    exe = os.path.join(root, "System32", "cmd.exe") if root else None
    if not exe or not os.path.isfile(exe):
        raise ScriptArgError("cmd.exe not found under SystemRoot")
    return exe


def _path_dirs(path):
    return [d.strip().strip('"') for d in (path or "").split(os.pathsep) if d.strip().strip('"')]


def _create_process_dirs():
    """Where CreateProcess looks for a bare name, PATH aside: this program's folder, the current
    folder (unless NoDefaultCurrentDirectoryInExePath), then the system folders."""
    root = os.environ.get("SystemRoot") or ""
    dirs = [os.path.dirname(os.path.abspath(sys.executable))]
    if not os.environ.get("NoDefaultCurrentDirectoryInExePath"):
        dirs.append(os.getcwd())
    if root:
        dirs += [os.path.join(root, "System32"), os.path.join(root, "System"), root]
    return dirs


def _bare_script(name, path):
    """The .bat or .cmd a bare name (no folder part) runs, or None (cmd as given). A name that
    ends .bat or .cmd is the file CreateProcess would start, in its order: its folders (the
    current one among them unless NoDefaultCurrentDirectoryInExePath), then this process's PATH;
    else the first in `path`'s folders. An extensionless name is never looked for in the current
    folder, and never in place of what CreateProcess starts today: one it finds as name.exe or
    name.com (its folders, this process's PATH or `path`) stays as given, so a python.bat shim
    ahead of python.exe on PATH leaves `python` alone; so does any other extension
    (CreateProcess runs the name itself). Otherwise `path`'s folders in order, each over
    PATHEXT's order; the first file there decides."""
    ext = os.path.splitext(name)[1].lower()
    if ext and ext not in SCRIPT_EXTS:
        return None
    if ext:
        for d in _create_process_dirs() + _path_dirs(os.environ.get("PATH")):
            if os.path.isfile(os.path.join(d, name)):
                return os.path.join(d, name)
        cands = [name]
    else:
        progs = (name + ".exe", name + ".com")
        dirs = _create_process_dirs() + _path_dirs(os.environ.get("PATH")) + _path_dirs(path)
        if any(os.path.isfile(os.path.join(d, n)) for d in dirs for n in progs):
            return None
        exts = (os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD").split(os.pathsep)
        cands = [name + e for e in exts if e]
    for d in _path_dirs(path):
        for c in cands:
            p = os.path.join(d, c)
            if os.path.isfile(p):
                return p if os.path.splitext(p)[1].lower() in SCRIPT_EXTS else None
    return None


def _cmd_word(text, what):
    """text as one word of the cmd.exe line script_command builds: as given when cmd reads no
    syntax in it, else in double quotes (backslashes before the closing quote doubled, so the
    program the script hands it to does not read an escaped quote). Raises ScriptArgError when
    it holds what cmd expands or splits even inside quotes (CMD_UNSAFE)."""
    for ch, shown in CMD_UNSAFE:
        if ch in text:
            raise ScriptArgError("%s holds %s; cmd.exe would expand or split a `%%`, `\"` or line "
                                 "break, so a .bat or .cmd cannot take it literally" % (what, shown))
    if text and not CMD_QUOTE.intersection(text):
        return text
    return '"%s%s"' % (text, "\\" * (len(text) - len(text.rstrip("\\"))))


def script_command(cmd, env=None):
    """What run_command starts for cmd (an argument list) on Windows: cmd as given, unless its
    first item names a .bat or .cmd. A trailing dot or space after .bat or .cmd is dropped, as
    Windows drops it (x.bat. starts x.bat). A name with a folder part (./gradlew) is looked up
    only beside itself, as shutil.which does: itself when it ends in a PATHEXT extension, else
    name+each PATHEXT extension (CreateProcess tries the name itself, WinError 193 for a shell
    script, and appends only .exe). A bare name: _bare_script (x.bat where CreateProcess would
    find it, the current folder included; else on env's PATH). A .bat or .cmd, so
    found or named, starts as `"<SystemRoot>\\System32\\cmd.exe" /d /v:off /s /c ""<its full
    path>" <args>"` (comspec), a string the caller starts with shell=False: cmd.exe is the
    process the run's job takes, and the batch's children inherit the job. /v:off: delayed
    expansion set in the registry would expand !VAR! in an argument. CreateProcess's own start of
    a .bat quotes for a program, not for cmd, so an unquoted & or a % in an argument there runs
    as cmd syntax; here each argument goes through _cmd_word (ScriptArgError for a %, " or line
    break). Anything else (an .exe or .com, no match, a string cmd) and every other platform:
    cmd itself. First, a command whose program is a WindowsApps App Execution Alias raises
    AliasStubError (alias_check)."""
    if kitlib.platform.NAME != "windows" or not isinstance(cmd, (list, tuple)) or not cmd:
        return cmd
    alias_check(cmd)
    name = str(cmd[0])
    trimmed = name.rstrip(" .")        # Windows drops them: CreateProcess starts x.bat for x.bat.
    if os.path.splitext(trimmed)[1].lower() in SCRIPT_EXTS:
        name = trimmed
    path = (os.environ if env is None else env).get("PATH")
    if os.path.dirname(name):
        found = shutil.which(name, path=path)       # a folder part: beside itself only
    else:
        found = _bare_script(name, path)
    if not found or os.path.splitext(found)[1].lower() not in SCRIPT_EXTS:
        return cmd
    root, fext = os.path.splitext(os.path.abspath(found))
    bat = root + fext.lower()           # which's PATHEXT case (.BAT): the file's own is moot
    base = os.path.basename(bat)
    if "%" in bat:                      # a path holds no " or line break
        raise ScriptArgError("the path of %s holds `%%`; cmd.exe would expand it, so it cannot "
                             "start %s" % (base, bat))
    words = ['"%s"' % bat] + [_cmd_word(str(a), "argument %d of %s" % (i, base))
                              for i, a in enumerate(cmd[1:], 1)]
    return '"%s" /d /v:off /s /c "%s"' % (comspec(), " ".join(words))
