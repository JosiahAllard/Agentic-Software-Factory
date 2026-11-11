"""A held run (kitlib.lock.run, whose docstring gives the whole run): the Slot book() returns and
the Launcher slot() yields. The Slot starts the run's commands in its job, pinned, with the
environment they get (MACHINE_LOCK_RUN, the cpuset, the gpus, no shared build daemons, docker's
cpu-only variables), measures their use (Usage), watches for console windows on Windows, ends
the tree when the run is cancelled, interrupted or errs, and releases the run (kitlib.lock.run_end
settle).

Nothing here prints: progress lines go to store.say.
"""
import contextlib
import os
import subprocess
import time

import kitlib.platform
from kitlib.lock import giveback as gbk
from kitlib.lock import jobset
from kitlib.lock import layout as lay
from kitlib.lock import site
from kitlib.lock import stale
from kitlib.lock.store import LockError, iso, read_record, remove
from kitlib.proc import daemons, process
from kitlib.platform.windows import cpu, winwatch
from kitlib.proc.job import Job, JobError, cores_text
from kitlib.lock.run_cmdline import alias_check
from kitlib.lock.run_end import (child_code, end_sparing, move_leftovers, on_release, settle,
                                 tell_daemons)
from kitlib.lock.layout import POOLS
from kitlib.lock.queue import PICK


NEST_ENV = "MACHINE_LOCK_RUN"
ORIGIN_ENV = "MACHINE_LOCK_ORIGIN"      # a detached run's launcher ancestry, pid:ctime;...
CPUSET_ENV, CPUMASK_ENV, CPUS_ENV = "MACHINE_LOCK_CPUSET", "MACHINE_LOCK_CPUMASK", "MACHINE_LOCK_CPUS"
GPUS_ENV = "MACHINE_LOCK_GPUS"          # the gpu cards the run holds, by adapter index
XLA_FRACTION_ENV, XLA_DEFAULT_FRACTION = "XLA_PYTHON_CLIENT_MEM_FRACTION", "0.3"
KILL_HOW = ("interrupted", "cancelled", "error")
# A run watches for console windows (kitlib.platform.windows.winwatch) from its first command's
# start to its release (Slot.watch_stop); False turns the monitor off. Windows only: there are no
# console windows to watch elsewhere.
WATCH_WINDOWS = True
# What a `docker exec` run holding no gpu slot gets, so the process in the container keeps
# off the gpus the lock gave other runs (the job cannot reach into the container).
DOCKER_CPU_ENV = (("CUDA_VISIBLE_DEVICES", ""), ("JAX_PLATFORMS", "cpu"))
# What every run's commands get so a build never runs on a daemon outside the run's job (the
# owner's choice: off under the lock, warm-daemon reuse given up): a client that finds an idle
# daemon in its registry hands the build to it, and that daemon, started outside any run or left
# unpinned by an earlier one, builds on every core. Gradle's off switch: the build runs in the
# client, or in a single-use daemon that is the client's child, so it stays in the job, pinned.
# (variable, flag added to its value) - no_shared_daemons.
NO_SHARED_DAEMON_ENV = (("GRADLE_OPTS", "-Dorg.gradle.daemon=false"),)
# docker exec options that take a value as the next argument (`docker exec --help`).
DOCKER_EXEC_VALUE_OPTS = ("-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir",
                          "--detach-keys")


