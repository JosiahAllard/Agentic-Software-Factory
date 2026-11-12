"""The cgroup v2 and systemd side of Linux containment (kitlib.platform.linux.contain, whose
docstring gives the layout): a kit job's cgroup names (TOP, NESTED, LEFT, LEAF), reading and
writing a cgroup's files, the job this process runs in (parent_job), the boot hook's cgroup
(delegated_group, sweep), the cgroup2 mount, the user bus (bus_env), the systemctl and busctl
calls, and removing an emptied cgroup. The seams a test points at a fake (CGROOT, PROC,
SYSTEMD_RUN, SYSTEMCTL, BUSCTL, SYSTEMD_DIR) are here.

This module imports on any platform; its calls need Linux."""
import errno
import itertools
import os
import re
import secrets
import shutil
import stat as statmod
import subprocess
import time


CGROOT = '/sys/fs/cgroup'
PROC = '/proc'
SH = '/bin/sh'
SYSTEMD_RUN = 'systemd-run'       # seams: a test points these at a fake
SYSTEMCTL = 'systemctl'
BUSCTL = 'busctl'
PREFIX_ENV = 'KIT_SCOPE_PREFIX'
PREFIX = 'pmkit'
CALL_SECONDS = 30                 # a systemctl or busctl call
HELP_SECONDS = 5                  # systemd-run --help
LEAF, WATCH_LEAF = 'main', 'w'
TOP = re.compile(r'[A-Za-z0-9_.-]+-job-\d+-\d+-[0-9a-f]+\.scope\Z')
TOP_PID = re.compile(r'[A-Za-z0-9_.-]+-job-(\d+)-\d+-[0-9a-f]+\.scope\Z')   # TOP, its maker's pid
LEFT = re.compile(r'[A-Za-z0-9_.-]+-leftovers-(\d+)-\d+-[0-9a-f]+\Z')
NESTED = re.compile(r'j-\d+-\d+-[0-9a-f]+\Z')
BASE = '/pm-kit'                  # the boot hook's cgroup (kitlib.machinesetup.delegation)
SYSTEMD_DIR = '/run/systemd/system'
HOOKS = ('/etc/local.d/pm-kit-cgroup.start', '/etc/cron.d/pm-kit')
GROUP_CONTROLLERS = ('cpuset', 'memory', 'pids')
ANCHOR = BASE + '/anchor'          # the leaf the kit's tmux server runs in
ATTACH = 'tmux -L pm-kit attach -t pm-kit-anchor \\; new-window'     # delegation.ATTACH
# Not kitroot.root(): kitroot imports kitlib.platform, so a platform module naming it would be a cycle.
SETUP = 'sudo python3 %s --delegation --apply' % os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'tools', 'machine_setup.py')
_PREFIX_OK = re.compile(r'[A-Za-z0-9_.-]{1,40}\Z')
_count = itertools.count(1)


def prefix():
    """The first word of every top-level scope's name: KIT_SCOPE_PREFIX when it is a plain name
    (a test run's own, so its scopes can be listed and checked gone), else pmkit."""
    p = os.environ.get(PREFIX_ENV, '')
    return p if _PREFIX_OK.match(p) else PREFIX


def _tag():
    return '%d-%d-%s' % (os.getpid(), next(_count), secrets.token_hex(3))


