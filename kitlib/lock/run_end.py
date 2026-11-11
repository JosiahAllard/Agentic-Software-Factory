"""How a run under the lock ends (kitlib.lock.run, whose docstring gives the whole run): settle
frees its queue entry and records it, on_release runs the caller's --on-release command,
end_sparing ends the job's tree but for a build daemon another job may use (tell_daemons says
what was spared), move_leftovers moves what runs on out of a job closed without a kill, and
request_stop asks a run to stop.

Nothing here prints: progress lines go to store.say.
"""
import contextlib
import ctypes
import os
import signal
import subprocess
import time

import kitlib.platform
from kitlib.lock import jobset
from kitlib.lock import queue as qm
from kitlib.lock import notice
from kitlib.lock.store import create_json, iso, write_json
from kitlib.platform.linux import process as procfs
from kitlib.proc import daemons, process
from kitlib.platform.windows import win32
from kitlib.proc.job import Job


END_ENV = "MACHINE_LOCK_END"            # how the run ended, for --on-release
# end_sparing, when it spares a shared daemon: how many passes it ends the job's other
# processes in, how long it waits after each for them to leave the job, and how long after the
# last for one still listed to exit before it says it runs on.
END_PASSES, END_PASS_S, END_WAIT_S = 10, 0.1, 1.0
# end_sparing's result for a daemon when daemons.stop raised (it may have ended some first).
NOT_JUDGED = "not judged "


def child_code(code):
    return code - (1 << 32) if code > 0x7FFFFFFF else code     # NTSTATUS exit codes


def filetime_now():
    """The system time now as a FILETIME count (100 ns units since 1601), as
    GetSystemTimeAsFileTime reads it. _end_others compares process creation times
    (kitlib.proc.process times) with it. That a process created after this call never has an
    earlier creation time rests on Windows stamping creation times from this same system clock:
    inferred from the documented clocks, not measured, and void if the clock is set back.
    On Linux: the seam's stamp in the Linux ctime's encoding, the boot and the clock ticks since
    it (kitlib.platform.linux.process stamp_now)."""
    if kitlib.platform.NAME == "linux":
        return procfs.stamp_now()
    ft = win32.FILETIME()
    win32.K32.GetSystemTimeAsFileTime(ctypes.byref(ft))
    return ft.value()


def settle(store, run, code, how, mutex=None):
    """The run's queue entry goes (what it held is free), its record moves to ended and its
    history line is written, then its run mutex is released. Ctrl+C is ignored meanwhile
    (on the main thread), so a second Ctrl+C cannot leave its entry or record behind. With the
    queue unreadable the record still moves: the next look drops the entry once the mutex is
    gone. Returns the ended record."""
    try:
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:                      # not the main thread
        old = None
    try:
        store.pause("SETTLE")
        ended = None
        try:
            with store.lock("queue", 60):
                q = qm.load(store)
                n = len(q["entries"])
                q["entries"] = [e for e in q["entries"] if e["id"] != run.id]
                if len(q["entries"]) != n:
                    qm.save(store, q)
                ended = run.finish(code, how)
        except OSError:
            pass
        if ended is None:
            ended = run.finish(code, how)
        store.history_append(ended)
        notice.ended(store, ended)
        if mutex is not None:
            mutex.release()
        return ended
    finally:
        if old is not None:
            signal.signal(signal.SIGINT, old)


def on_release(store, cmdline, env, how, run=None):
    """The caller's --on-release command: after the run's command ended (any way but the wrapper
    dying) and before its slots are freed; for work the job cannot end (a process `docker exec`
    started in a container). It runs in a job of its own, ended as a tree past
    settings.on_release_s or on a second Ctrl+C. Its exit is reported, never the run's. The
    record says `releasing` meanwhile, so stop waits for it on its own budget."""
    if not cmdline:
        return
    e = dict(env)
    e[END_ENV] = how
    limit = store.settings.on_release_s
    if run is not None:
        with contextlib.suppress(OSError):
            run.save(releasing=iso(), release_s=limit)
    job = None
    try:
        job = Job(None)
        p = job.start(cmdline, shell=True, **(run.popen_kw(e) if run else {"env": e}))
        try:
            rc = p.wait(limit)
        except subprocess.TimeoutExpired:
            job.end(kill=True)
            store.say("WARNING: the --on-release command ran past %d s; its process tree was ended"
                      % limit)
            return
        if rc:
            store.say("WARNING: the --on-release command exited %d" % child_code(rc))
    except OSError as err:
        store.say("WARNING: the --on-release command could not start: %s" % err)
    except BaseException:                   # a second Ctrl+C
        if job is not None:
            job.end(kill=True)
        store.say("Ctrl+C: the --on-release command's process tree was ended")
    finally:
        if job is not None:
            job.end(kill=False)


