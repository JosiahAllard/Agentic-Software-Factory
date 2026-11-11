"""The bridge's Blenders (docs/blender-mcp.md "Lifetime"): each one booked through the machine lock,
started on a port of its own, called through the add-on, watched, and closed.

Open: a free port of PORTS (no live lock entry holds its resource, nothing listens on it and an
exclusive bind of it works), then a lock booking of kind KIND with gpu_share GPU_SHARE (one gpu
slot) that holds resource blender-port:<port> for its whole life, then Blender started in that run's
job (kitlib.lock.run_held.Slot.start), then a wait until the port listens and every listener on it
is a process of that job. A booking refused because another bridge took the port first tries the
next port. Anything that fails after the booking releases it, killing what it started.

Calls: one at a time per Blender (the add-on serves one), each with a deadline (wire.call). Before
each call the port's listeners are read again: the add-on binds with SO_REUSEADDR, so on Windows
another process could listen on the same port; a listener that is not this Blender's ends it, so
no call can reach the other. A call that passes its deadline ends the Blender: the add-on has no
way to stop running code, and a Blender stuck in a call would hold its slot to no purpose.

Ends: blender_close and the idle limit stop the add-on's server (Blender exits 0), then release
the booking; a Blender that does not exit within EXIT_S is ended with its job. The watcher thread
(start_watcher) also ends a Blender whose run the lock was asked to stop (the owner, or the lock's
own stale check) and releases one that exited by itself. shutdown (the MCP client went away)
closes every one. If this process dies instead, the run's job ends Blender (a Windows job object
is kill-on-close; on Linux the job's watchdog does it, docs/blender-mcp.md "Known limits") and the
lock drops the booking of a dead wrapper.

A close and the idle close never stop a Blender in a call: blender_close is refused (busy), the
idle close looks at busy under the instance's lock and lets it be, and a close that has begun
refuses every new call. shutdown ends one still in a call with its job.

Linux: the listener table (/proc/net, kitlib.proc.tcp.listeners) names no owner, so a listener on
the port counts as Blender's. Linux refuses a second listener on a port unless both
set SO_REUSEPORT, which the add-on does not.
"""
import contextlib
import os
import re
import socket
import shutil
import subprocess
import tempfile
import threading
import time

from kitlib.lock import queue as queue_mod
from kitlib.lock import run as run_mod
from kitlib.lock.store import EXIT_BUSY, LockError, Store, iso

from . import GPU_SHARE, IDLE_S, KIND, PORTS, RESOURCE_PREFIX, addon, codes, wire

READY_S = 120.0         # Blender's start, to its port listening
STOP_S = 10.0           # the stop call
EXIT_S = 15.0           # from the stop call to Blender's exit
POLL_S = 0.2
WATCH_S = 5.0
PORT_TRIES = 3
WAIT_MAX_S = 600.0
GONE_KEPT = 20          # ended Blenders blender_list still shows
LOG_TAIL = 2000


class BridgeError(Exception):
    """A tool's refusal or failure: code (a short word) and the line that explains it."""

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code, self.message = code, message


