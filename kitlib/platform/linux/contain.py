"""Containment on Linux: the Job of the process face (docs/linux-support-plan.md section 2.3 and
section 8, slice 5). On Linux the face's Job is this module's Job, with the same methods and
meanings, on the strongest tier the machine gives, probed at run time (probe()):

  S1  a cgroup v2 scope of the user's systemd (`systemd-run --user --scope -p Delegate=yes`) with
      the cpuset controller: the cpus pinned by the cgroup, the tree killed as one (cgroup.kill)
  S2  the same scope without cpuset (systemd's default delegation): kill and memory as S1, the
      cpus pinned by affinity only, which a child can set back
  W1  no scope, a user and PID namespace of the job's own (it needs unprivileged user
      namespaces): the tree killed as one by its pid 1's kill, the cpus by affinity, peak
      memory sampled
  W2  no scope: kill by tree walk and an environment marker, the cpus by affinity, peak memory
      sampled; what it lacks is in its report

probe() never trusts an exit code: S1 and S2 come from a trial job, the real start path on
`sleep`, read back (its cgroup, cpuset.cpus.effective, its affinity, memory.peak) and killed
through cgroup.kill, and only a trial that is seen to die and whose scope is seen to go counts.
W1 comes from the same kind of trial (w1_trial): an NsJob on `sleep`, seen in a namespace of its
own, killed through its pid 1 and seen gone.
A process that itself runs in a kit job's leaf (a pool worker, a unit, a slot's command) reads the
tier from that job's cgroup instead, with no trial, and makes its jobs inside it (nesting, below).
Tier.text names the tier and what it lacks, the line the lock and its runs show (slice 7).

The layout of a job's cgroup J (S1, S2): the command and its tree in J/main, the kill_on_close
watchdog in J/w, and the jobs a member makes in J/j-<owner pid>-<n>-<hex>, so killing J kills the
jobs nested in it, as on Windows. A top-level J is the scope `<prefix>-job-<pid>-<n>-<hex>.scope`
(prefix: KIT_SCOPE_PREFIX, default pmkit); a nested J is a directory the owner makes in its own
job's cgroup (the scope is delegated, so the owner may), removed by close once it is empty.

No systemd (delegated_group): the boot hook (kitlib.machinesetup.delegation) makes BASE, /pm-kit,
gives it to the kit user and starts the kit's tmux server (`tmux -L pm-kit`) in its leaf
/pm-kit/anchor. A process under BASE (in that server, or in a job made there) makes each
top-level J as a folder BASE/<prefix>-job-<pid>-<n>-<hex>.scope, the way a nested job is made: the
stub's move is allowed because BASE, their common ancestor, is the user's. The probe tries BASE
(a trial job there) before a systemd scope; close removes J once it is empty, leftovers() moves to
a BASE/<prefix>-leftovers-... folder, and each probe first sweeps BASE of the folders whose maker
has gone and which hold nothing (no systemd collects them). A process outside BASE gets the
lower tier and is told to start from the kit's tmux server (ATTACH), or, when that server has
ended (ANCHOR empty), to run the root step again (SETUP) or reboot: only root starts it there.

Short jobs (Job(short=True): a git call, where a scope of its own, about 85 ms, would cost more
than the command): outside any kit job, each is a nested job in this process's carrier, one
top-level job scope made at the first short job whose command, the holder (`sh -c 'read _'`),
reads a pipe only this process writes, so the holder ends when this process does and systemd
collects the scope once its jobs have ended too. The carrier kills nothing; each short job keeps
its own cgroup, kill, watchdog and leftovers. A stub cannot move itself into the carrier from a
login session's scope (cgroup v2 lets a process be moved only by a writer of the two cgroups'
common ancestor, there root's user-<uid>.slice), so systemd moves it: the stub waits for `in` on
its go pipe while the owner calls AttachProcessesToUnit and reads the stub's cgroup back. The
same call serves a caller anywhere, so there is one path. When that call fails the carrier takes
no more jobs; when it fails or the stub is not seen in place, the stub is ended before go and the
job gets a scope of its own: the command runs once either way. In a kit job's leaf short is moot
(the job nests there); on W1 and W2 there is no scope to share. Blast radius: stopping the
carrier (or systemd-oomd choosing it) ends every short job of this process at once. A forked
child makes its own carrier.

Start, Windows' CREATE_SUSPENDED: the command runs through a sh stub that joins J/main (`echo 0`
to its cgroup.procs, so the stub moves itself), checks /proc/self/cgroup, forks the watchdog, says
so on a ready pipe and waits on a go pipe. Job.start reads the ready line, checks the stub's pid
and cgroup, pins, runs before_resume, then sends go, and the stub execs the command: no instant of
the command runs outside J or unpinned. systemd-run execs in place, so Popen's pid and returncode
are the command's. Variables added only so systemd-run reaches the user bus, and systemd's
INVOCATION_ID, are unset before the exec.

kill_on_close: the watchdog, a subshell of the stub in J/w that ignores INT, HUP, TERM and QUIT,
reads `arm`, `disarm` and `release` lines on a pipe whose write end only the owner holds. Release
ends it; end of file while armed (the owner closed the job, or died, SIGKILL included) writes
J/cgroup.kill, which ends it too. It is in J, so it never outlives J's kill, and it never kills
anything but J. Every write of cgroup.kill, the owner's and the watchdog's, targets J only: the
owner checks the path is a kit job's cgroup, named as this job named it, before it writes.

Stragglers (leftovers): end(kill=False) unpins J and releases the watchdog, so what the command
left keeps running unpinned in J, in the outer job when J is nested, as on Windows; leftovers()
moves them out of J into a scope of their own (top-level, through systemd) or a sibling job
directory (nested). Its call sites are slice 7's.

W2: the stub carries PMKIT_JOB, a list of job tokens each child inherits; the members are the
processes whose environment names the token plus the command's descendants. terminate stops every
member (SIGSTOP), then kills each (SIGKILL) through a pidfd opened before its identity is checked,
in bounded passes. Its watchdog is a separate python, in its own session, that kills by the token
on end of file while armed. Deviation from the plan: no PR_SET_CHILD_SUBREAPER. A subreaper must
be an ancestor, so the stub would have to be one, and the setting survives exec, so the command
would be made the subreaper of its own orphans and gather their zombies; the token finds orphans
instead. A process that empties its environment and leaves the tree is missed (the report says
so). processes() is None on every tier: a cgroup has no count of the processes ever started in it.

W1 (NsJob): the starter, a python (the AppArmor user-namespace rule of Ubuntu 23.10 and later can
be met for the interpreter alone), reads its ids, unshares a user and a PID namespace, maps its own
uid and gid onto themselves (setgroups denied), and forks the job's pid 1, the reaper. Root never
gets W1 (W1_ROOT): mapped alone, uid 0 keeps no capability over another user's files, and only a
writer in the parent namespace could map more, so root's unscoped tier is W2. The reaper
makes a mount namespace of its own, mounts a fresh /proc (the command sees the job's processes
only, by their pids in the namespace), forks the stub (W2's: the ready and go pipes, then exec),
closes its standard streams and then reaps every orphan in the job. It reads the watch pipe as
W2's watchdog does: end of file while armed ends it, and the kernel kills every process in the
namespace with it (pid_namespaces(7): nothing in it outlives its pid 1); released (or disarmed at
end of file) it stays until its last child has ended. The starter, the Popen the caller holds,
ignores the terminal's SIGINT, SIGQUIT and SIGHUP (the command gets its own), exits with the
command's status (a signal raised on itself), and is the job's lifeline: a starter killed before
the command ends (p.kill(), SIGTERM) ends the job. The owner reads the outer /proc: the members
are pid 1's descendants, every process in the namespace and in those nested in it (the starter
and the reaper are not members); terminate kills pid 1 through a pidfd opened at start; and
leftovers() spares pids: the job's kill then goes member by member, as W2's, and pid 1 stays
while they run. A pid the command prints is its pid in the namespace, not the owner's. The command
gets W1_ENV, its namespace: a probe inside the job (_in_w1) skips the scope trial, since the user
systemd would take the pids it names for its own namespace's, and is W2, never a nested W1: its
jobs stay in this namespace, which the lock's run record lists (jobns), so a run nested at any
depth is in that run, and the job's kill ends them all.

The parts: the cgroup and systemd side is kitlib.platform.linux.contain_cgroup, the /proc reads
kitlib.platform.linux.contain_proc, the stub, watchdog and starter scripts
kitlib.platform.linux.contain_scripts; this module holds the tiers, the probe, the carrier and
the Jobs.

This module imports on any platform; its calls need Linux."""
import errno
import os
import select
import shutil
import signal
import subprocess
import sys
import threading
import time