# ------------------------------------------------------------------ ending a run's tree
def tell_daemons(store, rid, rec, done):
    """Says each of daemons.stop's results for run rid and lists them in rec's `daemons` (rec
    None, a nested slot's: not listed): a pid already listed (by an earlier judgement of this
    run, as a second end_tree after a Ctrl+C makes) takes its new result; the others listed
    stay."""
    for pid, label, res in done:
        if res.startswith("left: "):
            store.say("run %s: left the %s (pid %d) running: %s"
                      % (rid, label, pid, res[len("left: "):]))
        elif res.startswith(NOT_JUDGED):
            store.say("WARNING: run %s: the %s (pid %d) was %s; its tree is spared if it "
                      "still runs" % (rid, label, pid, res))
        else:
            store.say("run %s: stopped the %s (pid %d) it left: %s" % (rid, label, pid, res))
    if rec is not None:
        new = [{"pid": pid, "what": label, "result": res} for pid, label, res in done]
        judged = {d["pid"] for d in new}
        rec["daemons"] = [d for d in rec.get("daemons") or ()
                          if d.get("pid") not in judged] + new


def move_leftovers(store, rid, job):
    """Before run rid's job is closed without a kill: unpins it (job.unpin), then moves what still
    runs in it out of it (Job.leftovers, Linux: into a scope of its own, or, on W2, out of the
    job's kill), so it runs on, unpinned, and the run's own scope ends. The unpin comes first:
    on S2 and W2 the pin is each process's affinity, and the job's own unpin (at its close)
    reaches only the processes still in it, so a moved one, and what it starts later, would keep
    cores the lock has given to other runs. An unpin that fails is said and the move goes on. A
    job with no leftovers (a Windows job object, which cannot let a process out) is neither
    unpinned nor moved here. A move that fails is said and never fails the release: what is left
    runs on in the job, unpinned, as on Windows. Returns the pids moved."""
    move = getattr(job, "leftovers", None)
    if move is None or not jobset.is_open(job):
        return []
    try:
        job.unpin()
    except Exception as e:                  # noqa: BLE001 - the move and the release go on
        with contextlib.suppress(Exception):
            store.say("WARNING: run %s: its job could not be unpinned before what it left was "
                      "moved out: %s" % (rid, e))
    try:
        moved = list(move() or [])
    except Exception as e:                  # noqa: BLE001 - the release goes on
        with contextlib.suppress(Exception):
            store.say("WARNING: run %s: what it left could not be moved out of its job: %s; it "
                      "runs on there, unpinned" % (rid, e))
        return []
    if moved:
        with contextlib.suppress(Exception):
            store.say("run %s: left %s running, moved out of its job"
                      % (rid, ", ".join("pid %d" % p for p in moved)))
    return moved


def end_sparing(store, rid, job, rec):
    """Ends every process in run rid's job but a build daemon another job's client uses or may
    use, with that daemon's tree: every daemon kitlib.proc.daemons stop leaves, so any MSBuild
    node and the compiler server (PIPE_LABELS: their clients cannot be told), with what they
    started. True when it spared one: the caller then closes the job without killing it
    (job.end(kill=False)). The kill path of a foreground run (Slot.end_tree) and of a background
    one (kitlib.lock.background end_child). A daemon is shared: one this run's client started
    sits in this job, yet any client on the machine may be building on it, and
    TerminateJobObject would end it under that client. Windows cannot take a process out of a
    job, so the daemons are judged first (kitlib.proc.daemons stop, as Slot.stop_daemons does,
    whatever machine_lock.daemons says: one serving only this job is ended, as the kill always
    did) and the rest are ended one by one. The judgement is said and listed in rec's `daemons`
    (tell_daemons).

    No job, or a closed one: nothing is done. Nothing found, or nothing left:
    TerminateJobObject, as before. The job's processes or the daemons unreadable:
    TerminateJobObject (fail closed: the run's tree surely ends). When daemons.stop raises,
    every daemon found is "not judged" (stop may have ended some) and each one still running at
    its creation time is spared. Otherwise, in up to END_PASSES passes (_end_others), every job
    process outside the spared trees (each left daemon, at its creation time or, when that
    cannot be read, while not surely gone, with its descendants, read again each pass so a
    worker it starts is spared too) is ended by terminate_verified, oldest first so a parent
    cannot start more. One still running in the job after the last pass (and END_WAIT_S more)
    is said, and runs on once the job is closed unpinned. A store.say that fails is dropped:
    what is ended and spared is unchanged. When it fails in tell_daemons, the results after it
    are not said and rec is not given this judgement's results, so a pid judged again keeps its
    earlier one there.

    Each step catches Exception, so what can go up is a BaseException that is not an Exception,
    such as the KeyboardInterrupt of a second Ctrl+C. It goes up wherever it lands, the job then
    partly ended; the caller ends the whole job, or judges it again."""
    if not jobset.is_open(job):
        return False
    try:
        found = daemons.find(job.pids())
    except Exception as e:                  # noqa: BLE001 - cannot tell: the kill goes ahead
        try:
            store.say("WARNING: run %s: build daemons not checked before its tree was "
                      "ended: %s" % (rid, e))
        except Exception:                   # noqa: BLE001 - not said: the kill goes ahead
            pass
        found = []
    if not found:
        job.terminate(1)
        return False
    try:
        done = daemons.stop(found, job.contains)
    except Exception as e:                  # noqa: BLE001 - cannot judge: spare what still runs
        # stop may have ended some before it raised: none is said to be left running.
        done = [(pid, label, "%s(%s: %s)" % (NOT_JUDGED, type(e).__name__, e))
                for pid, _ct, label in found]
    try:
        tell_daemons(store, rid, rec, done)
    except Exception:                       # noqa: BLE001 - not said: the kill goes on as judged
        pass
    left = {pid for pid, _label, res in done if res.startswith(("left: ", NOT_JUDGED))}
    kept = [(pid, ct) for pid, ct, _label in found if pid in left]
    if not kept:
        job.terminate(1)
        return False
    try:
        rest = _end_others(job, kept)
    except Exception as e:                  # noqa: BLE001 - the job cannot be read: end it all
        job.terminate(1)
        try:
            store.say("WARNING: run %s: its job could not be read while its tree was "
                      "ended (%s); the whole job was ended, the daemons it had left "
                      "among them" % (rid, e))
        except Exception:                   # noqa: BLE001 - not said: the job is ended
            pass
        return False
    for pid in rest:
        try:
            store.say("WARNING: run %s: pid %d in its job could not be ended; it runs "
                      "on, unpinned" % (rid, pid))
        except Exception:                   # noqa: BLE001 - not said: the spared stay spared
            pass
    return True


