"""A run under the lock.

book() joins the queue, waits for the run's turn and returns a Slot that holds its cpu and gpu
slots. The Slot's launcher starts the run's commands in the run's job object (kitlib.proc.job):
pinned to its cores, measured and, when the run is cancelled, interrupted or errs, ended as a
tree but for a build daemon that serves or may serve another job (Slot.end_tree). release()
frees what it held and records the run. slot() is book() as a context manager that yields the
launcher. run_command() is `machine_lock.py run`: book, one command, release.

Nothing here prints: progress lines go to store.say, which is the CLI's stderr and
nowhere by default. A refusal raises LockError with the CLI's exit code.

Nesting: a run's commands get MACHINE_LOCK_RUN=<run id>:<wrapper pid>:<wrapper ctime>.
A run started under it (nest_check: the named wrapper is an ancestor, alive, and holds) inherits
each pool its parent holds enough of and takes only the rest; one that needs more of a pool than
its parent has, but some, is refused (exit 2): it would wait for the rest while its parent waits
for it. A run that inherits everything takes no queue place; its commands run in a job nested
in its parent's (still measured and ended as a tree).

On Linux a job starts one command (kitlib.platform.linux.contain), so a Slot's job is a
kitlib.lock.jobset JobSet there: a job of its own per command, read, pinned and ended as one.

The parts: the wait for the run's turn in the queue (acquire) is kitlib.lock.run_wait; the held
run (the Slot and its Launcher, what its commands get and how its use is measured)
kitlib.lock.run_held; how a run ends (settle, end_sparing, move_leftovers, request_stop)
kitlib.lock.run_end; a command's program and command line on Windows (script_command,
alias_check) kitlib.lock.run_cmdline. This module books: the run's id and record, nesting,
book, slot and run_command.
"""
import contextlib
import datetime
import os
import re
import subprocess

import kitlib.platform
from kitlib.lock import giveback as gbk
from kitlib.lock import holds as hl
from kitlib.lock import kinds as kinds_mod
from kitlib.lock import layout as lay
from kitlib.lock import queue as qm
from kitlib.lock import notice
from kitlib.lock.store import (EXIT_HOLD, EXIT_REQUIRE, EXIT_USAGE, LockError, Store, booking_hosts,
                               caller, iso, new_id, parse_iso, read_record, remove, session_of,
                               tree_role, utc_now, write_json)
from kitlib.platform.linux import process as procfs
from kitlib.proc import bash, process
from kitlib.platform.windows import cpu
from kitlib.lock.run_cmdline import bare_hint, script_command, ScriptArgError
from kitlib.lock.run_end import settle
from kitlib.lock.run_held import NEST_ENV, ORIGIN_ENV, Slot, Usage
from kitlib.lock.run_wait import acquire
from kitlib.lock.layout import POOLS


RUN_ID_RE = re.compile(r"r-\d{8}T\d{6}Z-[0-9a-f]{6}")   # store.new_id("r")
NEST_RE = re.compile(r"^(%s):(\d+):(\d+)$" % RUN_ID_RE.pattern)
CLAIM_TRIES = 16            # claim_id: how many ids it draws before it gives up
BG_ENV = "MACHINE_LOCK_BG"
# The `how` of a run that got its slots and started nothing; kitlib.gate.budget never counts it.
NOT_STARTED = "not started"


# ------------------------------------------------------------------ nesting
def _ancestors():
    try:
        return {(int(p), int(c)) for p, c in process.own_ancestors()}
    except Exception:
        return set()


def _own_pidns():
    """This process's pid namespace (Linux), else None."""
    return procfs.pidns() if kitlib.platform.NAME == "linux" else None


