"""No console window from any start in this process: a process-wide hook on CreateProcess, the
Windows half of the console hook (the incident log and install's caller are the face's).

Why: Windows opens a console window for a console program only when its parent has NO console and
the start makes no console choice of its own. A child of a process that has a console, shown or
hidden, shares it and opens none. With Windows Terminal as the default terminal SW_HIDE does not
help: the window shows anyway.

The hook: install() wraps _winapi.CreateProcess, which subprocess.Popen and multiprocessing's
spawn both call at call time. On the first start whose creationflags make no console choice
(CREATE_NEW_CONSOLE, DETACHED_PROCESS or CREATE_NO_WINDOW), the wrapper calls
ensure_windowless_console() once: a process with no console (pythonw, a scheduled task, a hook run
by a windowed program) gets a console with no window, and every child after that shares it, so
none opens a window. A process that has a console is left as it is. After that first start the
wrapper costs one module-global check per start. When no console can be had, every start that
makes no console choice gets CREATE_NO_WINDOW instead. Nothing here loads ctypes until the first
such start.

ensure_windowless_console(): AllocConsoleWithOptions with ALLOC_CONSOLE_MODE_NO_WINDOW where
kernel32 exports it (measured on Windows 11 under pythonw: hr 0, a new
console, no Windows Terminal or OpenConsole process, the std handles unchanged, a bare child's
GetConsoleWindow() 0). Else the fallback: a holder program started with CREATE_NO_WINDOW, so it
has a console with no window; AttachConsole(holder), then the holder ends and the console stays
with this process (measured too: attached after about 16 ms of ERROR_INVALID_HANDLE while
the holder started, std handles unchanged, a bare child's GetConsoleWindow() 0). FORCE_FALLBACK
makes ensure take the fallback where the export exists (the tests). Each time ensure gives the
process a console it calls install's `on_incident(how, child)`.

Ctrl+C: a console Ctrl+C has no window to be typed into here; only GenerateConsoleCtrlEvent from a
process on the same console sends one (no kit code calls it). A pythonw process that gains a
console would be ended by one at once (exit 0xC000013A, measured: Python's SIGINT
handler, SIG_IGN included, never runs under pythonw), where before it had no console and got none.
So ensure gives it a console control handler that takes Ctrl+C and Ctrl+Break for this process
(_take_ctrl). A handler is not inherited: a child on the console gets them as before
(docs/daily-full.md "The schedule").

Windows only: off Windows install() does nothing, and has_console() answers True without asking
kernel32.
"""
import _thread
import os
import sys

if os.name == 'nt':
    import _winapi
else:                                   # pragma: no cover - Windows only
    _winapi = None

CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000
CONSOLE_CHOICE = CREATE_NEW_CONSOLE | DETACHED_PROCESS | CREATE_NO_WINDOW

ALLOC_CONSOLE_MODE_NO_WINDOW = 2        # ALLOC_CONSOLE_MODE
ALLOC_CONSOLE_RESULT_NEW = 1            # ALLOC_CONSOLE_RESULT: 0 none, 1 new, 2 existing
ALLOC_CONSOLE_RESULT_EXISTING = 2
ERROR_INVALID_HANDLE = 6                # AttachConsole: the holder has no console yet
CTRL_C_EVENT, CTRL_BREAK_EVENT = 0, 1   # the console control events _take_ctrl takes
HOLDER_WAIT_S = 5.0                     # how long the fallback waits for the holder's console

MARK = '__kit_windowless__'             # set on the wrapper: install() is done in this process

# ensure_windowless_console's answers
PRESENT = 'present'                     # the process had a console already
ALLOCATED = 'alloc'                     # AllocConsoleWithOptions gave it one with no window
ATTACHED = 'attach'                     # the fallback: attached to a holder's windowless console
NONE = 'none'                           # no console could be had

FORCE_FALLBACK = False                  # tests: ensure takes the holder path regardless