def bindable(port):
    """True when an exclusive bind of 127.0.0.1:port works now (nothing else is bound there)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def listening(port):
    """The pids that listen on TCP `port` now (any address; kitlib.proc.tcp.listeners). On Linux
    the table names no owner, so it is {None} when anything listens there, else empty."""
    from kitlib.proc import tcp
    return {pid for end, pid in tcp.listeners() if end[2] == port}


def ports_booked(store):
    """The port resources (blender-port:<n>) of the lock queue's live entries, held or waiting:
    a port a run holds, or waits to hold, is not free. Raises OSError (QueueUnreadable)."""
    return {r for r in (queue_mod.entry_resource(e) for e in queue_mod.load(store)['entries']
                        if not store.run_dead(e))
            if r and r.startswith(RESOURCE_PREFIX)}


def _slug(worktree):
    base = re.sub(r'[^A-Za-z0-9._-]+', '-', os.path.basename(worktree)).strip('-.')
    return 'blender-' + (base[:50] or 'worktree')


def _repo(worktree):
    try:
        from kitlib.git import defaults
        return defaults.repo_name(worktree) or os.path.basename(worktree)
    except Exception:           # not a git checkout, or git cannot say: the folder's name
        return os.path.basename(worktree)


def _tail(path):
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - LOG_TAIL))
            return f.read().decode('utf-8', 'replace').strip()
    except OSError:
        return ''


class Instance:
    """One open Blender. mu orders a call against an end and is held only briefly, never across a
    call or a wait for Blender's exit; busy is set while a call runs, closing once a close has
    begun (no call starts after it); done is set once it has ended."""

    def __init__(self, handle, port, worktree, exe, slot, proc, log_path, now):
        self.handle, self.port, self.worktree, self.exe = handle, port, worktree, exe
        self.slot, self.proc, self.log_path = slot, proc, log_path
        self.mu = threading.RLock()
        self.busy, self.closing, self.ended, self.pycache = False, False, None, None
        self.done = threading.Event()
        self.opened, self.last, self.calls, self.renders = now, now, 0, 0

    def info(self, now):
        out = {'handle': self.handle, 'port': self.port, 'pid': self.proc.pid, 'run': self.slot.id,
               'worktree': self.worktree, 'blender': self.exe, 'gpu_slots': list(self.slot.gpus),
               'cpu_slots': list(self.slot.cores), 'calls': self.calls, 'renders': self.renders,
               'idle_s': round(max(0.0, now - self.last), 1), 'log': self.log_path}
        if self.ended:
            out.update(self.ended)
        return out


class Bridge:
    """The Blenders of one MCP server process. Every collaborator is a parameter so the tests can
    run it on a scratch lock store with a stand-in Blender:

    store: the lock store (None: the machine's, made at the first open); env: the environment
    Blender gets (None: os.environ); ready(env) -> the blender to start (addon.ready: found, new
    enough, add-on pinned); argv(exe, port) -> its command; listening(port) and bindable(port) as
    above; clock: time for the idle limit; kinds_table: the lock kinds (None: the store's)."""

    def __init__(self, store=None, env=None, ready=None, argv=addon.launch_argv, listening=listening,
                 bindable=bindable, clock=time.monotonic, idle_s=IDLE_S, ready_s=READY_S,
                 ports=PORTS, kinds_table=None, out_dir=None, book=run_mod.book):
        self.store, self.env, self.argv = store, env, argv
        self.ready = ready or (lambda env: addon.ready(env))
        self.listening, self.bindable, self.clock = listening, bindable, clock
        self.idle_s, self.ready_s, self.ports = idle_s, ready_s, tuple(ports)
        self.kinds_table, self.book = kinds_table, book
        self.out_dir = out_dir or os.path.join(tempfile.gettempdir(), 'pm-kit-blender')
        self.mu = threading.Lock()
        self.instances, self.gone = {}, []
        self.stopping, self.watcher = threading.Event(), None

    # ------------------------------------------------------------------ open
    def _store(self):
        if self.store is None:
            self.store = Store()
        return self.store

    def open(self, worktree, wait_s=0):
        """Opens a Blender for `worktree` (an absolute folder): its info. wait_s: how long to wait
        for the lock (0, the default: now or never; at most WAIT_MAX_S). BridgeError when it
        cannot."""
        if not (isinstance(worktree, str) and os.path.isabs(worktree) and os.path.isdir(worktree)):
            raise BridgeError('invalid', 'worktree must be an absolute path to an existing folder')
        if not (isinstance(wait_s, (int, float)) and 0 <= wait_s <= WAIT_MAX_S):
            raise BridgeError('invalid', 'wait_s must be 0 to %d seconds' % WAIT_MAX_S)
        worktree = os.path.normpath(worktree)
        try:
            exe = self.ready(self.env)
        except addon.SetupError as e:
            raise BridgeError('setup', str(e))
        store = self._store()
        slot, port = self._book(store, _repo(worktree), _slug(worktree), wait_s)
        proc = pycache = None
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            log_path = os.path.join(self.out_dir, '%s.log' % slot.id)
            pycache = tempfile.mkdtemp(prefix='%s-pyc-' % slot.id, dir=self.out_dir)
            if not self.bindable(port):
                raise BridgeError('port', 'port %d was taken after it was booked; try again' % port)
            env = addon.launch_env(getattr(slot, 'env', None) or self.env or os.environ, pycache)
            with open(log_path, 'wb') as log:
                proc = slot.start(self.argv(exe, port), stdin=subprocess.DEVNULL, stdout=log,
                                  stderr=subprocess.STDOUT, cwd=worktree, env=env)
            self._wait_ready(slot, proc, port, log_path)
        except BaseException as e:
            with contextlib.suppress(Exception):
                slot.release(1, 'error')        # 'error' ends the job's processes
            if proc is not None:
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    proc.wait(EXIT_S)
            if pycache is not None:
                shutil.rmtree(pycache, ignore_errors=True)
            if isinstance(e, OSError):
                raise BridgeError('start', 'Blender could not be started: %s' % e)
            raise
        inst = Instance(slot.id, port, worktree, exe, slot, proc, log_path, self.clock())
        inst.pycache = pycache
        with self.mu:
            self.instances[inst.handle] = inst
        return inst.info(self.clock())

    def _free_ports(self, store):
        held = ports_booked(store)
        with self.mu:
            mine = {i.port for i in self.instances.values()}
        for port in self.ports:
            if (RESOURCE_PREFIX + str(port)) in held or port in mine:
                continue
            if self.listening(port) or not self.bindable(port):
                continue
            yield port

    def _book(self, store, repo, slug, wait_s):
        for attempt in range(PORT_TRIES):
            try:
                port = next(iter(self._free_ports(store)), None)
            except OSError as e:
                raise BridgeError('lock', 'the lock store could not be read: %s' % e)
            if port is None:
                raise BridgeError('ports', 'every bridge port (%d-%d) is held or in use'
                                  % (self.ports[0], self.ports[-1]))
            res = RESOURCE_PREFIX + str(port)
            try:
                slot = self.book(KIND, repo, slug, gpu_share=GPU_SHARE, resource=res,
                                 wait_s=wait_s or None, no_wait=not wait_s, store=store,
                                 note='Blender MCP bridge on port %d' % port,
                                 kinds_table=self.kinds_table, env=self.env)
            except LockError as e:
                taken = False
                with contextlib.suppress(OSError):
                    taken = res in ports_booked(store)
                if e.code == EXIT_BUSY and taken and attempt + 1 < PORT_TRIES:
                    continue            # another bridge took this port first
                raise BridgeError('lock', 'the machine lock gave no slot for a Blender: %s (exit %d)'
                                  % (e.message, e.code))
            return slot, port
        raise BridgeError('ports', 'other bridges kept taking the free ports; try again')

    def _ours(self, slot, proc, pid):
        """Is the listener `pid` this Blender (its process, or one its run's job holds)? None (a
        Linux listener, its owner not read) counts as Blender's. Job.contains answers "in", "out",
        "gone" or "unknown"; only "in" is Blender's."""
        if pid is None or pid == proc.pid:
            return True
        try:
            return slot.job.contains(pid) == 'in'
        except Exception:
            return False

    def _wait_ready(self, slot, proc, port, log_path):
        deadline = time.monotonic() + self.ready_s
        while True:
            code = proc.poll()
            if code is not None:
                raise BridgeError('start', 'Blender exited with code %s before it listened on port '
                                  '%d; its log ends: %s' % (code, port, _tail(log_path)))
            owners = self.listening(port)
            foreign = sorted(p for p in owners if not self._ours(slot, proc, p))
            if foreign:
                raise BridgeError('port', 'port %d has a listener that is not this Blender (pid %s); '
                                  'Blender was ended' % (port, ', '.join(map(str, foreign))))
            if owners:
                return
            if slot.stop_asked():
                raise BridgeError('stopped', 'the machine lock stopped run %s while Blender started'
                                  % slot.id)
            if time.monotonic() > deadline:
                raise BridgeError('start', 'Blender did not listen on port %d within %d s; its log '
                                  'ends: %s' % (port, self.ready_s, _tail(log_path)))
            time.sleep(POLL_S)

    # ------------------------------------------------------------------ calls
    def _live(self, handle):
        with self.mu:
            inst = self.instances.get(handle)
            gone = None if inst else next((g for g in self.gone if g.handle == handle), None)
        if inst is not None:
            return inst
        if gone is not None:
            raise BridgeError('ended', 'Blender %s has ended: %s' % (handle, gone.ended['why']))
        raise BridgeError('unknown', 'no open Blender has handle %r; blender_list shows them'
                          % (handle,))

    def _check_port(self, inst):
        """Ends inst (BridgeError) when Blender has exited or a listener not its own shares its
        port; BridgeError when nothing listens."""
        code = inst.proc.poll()
        if code is not None:
            self._end(inst, 'Blender exited by itself (code %s)' % code)
            raise BridgeError('ended', 'Blender %s had exited (code %s); its booking is released'
                              % (inst.handle, code))
        try:
            owners = self.listening(inst.port)
        except OSError as e:
            raise BridgeError('port', 'the listener table could not be read: %s' % e)
        foreign = sorted(p for p in owners if not self._ours(inst.slot, inst.proc, p))
        if foreign:
            why = ('port %d gained a listener that is not this Blender (pid %s), so Blender was '
                   'ended before any call could reach it' % (inst.port, ', '.join(map(str, foreign))))
            self._end(inst, why, kill=True)
            raise BridgeError('port', why)
        if not owners:
            raise BridgeError('port', 'Blender %s is running but nothing listens on port %d'
                              % (inst.handle, inst.port))

    def call(self, handle, code, timeout_s):
        """The add-on's reply to `code` in Blender `handle`. Past timeout_s the Blender is ended
        (BridgeError timeout)."""
        inst = self._live(handle)
        with inst.mu:
            if inst.ended:
                raise BridgeError('ended', 'Blender %s has ended: %s' % (handle, inst.ended['why']))
            if inst.closing:
                raise BridgeError('ended', 'Blender %s is closing' % handle)
            self._check_port(inst)
            inst.busy = True
        try:
            return wire.call(inst.port, code, timeout_s)
        except wire.CallTimeout:
            why = 'a call ran past its %g s limit, so Blender was ended' % timeout_s
            self._end(inst, why, kill=True)
            raise BridgeError('timeout', why + '; open a new one with blender_open')
        except wire.WireError as e:
            if inst.ended:
                raise BridgeError('ended', 'Blender %s ended during the call: %s'
                                  % (handle, inst.ended['why']))
            with contextlib.suppress(subprocess.TimeoutExpired):
                inst.proc.wait(1.0)     # a reply cut short by Blender's exit: let the exit land
            rc = inst.proc.poll()
            if rc is not None:
                self._end(inst, 'Blender exited during a call (code %s)' % rc)
                raise BridgeError('ended', 'Blender %s exited during the call (code %s); its '
                                  'booking is released' % (handle, rc))
            raise BridgeError('wire', str(e))
        finally:
            with inst.mu:
                inst.busy, inst.last = False, self.clock()
                inst.calls += 1

    def render(self, handle, path=None, width=1280, height=720, engine='workbench', camera=None,
               timeout_s=300.0):
        """Renders Blender `handle`'s scene to a PNG (codes.render): the reply, whose result
        names the file. path: an absolute .png path (default: a new file in out_dir)."""
        inst = self._live(handle)
        if path is None:
            path = os.path.join(self.out_dir, handle, 'render-%03d.png' % (inst.renders + 1))
        elif not (os.path.isabs(path) and path.lower().endswith('.png')):
            raise BridgeError('invalid', 'path must be an absolute path ending in .png')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            code = codes.render(path, width, height, engine, camera)
        except ValueError as e:
            raise BridgeError('invalid', str(e))
        reply = self.call(handle, code, timeout_s)
        if reply.get('status') == 'ok':
            inst.renders += 1
        return reply

    # ------------------------------------------------------------------ ends
    def _end(self, inst, why, kill=False):
        """Ends inst once: its booking released, with its job's processes ended (kill, or
        Blender still running), else as done with Blender's exit code. Returns its end fields."""
        with inst.mu:
            if inst.ended:
                return inst.ended
            try:
                return self._end_once(inst, why, kill)
            finally:
                inst.done.set()

    def _end_once(self, inst, why, kill):
        with inst.mu:
            code = inst.proc.poll()
            how = 'cancelled' if kill or code is None else 'done'
            inst.ended = {'ended': iso(), 'why': why, 'how': how, 'code': code}
            with self.mu:
                self.instances.pop(inst.handle, None)
                self.gone = (self.gone + [inst])[-GONE_KEPT:]
            try:
                rec = inst.slot.release(1 if how == 'cancelled' else code, how)
            except Exception as e:      # the record may be left; the lock drops a dead wrapper's
                inst.ended['release_error'] = '%s: %s' % (type(e).__name__, e)
                rec = None
            if how == 'cancelled':
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    inst.proc.wait(EXIT_S)
                inst.ended['code'] = inst.proc.poll()
            if isinstance(rec, dict):
                inst.ended['run_how'] = rec.get('how')
            if inst.pycache:
                shutil.rmtree(inst.pycache, ignore_errors=True)
            if how == 'done' and code == 0:
                with contextlib.suppress(OSError):
                    os.remove(inst.log_path)
            return inst.ended

    def close(self, handle, why='closed by blender_close'):
        """Stops Blender `handle` (the add-on's stop, then its exit) and releases its booking; a
        Blender that does not exit within EXIT_S is ended with its job. Its info, ended. Refused
        (BridgeError busy) while a call runs in it; a close another thread began is waited for."""
        inst = self._live(handle)
        self._close(inst, why)
        return inst.info(self.clock())

    def _close(self, inst, why, idle_s=None):
        """close, for inst. idle_s: the watcher's idle close, which does nothing (False) unless,
        when it takes inst.mu, no call runs and none ended within idle_s. True once inst ended."""
        with inst.mu:
            if inst.ended:
                return True
            if not inst.closing:
                if inst.busy:
                    if idle_s is not None:
                        return False
                    raise BridgeError('busy', 'Blender %s is running a call; close it once the call '
                                      'returns' % inst.handle)
                if idle_s is not None and self.clock() - inst.last < idle_s:
                    return False
                inst.closing = True
                mine = True
            else:
                mine = False
        if not mine:                    # another thread's close: its stop and wait are bounded
            inst.done.wait(STOP_S + 2 * EXIT_S + 5)
            return bool(inst.ended)
        try:
            if inst.proc.poll() is None:
                with contextlib.suppress(wire.WireError):
                    wire.call(inst.port, codes.STOP, STOP_S)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    inst.proc.wait(EXIT_S)
        finally:
            self._end(inst, why)
        return True

    def list(self):
        """{open: [info], ended: [info]} of this server's Blenders, ended ones newest last."""
        now = self.clock()
        with self.mu:
            live, gone = list(self.instances.values()), list(self.gone)
        return {'open': [i.info(now) for i in live], 'ended': [g.info(now) for g in gone]}

    def tick(self):
        """One look by the watcher: a Blender whose run the lock was asked to stop is ended, one
        that exited is released, and one idle past idle_s is closed."""
        now = self.clock()
        with self.mu:
            live = list(self.instances.values())
        for inst in live:
            try:
                with inst.mu:
                    if inst.closing or inst.ended:  # its close ends it, with its own why
                        continue
                if inst.slot.stop_asked():
                    self._end(inst, 'the machine lock stopped run %s' % inst.slot.id, kill=True)
                    continue
                code = inst.proc.poll()
                if code is not None:
                    self._end(inst, 'Blender exited by itself (code %s)' % code)
                elif now - inst.last >= self.idle_s:    # _close looks again, under inst.mu
                    self._close(inst, 'idle for %d min, so it was closed' % (self.idle_s // 60),
                                idle_s=self.idle_s)
            except BridgeError:
                pass
            except Exception:           # one Blender's failure must not stop the watcher
                pass

    def start_watcher(self, period_s=WATCH_S):
        def loop():
            while not self.stopping.wait(period_s):
                self.tick()
        self.watcher = threading.Thread(target=loop, name='blender-watcher', daemon=True)
        self.watcher.start()

    def shutdown(self, why='the bridge ended (its MCP client went away)'):
        """Stops the watcher and closes every open Blender; one still in a call is ended with its
        job, as nothing is left to take the call's reply."""
        self.stopping.set()
        with self.mu:
            live = list(self.instances.values())
        for inst in live:
            with contextlib.suppress(Exception):
                try:
                    self._close(inst, why)
                except BridgeError as e:
                    if e.code != 'busy':
                        raise
                    self._end(inst, why, kill=True)
