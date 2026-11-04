"""kitlib.blendermcp.instance: the bridge's Blenders on a scratch lock store, each a stand-in
Blender (fakeblender.py) booked with the real render kind, on ports the system gave this test."""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from tests.lock.helpers import KINDS, OWNER, REPO, StoreCase, clean_env

if os.name == 'nt':
    from kitlib.blendermcp import RESOURCE_PREFIX, instance
    from kitlib.blendermcp.instance import Bridge, BridgeError
    from kitlib.lock import api
    from kitlib.lock import run as run_mod
    from kitlib.proc import process

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fakeblender.py')


def system_ports(n):
    """n distinct ports the system gives port-0 binds, closed again."""
    socks = []
    try:
        for _ in range(n):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            socks.append(s)
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


class BridgeCase(StoreCase):
    def setUp(self):
        super().setUp()
        with open(os.path.join(REPO, 'kitlib', 'lock', 'kinds.json'), encoding='utf-8') as f:
            render = json.load(f)['kinds']['render']
        kinds = {'kinds': dict(KINDS['kinds'], render=render)}
        with open(self.kinds_path, 'w', encoding='utf-8') as f:
            json.dump(kinds, f)
        self.store = self.make_store()
        # a medium kind (render, engine-test) is one fake cpu slot here, so the two runs a test
        # holds at once fit the two slots confine leaves inside the gate's 4-thread run (each
        # medium booking would take 2 of them under the default division)
        res = api.set_division(OWNER, {'medium_cores': 1}, self.store)
        self.assertTrue(res.get('ok'), res)
        self.assertEqual(self.layout()['medium_cores'], 1)
        self.confine(2)
        self.wt = os.path.join(self.tmp, 'wt')
        os.makedirs(self.wt)
        self.opts = []
        self.now = 1000.0
        self.bridge = Bridge(store=self.store, env=clean_env(), ready=lambda env: sys.executable,
                             argv=self.argv, ports=system_ports(4), clock=lambda: self.now,
                             idle_s=60, ready_s=20, out_dir=os.path.join(self.tmp, 'out'))
        self.addCleanup(self.bridge.shutdown)

    def argv(self, exe, port):
        return [exe, FAKE, '--port', str(port)] + self.opts

    def ports_held(self):
        """The port resources live runs of the store hold, from their records."""
        return sorted(r['resource'] for r in self.store.live_runs()
                      if str(r.get('resource')).startswith(RESOURCE_PREFIX))

    def ended(self, handle):
        return next(g for g in self.bridge.list()['ended'] if g['handle'] == handle)

    def gone(self, pid):
        deadline = time.monotonic() + 10
        while process.alive(pid, None) and time.monotonic() < deadline:
            time.sleep(0.05)
        return not process.alive(pid, None)

    def run_how(self, rid):
        rec = api.run_record(rid, self.store)
        return rec and rec.get('how')


