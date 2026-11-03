"""The bridge end to end with the real Blender and the machine's lock, opt in: it runs only when
PM_KIT_BLENDER_LIVE=1 (docs/blender-mcp.md "Testing"), never in the gate.

It starts mcp/blender_server.py as a real run (tests.home.unmarked_env: the machine's lock store
is fenced under the tests) and drives it over stdio: open a Blender, inspect it, build a cube,
render a PNG, close it; then open a second one and let a call run past its limit, which must end
that Blender. Every Blender is booked through the machine lock by the server, and the server
closes any left open when its stdin ends. Its files go to the temp folder (TEMP/TMP).

    set PM_KIT_BLENDER_LIVE=1 && python -m unittest tests.blendermcp.test_live
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from tests.home import unmarked_env

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRY = os.path.join(REPO, 'mcp', 'blender_server.py')
LIVE = os.environ.get('PM_KIT_BLENDER_LIVE') == '1'
PNG = b'\x89PNG\r\n\x1a\n'


class Client:
    def __init__(self, cwd):
        from kitlib.proc import spawn
        self.p = spawn.popen([sys.executable, ENTRY], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, env=unmarked_env(), cwd=cwd)
        self.mid = 0

    def rpc(self, method, params=None):
        self.mid += 1
        msg = {'jsonrpc': '2.0', 'id': self.mid, 'method': method, 'params': params or {}}
        self.p.stdin.write(json.dumps(msg).encode() + b'\n')
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise AssertionError('the server ended (code %s)' % self.p.wait(10))
        return json.loads(line)

    def tool(self, name, **args):
        res = self.rpc('tools/call', {'name': name, 'arguments': args})['result']
        return res['structuredContent'], res['isError']

    def close(self):
        if self.p.poll() is None:
            self.p.stdin.close()            # the server closes every Blender it opened
            try:
                self.p.wait(60)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self.p.stdout.close()


@unittest.skipUnless(LIVE, 'the live Blender test runs only with PM_KIT_BLENDER_LIVE=1')
class LiveBlender(unittest.TestCase):
    def setUp(self):
        self.wt = tempfile.mkdtemp(prefix='blender-live-')
        self.addCleanup(shutil.rmtree, self.wt, True)
        self.c = Client(self.wt)
        self.addCleanup(self.c.close)
        self.assertEqual(self.c.rpc('initialize')['result']['serverInfo']['name'], 'blender')

    def gone(self, pid):
        from kitlib.proc import process
        deadline = time.monotonic() + 30
        while process.alive(pid, None) and time.monotonic() < deadline:
            time.sleep(0.2)
        return not process.alive(pid, None)

    def test_open_build_render_close_then_a_call_past_its_limit(self):
        t0 = time.monotonic()
        out, err = self.c.tool('blender_open', worktree=self.wt, wait_s=60)
        self.assertFalse(err, out)
        b = out['blender']
        print('\nopened Blender pid %s on port %d (run %s, gpu slot %s) in %.1f s'
              % (b['pid'], b['port'], b['handle'], b['gpu_slots'], time.monotonic() - t0))
        self.assertEqual(len(b['gpu_slots']), 1)
        h = b['handle']

        out, err = self.c.tool('blender_inspect', handle=h)
        self.assertFalse(err, out)
        self.assertTrue(out['result']['blender'].startswith(('5.', '6.')), out)

        out, err = self.c.tool('blender_exec', handle=h, code=(
            'import bpy\n'
            'bpy.ops.object.select_all(action="SELECT")\nbpy.ops.object.delete()\n'
            'bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))\n'
            'bpy.context.active_object.name = "LiveCube"\n'
            'result = {"objects": [o.name for o in bpy.context.scene.objects]}'))
        self.assertFalse(err, out)
        self.assertEqual(out['result'], {'objects': ['LiveCube']})

        png = os.path.join(self.wt, 'cube.png')
        t1 = time.monotonic()
        out, err = self.c.tool('blender_render', handle=h, path=png, width=320, height=240)
        self.assertFalse(err, out)
        print('rendered %s (%s, %d bytes, framed camera %s) in %.1f s' % (
            png, out['result']['engine'], out['result']['bytes'], out['result']['framed_camera'],
            time.monotonic() - t1))
        with open(png, 'rb') as f:
            self.assertEqual(f.read(8), PNG)
        out, err = self.c.tool('blender_inspect', handle=h)
        self.assertEqual([o['name'] for o in out['result']['objects']], ['LiveCube'],
                         'the render left its framing camera behind')

        out, err = self.c.tool('blender_close', handle=h)
        self.assertFalse(err, out)
        self.assertEqual((out['blender']['how'], out['blender']['code']), ('done', 0))
        self.assertTrue(self.gone(b['pid']))

        out, err = self.c.tool('blender_open', worktree=self.wt, wait_s=60)
        self.assertFalse(err, out)
        b2 = out['blender']
        out, err = self.c.tool('blender_exec', handle=b2['handle'],
                               code='import time\ntime.sleep(120)\nresult = {}', timeout_s=3)
        self.assertTrue(err)
        self.assertEqual(out['error']['code'], 'timeout')
        self.assertTrue(self.gone(b2['pid']))
        out, err = self.c.tool('blender_list')
        self.assertEqual(out['open'], [])
        ended = {e['handle']: e for e in out['ended']}
        self.assertEqual(ended[b2['handle']]['how'], 'cancelled')
        self.assertEqual(ended[h]['how'], 'done')
        print('a call past its limit ended Blender pid %s; open now: %s' % (b2['pid'], out['open']))


if __name__ == '__main__':
    unittest.main()