from kitlib.platform.jobparts import JobError, _Held
from kitlib.platform.linux import cpu as lcpu
from kitlib.platform.linux import hardware
from kitlib.platform.linux import process as lproc
from kitlib.platform.mask import cores_text, mask_text
from kitlib.platform.linux.contain_scripts import NSTART, STUB, WATCH, WSTUB
from kitlib.platform.linux.contain_cgroup import (bus_env, BUSCTL, _call, cg_dir, CGROOT, cgroup2,
                                                  cgroup_of, delegated_group, LEAF, no_expand,
                                                  owned, parent_job, prefix, _read, _read_int,
                                                  rmtree_cgroup, SH, sweep, SYSTEMCTL, SYSTEMD_RUN,
                                                  _tag, _tool, wait_empty, WATCH_LEAF, _write)
from kitlib.platform.linux.contain_proc import (_boot, cpu_ticks, MARK_ENV, marked, _nspid, _pidns,
                                                _pids, pstat, rss, _same, tree, vm_hwm)


W1_ENV = 'PMKIT_W1_NS'            # NSTART sets it to its job's pid namespace (_in_w1)
READY_SECONDS = 30.0              # a stub has this long to say it is in its cgroup
GONE_SECONDS = 5.0                # a killed job has this long to empty
TIDY_SECONDS = 0.5                # a released nested job has this long to empty before close
KILL_PASSES = 8
CONTROLLERS = ('cpuset', 'cpu', 'memory', 'pids')

S2_LACK = 'cpu pinning by the cgroup (affinity only: a child can set its own back)'
PEAK_LACK = 'peak memory (sampled)'
W2_LACKS = ('a guaranteed kill (a tree walk and an environment marker: a process that empties '
            'its environment and leaves the tree is missed)',
            'cleanup when its watchdog is killed', 'cpu pinning (affinity only)', PEAK_LACK)
W1_LACKS = (S2_LACK, PEAK_LACK)
WHAT = {'S1': 'strong', 'S2': 'partial', 'W1': 'partial', 'W2': 'weak'}


def _error(text):
    return JobError(text)


# ------------------------------------------------------------------ the tier
class Tier:
    """What probe() found: `name` S1, S2, W1 or W2, `lacks` what it does not give, `why` the
    reason there is no scope (W1, W2), `w1` why W1 is not used (W2), `parent` the job cgroup a
    nested owner runs in, `base` the boot hook's cgroup its top-level jobs are made in (no
    systemd). `text` is the line a status shows."""

    def __init__(self, name, lacks=(), why=None, w1=None, parent=None, base=None):
        self.name, self.lacks, self.why, self.w1 = name, tuple(lacks), why, w1
        self.parent, self.base = parent, base

    @property
    def scoped(self):
        return self.name in ('S1', 'S2')

    @property
    def text(self):
        parts = [self.name]
        if self.base:
            parts.append('jobs in the kit\'s cgroup %s' % self.base)
        if self.why:
            parts.append('no cgroup scope: %s' % self.why)
        if self.lacks:
            parts.append('lacks ' + ', '.join(self.lacks))
        if self.w1:
            parts.append(self.w1)
        return 'containment: %s (%s)' % (WHAT[self.name], '; '.join(parts))

    def __repr__(self):
        return '<Tier %s>' % self.text


_probe_lock = threading.Lock()
_probed = None


def probe(fresh=False):
    """This process's tier, found once (fresh: again)."""
    global _probed
    with _probe_lock:
        if _probed is None or fresh:
            _probed = _probe()
        return _probed


IN_W1 = 'it runs in a W1 job, whose pids the user systemd cannot name'
W1_NESTED = 'W1 not nested: its jobs stay in the namespace of the W1 job it runs in'


def _in_w1():
    """True when this process runs in a W1 job: W1_ENV names its own pid namespace. Its pids
    are not the user systemd's there, and a scope trial would name another process to it."""
    ns = os.environ.get(W1_ENV)
    return bool(ns) and ns == str(lproc.pidns())


def _probe():
    if _in_w1():
        # W2, never a nested W1: every level stays in the job's namespace, which the run's
        # record lists (jobns), so a run nested at any depth is in that run (lock nest_check);
        # the W1 job's kill ends them all
        return Tier('W2', W2_LACKS, why=IN_W1, w1=W1_NESTED)
    parent = parent_job()
    if parent is not None:
        tier = nested_tier(parent)
        if tier is not None:
            return tier
        # a scope of its own would take its jobs out of the job it runs in
        return _unscoped('it runs in kit job %s, which cannot hold a job' % parent)
    # no systemd: the boot hook's group, when this process runs under it (its tmux server)
    base, gwhy = delegated_group()
    if base is not None:
        sweep(base)
        name, lacks, why = trial(base)
        if name:
            return Tier(name, lacks, base=base)
        gwhy = why
    name, lacks, why = trial()
    if name:
        return Tier(name, lacks)
    return _unscoped(gwhy or why)


W1_ROOT = ("W1 not used as root: its namespace maps root's own id only, so root's capabilities "
           "would not reach another user's files (a 0750 home folder closed to its git)")


def _unscoped(why):
    """W1 when its trial works here, else W2 with why W1 is not used. Root (sudo's root steps,
    which have no user systemd) is W2 without a trial: W1_ROOT."""
    if hasattr(os, 'geteuid') and os.geteuid() == 0:    # (the tests run this module on Windows)
        return Tier('W2', W2_LACKS, why=why, w1=W1_ROOT)
    no = w1_trial()
    if no is None:
        return Tier('W1', W1_LACKS, why=why)
    return Tier('W2', W2_LACKS, why=why, w1='W1 unavailable (%s)' % no)