class Opening(BridgeCase):
    def test_two_blenders_each_get_a_port_a_gpu_slot_and_a_booking_of_their_own(self):
        a = self.bridge.open(self.wt)
        b = self.bridge.open(self.wt)
        self.assertNotEqual(a['port'], b['port'])
        self.assertNotEqual(a['handle'], b['handle'])
        self.assertEqual((len(a['gpu_slots']), len(b['gpu_slots'])), (1, 1))
        self.assertNotEqual(a['gpu_slots'], b['gpu_slots'])
        self.assertEqual(self.ports_held(), sorted(RESOURCE_PREFIX + str(i['port']) for i in (a, b)))
        rec = api.run_record(a['handle'], self.store)
        self.assertEqual((rec['kind'], rec.get('gpu_share')), ('render', 'training'))
        reply = self.bridge.call(a['handle'], 'print("hi")\nresult = {"sum": 1 + 1}', 10)
        self.assertEqual(reply, {'status': 'ok', 'result': {'sum': 2}, 'stdout': 'hi\n'})
        err = self.bridge.call(b['handle'], 'raise ValueError("nope")', 10)
        self.assertEqual(err['status'], 'error')
        self.assertIn('ValueError: nope', err['message'])

        closed = self.bridge.close(a['handle'])
        self.assertEqual((closed['how'], closed['code']), ('done', 0))
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ports_held(), [RESOURCE_PREFIX + str(b['port'])])
        self.assertEqual(self.run_how(a['handle']), 'done')
        self.assertFalse(os.path.exists(a['log']), 'a clean exit leaves no log')
        with self.assertRaises(BridgeError) as cm:
            self.bridge.call(a['handle'], 'result = {}', 5)
        self.assertEqual(cm.exception.code, 'ended')
        with self.assertRaises(BridgeError) as cm:
            self.bridge.call('r-nope', 'result = {}', 5)
        self.assertEqual(cm.exception.code, 'unknown')

    def test_blender_reads_its_python_caches_only_from_a_fresh_folder_of_its_own(self):
        a = self.bridge.open(self.wt)
        code = ('import os, sys\n'
                'result = {"prefix": sys.pycache_prefix, "write": sys.dont_write_bytecode,\n'
                '          "python": sorted(k for k in os.environ if k.upper().startswith("PYTHON")),\n'
                '          "run": os.environ.get("MACHINE_LOCK_RUN", "")}')
        got = self.bridge.call(a['handle'], code, 10)['result']
        prefix = got['prefix']
        self.assertTrue(prefix and os.path.isdir(prefix), got)
        self.assertEqual(os.listdir(prefix), [])
        self.assertEqual(os.path.dirname(prefix), os.path.join(self.tmp, 'out'))
        self.assertTrue(got['write'])
        self.assertEqual(got['python'], ['PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PYTHONPYCACHEPREFIX'])
        self.assertTrue(got['run'].startswith(a['handle']), got)      # the lock's own env is kept
        self.bridge.close(a['handle'])
        self.assertFalse(os.path.exists(prefix))

    def test_with_no_gpu_slot_free_an_open_is_refused_at_once_and_leaves_nothing(self):
        a = self.bridge.open(self.wt)
        b = self.bridge.open(self.wt)
        t0 = time.monotonic()
        with self.assertRaises(BridgeError) as cm:
            self.bridge.open(self.wt)
        self.assertEqual(cm.exception.code, 'lock')
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(len(self.ports_held()), 2)
        self.assertEqual(sorted(i['handle'] for i in self.bridge.list()['open']),
                         sorted([a['handle'], b['handle']]))

    def test_a_port_in_use_is_skipped(self):
        busy = socket.socket()
        busy.bind(('127.0.0.1', self.bridge.ports[0]))
        busy.listen(1)
        self.addCleanup(busy.close)
        a = self.bridge.open(self.wt)
        self.assertNotEqual(a['port'], self.bridge.ports[0])

    def test_a_port_another_run_holds_is_skipped(self):
        other = run_mod.book('engine-test', 'kit', 'other', store=self.store, env=clean_env(),
                             no_wait=True, resource=RESOURCE_PREFIX + str(self.bridge.ports[0]))
        self.addCleanup(other.release, 0, 'done')
        self.assertEqual(instance.ports_booked(self.store),
                         {RESOURCE_PREFIX + str(self.bridge.ports[0])})
        a = self.bridge.open(self.wt)
        self.assertEqual(a['port'], self.bridge.ports[1])

    def test_a_blender_that_never_listens_is_ended_and_its_booking_released(self):
        pidfile = os.path.join(self.tmp, 'pid')
        self.opts = ['--ready-delay', '60', '--pidfile', pidfile]
        self.bridge.ready_s = 1.0
        # the fake writes its pid at startup, which on a loaded machine can come after the 1 s
        # budget; hold the bridge's first look at the port after the spawn until the pid is
        # written (bounded by a deadline of our own), so the budget is spent only afterwards
        spawned = []
        real_argv, real_listening = self.bridge.argv, self.bridge.listening

        def argv(exe, port):
            spawned.append(port)
            return real_argv(exe, port)

        def listening(port):
            deadline = time.monotonic() + 60
            while spawned and not (os.path.exists(pidfile) and os.path.getsize(pidfile)) and time.monotonic() < deadline:
                time.sleep(0.05)
            return real_listening(port)

        self.bridge.argv, self.bridge.listening = argv, listening
        with self.assertRaises(BridgeError) as cm:
            self.bridge.open(self.wt)
        self.assertEqual(cm.exception.code, 'start')
        self.assertTrue(os.path.exists(pidfile), 'the fake never wrote its pid')
        with open(pidfile) as f:
            self.assertTrue(self.gone(int(f.read())))
        self.assertEqual(self.ports_held(), [])
        self.assertEqual(self.bridge.list()['open'], [])

    def test_a_blender_that_exits_at_start_is_reported_with_its_code(self):
        self.opts = ['--exit-at-start', '5']
        with self.assertRaises(BridgeError) as cm:
            self.bridge.open(self.wt)
        self.assertEqual(cm.exception.code, 'start')
        self.assertIn('code 5', cm.exception.message)
        self.assertEqual(self.ports_held(), [])

    def test_open_refuses_a_worktree_that_is_not_an_absolute_folder(self):
        for wt in ('relative', os.path.join(self.tmp, 'missing'), None):
            with self.assertRaises(BridgeError) as cm:
                self.bridge.open(wt)
            self.assertEqual(cm.exception.code, 'invalid')
        with self.assertRaises(BridgeError):
            self.bridge.open(self.wt, wait_s=instance.WAIT_MAX_S + 1)
        self.assertEqual(self.ports_held(), [])