def nest_check(store, value):
    """(parent, None) when MACHINE_LOCK_RUN's value names the run this process is inside (the
    named wrapper is one of its ancestors, alive, and its record says held), else (None, why).
    On Linux a process in a job's own pid namespace (contain's W1) sees none of its ancestors past
    that job's pid 1, so the wrapper is not one of them; there it is in the run when its record
    lists this process's pid namespace in `jobns` (Slot.start records each job's before the
    command runs) and the run's lock is held (store.run_mutex_held).
    parent = {id, pid, ctime, kind, shares, resource (None: it names none), has: {pool: slots it
    holds or inherited}, cores, gpus}.
    A record that is there but cannot be read (its wrapper's os.replace is swapping it) is read
    again for settings.queue_read_s, then LockError (EXIT_REQUIRE): never "holds no lock", which
    would have this run take its own lock inside one that holds, and wait on that run's slots."""
    try:
        m = NEST_RE.match(value or "")
        if not m:
            return None, "malformed value"
        rid, pid, ct = m.group(1), int(m.group(2)), int(m.group(3))
        ancestor = (pid, ct) in _ancestors()
        ns = None if ancestor else _own_pidns()
        if not ancestor and ns is None:
            return None, "the named wrapper is not an ancestor of this run"
        path = store.path("active", rid + ".json")
        d, there = read_record(path, store.settings.queue_read_s)
        if there and d is None:
            raise LockError(EXIT_REQUIRE, "require-lock: %s names run %s, whose record %s could "
                                          "not be read for %g s; the command was not run"
                            % (NEST_ENV, rid, path, store.settings.queue_read_s))
        if not d or d.get("state") != "held":
            return None, "the named run holds no lock"
        if (d.get("pid"), d.get("ctime")) != (pid, ct):
            return None, "the record names another wrapper"
        if not ancestor:
            jobns = d.get("jobns")
            if not isinstance(jobns, list):
                return None, "the named wrapper is not an ancestor of this run"
            if ns not in jobns:
                return None, ("the named wrapper is not an ancestor of this run, nor is this run "
                              "in a job it started")
            if store.run_mutex_held(rid) is not True:
                return None, "the holder is dead"
        elif not process.alive(pid, ct):
            return None, "the holder is dead"
        has = d.get("has") if isinstance(d.get("has"), dict) else {}
        return {"id": rid, "pid": pid, "ctime": ct, "kind": d.get("kind"),
                "shares": dict(d.get("shares") or {}), "resource": d.get("resource"),
                "has": {p: n for p, n in has.items() if p in POOLS and type(n) is int and n > 0},
                "cores": list(d.get("cores") or []), "gpus": list(d.get("gpus") or [])}, None
    except LockError:
        raise
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def nest_need(parent, kind, need):
    """(own, has, None) for a run of `kind` needing `need` nested in `parent` (None: not
    nested): own, what it takes itself; has, what it holds or inherits, per pool. Else (None,
    None, why)."""
    own, has = dict(need), {p: need[p] for p in POOLS}
    for p in POOLS:
        ph = (parent or {}).get("has", {}).get(p, 0)
        if not need[p] or not ph:
            continue
        if ph < need[p]:
            return None, None, ("a %s run needs %s, but the run it is nested in (%s, run %s) holds "
                                "%s; start it outside that run" % (
                                    kind, lay.need_text({p: need[p]}), parent.get("kind"),
                                    parent["id"], lay.need_text({p: ph})))
        own[p], has[p] = 0, ph
    return own, has, None


def bg_ancestor(env, store=None):
    """True when a background run's wrapper (MACHINE_LOCK_BG) is an ancestor of this process.
    On Linux, given the store, also when the wrapper's record names a pid namespace other than
    this process's (a job's own, contain's W1): its ancestors cannot be seen from in there, so
    it is taken as one (fail closed: the caller refuses a gpu lock)."""
    m = NEST_RE.match(env.get(BG_ENV) or "")
    if not m:
        return False
    if (int(m.group(2)), int(m.group(3))) in _ancestors():
        return True
    ns = _own_pidns() if store is not None else None
    if ns is None:
        return False
    d, _there = read_record(store.path("active", m.group(1) + ".json"))
    theirs = d.get("pidns") if isinstance(d, dict) else None
    return theirs is not None and theirs != ns