def nested_tier(parent):
    """The tier of jobs made inside job cgroup `parent`, read from its controllers; None when it
    cannot hold one (no cgroup.kill, or its controllers cannot be read)."""
    d = cg_dir(parent)
    try:
        ctl = set(_read(d + '/cgroup.subtree_control').split())
    except OSError:
        return None
    if not os.path.exists(d + '/cgroup.kill'):
        return None
    lacks = [] if 'memory' in ctl else [PEAK_LACK]
    if 'cpuset' in ctl:
        return Tier('S1', lacks, parent=parent)
    return Tier('S2', [S2_LACK] + lacks, parent=parent)


def trial(base=None):
    """(tier name or None, lacks, why not) from a trial job: ScopeJob's own start on `sleep`,
    pinned to one cpu, read back and killed. The exit codes of systemd-run and of the trial are
    never what decides. `base`: the boot hook's cgroup (delegated_group), where the job is a
    folder this process makes, with no systemd-run."""
    if not cgroup2():
        return None, (), 'no cgroup v2 mount at %s' % CGROOT
    if base is None:
        if _tool(SYSTEMD_RUN) is None:
            return None, (), 'no systemd-run'
        if bus_env(os.environ) is None:
            return None, (), ('no user systemd bus (no XDG_RUNTIME_DIR, no /run/user/%d/bus)'
                              % os.getuid())
    try:
        want = max(os.sched_getaffinity(0))
    except (OSError, ValueError):
        return None, (), 'this process\'s affinity cannot be read'
    job = ScopeJob(1 << want, True, tier=Tier('S1', base=base))
    what = 'scope' if base is None else 'job'
    p = None
    try:
        try:
            p = job.start(['sleep', '30'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
        except OSError as e:
            if base is not None:
                return None, (), 'a trial job in %s could not be made (%s)' % (base, e)
            lack = ('' if no_expand(_tool(SYSTEMD_RUN)) else
                    '; its systemd-run --help names no --expand-environment')
            return None, (), 'a trial scope could not be made (%s)%s' % (e, lack)
        d = cg_dir(job.cg)
        try:
            eff = set(hardware.parse_cpulist(_read(d + '/cpuset.cpus.effective')))
        except (OSError, ValueError):
            eff = None
        try:
            aff = set(os.sched_getaffinity(p.pid))
        except OSError:
            aff = None
        pinned = eff == {want} and aff == {want}
        peak = _read_int(d + '/memory.peak')
        wrote = job.terminate()
        try:
            p.wait(GONE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        died = p.returncode == -signal.SIGKILL
        # systemd removes its scope; the group's job folder is emptied, and close removes it
        gone = _wait_gone(d, GONE_SECONDS) if base is None else wait_empty(job.cg, GONE_SECONDS)
        if not (wrote and died and gone):
            return None, (), ('the trial %s was not ended by its cgroup.kill (written %s, '
                              'the command %s, the %s %s)'
                              % (what, wrote, 'killed' if died else 'exit %r' % p.returncode,
                                 what, 'gone' if gone else 'still there'))
    finally:
        job.close()
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
        if base is None and job.cg is not None and os.path.isdir(cg_dir(job.cg)):
            _call([_tool(SYSTEMCTL) or SYSTEMCTL, '--user', 'stop', job.unit + '.scope'])
    lacks = [] if peak is not None else [PEAK_LACK]
    if not pinned:
        return 'S2', [S2_LACK] + lacks, None
    return 'S1', lacks, None


def _wait_gone(d, seconds):
    end = time.monotonic() + seconds
    while os.path.isdir(d):
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)
    return True


def w1_trial():
    """None when W1 works here, else why not: a trial job, NsJob's own start on `sleep`, whose
    command is seen in a PID namespace of its own and which is killed through its pid 1, the
    command seen killed and pid 1 seen gone. No exit code decides alone."""
    if not hasattr(os, 'unshare') or not hasattr(os, 'pidfd_open'):
        return 'this python has no os.unshare or os.pidfd_open'
    job = NsJob(None, True, tier=Tier('W1', W1_LACKS))
    p = None
    try:
        try:
            p = job.start(['sleep', '30'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as e:
            return 'a trial job could not start (%s)' % e
        seen = job.contains(job.command) == 'in' and job.ns != _pidns('self')
        wrote = job.terminate()
        try:
            p.wait(GONE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        died = p.returncode == -signal.SIGKILL
        gone = _wait_ended(job.reaper, GONE_SECONDS)
        if not (seen and wrote and died and gone):
            return ("the trial job was not ended by its pid 1's kill (in its namespace %s, "
                    'killed %s, the command %s, pid 1 %s)'
                    % (seen, wrote, 'killed' if died else 'exit %r' % p.returncode,
                       'gone' if gone else 'still there'))
    finally:
        job.close()
        if p is not None and p.poll() is None:
            p.kill()
            p.wait()
    return None


def _wait_ended(pid, seconds):
    end = time.monotonic() + seconds
    while True:
        st = pstat(pid)
        if st is None or st[2]:
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.01)


# ------------------------------------------------------------------ the carrier
class Carrier:
    """This process's carrier: a top-level job scope (`name`, cgroup `cg`) whose command, the
    holder, reads a pipe only this process writes and ends at its end of file. Short jobs are
    nested in it. It kills nothing; `off` is why it takes no more jobs."""

    def __init__(self, job):
        self.pid, self.job, self.holder = os.getpid(), job, job.p
        self.cg, self.name, self.off = job.cg, job.name, None

    def alive(self):
        return self.holder.poll() is None and os.path.isdir(cg_dir(self.cg))

    def refuse(self, why):
        with _carrier_lock:
            if self.off is None:
                self.off = why
            self._end()

    def _end(self):
        """Lets the holder go (its pipe closed), so the scope ends once its jobs have."""
        try:
            self.holder.stdin.close()
        except OSError:
            pass
        try:
            self.holder.wait(GONE_SECONDS)
        except subprocess.TimeoutExpired:
            self.holder.kill()
            self.holder.wait()


_carrier_lock = threading.Lock()
_carrier = None


def carrier(tier):
    """This process's carrier for a short job of tier `tier` (made the first time, and again when
    the last one has gone), or None: it takes no more jobs, or one cannot be made now."""
    global _carrier
    with _carrier_lock:
        c = _carrier
        if c is not None and c.pid == os.getpid():
            if c.off is not None:
                return None
            if c.alive():
                return c
            c._end()
        _carrier = None
        job = ScopeJob(None, False, tier=Tier(tier.name, tier.lacks))
        try:
            job.start([SH, '-c', 'read _'], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, cwd='/')
        except (OSError, ValueError):
            job.close()
            return None
        job.close()                          # released: its watchdog ends, the holder runs on
        _carrier = Carrier(job)
        return _carrier


def drop_carrier():
    """Lets this process's carrier go (a test's cleanup; an owner that is done with short jobs).
    The next short job makes a new one."""
    global _carrier
    with _carrier_lock:
        c, _carrier = _carrier, None
        if c is not None and c.pid == os.getpid():
            c._end()


def _forked():
    """In a forked child: the parent's carrier is not this process's (its holder's pipe closed
    here, so the parent's exit still ends it)."""
    global _carrier, _carrier_lock
    _carrier_lock = threading.Lock()
    c, _carrier = _carrier, None
    if c is not None:
        try:
            c.holder.stdin.close()
        except OSError:
            pass


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_forked)


# ------------------------------------------------------------------ the job
_mixed = {}


class Job:
    """The face's Job on Linux: Job(mask=None, kill_on_close=True) is a ScopeJob (S1, S2), an
    NsJob (W1) or a WeakJob (W2) as probe() finds (`tier` instead: a test's or the trial's). One
    command per job: start a second time and it raises JobError. `pin`, `effective` and `note`
    mean what they mean on Windows. short=True: a short command (a git call), carried in this
    process's carrier scope instead of a scope of its own when it can be (the module docstring)."""

    def __new__(cls, mask=None, kill_on_close=True, tier=None, short=False):
        if not issubclass(cls, (ScopeJob, WeakJob)):
            tier = tier or probe()
            impl = ScopeJob if tier.scoped else NsJob if tier.name == 'W1' else WeakJob
            if cls is not Job:              # a subclass of Job (a test's): mixed with the tier's
                key = (cls, impl)
                if key not in _mixed:
                    _mixed[key] = type(cls.__name__, (cls, impl), {})
                impl = _mixed[key]
            cls = impl
        return object.__new__(cls)

    def __init__(self, mask=None, kill_on_close=True, tier=None, short=False):
        self.tier = tier or probe()
        self.short = bool(short)
        self.kill_on_close = bool(kill_on_close)
        self.note, self.p, self.closed = None, None, False
        self._child, self._k, self._g, self._peak = None, None, None, 0
        every = hardware.all_mask()
        self.mask = (mask & every) if mask is not None else None
        if mask is not None and not self.mask:
            self.note, self.mask = ('this machine has none of cores %s'
                                    % cores_text(mask)), None
        self.wd = None
        self.pin = 'pinned' if self.mask is not None else None
        self.effective = self.mask

    # ---- start
    def start(self, cmd, before_resume=None, **kw):
        """Popen(cmd) through the stub, in the job and pinned before it runs, then let go; as
        kitlib.platform.windows.job.Job.start on Windows (Ctrl+C held the same way, _Held). Raises
        OSError when the command cannot start (FileNotFoundError when it is not found), and JobError
        when it cannot be put in the job; the stub is then ended. `creationflags` are Windows's and
        dropped; shell=True runs cmd under /bin/sh."""
        if self._child is not None or self.closed:
            raise _error('a job on Linux starts one command; this one has started one or is closed')
        kw.pop('creationflags', None)
        if kw.pop('shell', False):
            args = [cmd] if isinstance(cmd, (str, bytes)) else list(cmd)
            cmd = [SH, '-c'] + args
        elif isinstance(cmd, (str, bytes)):
            cmd = [cmd]
        cmd = [os.fsdecode(a) for a in cmd]
        if not cmd:
            raise ValueError('an empty command')
        if kw.get('executable') is not None:
            raise ValueError('Job.start on Linux takes no executable=')
        env = dict(os.environ if kw.get('env') is None else kw['env'])
        _find(cmd[0], env, kw.get('cwd'))
        held = _Held()
        try:
            p = self._begin(cmd, env, kw)
            try:
                self._pin_start(p)
                self.check_pin(p)
                if before_resume is not None:
                    before_resume(p)
                held.deliver()
                self._go()
                held.deliver()
            except BaseException:
                self._abort(p)
                raise
            self.p = p
        finally:
            held.close()
        return p

    def _ready(self, p, fd, want=('s',)):
        """{kind: words} of the stub's ready lines up to its `s` line (each kind in `want`);
        JobError when it ends or says `x` first, or is silent READY_SECONDS."""
        buf, lines = b'', {}
        end = time.monotonic() + READY_SECONDS
        poller = select.poll()
        poller.register(fd, select.POLLIN)
        while not all(k in lines for k in want):
            left = end - time.monotonic()
            if left <= 0:
                raise _error('the command was not in its job %ds after its start' % READY_SECONDS)
            if not poller.poll(min(left, 0.25) * 1000):
                continue
            chunk = os.read(fd, 4096)
            if not chunk:
                why = ' '.join(lines['x']) if 'x' in lines else None
                if why is None:
                    try:
                        why = 'its starter ended first (exit %s)' % p.wait(GONE_SECONDS)
                    except subprocess.TimeoutExpired:
                        why = 'its starter closed the ready pipe'
                raise _error('the command could not be put in its job: %s' % why)
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                words = line.decode('utf-8', 'replace').split()
                if words:
                    lines[words[0]] = words[1:]
        return lines

    def _go(self):
        try:
            os.write(self._g, b'go\n')
        except OSError as e:
            raise _error('the command ended before it was let go (%s)' % e)
        finally:
            _close(self._g)
            self._g = None

    def _abort(self, p):
        """A start that failed: the job's processes ended (the stub at least), its pipes closed,
        so its watchdog ends too, and the caller's pipes to the stub closed (no one gets p)."""
        try:
            if not self._kill_all():
                p.kill()
        except OSError:
            p.kill()
        p.wait()
        _close(self._g)
        _close(self._k)
        self._g = self._k = None
        self._reap_watchdog()
        for f in (p.stdin, p.stdout, p.stderr):
            if f is not None:
                try:
                    f.close()
                except OSError:
                    pass

    def _reap_watchdog(self):
        pass

    def assign(self, proc):
        """Puts a live process (a Popen, or a pid) in the job; raises JobError when it cannot."""
        raise _error('a job on Linux holds only the command it started '
                     '(W1 and W2 have no way to add one)')

    def assign_self(self):
        self.assign(os.getpid())

    # ---- pin
    def check_pin(self, proc):
        """Reads proc's affinity back: a limit outside the job (an outer cpuset) wins, so a mask
        it overrides makes pin "overridden" and effective what it runs on."""
        if self.pin != 'pinned' or self.mask is None:
            return self.pin
        try:
            got = lcpu.cpus_mask(os.sched_getaffinity(int(getattr(proc, 'pid', proc))))
        except OSError:
            return self.pin
        if got != self.mask:
            mt = mask_text
            self.pin, self.effective = 'overridden', got
            self.note = ('a limit outside the job wins: the command runs on cpus %s, not %s'
                         % (mt(got), mt(self.mask)))
        return self.pin

    def _pin_affinity(self, pids, mask):
        ids = lcpu.mask_cpus(mask) if mask is not None else hardware.threads()
        ok = True
        for pid in pids:
            if lcpu.set_threads(pid, ids) not in ('pinned', 'gone'):
                ok = False
        return ok

    def narrow(self, mask):
        """Pins the pinned job to `mask`, a non-empty part of its mask, while it runs; False, the
        pin unchanged, when it is not pinned, `mask` is not part of its mask, or it is refused."""
        if self.closed or self.pin != 'pinned' or self.mask is None:
            return False
        if not mask or mask & ~self.mask:
            return False
        return self.repin(mask)

    def repin(self, mask):
        """Sets the pinned job's mask to `mask` (narrow's undo); False, unchanged, when refused."""
        if self.closed or self.pin != 'pinned' or not mask:
            return False
        if self._child is None:
            self.mask = self.effective = mask
            return True
        if not self._set_pin(mask):
            self._set_pin(self.mask)
            return False
        self.mask = self.effective = mask
        return True

    def unpin(self):
        """The processes left in the job get every cpu back, and the job drops kill_on_close."""
        if self.mask is not None and self._child is not None:
            self._set_pin(None)
        self.mask = self.pin = self.effective = None
        self.kill_on_close = False
        self._watch('disarm')

    # ---- read
    def processes(self):
        """None on Linux: no tier counts the processes ever started in a job."""
        return None

    def tree_working_set(self):
        """Bytes: the resident sets of the job's processes now, summed."""
        try:
            return sum(rss(pid) for pid in self.pids())
        except OSError:
            return 0

    # ---- end
    def set_kill_on_close(self, on):
        self.kill_on_close = bool(on)
        return self._watch('arm' if on else 'disarm')

    def _watch(self, line):
        if self._k is None:
            return False
        try:
            os.write(self._k, (line + '\n').encode('ascii'))
            return True
        except OSError:
            return False

    def end(self, kill):
        """kill: end every process in the job. Else let them live, unpinned. Closes the job."""
        if self.closed:
            if kill and self.p is not None and self.p.poll() is None:
                self.p.kill()
            return
        if kill:
            self.terminate(1)
        else:
            self.unpin()
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _close(fd):
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _find(prog, env, cwd):
    """Raise as Popen would when prog cannot be run: the stub's exec would fail too late."""
    if '/' in prog:
        path = prog if os.path.isabs(prog) or not cwd else os.path.join(os.fspath(cwd), prog)
        if not os.path.isfile(path):
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), prog)
        if not os.access(path, os.X_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), prog)
        return
    if shutil.which(prog, path=env.get('PATH', os.defpath)) is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), prog)