_UNKNOWN, _DONE, _NO_WINDOW = 0, 1, 2
_mode = _UNKNOWN                        # the wrapper's state; _DONE after the first resolve
_lock = _thread.RLock()
_original = None                        # the CreateProcess the wrapper delegates to
_on_incident = None                     # install's on_incident(how, child), or None
_kernel32 = None
_ctrl_handler = None                    # _take_ctrl's handler, kept so it is never freed


def _k32():
    """A private kernel32 binding, made on first use (ctypes loads here, never on import)."""
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.GetConsoleProcessList.argtypes = (ctypes.POINTER(wintypes.DWORD), wintypes.DWORD)
        k.GetConsoleProcessList.restype = wintypes.DWORD
        k.AttachConsole.argtypes = (wintypes.DWORD,)
        k.AttachConsole.restype = wintypes.BOOL
        k.GetConsoleWindow.argtypes = ()
        k.GetConsoleWindow.restype = ctypes.c_void_p
        _kernel32 = k
    return _kernel32


def has_console():
    """True when this process is attached to a console, with a window or without one.
    GetConsoleProcessList, not GetConsoleWindow: a console made with CREATE_NO_WINDOW, or by
    ensure_windowless_console, has a GetConsoleWindow() of 0 (measured).
    Off Windows always True, and kernel32 is never asked: no start there opens a console window,
    so a child needs no flag for one."""
    import kitlib.platform
    if kitlib.platform.NAME != 'windows':
        return True
    from ctypes import wintypes
    one = (wintypes.DWORD * 1)()
    return _k32().GetConsoleProcessList(one, 1) != 0


def _alloc_no_window():
    """AllocConsoleWithOptions(ALLOC_CONSOLE_MODE_NO_WINDOW): ALLOCATED (a new console),
    PRESENT (one was there), NONE (refused), or None when kernel32 does not export it."""
    import ctypes
    from ctypes import wintypes
    k = _k32()
    try:
        fn = k.AllocConsoleWithOptions
    except AttributeError:
        return None

    class OPTIONS(ctypes.Structure):
        _fields_ = [('mode', ctypes.c_int), ('useShowWindow', wintypes.BOOL),
                    ('showWindow', wintypes.WORD)]
    fn.argtypes = (ctypes.POINTER(OPTIONS), ctypes.POINTER(ctypes.c_int))
    fn.restype = ctypes.c_long
    result = ctypes.c_int(-1)
    hr = fn(ctypes.byref(OPTIONS(ALLOC_CONSOLE_MODE_NO_WINDOW, 0, 0)), ctypes.byref(result))
    if hr < 0:
        return NONE
    if result.value == ALLOC_CONSOLE_RESULT_NEW:
        return ALLOCATED
    if result.value == ALLOC_CONSOLE_RESULT_EXISTING:
        return PRESENT
    return NONE


def _holder_argv():
    """A console program that waits for its stdin to close: cmd.exe (ComSpec), else the console
    python beside this interpreter; None when there is neither."""
    comspec = os.environ.get('ComSpec')
    if comspec and os.path.isfile(comspec):
        return [comspec, '/d', '/q', '/c', 'set /p _=']
    py = os.path.join(os.path.dirname(sys.executable), 'python.exe')
    if os.path.isfile(py):
        return [py, '-I', '-S', '-c', 'import sys; sys.stdin.read()']
    return None


def _attach_holder(wait_s=HOLDER_WAIT_S):
    """The fallback: ATTACHED when this process is now on a holder's windowless console, else
    NONE. The holder is this process's own child; it ends when its stdin closes."""
    import ctypes
    import subprocess
    import time
    argv = _holder_argv()
    if argv is None:
        return NONE
    holder = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    k = _k32()
    attached = False
    try:
        end = time.monotonic() + wait_s
        while True:
            if k.AttachConsole(holder.pid):
                attached = True
                break
            if (ctypes.get_last_error() != ERROR_INVALID_HANDLE or holder.poll() is not None
                    or time.monotonic() > end):
                break
            time.sleep(0.005)
    finally:
        try:
            holder.stdin.close()
            holder.wait(wait_s)
        except Exception:               # noqa: BLE001 - our own child: end it by its handle
            holder.kill()
            holder.wait()
    return ATTACHED if attached else NONE