def run_ancestors(detached, env):
    """[[pid, ctime]] of the processes this run was started under (stop's and the API's
    ownership check). A detached wrapper's parents are WMI's, so it adds its launcher's."""
    out = _ancestors()
    if detached:
        for item in (env.get(ORIGIN_ENV) or "").split(";"):
            m = re.fullmatch(r"(\d+):(\d+)", item)
            if m:
                out.add((int(m.group(1)), int(m.group(2))))
    return sorted([p, c] for p, c in out)


def origin_pairs(env):
    """[(pid, ctime)] a detached wrapper's launcher recorded in ORIGIN_ENV."""
    out = []
    for item in (env.get(ORIGIN_ENV) or "").split(";"):
        m = re.fullmatch(r"(\d+):(\d+)", item)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out


def run_role(detached, env):
    """owner or agent: who started this run, from the process tree, for admits (only the
    owner's runs use an owner whole hold). A detached wrapper's own parents are WMI's, so
    its launcher's ancestry decides (store.tree_role)."""
    if detached:
        return tree_role(origin_pairs(env))
    return caller()["role"]


def check_run_id(rid):
    """Raises LockError (usage, exit 2) unless rid is None or shaped as store.new_id("r") makes a
    run id: a given id names files under the store, so nothing else may reach a path."""
    if rid is not None and not (isinstance(rid, str) and RUN_ID_RE.fullmatch(rid)):
        raise LockError(EXIT_USAGE, "usage: %r is not a run id (r-<UTC second>-<6 hex>)" % (rid,))


def id_left(store, rid):
    """The first file of an earlier run with id rid still in the store (its record, stop file,
    ended record or background folder), or None."""
    for p in (store.path("active", rid + ".json"), store.path("active", rid + ".stop"),
              store.path("ended", rid + ".json"), store.path("background", rid + ".d")):
        if os.path.lexists(p):
            return p
    return None


def claim_id(store, rid=None, tries=CLAIM_TRIES):
    """(id, its run mutex, held): the id a new run takes, claimed before any of its records exist.
    An id is taken while another holds its run mutex or anything of an earlier run with it is
    left (id_left). The files are looked at once the mutex is held: no live run then has the id,
    and a run that had it wrote its ended record before it let the mutex go (settle). A drawn id
    that is taken is drawn again, up to `tries` times; a given one (rid, as --detached-id) is
    refused. Raises LockError: usage (exit 2) for a rid not shaped as a run id, exit 10 when no id
    is had. A refusal writes nothing."""
    check_run_id(rid)
    why = None
    for _ in range(1 if rid else tries):
        cand = rid or new_id("r")
        try:
            mutex = store.run_mutex(cand)
        except OSError as e:
            why = str(e)
            continue
        left = id_left(store, cand)
        if left is None:
            return cand, mutex
        mutex.release()
        why = "%s exists" % left
    if rid:
        raise LockError(EXIT_REQUIRE, "require-lock: run id %s is taken (%s); the command was "
                                      "not run" % (rid, why))
    raise LockError(EXIT_REQUIRE, "require-lock: no free run id in %d draws (the last: %s); the "
                                  "command was not run" % (tries, why))