# dash, the usual /bin/sh, takes a single-digit descriptor only in a redirection (`>&12` is a
# syntax error), and a busy process's pipes are past 9: this moves the stub's pipes to 3, 4, 5...
# (FDS), closes the originals and execs the stub, the pid unchanged. The copies above 9 are
# close-on-exec, so the stub holds its pipes once each.
REMAP = ('import fcntl,os,sys\n'
         'a=sys.argv;n=int(a[1]);fds=[int(x) for x in a[2:2+n]]\n'
         'hi=[fcntl.fcntl(f,fcntl.F_DUPFD_CLOEXEC,10) for f in fds]\n'
         'for f in fds:os.close(f)\n'
         'for i,f in enumerate(hi):os.dup2(f,3+i)\n'
         'os.execv(a[2+n],a[2+n:])\n')
FDS = ('3', '4', '5')


def _popen(argv, kw, env, fds):
    """subprocess.Popen of the stub (argv, from its shell on) with the caller's keywords, its
    pipes `fds` moved to FDS first (REMAP)."""
    if kw.get('pass_fds'):
        raise ValueError('Job.start on Linux takes no pass_fds=: the stub owns descriptors 3-5')
    py = sys.executable
    if not py or not os.path.isabs(py):
        raise _error('no interpreter path to start the stub with (sys.executable is %r)' % py)
    kw = dict(kw)
    kw['env'] = env
    kw['pass_fds'] = tuple(fds)
    kw['close_fds'] = True
    lead = [py, '-I', '-S', '-c', REMAP, str(len(fds))] + [str(f) for f in fds]
    return subprocess.Popen(lead + list(argv), creationflags=0, **kw)


