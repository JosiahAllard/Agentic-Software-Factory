"""The /proc reads of Linux containment (kitlib.platform.linux.contain): a process's memory (rss,
vm_hwm) and cpu ticks, its stat read once (pstat), a process tree by ctime (tree), the processes
an environment marker names (marked: W2's members) and a process's PID namespaces (_pidns,
_nspid).

This module imports on any platform; its calls need Linux."""
import os

from kitlib.platform.linux import process as lproc
from kitlib.platform.linux.contain_cgroup import PROC, _read


MARK_ENV = 'PMKIT_JOB'


def rss(pid):
    """Bytes resident of pid now (statm), 0 when gone."""
    try:
        return int(_read('%s/%d/statm' % (PROC, pid)).split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return 0


def vm_hwm(pid):
    """Bytes: the peak resident set of pid (VmHWM), 0 when gone."""
    try:
        for line in _read('%s/%d/status' % (PROC, pid)).splitlines():
            if line.startswith('VmHWM:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def cpu_ticks(pid):
    """utime + stime + cutime + cstime of pid in clock ticks, 0 when gone."""
    try:
        text = _read('%s/%d/stat' % (PROC, pid))
        f = text[text.rindex(')') + 2:].split()
        return sum(int(x) for x in f[11:15])
    except (OSError, ValueError, IndexError):
        return 0


def _pids():
    """Every pid PROC lists, or [] when it cannot be read."""
    try:
        return [int(n) for n in os.listdir(PROC) if n.isdigit()]
    except OSError:
        return []


def _stat_bytes(pid):
    with open('%s/%d/stat' % (PROC, pid), 'rb') as f:
        return f.read()


def pstat(pid, boot=None):
    """(ctime, ppid, exited) of pid from ONE read of its stat, so the parent and the identity are
    the same process's; None when it is gone or cannot be read."""
    try:
        data = _stat_bytes(pid)
        close = data.rindex(b')')
        f = data[close + 2:].split()
        return (lproc._ctime(int(f[19]), boot), int(f[1]),
                f[0].decode('ascii', 'replace') in lproc.EXITED)
    except (OSError, ValueError, IndexError):
        return None


def _boot():
    try:
        return lproc.boot()
    except OSError:
        return None


def tree(root, ctime, boot=None):
    """{pid: ctime} of root's descendants, or {} when root is not the process ctime names. Each
    child's parent and ctime come from one stat read, taken between two reads of the parent that
    find it unchanged, so a pid that ends and is reused while the walk runs never joins the tree
    (it is under its new parent, or its parent is gone)."""
    boot = _boot() if boot is None else boot
    if boot is None or ctime is None:
        return {}
    kids = {}
    for pid in _pids():
        st = pstat(pid, boot)
        if st is not None and pid != root:
            kids.setdefault(st[1], []).append(pid)
    out = {}
    queue = [(root, ctime)]
    while queue:
        q, qc = queue.pop(0)
        if not kids.get(q) or not _same(q, qc, boot):
            continue
        got = []
        for c in kids[q]:
            st = pstat(c, boot)
            if st is not None and st[1] == q and c not in out:
                got.append((c, st[0]))
        if not _same(q, qc, boot):
            continue
        for c, ct in got:
            out[c] = ct
            queue.append((c, ct))
    return out


def _same(pid, ctime, boot):
    st = pstat(pid, boot)
    return st is not None and st[0] == ctime


def marked(token):
    """{pid: ctime} of the processes whose environment's PMKIT_JOB names token (the ctime read
    before and after the environment, so the two are the same process's)."""
    want = token.encode('ascii')
    boot = _boot()
    out = {}
    if boot is None:
        return out
    for pid in _pids():
        st = pstat(pid, boot)
        if st is not None and _marks(pid, want) and _same(pid, st[0], boot):
            out[pid] = st[0]
    return out


def _marks(pid, want):
    try:
        with open('%s/%d/environ' % (PROC, pid), 'rb') as f:
            env = f.read().split(b'\0')
    except OSError:
        return False
    key = MARK_ENV.encode('ascii') + b'='
    return any(v.startswith(key) and want in v[len(key):].split() for v in env)


def _pidns(pid):
    """The inode of pid's PID namespace ('self': this process's), None when it cannot be read."""
    return lproc.pidns(pid)


def _nspid(pid):
    """pid's pids in each PID namespace this /proc sees it in, outermost first (its status NSpid
    line), [] when it cannot be read: the last is its pid in its own namespace."""
    try:
        for line in _read('%s/%s/status' % (PROC, pid)).splitlines():
            if line.startswith('NSpid:'):
                return [int(x) for x in line.split()[1:]]
    except (OSError, ValueError):
        pass
    return []