class Run:
    """A run's record, active/<id>.json while it lives, ended/<id>.json after. Its id comes from
    claim_id (book, run_background); one made here is not checked against the store."""

    def __init__(self, store, L, repo, slug, kind, note, cmd, state, rid=None, detached=False,
                 env=None, log=None):
        env = os.environ if env is None else env
        self.store = store
        ancestors = run_ancestors(detached, env)
        self.rec = {"id": rid or new_id("r"), "state": state, "repo": repo, "slug": slug,
                    "kind": kind, "note": note, "pid": os.getpid(), "ctime": process.own_ctime(),
                    "since": iso(), "cmd": [str(c) for c in cmd or ()][:20],
                    "detached": bool(detached), "holding": [], "slots": {}, "cores": [],
                    "gpus": [], "shares": {}, "session": session_of(env),
                    "ancestors": ancestors, "hosts": booking_hosts(ancestors),
                    "role": run_role(detached, env),
                    "layout": lay.slot_key(L),
                    "log": getattr(log, "name", None) if log is not None else None}
        if kitlib.platform.NAME == "linux":
            self.rec["pidns"] = procfs.pidns()  # its pid is this namespace's (store.record_state)
        self.id = self.rec["id"]
        self.rel = "active/%s.json" % self.id
        self.log = log
        self.usage = Usage(L["cpu_slot_threads"], store.settings.usage_sample_s)
        self.ended = None

    def save(self, **kw):
        self.rec.update(kw)
        self.store.write(self.rel, self.rec)

    def stop_asked(self):
        return os.path.exists(self.store.path("active", self.id + ".stop"))

    def finish(self, code, how):
        """The record moves to the ended folder; returns it."""
        self.ended = dict(self.rec, state="ended", code=code, how=how, ended=iso())
        stop, _ = read_record(self.store.path("active", self.id + ".stop"))
        if stop:
            self.ended["stopped_by"] = {"by": stop.get("by"), "session": stop.get("session")}
            if stop.get("why"):
                self.ended["stopped_by"]["why"] = stop["why"]
        with contextlib.suppress(OSError):
            write_json(self.store.path("ended", self.id + ".json"), self.ended)
        remove(self.store.path("active", self.id + ".json"))
        remove(self.store.path("active", self.id + ".stop"))
        remove(gbk.request_path(self.store, self.id))
        self.store.bump()
        return self.ended

    def popen_kw(self, env):
        kw = {"env": env}
        if self.log is not None:
            kw.update(stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log)
        return kw


def _relayout(store, L):
    """The layout in force now, read under the queue mutex: a layout adopted between book()'s
    first look and its record being saved is seen here; after this, none can be (a live run
    blocks adoption, kitlib.lock.layout.in_force)."""
    with store.lock("queue", store.settings.queue_lock_s):
        cur = store.read("layout.json")
    return cur if lay.valid_layout(cur) and cur["key"] != L["key"] else L


def within_of(L, within, kind, need, own, parent, machine):
    """(the thread list as text, [cpu slot]) of book's within on layout L: the slots whose threads
    all lie in it. machine: the thread ids this machine has. A thread it names that the owner's
    division leaves out of every slot is no error; it is just not used. LockError 2 when it is not
    a thread list, names a thread the machine does not have, holds no whole slot, holds fewer
    slots than the run takes itself (own), or the run is nested and inherits its parent's cores
    while they are not all inside it."""
    try:
        if isinstance(within, str):
            ts = lay.parse_threads(within)
        elif (isinstance(within, (list, tuple, set, frozenset, range)) and within
              and all(type(t) is int and 0 <= t <= lay.MAX_THREAD for t in within)):
            ts = sorted(set(within))
        else:
            raise ValueError("a thread list is like 8-15,20 (or a list of thread ids), not %r"
                             % (within,))
    except ValueError as e:
        raise LockError(EXIT_USAGE, "usage: --within: %s. Exit 2." % e)
    text = cpu.mask_text(cpu.bits_mask(ts))
    stray = [t for t in ts if t not in machine]
    if stray:
        raise LockError(EXIT_USAGE, "usage: --within %s: thread%s %s %s not threads of this "
                                    "machine (it has threads %s). Exit 2." % (
                                        text, "" if len(stray) == 1 else "s",
                                        cpu.mask_text(cpu.bits_mask(stray)),
                                        "is" if len(stray) == 1 else "are",
                                        cpu.mask_text(cpu.bits_mask(machine))))
    slots = lay.within_slots(L, ts)
    if not slots:
        raise LockError(EXIT_USAGE, "usage: --within %s holds no whole cpu slot (a slot is %d "
                                    "threads, all of them inside). Exit 2."
                        % (text, L["cpu_slot_threads"]))
    if own["cpu"] > len(slots):
        raise LockError(EXIT_USAGE, "usage: --within %s holds %d whole cpu slot%s (%s), but a %s "
                                    "run takes %d; ask for fewer with --cores %d or less. Exit 2."
                        % (text, len(slots), "" if len(slots) == 1 else "s",
                           lay.slots_text("cpu", slots), kind, own["cpu"], len(slots)))
    if parent and need["cpu"] and not own["cpu"]:
        pc = [i for i in parent.get("cores") or [] if type(i) is int]
        if not pc or not set(pc) <= set(slots):
            raise LockError(EXIT_USAGE, "usage: nested run refused: it runs on the cores of the "
                                        "run it is nested in (run %s, %s), and they are not all "
                                        "inside --within %s; start it outside that run. Exit 2."
                            % (parent["id"], lay.slots_text("cpu", pc), text))
    return text, slots