class ScopeJob(Job):
    """S1 and S2: the job is a cgroup, J (the module docstring has its layout)."""

    base = None                                  # set from the tier in __init__

    def __init__(self, mask=None, kill_on_close=True, tier=None, short=False):
        Job.__init__(self, mask, kill_on_close, tier, short)
        self.cg = self.unit = self.name = None
        self.parent = self.tier.parent
        self.base = self.tier.base               # top-level: a folder in the boot hook's group
        self.carried = None                      # the carrier this job is in
        self.cpuset = self.tier.name == 'S1'     # the pin is the cgroup's, not affinity

    def _begin(self, cmd, env, kw):
        """A short job outside any kit job goes in this process's carrier; when the carrier
        cannot take it (none can be made, the move is refused or not seen), the stub is ended
        before go and the job gets a scope of its own, so the command runs once."""
        via = (carrier(self.tier) if self.short and self.parent is None and self.base is None
               else None)
        if via is None:
            return self._begin_in(cmd, env, kw, None)
        self.parent, self.carried = via.cg, via
        try:
            return self._begin_in(cmd, env, kw, via)
        except JobError as e:
            self.parent = self.carried = self.cg = self.name = self.unit = self.wd = None
            if getattr(e, 'refused', False):
                via.refuse(str(e))
        return self._begin_in(cmd, env, kw, None)

    def _put(self, via, made, p):
        """Asks systemd to move the stub p (it waits for `in`) into made/main of carrier `via`,
        reads it back, and lets the stub on. JobError when the call fails (`refused` set: the route
        is refused, so the carrier takes no more jobs) or the stub is not seen there; the stub is
        ended first."""
        ok, err = _call([_tool(BUSCTL) or BUSCTL, '--user', 'call', 'org.freedesktop.systemd1',
                         '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                         'AttachProcessesToUnit', 'ssau', via.name,
                         made[len(via.cg):] + '/' + LEAF, '1', str(p.pid)])
        try:
            got = cgroup_of(p.pid)
        except OSError:
            got = None
        why = None
        if not ok:
            why = 'systemd did not move the command into the carrier: %s' % (err or '?')
        elif got != made + '/' + LEAF:
            why = 'the command is in %s, not the carrier\'s %s/%s' % (got, made, LEAF)
        else:
            try:
                os.write(self._g, b'in\n')
                return
            except OSError as e:
                why = 'the stub ended before it was let in (%s)' % e
        _close(self._g)                 # the stub reads end of file and exits; killed anyway
        self._g = None
        p.kill()
        p.wait()
        e = _error(why)
        e.refused = not ok              # the route itself: the carrier takes no more jobs
        raise e

    def _begin_in(self, cmd, env, kw, via):
        r_r, r_w = os.pipe()
        g_r, g_w = os.pipe()
        k_r, k_w = os.pipe()
        self._g, self._k = g_w, k_w
        made, p = None, None
        try:
            unset = []
            if self.parent is None and self.base is not None:
                # the boot hook's group: a folder this process makes, as a nested job's, named
                # as a top-level job's (no systemd to name or collect it; sweep removes it)
                self.unit = '%s-job-%s' % (prefix(), _tag())
                self.name = self.unit + '.scope'
                made = self.base + '/' + self.name
                self._make_nested(made)
                argv, join, penv = [], made, env
            elif self.parent is None:
                self.unit = '%s-job-%s' % (prefix(), _tag())
                self.name = self.unit + '.scope'
                run = _tool(SYSTEMD_RUN)
                benv = bus_env(env)
                if run is None or benv is None:
                    raise _error('no systemd-run or no user bus for a scope')
                unset = [n for n in benv if n not in env]
                if 'INVOCATION_ID' not in env:
                    unset.append('INVOCATION_ID')
                props = ['-p', 'Delegate=yes']
                if self.mask is not None and self.cpuset:
                    props += ['-p', 'AllowedCPUs=' + mask_text(self.mask)]
                argv = ([run, '--user', '--scope', '--quiet', '--collect'] + no_expand(run)
                        + ['--unit', self.unit] + props + ['--'])
                join, penv = '', benv
            else:
                self.name = 'j-' + _tag()
                made = self.parent + '/' + self.name
                self._make_nested(made)
                argv, join, penv = [], made, env
            argv += [SH, '-c', STUB, 'pmkit-job', self.name, join] + list(FDS) + [
                '1' if self.kill_on_close else '0', ' '.join(unset),
                '0' if via is None else '1'] + cmd
            p = _popen(argv, kw, penv, (r_w, g_r, k_r))
        except BaseException:
            for fd in (r_r, r_w, g_r, g_w, k_r, k_w):
                _close(fd)
            self._g = self._k = None
            if made is not None:
                rmtree_cgroup(made)
            raise
        for fd in (r_w, g_r, k_r):
            _close(fd)
        try:
            try:
                if via is not None:
                    self._put(via, made, p)
                lines = self._ready(p, r_r)
            finally:
                _close(r_r)
            if lines.get('w'):
                self.wd = int(lines['w'][0])
            s = lines['s']
            if len(s) != 2 or int(s[0]) != p.pid:
                raise _error('the ready line named pid %s, not the command\'s %d' % (s[:1], p.pid))
            cg = s[1]
            if made is not None and cg != made or not owned(cg, self.name):
                raise _error('the command is in %s, not a cgroup this job made' % cg)
            if cgroup_of(p.pid) != cg + '/' + LEAF:
                raise _error('the command is not in its job\'s leaf %s/%s' % (cg, LEAF))
            self.cg = cg
            if made is None:
                self._enable(cg)
        except BaseException:
            self._abort(p)
            if made is not None and wait_empty(made, GONE_SECONDS):
                rmtree_cgroup(made)
            raise
        self._child = p
        return p

    def assign(self, proc):
        """Puts a live process (a Popen, or a pid) in the job's main leaf; raises JobError when
        the job has no cgroup yet or the move is refused."""
        pid = int(getattr(proc, 'pid', proc))
        if self.cg is None or self.closed:
            raise _error('the job has no cgroup yet: it gets one when it starts its command')
        try:
            _write(cg_dir(self.cg) + '/' + LEAF + '/cgroup.procs', str(pid))
        except OSError as e:
            raise _error('pid %d could not be put in %s (%s)' % (pid, self.cg, e))

    def _make_nested(self, cg):
        d = cg_dir(cg)
        os.mkdir(d)
        try:
            self._enable(cg)
            os.mkdir(d + '/' + LEAF)
            os.mkdir(d + '/' + WATCH_LEAF)
            if self.mask is not None and self.cpuset:
                if os.path.exists(d + '/cpuset.cpus'):
                    _write(d + '/cpuset.cpus', mask_text(self.mask))
                else:
                    self.cpuset = False
                    self.note = 'the cpus are pinned by affinity only: the outer job has no cpuset'
        except OSError as e:
            rmtree_cgroup(cg)
            raise _error('the job cgroup %s could not be made (%s)' % (cg, e))

    def _enable(self, cg):
        """Turns on, for the cgroups under cg, the controllers cg has (nested jobs pin and
        measure with them). Best effort: a controller left off shows in nested_tier."""
        d = cg_dir(cg)
        try:
            have = set(_read(d + '/cgroup.controllers').split())
        except OSError:
            return
        want = ' '.join('+' + c for c in CONTROLLERS if c in have)
        if want:
            try:
                _write(d + '/cgroup.subtree_control', want)
            except OSError:
                pass

    def _pin_start(self, p):
        if self.mask is None or self.cpuset:
            return
        if not self._pin_affinity([p.pid], self.mask):
            self.note = 'the core pin could not be set (affinity refused)'
            self.mask = self.pin = self.effective = None

    def _set_pin(self, mask):
        """Pins J to mask (None: every cpu) and reads it back; True when it holds."""
        if self.cg is None:
            return False
        if not self.cpuset:
            return self._pin_affinity(self.pids(), mask)
        text = mask_text(mask) if mask is not None else ''
        d = cg_dir(self.cg)
        if self.parent is None and self.base is None:
            _call([_tool(SYSTEMCTL) or SYSTEMCTL, '--user', 'set-property', '--runtime',
                   self.name, 'AllowedCPUs=' + text])
        else:
            try:
                _write(d + '/cpuset.cpus', text or '\n')
            except OSError:
                return False
        want = set(lcpu.mask_cpus(mask)) if mask is not None else None
        end = time.monotonic() + 2.0
        while True:
            try:
                eff = set(hardware.parse_cpulist(_read(d + '/cpuset.cpus.effective')))
            except (OSError, ValueError):
                eff = None
            if eff is not None and (eff == want if want is not None else
                                    eff == set(hardware.threads()) or not text):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)

    # ---- read
    def pids(self):
        """The pids in J now (its main leaf and the jobs nested in it, never a watchdog);
        [] once J is gone; raises OSError when J cannot be read."""
        if self.cg is None:
            return []
        top = cg_dir(self.cg)
        out = []
        for root, dirs, _files in os.walk(top):
            dirs[:] = [x for x in dirs if x != WATCH_LEAF]
            try:
                text = _read(root + '/cgroup.procs')
            except FileNotFoundError:
                continue
            out.extend(int(x) for x in text.split())
        return [p for p in out if p != self.wd]

    def contains(self, pid):
        """"in" (J or a job nested in it holds it), "out", "gone" or "unknown"."""
        pid = int(pid)
        if self.cg is None or pid <= 0:
            return 'unknown'
        try:
            cg = cgroup_of(pid)
        except OSError:
            return 'unknown'
        if cg is None:
            return 'gone'
        return 'in' if cg == self.cg or cg.startswith(self.cg + '/') else 'out'

    def usage(self):
        """(CPU seconds so far, J's peak memory (memory.peak; else the most seen), its tree's
        resident set now), or None (not started, closed or gone)."""
        if self.cg is None or self.closed:
            return None
        d = cg_dir(self.cg)
        try:
            usec = None
            for line in _read(d + '/cpu.stat').splitlines():
                if line.startswith('usage_usec '):
                    usec = int(line.split()[1])
            if usec is None:
                return None
        except (OSError, ValueError):
            return None
        ws = self.tree_working_set()
        peak = _read_int(d + '/memory.peak')
        if peak is None:
            self._peak = max(self._peak, _read_int(d + '/memory.current') or ws)
            peak = self._peak
        own = vm_hwm(self.p.pid) if self.p is not None else 0
        return (usec / 1e6, peak, max(own, ws))

    # ---- end
    def _kill_all(self):
        """Writes J's cgroup.kill; True written or J gone, False when no J is known or the write
        is refused. Raises JobError, writing nothing, when J is not a cgroup this job made."""
        if self.cg is None:
            return False
        top = self.parent if self.parent is not None else self.base
        if not owned(self.cg, self.name) or (top is not None and
                                             self.cg != top + '/' + self.name):
            raise _error('refused to kill %r: not the cgroup this job made' % self.cg)
        try:
            _write(cg_dir(self.cg) + '/cgroup.kill', '1')
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    def terminate(self, code=1):
        """Kills every process in J (cgroup.kill; a signal carries no exit code). Only starts each
        end, as on Windows."""
        return self._kill_all()

    def leftovers(self, pids=None):
        """Moves pids (default: every process in J) out of J, into a scope of their own
        (top-level J) or a sibling job directory (nested J), unpinned and out of J's kill.
        Returns the pids moved; raises JobError when one could not be moved."""
        if self.cg is None:
            return []
        pids = [p for p in (self.pids() if pids is None else pids)
                if p != self.wd and self.contains(p) == 'in']
        if not pids:
            return []
        if self.parent is None and self.base is None:
            unit = '%s-leftovers-%s.scope' % (prefix(), _tag())
            _call([_tool(BUSCTL) or BUSCTL, '--user', 'call', 'org.freedesktop.systemd1',
                   '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager',
                   'StartTransientUnit', 'ssa(sv)a(sa(sv))', unit, 'fail', '2', 'PIDs', 'au',
                   str(len(pids))] + [str(p) for p in pids] + ['CollectMode', 's',
                                                               'inactive-or-failed', '0'])
            dest = None
        else:
            # nested: a sibling job folder; in the boot hook's group: a leftovers folder there,
            # which sweep removes once it is empty and this process has gone
            dest = (self.parent + '/j-' + _tag() if self.parent is not None
                    else self.base + '/%s-leftovers-%s' % (prefix(), _tag()))
            try:
                os.mkdir(cg_dir(dest))
                for p in pids:
                    try:
                        _write(cg_dir(dest) + '/cgroup.procs', str(p))
                    except ProcessLookupError:
                        pass
            except OSError as e:
                raise _error('the leftovers could not be moved to %s (%s)' % (dest, e))
        end = time.monotonic() + GONE_SECONDS
        while True:
            still = [p for p in pids if self.contains(p) == 'in']
            if not still:
                return pids
            if time.monotonic() >= end:
                raise _error('leftovers %s are still in the job' % still)
            time.sleep(0.01)

    def close(self):
        """Kill-on-close: kills J. Else releases the watchdog, so what runs in J runs on. A
        nested J is removed once it is empty (TIDY_SECONDS at most for a released one)."""
        if self.closed:
            return
        self.closed = True
        killed = False
        if self.kill_on_close and self.cg is not None:
            try:
                killed = self._kill_all()
            except OSError:
                killed = False
        elif self._k is not None:
            self._watch('release')
        _close(self._k)
        self._k = None
        _close(self._g)
        self._g = None
        if (self.parent is not None or self.base is not None) and self.cg is not None:
            if wait_empty(self.cg, GONE_SECONDS if killed else TIDY_SECONDS):
                rmtree_cgroup(self.cg)