def ensure_windowless_console(fallback=None, child=None):
    """Give this process a console with no window unless it has one. PRESENT (it had one: nothing
    done), ALLOCATED (AllocConsoleWithOptions), ATTACHED (the holder fallback) or NONE (no console
    could be had). `fallback` True takes the holder path even where AllocConsoleWithOptions is
    exported (None: FORCE_FALLBACK). `child` (the command line about to start) goes to install's
    on_incident. Idempotent: a second call answers PRESENT. Windows only."""
    with _lock:
        if has_console():
            return PRESENT
        how = None
        if not (FORCE_FALLBACK if fallback is None else fallback):
            how = _alloc_no_window()
        if how is None or how == NONE:
            how = _attach_holder()
        if how in (ALLOCATED, ATTACHED):
            try:
                _take_ctrl()
            except Exception:           # noqa: BLE001 - the console stays; the start goes on
                pass
            if _on_incident is not None:
                _on_incident(how, child)
        return how


def ctrl_taken(event):
    """_take_ctrl's handler: True (handled) for Ctrl+C and Ctrl+Break, False for any other event
    (it goes on to the next handler)."""
    return event in (CTRL_C_EVENT, CTRL_BREAK_EVENT)


def _take_ctrl():
    """Register a console control handler (ctrl_taken) that answers Ctrl+C and Ctrl+Break as
    handled, so this process, which had no console, is not ended by one sent to the console it now
    has. Per process: a child does not inherit it. True when registered."""
    global _ctrl_handler
    import ctypes
    from ctypes import wintypes
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    if _ctrl_handler is None:
        _ctrl_handler = proto(ctrl_taken)
    k = _k32()
    k.SetConsoleCtrlHandler.argtypes = (proto, wintypes.BOOL)
    k.SetConsoleCtrlHandler.restype = wintypes.BOOL
    return bool(k.SetConsoleCtrlHandler(_ctrl_handler, True))


def _create_process(app, cmd, proc_attrs, thread_attrs, inherit, flags, env, cwd, startup):
    """_winapi.CreateProcess, with this process given a windowless console before its first
    child that makes no console choice (install)."""
    if _mode != _DONE and not flags & CONSOLE_CHOICE:
        flags = flags | CREATE_NO_WINDOW if _mode == _NO_WINDOW else _resolve(flags, app, cmd)
    return _original(app, cmd, proc_attrs, thread_attrs, inherit, flags, env, cwd, startup)


setattr(_create_process, MARK, True)


def _resolve(flags, app, cmd):
    """The first start with no console choice: ensure once, then `flags` (this process has a
    console now, which the child shares) or `flags` | CREATE_NO_WINDOW (it has none)."""
    global _mode
    with _lock:
        if _mode == _UNKNOWN:
            try:
                how = ensure_windowless_console(child=cmd if cmd is not None else app)
            except Exception:           # noqa: BLE001 - never stop the start
                how = NONE
            _mode = _NO_WINDOW if how == NONE else _DONE
    return flags | CREATE_NO_WINDOW if _mode == _NO_WINDOW else flags


def installed():
    """True when _winapi.CreateProcess is a kit wrapper (this module's, or another kit copy's)."""
    return _winapi is not None and bool(getattr(_winapi.CreateProcess, MARK, False))


def install(on_incident=None):
    """Wrap _winapi.CreateProcess once per process (idempotent: a wrapper already there, from
    this module or another kit copy's, is kept). `on_incident(how, child)` is called each time
    ensure gives this process a console; it must not raise. Off Windows nothing."""
    global _original, _on_incident
    if _winapi is None:
        return False
    with _lock:
        if installed():
            return False
        _original = _winapi.CreateProcess
        _on_incident = on_incident
        _winapi.CreateProcess = _create_process
    return True
