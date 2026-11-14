"""Linux process helpers for procwatch.py, with winproc's interface (standard library only): an
adapter over kitlib.platform.linux.process (the /proc readers and the pidfd kill) and
contain_proc (rss, CPU ticks). Times are integers in 100 ns units, as winproc's FILETIMEs are:
a ctime is the process's start since boot, filetime_now() the boot clock, so the age is the same
subtraction. msys_table() carries what Git's `ps -l` carries on Windows: the real parent and the
terminal, so classify's MSYS path judges a parent that has exited (a parent pid of 1 only counts
as exited for a process that does not lead its own session: a systemd service's child, or a
daemon, is not an orphan). A process reparented to a subreaper (systemd --user) is not seen as
orphaned.
"""
import os
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.append(ROOT)

from kitlib.platform.linux import contain_proc, process as lproc  # noqa: E402

UNIT = 10 ** 7                    # 100 ns units per second
LOW = (1 << 64) - 1               # a ctime of lproc: (boot id << 64) | starttime ticks

snapshot = lproc.snapshot


def sleep(s):
    time.sleep(s)


def _units(ticks):
    return ticks * UNIT // lproc.clk_tck()


def filetime_now():
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) // 100


def _ctime(pid):
    """(lproc's ctime, the 100 ns one) of pid, or (None, None) when it is gone or unreadable."""
    full = lproc.proc_ctime(pid)
    return (None, None) if full is None else (full, _units(full & LOW))


def _stat_fields(pid):
    """(session id, tty_nr) of pid from its stat, or None."""
    try:
        with open("%s/%d/stat" % (lproc.PROC, pid), "rb") as f:
            data = f.read()
        fields = data[data.rfind(b")") + 2:].split()
        return int(fields[3]), int(fields[4])
    except (OSError, ValueError, IndexError):
        return None


def cpu_sample(pids):
    """{pid: (ctime, cpu seconds)} for the pids that can be read."""
    out = {}
    for pid in pids:
        _full, ct = _ctime(pid)
        if ct is not None:
            out[pid] = (ct, contain_proc.cpu_ticks(pid) / lproc.clk_tck())
    return out


def details(pid):
    """dict of ctime, cpu, image, cmd, handles, ws, priv for one pid; fields None when unreadable.
    cpu counts the ticks of children it has reaped too (contain_proc.cpu_ticks). gone = True when
    no process has that pid any more."""
    d = {"ctime": None, "cpu": None, "image": None, "cmd": None, "handles": None, "ws": None,
         "priv": None, "gone": False}
    try:
        full, d["ctime"] = _ctime(pid)
    except (OSError, ValueError):
        return d
    if full is None:
        d["gone"] = True
        return d
    d["cpu"] = contain_proc.cpu_ticks(pid) / lproc.clk_tck()
    d["cmd"] = lproc.command_line(pid, full)
    d["ws"] = contain_proc.rss(pid)
    try:
        d["image"] = os.readlink("%s/%d/exe" % (lproc.PROC, pid))
    except OSError:
        pass
    try:
        d["handles"] = len(os.listdir("%s/%d/fd" % (lproc.PROC, pid)))
    except OSError:
        pass
    return d


def msys_table():
    """{pid: (pid, parent pid, tty)} for every process: tty `?` = no terminal, else `pts/<n>`
    (tty_nr). The parent is 0 (exited) when it is 1 and the process leads no session of its own."""
    table = {}
    for pid, ppid, _name in lproc.snapshot():
        got = _stat_fields(pid)
        if got is None:
            continue
        sid, tty = got
        table[pid] = (pid, 0 if ppid == 1 and sid != pid else ppid, "?" if tty == 0 else "pts/%d" % tty)
    return table


def perf_info():
    """Machine totals: none on Linux (the paged-pool check is Windows's)."""
    return None


def terminate_verified(pid, ctime):
    """SIGKILL pid only if it is still the process created at ctime (a 100 ns value from
    details). Returns 'killed', 'gone', 'reused' or 'denied' (lproc.terminate_verified)."""
    full, seen = _ctime(pid)
    if full is None:
        return "gone"
    if seen != ctime:
        return "reused"
    return lproc.terminate_verified(pid, full)