class WeakJob(Job):
    """W2: no cgroup. The members are the processes whose PMKIT_JOB names this job's token, and
    the command's descendants."""

    def __init__(self, mask=None, kill_on_close=True, tier=None, short=False):
        Job.__init__(self, mask, kill_on_close, tier, short)
        self.token = 'w' + _tag()
        self.spared = set()
        self._ctime = None

    def _begin(self, cmd, env, kw):
        env = dict(env)
        env[MARK_ENV] = (env.get(MARK_ENV, '') + ' ' + self.token).strip()
        r_r, r_w = os.pipe()
        g_r, g_w = os.pipe()
        self._g = g_w
        try:
            p = _popen([SH, '-c', WSTUB, 'pmkit-job'] + list(FDS[:2]) + cmd, kw, env,
                       (r_w, g_r))
        except BaseException:
            for fd in (r_r, r_w, g_r, g_w):
                _close(fd)
            self._g = None
            raise
        _close(r_w)
        _close(g_r)
        try:
            try:
                lines = self._ready(p, r_r)
            finally:
                _close(r_r)
            if lines['s'][:1] != [str(p.pid)]:
                raise _error('the ready line named pid %s, not the command\'s %d'
                             % (lines['s'][:1], p.pid))
            self._ctime = lproc.proc_ctime(p.pid)
            self._child = p
            self._spawn_watchdog()
        except BaseException:
            self._abort(p)
            raise
        return p

    def _spawn_watchdog(self):
        k_r, k_w = os.pipe()
        try:
            self.wd = subprocess.Popen(
                [sys.executable, '-I', '-S', '-c', WATCH, self.token, str(k_r),
                 '1' if self.kill_on_close else '0', str(KILL_PASSES)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(k_r,), close_fds=True, start_new_session=True, creationflags=0)
        except OSError as e:
            _close(k_w)
            raise _error('the job\'s watchdog could not start (%s)' % e)
        finally:
            _close(k_r)
        self._k = k_w

    def _pin_start(self, p):
        if self.mask is not None and not self._pin_affinity([p.pid], self.mask):
            self.note = 'the core pin could not be set (affinity refused)'
            self.mask = self.pin = self.effective = None

    def _set_pin(self, mask):
        return self._pin_affinity(self.pids(), mask)

    def _proc(self):
        return self._child

    def _reap_watchdog(self):
        if self.wd is not None:
            try:
                self.wd.wait(GONE_SECONDS)
            except subprocess.TimeoutExpired:
                pass

    def members(self):
        """{pid: ctime} of the job's processes now."""
        boot = _boot()
        found = marked(self.token)
        p = self._proc()
        if p is not None and self._ctime is not None and boot is not None:
            found[p.pid] = self._ctime
            for pid, ct in tree(p.pid, self._ctime, boot).items():
                found.setdefault(pid, ct)
        for pid, ct in list(found.items()):
            st = pstat(pid, boot) if boot is not None else None
            if (pid in self.spared or (self.wd is not None and pid == self.wd.pid)
                    or st is None or st[0] != ct or st[2]):
                del found[pid]
        return found

    def pids(self):
        return sorted(self.members())

    def contains(self, pid):
        pid = int(pid)
        if pid <= 0:
            return 'unknown'
        if pid in self.members():
            return 'in'
        return 'gone' if lproc.proc_ctime(pid) is None else 'out'

    def usage(self):
        """(CPU seconds of the live members and their reaped children, the most memory seen
        (sampled at each call), the tree's resident set now), or None (closed or not started)."""
        if self.closed or self._proc() is None:
            return None
        pids = self.pids()
        ticks = sum(cpu_ticks(pid) for pid in pids)
        ws = sum(rss(pid) for pid in pids)
        self._peak = max(self._peak, ws)
        own = vm_hwm(self._proc().pid)
        return (ticks / float(os.sysconf('SC_CLK_TCK')), self._peak, max(own, ws))

    def _kill_pass(self):
        """One pass: every member stopped, then killed, each through a pidfd opened before its
        identity is checked. Returns how many it reached."""
        fds = []
        boot = _boot()
        for pid, ct in self.members().items():
            try:
                fd = os.pidfd_open(pid)
            except OSError:
                continue
            # the pidfd pins one process: the one with ct, or a reuse of the pid, which is let go
            st = pstat(pid, boot)
            if st is None or st[0] != ct:
                _close(fd)
                continue
            fds.append(fd)
        for sig in (signal.SIGSTOP, signal.SIGKILL):
            for fd in fds:
                try:
                    signal.pidfd_send_signal(fd, sig)
                except OSError:
                    pass
        for fd in fds:
            _close(fd)
        return len(fds)

    def _kill_all(self):
        if self._proc() is None:
            return False
        for _ in range(KILL_PASSES):
            if not self._kill_pass():
                return True
            time.sleep(0.02)
        return not self.members()

    def terminate(self, code=1):
        """Kills every member it can find, in bounded passes; True when none is left."""
        return self._kill_all()

    def leftovers(self, pids=None):
        """Takes pids (default: every member) out of the job's kill, and its watchdog's."""
        pids = [p for p in (self.pids() if pids is None else pids) if p in self.members()]
        self.spared.update(pids)
        if pids:
            self._watch('spare ' + ' '.join(str(p) for p in pids))
        return pids

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.kill_on_close and self._proc() is not None:
            self._kill_all()
        elif self._k is not None:
            self._watch('release')
        _close(self._k)
        self._k = None
        _close(self._g)
        self._g = None
        self._reap_watchdog()


class NsJob(WeakJob):
    """W1: no cgroup; the job is a user and PID namespace of its own whose pid 1 is the reaper
    (the module docstring). `p` is the starter; `command` is the command's pid, `reaper` pid 1's
    and `ns` the namespace's inode, all as this process sees them. The members are pid 1's
    descendants. kill: pid 1's, unless leftovers() spared some (then W2's passes over the
    members)."""

    def __init__(self, mask=None, kill_on_close=True, tier=None, short=False):
        WeakJob.__init__(self, mask, kill_on_close, tier, short)
        self.ns = self.reaper = self.command = None
        self._rfd = self._rct = None

    def _begin(self, cmd, env, kw):
        r_r, r_w = os.pipe()
        g_r, g_w = os.pipe()
        k_r, k_w = os.pipe()
        self._g, self._k = g_w, k_w
        try:
            p = _popen([sys.executable, '-I', '-S', '-c', NSTART,
                        '1' if self.kill_on_close else '0', SH, WSTUB] + cmd, kw, env,
                       (r_w, g_r, k_r))
        except BaseException:
            for fd in (r_r, r_w, g_r, g_w, k_r, k_w):
                _close(fd)
            self._g = self._k = None
            raise
        for fd in (r_w, g_r, k_r):
            _close(fd)
        try:
            try:
                lines = self._ready(p, r_r, want=('r', 's'))
            finally:
                _close(r_r)
            self._place(p, lines)
            self._child = p
        except BaseException:
            self._abort(p)
            raise
        p.pidns = self.ns
        return p

    def _place(self, p, lines):
        """Checks the ready lines name the starter's child as pid 1 of a namespace of its own and
        the stub as a process in it; keeps a pidfd on pid 1."""
        try:
            reaper, inner = int(lines['r'][0]), int(lines['s'][0])
        except (IndexError, ValueError):
            raise _error('a ready line is not a pid: %r' % lines)
        boot = _boot()
        st = pstat(reaper, boot)
        if st is None or st[1] != p.pid or st[2] or _nspid(reaper)[-1:] != [1]:
            raise _error("the ready line named pid %d, not the starter's pid 1" % reaper)
        try:
            fd = os.pidfd_open(reaper)
        except OSError as e:
            raise _error("no pidfd on the job's pid 1 (%s)" % e)
        # the pidfd pins one process: pid 1, or a reuse of its pid, which is let go unsignalled
        if not _same(reaper, st[0], boot):
            _close(fd)
            raise _error("the job's pid 1 ended before its pidfd was opened")
        self._rfd = fd
        self.reaper, self._rct, self.wd = reaper, st[0], reaper
        ns = _pidns(reaper)
        if ns is None or ns == _pidns('self'):
            raise _error("the job's pid 1 is in no PID namespace of its own")
        self.ns = ns
        for pid in _pids():
            c = pstat(pid, boot)
            if (c is not None and c[1] == reaper and _nspid(pid)[-1:] == [inner]
                    and _pidns(pid) == ns):
                self.command, self._ctime = pid, c[0]
                return
        raise _error("the ready line named pid %d, not a process in the job's namespace"
                     % inner)

    def _pin_start(self, p):
        if self.mask is not None and not self._pin_affinity(
                [p.pid, self.reaper, self.command], self.mask):
            self.note = 'the core pin could not be set (affinity refused)'
            self.mask = self.pin = self.effective = None

    def _reap_watchdog(self):
        _close(self._rfd)
        self._rfd = None

    def members(self):
        """{pid: ctime} of the job's processes now, but pid 1 and the spared: pid 1's
        descendants, which are every process in its namespace and in the namespaces nested in it
        (an orphan in a PID namespace goes to a reaper in it, at worst its pid 1)."""
        boot = _boot()
        if self.reaper is None or boot is None:
            return {}
        found = tree(self.reaper, self._rct, boot)
        for pid, ct in list(found.items()):
            st = pstat(pid, boot)
            if pid in self.spared or st is None or st[0] != ct or st[2]:
                del found[pid]
        return found

    def usage(self):
        """As WeakJob's, the peak resident set the command's own (VmHWM)."""
        if self.closed or self.command is None:
            return None
        pids = self.pids()
        ticks = sum(cpu_ticks(pid) for pid in pids)
        ws = sum(rss(pid) for pid in pids)
        self._peak = max(self._peak, ws)
        return (ticks / float(os.sysconf('SC_CLK_TCK')), self._peak,
                max(vm_hwm(self.command), ws))

    def _kill_all(self):
        """pid 1 killed through its pidfd (the kernel kills the rest), True once no member is
        left; with spared pids, W2's passes over the members instead, pid 1 left running."""
        if self._rfd is None:
            return False
        if self.spared:
            return WeakJob._kill_all(self)
        try:
            signal.pidfd_send_signal(self._rfd, signal.SIGKILL)
        except OSError:
            pass                                        # ended already
        end = time.monotonic() + GONE_SECONDS
        while self.members():
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True

    def leftovers(self, pids=None):
        """Takes pids (default: every member) out of the job's kill, and pid 1's. pid 1 knows
        a member by its pid in the job's namespace, at pid 1's depth of the member's NSpid (a
        member in a namespace nested in the job's has more pids after it)."""
        now = self.members()
        pids = [p for p in (sorted(now) if pids is None else pids) if p in now]
        self.spared.update(pids)
        depth = len(_nspid(self.reaper))
        inner = [n[depth - 1] for n in (_nspid(p) for p in pids) if depth and len(n) >= depth]
        if inner:
            self._watch('spare ' + ' '.join(str(n) for n in inner))
        return pids