def check_resource(resource):
    """book()'s resource rule: None, or a non-empty string; anything else is LockError usage
    (exit 2). The CLI calls it before a detach, so a refusal is never left in a detached log."""
    if resource is not None and not (isinstance(resource, str) and resource):
        raise LockError(EXIT_USAGE, "usage: a resource is a non-empty string, not %r" % (resource,))


def check_mem(mem_mb):
    """book()'s mem_mb rule: None, or a whole number of MB from 1; anything else is LockError
    usage (exit 2)."""
    if mem_mb is not None and (type(mem_mb) is not int or mem_mb <= 0):
        raise LockError(EXIT_USAGE, "usage: --mem must be a positive whole number of MB, not %r"
                        % (mem_mb,))


def mem_need(store, repo, kind, mode, mem_mb, entry, records=None):
    """(need_mem_mb, mem_from) of a run: mem_mb (--mem) when given ("flag"); else its history's
    need (Store.history_mem, from entry's mem_since) once that has mem_min_runs runs ("history");
    else its kind entry's mem_mb floor ("kind"); else settings.mem_default_mb ("default"), never
    0: a run whose need nobody knows is not admitted as needing nothing. records: the history
    already read (Store.history_records), else history_mem reads it."""
    if mem_mb is not None:
        return mem_mb, "flag"
    entry = entry or {}
    hist = store.history_mem(repo, kind, mode, entry.get("mem_since"), records=records)
    if hist is not None:
        return hist, "history"
    if entry.get("mem_mb") is not None:
        return entry["mem_mb"], "kind"
    return store.settings.mem_default_mb, "default"