def _end_others(job, kept):
    """end_sparing's passes: the pids still in job outside the trees of `kept` [(pid, ctime)]
    after the last one. Each pass takes a stamp (filetime_now), reads the job's pids, then the
    process table, and ends only targets created before the stamp, each by terminate_verified
    at the creation time it read. Raises OSError when the job or the process table cannot be
    read."""
    own = os.getpid()
    for n in range(END_PASSES + 1):
        # The job first, then the table: a listed process that still runs was running when
        # the table was read, so a worker a spared daemon started before the list shows in
        # the daemon's tree; one started after the list waits for the next pass.
        # The stamp before the list: a process at a listed pid created before it was alive
        # at the list, so it is the one listed (two live processes never share a pid). One
        # created at or after it may hold a reused pid: it is not ended in this pass (a new
        # job process is seen again by the next).
        stamp = filetime_now()
        pids = job.pids()
        snap = process.snapshot()
        spared = {own}
        for pid, ct in kept:
            # Its creation time unreadable (it cannot be opened) yet not surely gone: spared,
            # erring toward the client building on it.
            now = process.proc_ctime(pid)
            if now == ct or (now is None and process.alive(pid, ct)):
                spared.add(pid)
                spared.update(p for p, _c in process.descendants(pid, ct, snap))
        targets = [p for p in pids if p not in spared]
        if not targets:
            return []
        if n == END_PASSES:
            return _running(job, targets)
        aged = []
        for pid in targets:
            ct = process.proc_ctime(pid)
            if ct is not None and ct < stamp:
                aged.append((ct, pid))
        for ct, pid in sorted(aged):        # oldest first: a parent before its children
            process.terminate_verified(pid, ct)
        time.sleep(END_PASS_S)
    return []


def _running(job, targets):
    """The last pass's targets that still run in job, after waiting up to END_WAIT_S for them
    to exit: a pid the job listed may name a process that has exited since (one the last pass
    ended among them), and that one is not said to run on. A pid that cannot be opened counts
    as running (process.alive), as one Windows cannot place in or out of the job does."""
    end = time.monotonic() + END_WAIT_S
    while True:
        targets = [p for p in targets
                   if process.alive(p, None) and job.contains(p) != "out"]
        if not targets or time.monotonic() >= end:
            return targets
        time.sleep(END_PASS_S)


def request_stop(store, rid, by, session=None, why=None, exclusive=False):
    """Writes run rid's stop request (active/<id>.stop: {by_pid, at, by, session}, and why when
    given; session is the asker's session id or None, why the stale-run reap's reason,
    kitlib.lock.stale). exclusive (the stale-run reap's) writes it only when no request is there
    (create_json), so one made at the same moment is never written over. False when no such run
    is live, or with exclusive when a request was already there."""
    if not os.path.exists(store.path("active", rid + ".json")):
        return False
    doc = {"by_pid": os.getpid(), "at": iso(), "by": by, "session": session}
    if why:
        doc["why"] = why
    if exclusive:
        if not create_json(store.path("active", rid + ".stop"), doc):
            return False
    else:
        write_json(store.path("active", rid + ".stop"), doc)
    store.bump()
    return True