class Ending(BridgeCase):
    def test_a_call_past_its_limit_ends_blender_and_releases_its_booking(self):
        a = self.bridge.open(self.wt)
        t0 = time.monotonic()
        with self.assertRaises(BridgeError) as cm:
            self.bridge.call(a['handle'], 'import time\ntime.sleep(60)\nresult = {}', 1.0)
        self.assertEqual(cm.exception.code, 'timeout')
        self.assertLess(time.monotonic() - t0, 20)
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ended(a['handle'])['how'], 'cancelled')
        self.assertEqual(self.run_how(a['handle']), 'cancelled')
        self.assertEqual(self.ports_held(), [])

    def test_a_foreign_listener_on_the_port_ends_blender_before_any_call_reaches_it(self):
        a = self.bridge.open(self.wt)
        thief = socket.socket()
        thief.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            thief.bind(('127.0.0.1', a['port']))
        except OSError:
            thief.close()
            self.skipTest('this system refuses a second listener on the port')
        thief.listen(1)
        thief.settimeout(0.5)
        self.addCleanup(thief.close)
        with self.assertRaises(BridgeError) as cm:
            self.bridge.call(a['handle'], 'result = {}', 5)
        self.assertEqual(cm.exception.code, 'port')
        self.assertIn(str(os.getpid()), cm.exception.message)
        with self.assertRaises(socket.timeout):
            thief.accept()                  # the call never connected
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ended(a['handle'])['how'], 'cancelled')
        self.assertEqual(self.ports_held(), [])

    def test_a_stop_the_lock_was_asked_for_ends_blender(self):
        a = self.bridge.open(self.wt)
        self.assertTrue(run_mod.request_stop(self.store, a['handle'], 'test-owner'))
        self.bridge.tick()
        self.assertTrue(self.gone(a['pid']))
        end = self.ended(a['handle'])
        self.assertEqual(end['how'], 'cancelled')
        self.assertIn('machine lock stopped', end['why'])
        self.assertEqual(self.ports_held(), [])

    def test_a_blender_that_exits_by_itself_is_released(self):
        a = self.bridge.open(self.wt)
        self.bridge.call(a['handle'], 'import os, threading\n'
                         'threading.Timer(0.2, os._exit, (4,)).start()\nresult = {}', 5)
        self.assertTrue(self.gone(a['pid']))
        self.bridge.tick()
        end = self.ended(a['handle'])
        self.assertEqual((end['how'], end['code']), ('done', 4))
        self.assertIn('exited by itself', end['why'])
        self.assertEqual(self.ports_held(), [])
        self.assertTrue(os.path.exists(a['log']), 'a failed exit keeps its log')

    def test_a_blender_that_exits_during_a_call_is_named_so(self):
        a = self.bridge.open(self.wt)
        with self.assertRaises(BridgeError) as cm:
            self.bridge.call(a['handle'], 'import os\nos._exit(3)', 5)
        self.assertEqual(cm.exception.code, 'ended')
        self.assertIn('code 3', cm.exception.message)
        self.assertEqual(self.ports_held(), [])

    def test_an_idle_blender_is_closed_and_a_busy_one_is_not(self):
        a = self.bridge.open(self.wt)
        self.now += 59
        self.bridge.tick()
        self.assertEqual(len(self.bridge.list()['open']), 1)
        self.now += 2
        self.bridge.tick()
        end = self.ended(a['handle'])
        self.assertEqual((end['how'], end['code']), ('done', 0))
        self.assertIn('idle', end['why'])
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ports_held(), [])

    def call_in_thread(self, handle, code, timeout_s=20):
        """Starts bridge.call in a thread; returns (thread, out) once that call is running, out
        getting 'reply' or 'error'."""
        out = {}

        def run():
            try:
                out['reply'] = self.bridge.call(handle, code, timeout_s)
            except BridgeError as e:
                out['error'] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        inst = self.bridge.instances[handle]
        deadline = time.monotonic() + 10
        while not inst.busy and t.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(inst.busy, out)
        return t, out

    def test_the_idle_close_skips_a_blender_whose_call_began_after_the_watcher_looked(self):
        a = self.bridge.open(self.wt)
        inst = self.bridge.instances[a['handle']]
        started = []

        class Racing:
            """inst.mu, a call started in another thread the first time the watcher takes it."""
            def __init__(self, inner, test):
                self.inner, self.test, self.watcher = inner, test, threading.current_thread()

            def __enter__(self):
                if not started and threading.current_thread() is self.watcher:
                    started.append(self.test.call_in_thread(
                        a['handle'], 'import time\ntime.sleep(1)\nresult = {"ran": 1}'))
                return self.inner.__enter__()

            def __exit__(self, *exc):
                return self.inner.__exit__(*exc)
        inst.mu = Racing(inst.mu, self)
        self.now += 61
        self.bridge.tick()
        self.assertTrue(started, 'the watcher never took the lock')
        t, out = started[0]
        t.join(20)
        self.assertEqual(out.get('reply', {}).get('result'), {'ran': 1}, out)
        self.assertEqual([i['handle'] for i in self.bridge.list()['open']], [a['handle']])

    def test_closing_a_blender_mid_call_is_refused_and_the_call_goes_on(self):
        a = self.bridge.open(self.wt)
        t, out = self.call_in_thread(a['handle'], 'import time\ntime.sleep(1.5)\nresult = {"ran": 1}')
        t0 = time.monotonic()
        with self.assertRaises(BridgeError) as cm:
            self.bridge.close(a['handle'])
        self.assertEqual(cm.exception.code, 'busy')
        self.assertLess(time.monotonic() - t0, 1.0)
        t.join(20)
        self.assertEqual(out.get('reply', {}).get('result'), {'ran': 1}, out)
        self.assertEqual(len(self.bridge.list()['open']), 1)
        self.assertEqual(self.bridge.close(a['handle'])['how'], 'done')

    def test_a_call_while_blender_is_closing_is_refused_without_waiting_for_the_close(self):
        self.opts = ['--ignore-stop']
        a = self.bridge.open(self.wt)
        inst = self.bridge.instances[a['handle']]
        out, waiting, wait = {}, threading.Event(), inst.proc.wait

        def waits(*args, **kw):          # the close is past its stop call, waiting for the exit
            waiting.set()
            return wait(*args, **kw)
        inst.proc.wait = waits

        def close():
            out['end'] = self.bridge.close(a['handle'])
        with mock.patch.object(instance, 'EXIT_S', 3.0):
            t = threading.Thread(target=close, daemon=True)
            t.start()
            self.assertTrue(waiting.wait(10), 'the close never waited for the exit')
            t0 = time.monotonic()
            with self.assertRaises(BridgeError) as cm:
                self.bridge.call(a['handle'], 'result = {}', 5)
            waited = time.monotonic() - t0
            t.join(20)
        self.assertEqual(cm.exception.code, 'ended')
        self.assertLess(waited, 1.0, 'the call waited for the close')
        self.assertEqual(out['end']['how'], 'cancelled')
        self.assertEqual(self.ports_held(), [])

    def test_shutdown_ends_a_blender_mid_call_with_its_job(self):
        a = self.bridge.open(self.wt)
        t, out = self.call_in_thread(a['handle'], 'import time\ntime.sleep(30)\nresult = {}', 60)
        t0 = time.monotonic()
        self.bridge.shutdown()
        self.assertLess(time.monotonic() - t0, 8.0)
        t.join(20)
        self.assertEqual(out['error'].code, 'ended', out)
        self.assertEqual(self.ended(a['handle'])['how'], 'cancelled')
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ports_held(), [])

    def test_a_blender_that_ignores_the_stop_is_ended_with_its_job(self):
        self.opts = ['--ignore-stop']
        a = self.bridge.open(self.wt)
        with mock.patch.object(instance, 'EXIT_S', 1.0):
            end = self.bridge.close(a['handle'])
        self.assertEqual(end['how'], 'cancelled')
        self.assertTrue(self.gone(a['pid']))
        self.assertEqual(self.ports_held(), [])

    def test_shutdown_closes_every_blender(self):
        a = self.bridge.open(self.wt)
        b = self.bridge.open(self.wt)
        self.bridge.shutdown()
        self.assertEqual(self.bridge.list()['open'], [])
        for i in (a, b):
            self.assertTrue(self.gone(i['pid']))
            self.assertEqual(self.ended(i['handle'])['how'], 'done')
        self.assertEqual(self.ports_held(), [])


class Listeners(unittest.TestCase):
    def test_listening_reads_only_the_listener_table_on_every_platform(self):
        # kitlib.proc.tcp.listeners holds the Linux /proc/net reading too: (local, None) rows
        from kitlib.blendermcp import instance
        from kitlib.proc import tcp
        lo = b'\x7f\0\0\x01'
        rows = [((4, lo, 9877), None), ((4, lo, 9878), 12), ((6, b'\0' * 16, 9878), 13)]
        self.assertFalse(hasattr(instance, '_proc_net_listens'))
        for name in ('windows', 'linux'):
            with self.subTest(platform=name), mock.patch('kitlib.platform.NAME', name), \
                    mock.patch.object(tcp, 'listeners', return_value=rows) as table:
                self.assertEqual(instance.listening(9877), {None})
                self.assertEqual(instance.listening(9878), {12, 13})
                self.assertEqual(instance.listening(9879), set())
                self.assertEqual(table.call_count, 3)


if __name__ == '__main__':
    unittest.main()