def book(kind, repo, slug, cores=None, hold=None, gpu_share=None, wait_s=None, no_wait=False,
         expect_s=None, note=None, on_release_cmd=None, cmd=None, store=None, kinds_table=None,
         env=None, rid=None, detached=False, log=None, gate=False, resource=None, priority=None,
         within=None, tree=None, branch=None, checkout=None, mem_mb=None, gate_mode=None):
    """Waits for a run of kind `kind` for repo/slug to hold its slots and returns its Slot.
    Raises LockError (the CLI's exit code) when it does not. cores: fewer cpu slots than the
    kind's size, 1..that size, never more; the kind's size is its whole number of cores
    (at most agent_cap), or an alias's (heavy is every slot inside an owner whole hold); a table
    with a size past agent_cap fails closed (exit 10). hold: an owner whole
    hold or agent hold to run inside. gpu_share "training": one gpu slot on a shareable
    kind. wait_s: the longest wait (None: no limit); no_wait: now or never. expect_s: how long it
    expects to hold (default: its history's median, else its own hold's end, else warn_min).
    env: the environment its commands get (default os.environ); rid, detached and log are the
    CLI's (a detached run's id and log file). gate: a gate run (kitlib.gate.runner), recorded as
    `gate`; by default only a gate's leftover build daemons are stopped (Slot.stop_daemons).
    resource: an exclusive resource (a non-empty string, e.g. "landing:<git common dir>") the run
    holds for its whole life: it does not start while another run holding it is alive (the queue
    entry and record carry it). A nested run naming one its parent does not hold joins the queue
    for it even when the parent covers all it needs. priority "land": its entry plans ahead of
    every ordinary waiting run (kitlib.lock.queue.plan), for library callers only. The CLI takes
    resource (`machine_lock.py run --resource`), not priority; the MCP server takes neither.
    within: the threads the run must stay inside (the CLI's --within: a Linux cpuset list such as '8-15', or global thread ids), e.g. a container's cpuset; it takes
    only cpu slots whose threads all lie inside (within_of), and waits for those while others are
    free. The record carries `within` (the list) and `within_slots`; its queue entry `within`.
    tree: the git tree hash the run tests (a gate's: kitlib.gate.runner.gated), recorded as `tree`
    and kept in its history line, so a gate's run is matched to the exact tree it tested;
    Slot.note_tree records it on a run already held. branch: the git branch the run is for (a
    branch gate's, which kitlib.gate.budget counts its runs by), recorded as `branch` and kept in
    its history line. gate_mode: the gate's mode (kitlib.gate: select, full, docs, merged,
    rerun, daily or bisect), recorded as `gate_mode` and kept in its history line; Slot.note_tree
    records it on a run already held. checkout: the folder the run is booked from (the CLI's current directory):
    a kind that is neither a kit kind nor a kit alias is looked up in the "kinds" section of its
    repository's main checkout `.kit.json` (kitlib.lock.kinds.with_repo); None: kit kinds only.
    mem_mb: the memory the run needs, in MB (the CLI's --mem); the record keeps need_mem_mb and
    mem_from (mem_need). With its slots free, it waits while that need does not fit the commit
    headroom (acquire)."""
    store = store or Store()
    env = dict(os.environ if env is None else env)
    try:
        table = kinds_table or kinds_mod.load(store.kinds_path)
    except kinds_mod.KindsError as e:
        raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
    try:
        kind = kinds_mod.resolve(kind, table, store.kinds_path)
        table = kinds_mod.with_repo(table, kind, checkout, store.kinds_path)
    except kinds_mod.KindsError as e:
        raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
    if kind not in table:
        raise LockError(EXIT_USAGE, "usage: unknown kind %r (known: %s%s)" % (
            kind, ", ".join(sorted(table)), "" if checkout is None else
            "; a repository's own kinds are read from the \"kinds\" section of the .kit.json "
            "in the main checkout of %s" % checkout))
    k = table[kind]
    if not repo or not slug:
        raise LockError(EXIT_USAGE, "usage: a run names its repo and slug")
    if gpu_share not in (None, "training"):
        raise LockError(EXIT_USAGE, "usage: --gpu-share is training or nothing")
    if gpu_share and not k["share"]:
        raise LockError(EXIT_USAGE, "usage: --gpu-share training needs a kind whose gpu is shared")
    if expect_s is not None and not (isinstance(expect_s, (int, float)) and expect_s > 0):
        raise LockError(EXIT_USAGE, "usage: --expect must be a positive duration")
    check_mem(mem_mb)
    if priority is not None and priority not in qm.PRIORITIES:
        raise LockError(EXIT_USAGE, "usage: unknown priority %r (known: %s)"
                        % (priority, ", ".join(qm.PRIORITIES)))
    check_resource(resource)
    check_run_id(rid)
    L = lay.in_force(store)["layout"]
    try:                            # a size the layout cannot give fails closed
        kinds_mod.check_cap(table, L["agent_cap"])
    except kinds_mod.KindsError as e:
        raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
    whole = False
    if hold:
        try:
            h = hl.find(store, L, hold)
        except OSError as e:        # HoldsUnreadable among them: fail closed
            raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
        whole = bool(h and h["owner"] == "owner" and h["scope"] == "whole")

    def sized(L):
        try:                        # a table that skipped kinds.load fails closed
            top = lay.kind_cores(L, k, whole)
        except kinds_mod.KindsError as e:
            raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
        if cores is not None:
            if type(cores) is not int or not 1 <= cores <= top:
                raise LockError(EXIT_USAGE, "usage: --cores %s: a %s run takes 1 to %d cpu slots, "
                                            "its kind's size; a run asks for fewer, never more"
                                            % (cores, kind, top))
        return lay.need_for(L, k, gpu_share, cores, whole)

    need = sized(L)
    parent = None
    if env.get(NEST_ENV):
        parent, why = nest_check(store, env[NEST_ENV])
        if parent is None:
            store.say("%s ignored (%s); this run takes its own lock" % (NEST_ENV, why))
    own, has, why = nest_need(parent, kind, need)
    if own is None:
        raise LockError(EXIT_USAGE, "usage: nested run refused: %s. Exit 2." % why)
    wtext = wslots = None
    if within is not None:
        if hold:
            raise LockError(EXIT_USAGE, "usage: --within and --hold do not go together: a run "
                                        "inside a hold takes the hold's slots. Exit 2.")
        machine = set(lay.hardware(store)["threads"])
        wtext, wslots = within_of(L, within, kind, need, own, parent, machine)
    shares = hl.shares_for(k, gpu_share)
    if parent and not any(own.values()) and (resource is None or parent.get("resource") == resource):
        return Slot(store, None, L, own, parent, None, env, on_release_cmd, k, shares, log, gate)
    if own["gpu"] and bg_ancestor(env, store):
        raise LockError(EXIT_USAGE, "usage: a gpu lock inside a background run would pause that "
                                    "run; refused")
    # The id first, its run mutex held: an id another run has, live or ended, is never written
    # over, and a refusal here writes nothing (claim_id).
    rid, mutex = claim_id(store, rid)
    try:
        run = Run(store, L, repo, slug, kind, note, cmd, "waiting", rid, detached, env, log)
        mode = store.run_mode(priority, branch, gate)
        # History is read once, and only when a guess needs it: it is every line of the last
        # history_keep_days, and this mutex is held meanwhile.
        recs = store.history_records() if expect_s is None or mem_mb is None else None
        hist = None if expect_s is not None else store.history_expect(
            repo, kind, mode, records=recs)
        need_mem, mem_from = mem_need(store, repo, kind, mode, mem_mb, k, records=recs)
        run.rec.update(expect_s=expect_s if expect_s is not None else hist if hist is not None
                       else k["warn_min"] * 60,
                       expect_from="--expect" if expect_s is not None else "history"
                       if hist is not None else "guide",
                       warn_s=k["warn_min"] * 60 * store.settings.warn_scale,
                       need_mem_mb=need_mem, mem_from=mem_from,
                       need=own, has={p: n for p, n in has.items() if n}, hold=hold,
                       parent=parent["id"] if parent else None, gpu_share=gpu_share,
                       **({"gate": True} if gate else {}),
                       **({"tree": tree} if tree else {}),
                       **({"branch": branch} if branch else {}),
                       **({"gate_mode": gate_mode} if gate_mode else {}),
                       **({"resource": resource} if resource else {}),
                       **({"priority": priority} if priority else {}),
                       **({"within": wtext, "within_slots": {"cpu": wslots}} if wtext else {}))
    except BaseException:
        mutex.release()
        raise
    done, code, how = False, EXIT_REQUIRE, "not acquired"
    try:
        prefer = None
        if hold:
            # The claim first, so the run plans around every hold but its own.
            got_h, why = hl.claim(store, L, run.rec, hold, shares)
            if got_h is None:
                raise LockError(EXIT_HOLD, "NOT ACQUIRED: %s; exit 12" % why)
            run.rec.update(hold_owner=got_h["owner"], hold_scope=got_h["scope"])
            if expect_s is None:
                run.rec.update(expect_s=max(60, int((got_h["_end"] - utc_now()).total_seconds())),
                               expect_from="hold")
            if got_h["owner"] == "agent":
                try:
                    lost, to = hl.lost(got_h, [x for x in hl.records(store, L) if hl.strong(x)])
                except OSError as e:
                    raise LockError(EXIT_REQUIRE, "require-lock: %s; the command was not run" % e)
                prefer = {p: [i for i in got_h["slots"].get(p, []) if i not in lost.get(p, [])]
                          for p in POOLS}
                store.say("run %s claims %s%s" % (run.id, hl.text(got_h), (
                    "; it lost %s to %s, which always win; it plans around them as any run "
                    "does" % (hl.slots_text(lost), ", ".join(to))) if lost else ""))
            else:
                store.say("run %s runs inside %s" % (run.id, hl.text(got_h)))
        try:
            run.save(inherited=[p for p in POOLS if need[p] and not own[p]], shares=shares)
            L2 = _relayout(store, L)
        except OSError as e:
            raise LockError(EXIT_REQUIRE, "require-lock: the lock store %s could not be written "
                                          "(%s); the command was not run" % (store.root, e))
        if L2 is not L:
            L, need = L2, sized(L2)
            own, has, why = nest_need(parent, kind, need)
            if own is None:
                raise LockError(EXIT_USAGE, "usage: nested run refused: %s. Exit 2." % why)
            if wtext:
                wtext, wslots = within_of(L, within, kind, need, own, parent, machine)
                run.save(within=wtext, within_slots={"cpu": wslots})
            run.usage = Usage(L["cpu_slot_threads"], store.settings.usage_sample_s)
            run.save(need=own, has={p: n for p, n in has.items() if n}, layout=lay.slot_key(L))
        acquire(store, run, L, own, wait_s, no_wait, prefer, {"cpu": wslots} if wtext else None)
        if run.rec.get("hold_owner") == "agent":
            hl.claimed(store, run.id, hold)
        how = NOT_STARTED
        slot = Slot(store, run, L, own, parent, mutex, env, on_release_cmd, k, shares, log, gate)
        if parent is None:
            notice.started(store, run, kind, k, L)
        done = True
        return slot
    except LockError as e:
        code = e.code
        raise
    except KeyboardInterrupt:
        code, how = 130, "interrupted"
        raise
    except BaseException:
        code = 1
        raise
    finally:
        if not done:
            settle(store, run, code, how, mutex)