def _read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def _read_int(path):
    try:
        return int(_read(path).split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _write(path, text):
    with open(path, 'w', encoding='ascii') as f:
        f.write(text)


def cg_dir(cg):
    return CGROOT + cg


def cgroup_of(pid='self'):
    """The cgroup v2 path of pid ('/user.slice/...'), None when no such process; raises OSError
    when it cannot be read or has no cgroup v2 line."""
    try:
        text = _read('%s/%s/cgroup' % (PROC, pid))
    except (FileNotFoundError, ProcessLookupError):
        return None
    for line in text.splitlines():
        if line.startswith('0::'):
            return line[3:] or '/'
    raise OSError(errno.ENOENT, 'no cgroup v2 line in /proc/%s/cgroup' % pid)


def is_job_cgroup(cg):
    """True when cg is a cgroup a kit job made: a top-level kit job scope, or a nested job
    directory under one (each step a nested job name). Plain absolute paths only."""
    if not isinstance(cg, str) or not cg.startswith('/') or '\0' in cg:
        return False
    parts = cg.split('/')[1:]
    if any(p in ('', '.', '..') for p in parts):
        return False
    tops = [i for i, p in enumerate(parts) if TOP.match(p)]
    if not tops:
        return False
    return all(NESTED.match(p) for p in parts[tops[-1] + 1:])


def owned(cg, name):
    """True when cg is the job cgroup this job made: a kit job cgroup whose last step is `name`."""
    return is_job_cgroup(cg) and cg.rsplit('/', 1)[1] == name


def parent_job(own=None):
    """The job cgroup this process runs in (it is in that job's main leaf, with write access to
    the job's cgroup), or None."""
    try:
        cg = cgroup_of('self') if own is None else own
    except OSError:
        return None
    if not cg or not cg.endswith('/' + LEAF):
        return None
    parent = cg[:-len(LEAF) - 1]
    if not is_job_cgroup(parent) or not os.access(cg_dir(parent) + '/cgroup.procs', os.W_OK):
        return None
    return parent


def delegated_group(own=None):
    """(BASE, None) when this process may make its jobs in the boot hook's cgroup: it is this
    user's folder, holding the controllers, and this process runs under it (in the anchor, where
    the kit's tmux server runs, or in a job there), so a move needs only this user's write on
    BASE/cgroup.procs. Else (None, why not), or (None, None) when there is no such route to name
    (no cgroup v2, or no group on a systemd box). `own`: this process's cgroup (a test's)."""
    if not cgroup2():
        return None, None
    d = cg_dir(BASE)
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        if os.path.isdir(SYSTEMD_DIR):
            return None, None
        return None, ('the boot hook did not run (no %s; it is %s or %s, which '
                      '`machine_setup.py --delegation` sets up)' % (d, HOOKS[0], HOOKS[1]))
    except OSError as e:
        return None, 'the kit\'s cgroup %s cannot be read (%s)' % (BASE, e)
    if not statmod.S_ISDIR(st.st_mode):
        return None, 'the kit\'s cgroup %s is a link or a file, not a folder' % d
    me = os.getuid()
    if st.st_uid != me:
        return None, ('the kit\'s cgroup %s is owned by uid %d, not this user (uid %d)'
                      % (BASE, st.st_uid, me))
    try:
        cg = cgroup_of('self') if own is None else own
    except OSError as e:
        return None, 'this process\'s cgroup cannot be read (%s)' % e
    if not cg or not cg.startswith(BASE + '/'):
        if not populated(ANCHOR):
            return None, ('this process runs outside %s (in %s), and the kit\'s tmux server has '
                          'ended (%s is empty): run %s again, or reboot, to start it; a tmux '
                          'server started from a shell is not contained' % (BASE, cg, ANCHOR, SETUP))
        return None, ('this process runs outside %s (in %s): start work from the kit\'s tmux '
                      'server: %s' % (BASE, cg, ATTACH))
    if not os.access(d + '/cgroup.procs', os.W_OK):
        return None, 'the kit\'s cgroup %s: its cgroup.procs is not writable by this user' % BASE
    try:
        ctl = set(_read(d + '/cgroup.subtree_control').split())
    except OSError as e:
        return None, 'the kit\'s cgroup %s: its controllers cannot be read (%s)' % (BASE, e)
    lack = [c for c in GROUP_CONTROLLERS if c not in ctl]
    if lack:
        return None, 'the kit\'s cgroup %s lacks the controllers %s' % (BASE, ', '.join(lack))
    return BASE, None


def sweep(base):
    """Removes the job and leftovers folders in `base` whose maker (the pid in the name) has gone
    and which hold no process: systemd collects its scopes, and in the boot hook's group this
    does. A folder still in use, or not named as a kit job's, is never touched."""
    try:
        names = os.listdir(cg_dir(base))
    except OSError:
        return
    for n in names:
        m = TOP_PID.match(n) or LEFT.match(n)
        if m is None or os.path.exists('%s/%s' % (PROC, m.group(1))):
            continue
        cg = base + '/' + n
        if not populated(cg):
            rmtree_cgroup(cg)


def cgroup2():
    """True when /sys/fs/cgroup is a cgroup2 mount (mountinfo, not a guess)."""
    try:
        text = _read('%s/self/mountinfo' % PROC)
    except OSError:
        return False
    for line in text.splitlines():
        left, _, right = line.partition(' - ')
        f = left.split()
        if len(f) > 4 and f[4] == CGROOT and right.split()[:1] == ['cgroup2']:
            return True
    return False


def bus_env(env):
    """A copy of env that reaches the user's systemd bus (XDG_RUNTIME_DIR added when it is
    missing and /run/user/<uid>/bus exists), or None when no user bus can be named."""
    env = dict(env)
    if env.get('DBUS_SESSION_BUS_ADDRESS') or env.get('XDG_RUNTIME_DIR'):
        return env
    d = '/run/user/%d' % os.getuid()
    if os.path.exists(d + '/bus'):
        env['XDG_RUNTIME_DIR'] = d
        return env
    return None


def _tool(name):
    if os.sep in name:
        return name if os.access(name, os.X_OK) else None
    return shutil.which(name)


def _call(argv):
    """(True when it exited 0, its stderr) of a systemctl or busctl call; never trusted alone:
    each caller reads back what the call was to change."""
    env = bus_env(os.environ) or dict(os.environ)
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, env=env, timeout=CALL_SECONDS, creationflags=0)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, r.stderr.decode('utf-8', 'replace').strip()


_no_expand = {}


def no_expand(run):
    """['--expand-environment=no'] when systemd-run `run` names that switch in its --help, else
    []. systemd 254 added it (254 to 257: off by default with --scope; 258 turned it on), and an
    older one refuses it, but its --scope expands nothing: the stub's $ reach the shell either
    way. Asked once for each path."""
    if run not in _no_expand:
        try:
            out = subprocess.run([run, '--help'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=HELP_SECONDS,
                                 creationflags=0).stdout
        except (OSError, subprocess.SubprocessError):
            out = b''
        _no_expand[run] = ['--expand-environment=no'] if b'--expand-environment' in out else []
    return list(_no_expand[run])


def populated(cg):
    """True while cg or a cgroup under it holds a process; False when empty or gone."""
    try:
        for line in _read(cg_dir(cg) + '/cgroup.events').splitlines():
            if line.startswith('populated '):
                return line.split()[1] == '1'
    except OSError:
        return False
    return False


def wait_empty(cg, seconds):
    end = time.monotonic() + seconds
    pause = 0.001
    while populated(cg):
        if time.monotonic() >= end:
            return False
        time.sleep(pause)
        pause = min(pause * 2, 0.05)
    return True


def rmtree_cgroup(cg):
    """rmdir cg and the cgroups under it, deepest first; one still holding a process stays."""
    d = cg_dir(cg)
    for root, dirs, _files in os.walk(d, topdown=False):
        try:
            os.rmdir(root)
        except OSError:
            pass