# ------------------------------------------------------------------ the record
class Usage:
    """A run's measured use, from its job object: CPU seconds, the most physical cores
    busy over any sample window (CPU time per second over the threads a core has), the process
    tree's peak working set and the job's peak committed memory; and, for live views, the cores
    busy over the last window and the working set now. A background run's restarts add up."""

    def __init__(self, threads_per_core=1, sample_s=5.0):
        self.tpc, self.sample_s = max(1, threads_per_core), sample_s
        self.done_s, self.cur_s, self.peak_cores, self.last = 0.0, 0.0, 0.0, None
        self.peak_mem, self.peak_commit, self.now_cores, self.now_mem = 0, 0, 0.0, 0

    def sample(self, job):
        """Reads the job; True when a sample window closed (the record is worth saving)."""
        u = job.usage() if job is not None else None
        if u is None:
            return False
        self.cur_s, commit, mem = u
        self.now_mem = mem
        self.peak_mem, self.peak_commit = max(self.peak_mem, mem), max(self.peak_commit, commit)
        t = time.monotonic()
        if self.last is None:
            self.last = (t, self.cur_s)
            return False
        if t - self.last[0] < self.sample_s:
            return False
        rate = (self.cur_s - self.last[1]) / (t - self.last[0])
        self.now_cores = rate / self.tpc
        self.peak_cores = max(self.peak_cores, self.now_cores)
        self.last = (t, self.cur_s)
        return True

    def job_ended(self):
        self.done_s, self.cur_s, self.last, self.now_cores, self.now_mem = (
            self.done_s + self.cur_s, 0.0, None, 0.0, 0)

    def fields(self):
        return {"cpu_s": round(self.done_s + self.cur_s, 1),
                "peak_cpu_cores": round(self.peak_cores, 2),
                "peak_mem_mb": int(self.peak_mem // 2**20),
                "peak_commit_mb": int(self.peak_commit // 2**20),
                "cpu_cores_now": round(self.now_cores, 2), "mem_mb_now": int(self.now_mem // 2**20)}


def docker_cpu_env(cmd, holds_gpu):
    """cmd with `-e CUDA_VISIBLE_DEVICES= -e JAX_PLATFORMS=cpu` put right after `exec` when cmd is
    `docker exec ...` (an argument list) and the run holds no gpu slot; cmd itself
    otherwise. A variable the caller already sets with -e/--env before the container keeps the
    caller's value: it is not added again."""
    if holds_gpu or not isinstance(cmd, (list, tuple)) or len(cmd) < 2:
        return cmd
    if os.path.basename(str(cmd[0])).lower() not in ("docker", "docker.exe") or cmd[1] != "exec":
        return cmd
    given, args, i = set(), [str(a) for a in cmd[2:]], 0
    while i < len(args) and args[i].startswith("-") and args[i] != "--":
        a, val = args[i], None
        if a in DOCKER_EXEC_VALUE_OPTS:
            val = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        elif a.startswith("--env="):
            a, val = "--env", a[len("--env="):]
        elif a.startswith("-e") and len(a) > 2:
            a, val = "-e", a[2:]
        if a in ("-e", "--env") and val is not None:
            given.add(val.split("=", 1)[0])
        i += 1
    add = []
    for name, value in DOCKER_CPU_ENV:
        if name not in given:
            add += ["-e", "%s=%s" % (name, value)]
    return list(cmd[:2]) + add + list(cmd[2:])


def no_shared_daemons(env):
    """Adds each NO_SHARED_DAEMON_ENV flag to its variable in env (a dict, changed in place): after
    the caller's own value, which is kept, and only when that value does not hold the flag as a
    word already, so a nested run or a run started inside one does not add it twice. A caller's
    own -Dorg.gradle.daemon=true comes first and so loses (the JVM keeps the last -D); an
    explicit --daemon on the command line still wins (Gradle's rule)."""
    for name, flag in NO_SHARED_DAEMON_ENV:
        have = env.get(name) or ""
        if flag not in have.split():
            env[name] = "%s %s" % (have.rstrip(), flag) if have.strip() else flag
    return env


def export_threads(env, job):
    """The threads the command runs on (every thread of this machine when it is not pinned), as
    a Linux cpuset list of global thread ids and a hex mask (docker --cpuset-cpus)."""
    mask = job.mask if job.mask is not None else cpu.all_mask()
    env[CPUSET_ENV] = env[CPUS_ENV] = cpu.mask_text(mask)
    env[CPUMASK_ENV] = hex(mask)


def pin_fields(job):
    """The run record's pin fields from its job: pinned only when the mask is in force; pin is
    "pinned", "overridden" (an outer job's limit wins) or None; threads what it runs on."""
    return {"pinned": job.pin == "pinned", "pin": job.pin, "pin_note": job.note,
            "threads": cpu.mask_text(job.effective) if job.effective else None}


# ------------------------------------------------------------------ book, Slot, launcher
class Launcher:
    """What slot() yields: starts commands in the run's job. start(cmd, **popen_kw) ->
    Popen (env defaults to the run's: MACHINE_LOCK_RUN, the cpuset, the gpus); run(cmd, **kw) ->
    its exit code, waited for with the run's use sampled and a stop honoured; wait(p, timeout)
    -> exit code, None when p ran past timeout seconds and its tree was ended (Slot.wait);
    pids(); usage() -> the run's use so far; kill() ends every process in the job but a
    build daemon Slot.end_tree spares (one another job's client uses or may use), with its tree.
    id, cores (cpu slots), gpus (gpu slots), mask (the global thread mask, or None) and env read
    the run."""

    def __init__(self, slot):
        self._slot = slot

    id = property(lambda self: self._slot.id)
    cores = property(lambda self: list(self._slot.cores))
    gpus = property(lambda self: list(self._slot.gpus))
    mask = property(lambda self: self._slot.job.effective if self._slot.job else None)
    env = property(lambda self: dict(self._slot.env))

    def start(self, cmd, **kw):
        return self._slot.start(cmd, **kw)

    def wait(self, p, timeout=None):
        return self._slot.wait(p, timeout)[0]

    def run(self, cmd, **kw):
        return self.wait(self.start(cmd, **kw))

    def pids(self):
        return self._slot.job.pids() if jobset.is_open(self._slot.job) else []

    def usage(self):
        return self._slot.usage_fields()

    def kill(self):
        if self._slot.job is not None:
            self._slot.end_tree()


def note_jobns(run, then=None):
    """A before_resume for a run's job start (Linux): when the job gives its command a pid
    namespace of its own (p.pidns, contain's W1), the run's record lists it in `jobns` before
    the command runs, so a run nested in it is seen to be (nest_check). Then then(p), if given.
    A record that cannot be written is left: the nested run then takes its own lock."""
    def before(p):
        ns = getattr(p, "pidns", None)
        if ns is not None and ns not in run.rec.setdefault("jobns", []):
            run.rec["jobns"].append(ns)
            with contextlib.suppress(OSError):
                run.save()
        if then is not None:
            then(p)
    return before


class Slot:
    """A held run (book()). start/wait/release, or the launcher; a context manager that
    releases (how "error" and the tree ended when the block raised, "interrupted" on Ctrl+C).
    A block that raised names its exception on the ended record (exception_fields)."""

    def __init__(self, store, run, L, own, parent, mutex, env, on_release_cmd, kind_entry,
                 shares, log=None, gate=False):
        self.store, self.run, self.L, self.own, self.parent = store, run, L, own, parent
        self.mutex, self.on_release_cmd, self.log, self.gate = mutex, on_release_cmd, log, gate
        self.nested = run is None
        self.id = parent["id"] if self.nested else run.id
        self.cores = list(parent["cores"] if self.nested else run.rec["cores"])
        self.gpus = list(parent["gpus"] if self.nested else run.rec["gpus"])
        self.code, self.how, self.released, self.started, self.ended = 0, "done", False, False, None
        # end_tree: the tree was ended since the last start; it spared a shared daemon's tree.
        self.tree_ended, self.spared = False, False
        self.warn_s = kind_entry["warn_min"] * 60 * store.settings.warn_scale
        self.kind = kind_entry
        self.usage = run.usage if run else Usage(L["cpu_slot_threads"], store.settings.usage_sample_s)
        self.env = no_shared_daemons(dict(env))
        self.env.pop(ORIGIN_ENV, None)
        # Only the command start(give_back=True) starts may give slots back (kitlib.lock.giveback).
        self.env.pop(gbk.GIVE_ENV, None)
        # ack_due: a give-back's ack (the record's given_back) is still to be written.
        self.offer, self.offer_bad, self.given, self.ack_due = None, 0, [], False
        # The window monitor (start, watch_stop) and what it saw: None until it stops.
        self.watch, self.windows, self.windows_error, self.windows_gone = None, None, None, 0
        mask = None
        if not self.nested:
            self.env[NEST_ENV] = "%s:%d:%d" % (run.id, run.rec["pid"], run.rec["ctime"])
            if own["gpu"] and shares.get("gpu") == "training" and not self.env.get(XLA_FRACTION_ENV):
                self.env[XLA_FRACTION_ENV] = XLA_DEFAULT_FRACTION
            if own["cpu"]:
                mask = lay.cores_mask(L, run.rec["cores"])
        try:
            # A run with no cores of its own stays on its parent's: the job nests in the parent's.
            # Where a job starts one command (Linux), a job per command (kitlib.lock.jobset).
            self.job = jobset.JobSet(mask, make=Job) if jobset.one_command() else Job(mask)
        except JobError as e:
            raise LockError(1, "could not start the command: %s" % e)
        if own["cpu"]:
            export_threads(self.env, self.job)
        if own["gpu"]:
            self.env[GPUS_ENV] = ",".join(str(c) for c in lay.gpu_cards_of(L, run.rec["gpus"]))
        if not self.nested:
            with contextlib.suppress(OSError):
                run.save(**pin_fields(self.job))
        self.launcher = Launcher(self)

    def held_text(self):
        """'test holds cpu slots 0-1 for repo/slug (test, run r-...); the command
        runs on cores 0-3'"""
        if self.nested:
            return "nested: run %s already holds what it needs; the command runs in its job" % self.id
        r = self.run.rec
        return "%s holds %s for %s/%s (%s, run %s); the command runs on %s%s" % (
            r["kind"], " + ".join(lay.slots_text(p, r[PICK[p]]) for p in POOLS if r[PICK[p]]),
            r["repo"], r["slug"], r["kind"], self.id,
            cores_text(self.job.effective) if self.own["cpu"] else
            "the cores of run %s" % self.parent["id"], "; " + self.job.note if self.job.note else "")

    def note_tree(self, tree, gate_mode=None):
        """Records `tree`, the git tree hash this held run now tests (book's `tree`; a landing's
        merge, run in the slot the landing holds), and `gate_mode`, the mode of the gate it runs
        (book's `gate_mode`), on its record and so its history line. A nested slot has no record
        of its own and records nothing; nor is an empty tree or mode recorded. A failed write is
        ignored: the record is a note, never what holds the slots."""
        fields = dict(({"tree": tree} if tree else {}),
                      **({"gate_mode": gate_mode} if gate_mode else {}))
        if self.nested or not fields:
            return
        with contextlib.suppress(OSError):
            self.run.save(**fields)

    def start(self, cmd, give_back=False, **kw):
        """Popen(cmd) in the run's job; OSError when it cannot start, AliasStubError (one) when
        its program is a WindowsApps App Execution Alias, which would start it outside the job
        (alias_check). give_back: cmd may give back cpu slots it no longer needs while it runs
        (kitlib.lock.giveback); only a run that holds its own cpu slots offers it, and wait()
        serves it."""
        if self.released:
            raise LockError(1, "run %s has released its slots" % self.id)
        alias_check(cmd)
        if give_back and not self.nested and self.own["cpu"] and self.job.mask is not None:
            self.offer, self.offer_bad = gbk.request_path(self.store, self.id), 0
            remove(self.offer)
            kw["env"] = dict(kw.get("env") or self.env, **{gbk.GIVE_ENV: self.offer})
        if self.run is not None:
            for k, v in self.run.popen_kw(self.env).items():
                kw.setdefault(k, v)
        else:
            kw.setdefault("env", self.env)
        if (WATCH_WINDOWS and kitlib.platform.NAME == "windows" and self.watch is None
                and self.windows is None):
            self.watch_start()
        if self.run is not None and kitlib.platform.NAME == "linux":
            kw["before_resume"] = note_jobns(self.run, kw.get("before_resume"))
        pin = self.job.pin
        p = self.job.start(docker_cpu_env(cmd, bool(self.gpus)), **kw)
        self.started, self.tree_ended = True, False
        if self.job.pin != pin and self.run is not None:
            with contextlib.suppress(OSError):
                self.run.save(**pin_fields(self.job))
        return p

    def watch_start(self):
        """Starts the window monitor. Never raises: a monitor that cannot start is windows_error
        (and the record's `windows_error`), and the run goes on unwatched. self.watch is set before
        m.start(), which can wait up to winwatch.START_WAIT_S: a Ctrl+C inside it leaves the monitor
        for the release's watch_stop to stop, so its thread and hooks never outlive the run."""
        m = None
        try:
            m = winwatch.Monitor(ours=self.window_owner)
            self.watch = m
            m.start()
        except Exception as e:      # noqa: BLE001 - the monitor never fails a run
            self.watch = None
            with contextlib.suppress(Exception):
                if m is not None:
                    m.stop()
            self.windows, self.windows_error = [], "%s: %s" % (type(e).__name__, e)
            if self.run is not None:
                self.run.rec["windows_error"] = self.windows_error

    def window_owner(self, pid):
        """Is process pid this run's, for the window monitor: True (in the run's job, or a job
        nested in it), False (not in it) or None (gone, or Windows will not say)."""
        job = self.job
        return {"in": True, "out": False}.get(job.contains(pid) if job is not None else None)

    def watch_stop(self):
        """Stops the window monitor, once, and keeps what it saw: self.windows (a list of
        winwatch.Window), windows_error and windows_gone, and in the run's record `windows` (each
        Window.as_dict(), when any was seen, at most winwatch.RECORD_CAP of them, with
        `windows_truncated` the count left out) and `windows_error`. Returns self.windows: None
        when nothing was watched. Never raises an Exception: a monitor that fails to stop is its
        error. self.watch is cleared only once m.stop() has returned or raised an Exception, so a
        Ctrl+C inside it leaves the monitor for the release's second call to stop."""
        m = self.watch
        if m is None:
            return self.windows
        try:
            windows = list(m.stop())
        except Exception as e:      # noqa: BLE001 - the monitor never fails a run
            windows = []
            m.error = m.error or "%s: %s" % (type(e).__name__, e)
        self.watch = None
        self.windows, self.windows_error = windows, m.error
        self.windows_gone = getattr(m, "gone", 0)
        if self.run is not None:
            if windows:
                kept, cut = winwatch.records(windows)
                self.run.rec["windows"] = kept
                if cut:
                    self.run.rec["windows_truncated"] = cut
            if m.error:
                self.run.rec["windows_error"] = m.error
        return self.windows

    def window_lines(self, fails=False):
        """The window report (winwatch.report_lines) once the monitor has stopped; [] when
        nothing was watched. fails: the caller's verdict counts a window."""
        if self.windows is None:
            return []
        return winwatch.report_lines(self.windows, self.windows_error, self.windows_gone, fails)

    def stop_asked(self):
        return self.run is not None and self.run.stop_asked()

    def wait(self, p, timeout=None):
        """(exit code, how) of child p: its use sampled every settings.child_poll_s into the
        record; a stop ends the job's tree ("cancelled", 1; end_tree); past the kind's warn_min a
        warning, never a kill. `timeout` (seconds from this call; None: none): a child still
        running past it has the job's tree ended the same way, (None, "timeout"); self.code is
        then 1. A stop and the timeout are both seen on this thread, between two polls, a stop
        first, so the tree is ended once whichever comes. Either then names each process
        descended from p that is outside the job and still alive (escaped, escape_lines): it is
        not ended."""
        t0, warned = time.monotonic(), False
        while True:
            try:
                code, how = child_code(p.wait(self.store.settings.child_poll_s)), "done"
                break
            except subprocess.TimeoutExpired:
                pass
            if self.usage.sample(self.job) and self.run is not None:
                with contextlib.suppress(OSError):
                    self.run.save(**self.usage.fields())
            if self.offer is not None:
                self.serve_give_back()
            stop = self.stop_asked()
            if stop or (timeout is not None and time.monotonic() - t0 >= timeout):
                if stop:
                    # Read again for queue_read_s: a stop file being written (or swapped in) can
                    # refuse its first read, and its why is the only way to tell the lock's stop.
                    req, _there = read_record(self.store.path("active", self.id + ".stop"),
                                              self.store.settings.queue_read_s)
                    if isinstance(req, dict) and req.get("by") == stale.BY and req.get("why"):
                        self.store.say("run %s was ended by the lock: %s; its process tree is "
                                       "ended" % (self.id, req["why"]))
                    else:
                        self.store.say("run %s was cancelled by stop; its process tree is ended"
                                       % self.id)
                found = escaped(p.pid, self.job.contains)   # read while their parents live
                self.end_tree()
                try:                        # the command itself may be a daemon it spared
                    p.wait(10 if self.spared else None)
                except subprocess.TimeoutExpired:
                    pass
                for line in escape_lines(self.id, found):
                    self.store.say(line)
                code, how = (1, "cancelled") if stop else (None, "timeout")
                break
            if not warned and time.monotonic() - t0 > self.warn_s:
                self.store.say("WARNING: run %s (%s) has held its lock past %d min, the guide for "
                               "its kind; it is not stopped. Check it is still making progress."
                               % (self.id, self.run.rec["kind"] if self.run else "nested",
                                  self.warn_s // 60))
                warned = True
        self.code, self.how = 1 if code is None else code, how
        if self.offer is not None:
            remove(self.offer)
            self.offer = None
        return code, how

    def serve_give_back(self):
        """Does the offered command's give-back request, if one waits (kitlib.lock.giveback):
        narrow the job, then the record, then the queue entry, then the ack; else refuse,
        changing nothing. First it writes an ack an earlier poll could not.
        Returns why it was refused, or None. Never raises: a failure refuses."""
        req = {}
        if self.ack_due:
            # A give-back is done but its ack write failed: the record in memory carries it.
            with contextlib.suppress(OSError):
                self.run.save()
                self.ack_due = False
        try:
            req = gbk.read_request(self.offer) if self.offer else None
            if req is None:
                return None
            if "bad" in req and "id" not in req and self.offer_bad < 2:
                self.offer_bad += 1         # a file read mid-replace: it looks again next poll
                return None
            self.offer_bad = 0
            remove(self.offer)
            try:
                pids = self.job.pids()
            except OSError:
                pids = []
            why = gbk.check(req, self.cores, self.nested, bool(self.own["cpu"]), self.job.pin,
                            pids, process.alive)
            if why is None:
                why = self._give_back(req)
        except Exception as e:              # noqa: BLE001 - the run goes on holding every slot
            req, why = req if isinstance(req, dict) else {}, "%s: %s" % (type(e).__name__, e)
        if why is not None:
            self.store.say("run %s: give back refused: %s" % (self.id, why))
            if req.get("id") and self.run is not None:
                with contextlib.suppress(OSError):
                    self.run.save(give_back_refused={"id": req["id"], "why": why, "at": iso()})
        return why

    def _give_back(self, req):
        keep = [i for i in self.cores if i not in req["cpu"]]
        old = self.job.mask
        if not self.job.narrow(lay.cores_mask(self.L, keep)):
            return "the run's job could not be narrowed to %s" % lay.slots_text("cpu", keep)
        given = sorted(set(self.given) | set(req["cpu"]))
        env = dict(self.env)
        export_threads(env, self.job)
        ack = {"id": req["id"], "cpu": given, "cores": keep, "cpumask": env[CPUMASK_ENV],
               "cpuset": env[CPUSET_ENV], "at": iso()}
        try:
            # False: the slots are given back but the ack is not on disk yet; it stays in the
            # record in memory and serve_give_back writes it again on the next poll.
            self.ack_due = not gbk.shrink(self.store, self.run, keep, pin_fields(self.job), ack)
        except Exception as e:              # noqa: BLE001 - the queue still holds every slot
            # Widening the job's pin moves its processes back onto every slot (Job.repin).
            if self.job.repin(old):
                return ("the lock store could not be written (%s); the run keeps every slot "
                        "booked and its job is pinned to them again" % e)
            return ("the lock store could not be written (%s); the run keeps every slot booked, "
                    "but its job stays narrowed to %s (the pin could not be widened again)"
                    % (e, cores_text(self.job.effective)))
        self.cores, self.given, self.env = keep, given, env
        self.store.say("run %s gave back %s; it holds %s; its commands run on %s" % (
            self.id, lay.slots_text("cpu", req["cpu"]), lay.slots_text("cpu", keep),
            cores_text(self.job.effective)))
        return None

    def usage_fields(self):
        self.usage.sample(self.job)
        return self.usage.fields()

    def stop_daemons(self):
        """The build daemons (kitlib.proc.daemons) still in the run's job are stopped with their
        children when the machine config's machine_lock.daemons says so (kitlib.lock.site
        stops_daemons: by default only after a gate run); each is said and listed in the record's
        `daemons`. A daemon that serves a client outside the run's job (another run's build is
        using it), or whose clients cannot be told, is left running and its result says why
        (kitlib.proc.daemons stop). A nested slot leaves them to the run it is nested in, unless
        it is a gate: then it stops those in its own job and records none. Never raises: what it
        cannot read or stop runs on, unpinned, and a store.say that fails is dropped (the record
        is then not given these results)."""
        try:
            if not site.stops_daemons(self.store, self.gate):
                return
            found = daemons.find(self.job.pids())
            if not found:
                return
            done = daemons.stop(found, self.job.contains)
        except Exception as e:
            try:
                self.store.say("WARNING: run %s: build daemons not checked: %s" % (self.id, e))
            except Exception:               # noqa: BLE001 - not said: the release goes on
                pass
            return
        try:
            self.tell_daemons(done)
        except Exception:                   # noqa: BLE001 - not said: the release goes on
            pass

    def tell_daemons(self, done):
        """tell_daemons for this run: its results said, and listed in its record (none for a
        nested slot)."""
        tell_daemons(self.store, self.id, self.run.rec if self.run is not None else None, done)

    def end_tree(self):
        """The kill path (a cancel, Ctrl+C, an error, Launcher.kill): end_sparing on the run's
        job, which ends every process in it but a build daemon another job's client uses or may
        use, with that daemon's tree, and lists its judgement in the record's `daemons`. Sets
        self.spared to what end_sparing returns; release then closes the job without killing it.
        A second end_tree (the release's, after a Ctrl+C) adds its results to the record's
        `daemons` (tell_daemons), so what the first one stopped stays listed.

        Catches BaseException, so a KeyboardInterrupt (a second Ctrl+C) or any other BaseException
        that is not an Exception is caught, tree_ended and spared are cleared, and it is raised
        again. The release that follows then ends the tree again, and a release whose own
        end_tree it interrupts ends the whole job."""
        try:
            self._end_tree()
        except BaseException:               # a second Ctrl+C: release ends the tree again
            self.tree_ended = self.spared = False
            raise

    def _end_tree(self):
        self.tree_ended, self.spared = True, False
        self.spared = end_sparing(self.store, self.id, self.job,
                                  self.run.rec if self.run is not None else None)

    def release(self, code=None, how=None):
        """Ends the run: its job (the tree killed when interrupted, cancelled or an error, but for
        a build daemon end_tree spares, which runs on with its tree, unpinned; else its
        build daemons stopped when it is a gate (stop_daemons) and what else it left runs on,
        unpinned), its --on-release command, then its queue entry and record (settle). Returns
        the ended record (None for a nested slot). Once."""
        if self.released:
            return self.ended
        self.released = True
        code = self.code if code is None else code
        how = self.how if how is None else how
        try:
            if self.job is not None:
                if how == "interrupted" and self.job.p is not None:
                    try:                    # Ctrl+C reached the command too; give it 10 s
                        self.job.p.wait(10)
                    except BaseException:
                        pass
                if self.started:
                    self.usage.sample(self.job)
                    if self.run is not None:
                        self.run.rec.update(self.usage.fields())
                if self.started and (self.run is not None or self.gate) and how not in KILL_HOW:
                    self.stop_daemons()
                if how in KILL_HOW and not self.tree_ended:
                    try:
                        self.end_tree()
                    except BaseException:   # Ctrl+C again mid-kill: the whole job, as before
                        self.job.end(kill=True)
                        raise
                self.watch_stop()           # while the job can still say whose a window is
                # A spared daemon's tree is still in the job: closing it with kill-on-close set,
                # or TerminateJobObject, would end it. What runs on leaves the job where it can
                # (Linux: move_leftovers).
                kill = how in KILL_HOW and not self.spared
                if not kill:
                    move_leftovers(self.store, self.id, self.job)
                self.job.end(kill=kill)
                if self.started:
                    on_release(self.store, self.on_release_cmd, self.env, how, self.run)
        finally:
            self.watch_stop()
            if self.run is not None:
                self.ended = settle(self.store, self.run, code, how, self.mutex)
        return self.ended

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        if et is KeyboardInterrupt:
            self.release(130, "interrupted")
        elif et is not None:
            if self.run is not None and not self.released:
                self.run.rec.update(exception_fields(et, ev))
            self.release(1, "error")
        else:
            self.release()


def exception_fields(et, ev):
    """What a run's ended record says of the exception that ended its block `error` (how stays
    error): `exception`, its type (a builtin's bare name, else module.qualname), and
    `exception_text`, str(ev) cut to 200 characters and escaped to ASCII."""
    mod = getattr(et, "__module__", None)
    name = getattr(et, "__qualname__", None) or getattr(et, "__name__", "?")
    try:
        text = str(ev)[:200]
    except Exception:                       # noqa: BLE001 - a __str__ that raises
        text = "?"
    return {"exception": name if mod in (None, "builtins") else "%s.%s" % (mod, name),
            "exception_text": text.encode("ascii", "backslashreplace").decode("ascii")}


ESCAPE_CAVEAT = ("found by the parent pid chain, which a reused pid or an exited parent can make "
                 "wrong or incomplete")


def escaped(pid, contains, snap=None):
    """[(pid, ctime, exe name)] of the live descendants of the live process pid (a run's command),
    by the parent pid chain of one Toolhelp snapshot (process.descendants), that are outside the
    run's job (contains(pid) == "out"): an App Execution Alias, for one, starts its program
    outside the job of whoever starts it. Measured 2026-10-29, cmd.exe in a job running the
    python3 alias: the alias's stub (python3.exe) and the interpreter it starts are both outside
    the job, the stub's parent pid is cmd.exe and the interpreter's is the stub, so the walk finds
    both while cmd.exe and the stub live. Inexact (ESCAPE_CAVEAT): a process whose parent has
    exited is not found, and a reused pid can be. For a report only: nothing outside the job is
    ever ended. [] off Windows and when the processes cannot be read; never raises an Exception."""
    if kitlib.platform.NAME != "windows":
        return []
    try:
        snap = process.snapshot() if snap is None else snap
        names = {p: n for p, _pp, n in snap}
        return [(c, ct, names.get(c) or "?")
                for c, ct in process.descendants(pid, process.proc_ctime(pid), snap)
                if contains(c) == "out"]
    except Exception:                       # noqa: BLE001 - a report only: the stop goes on
        return []


def escape_lines(rid, found):
    """The warnings for escaped's processes (found) still alive once run rid's tree is ended;
    [] when none is. Never raises an Exception."""
    try:
        left = [(p, exe) for p, ct, exe in found if process.alive(p, ct)]
    except Exception:                       # noqa: BLE001 - a report only
        return []
    return ["WARNING: run %s: pid %d (%s) descends from the run's command but is outside the run's "
            "job: stop did not end it and it runs on, unpinned; the lock never ends a process "
            "outside the job (%s)"
            % (rid, p, exe, ESCAPE_CAVEAT) for p, exe in left]