@contextlib.contextmanager
def slot(kind, repo, slug, **kw):
    """with slot("test", repo, slug) as launch: launch.run([...]) -- book() as a context
    manager yielding the Launcher; released on leaving the block."""
    s = book(kind, repo, slug, **kw)
    with s:
        yield s.launcher


def run_command(cmd, kind, repo, slug, **kw):
    """`machine_lock.py run`: book, run cmd in the run's job, release, then say the run's window
    report (Slot.window_lines). Returns the command's exit code (130 on Ctrl+C, 1 when it could
    not start or stop cancelled it). Raises LockError when the run is refused or gives up
    waiting. A bare bash with no Git Bash to run it (kitlib.proc.bash), an argument a .bat or
    .cmd cannot take, or a WindowsApps App Execution Alias (script_command) is refused before the
    run books: 1, no record. The command
    starts in this process's folder; a bare name there that does not start is said with the
    .\\<name> form that runs (bare_hint). A .bat or .cmd, named or found for a name with no
    extension, starts through cmd.exe (script_command); the record keeps cmd as given.

    route: None, or route(cmd, kind, repo, slug, kw) asked after those checks and before the
    run books. None from it runs the command here; a number is the run's exit code, and nothing
    is booked here (kitlib.loadnode.dispatch runs it on a load node, docs/load-nodes.md)."""
    route = kw.pop("route", None)
    store = kw.get("store") or Store()
    kw["store"] = store
    try:
        bash.resolve(cmd, False, kw.get("env"))
        start = script_command(cmd, kw.get("env"))
    except (bash.BareBashError, ScriptArgError) as e:
        store.say("could not start the command: %s" % e)
        return 1
    if route is not None:
        code = route(cmd, kind, repo, slug, kw)
        if code is not None:
            return code
    s = book(kind, repo, slug, cmd=cmd, **kw)
    code, how = 1, NOT_STARTED
    try:
        try:
            p = s.start(start, give_back=True)
        except OSError as e:
            store.say("could not start the command: %s%s" % (e, bare_hint(cmd, e)))
            return 1
        store.say(s.held_text())
        code, how = s.wait(p)
        return code
    except KeyboardInterrupt:
        code, how = 130, "interrupted"
        store.say("Ctrl+C: run %s ends" % s.id)
        return 130
    finally:
        s.release(code, how)
        for line in s.window_lines():
            store.say(line)


def expected_end(since, expect_s, max_s=10 * 365 * 86400):
    """since + expect_s as an ISO time, or None when either is missing or unreadable."""
    try:
        return iso(parse_iso(since) + datetime.timedelta(seconds=min(float(expect_s), max_s)))
    except (TypeError, ValueError, OverflowError):
        return None
