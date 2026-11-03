"""kitlib.blendermcp.server: the tools, their argument checks and results, over a stand-in bridge;
the snippets of kitlib.blendermcp.codes compile; and mcp/blender_server.py as a process."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import warnings
from unittest import mock

from kitlib.blendermcp import SERVER_NAME, TOOL_NAMES, codes, server
from kitlib.blendermcp.instance import BridgeError
from tests.lock.helpers import REPO, clean_env

ENTRY = os.path.join(REPO, 'mcp', 'blender_server.py')


class StubBridge:
    """Records each call and answers from `replies` (name -> value, or an exception to raise)."""

    def __init__(self, **replies):
        self.calls, self.replies = [], replies
        self.watching = self.shut = False

    def _answer(self, name, *args):
        self.calls.append((name,) + args)
        r = self.replies.get(name)
        if isinstance(r, Exception):
            raise r
        return r

    def open(self, worktree, wait_s=0):
        return self._answer('open', worktree, wait_s)

    def list(self):
        return self._answer('list')

    def call(self, handle, code, timeout_s):
        return self._answer('call', handle, code, timeout_s)

    def render(self, *args):
        return self._answer('render', *args)

    def close(self, handle):
        return self._answer('close', handle)

    def start_watcher(self):
        self.watching = True

    def shutdown(self):
        self.shut = True


def rpc(srv, method, params=None, mid=1):
    msg = {'jsonrpc': '2.0', 'id': mid, 'method': method}
    if params is not None:
        msg['params'] = params
    return srv.handle(msg)


class Tools(unittest.TestCase):
    def tool(self, srv, name, args):
        res = rpc(srv, 'tools/call', {'name': name, 'arguments': args})['result']
        self.assertEqual(json.loads(res['content'][0]['text']), res['structuredContent'])
        return res['structuredContent'], res['isError']

    def test_initialize_and_the_tool_list(self):
        srv = server.Server(StubBridge())
        init = rpc(srv, 'initialize', {'protocolVersion': '2025-03-26'})['result']
        self.assertEqual(init['protocolVersion'], '2025-03-26')
        self.assertEqual(init['serverInfo']['name'], 'blender')
        tools = rpc(srv, 'tools/list')['result']['tools']
        self.assertEqual(tuple(t['name'] for t in tools), TOOL_NAMES)
        ro = {t['name'] for t in tools if t['annotations']['readOnlyHint']}
        self.assertEqual(ro, {'blender_list', 'blender_inspect'})
        self.assertEqual(rpc(srv, 'tools/call', {'name': 'lock_snapshot'})['error']['code'], -32602)
        self.assertEqual(rpc(srv, 'nope')['error']['code'], -32601)
        self.assertIsNone(srv.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))

    def test_arguments_are_checked_before_the_bridge_sees_them(self):
        b = StubBridge()
        srv = server.Server(b)
        for name, args in (('blender_open', {}), ('blender_open', {'worktree': 3}),
                           ('blender_open', {'worktree': '/w', 'extra': 1}),
                           ('blender_exec', {'handle': 'r-x'}),
                           ('blender_exec', {'handle': 'r-x', 'code': 'x', 'timeout_s': 0}),
                           ('blender_exec', {'handle': 'r-x', 'code': 'x', 'timeout_s': 99999}),
                           ('blender_render', {'handle': 'r-x', 'engine': 'povray'}),
                           ('blender_render', {'handle': 'r-x', 'width': 8}),
                           ('blender_render', {'handle': 'r-x', 'width': 1.5}),
                           ('blender_close', {'handle': True})):
            out, is_error = self.tool(srv, name, args)
            self.assertTrue(is_error, (name, args))
            self.assertEqual(out['error']['code'], 'invalid', (name, args))
        self.assertEqual(b.calls, [])

    def test_results_and_failures(self):
        b = StubBridge(open={'handle': 'r-1', 'port': 9877},
                       call={'status': 'ok', 'result': {'a': 1}, 'stdout': 'x'},
                       close={'handle': 'r-1', 'how': 'done'}, list={'open': [], 'ended': []})
        srv = server.Server(b)
        self.assertEqual(self.tool(srv, 'blender_open', {'worktree': '/w', 'wait_s': 5}),
                         ({'ok': True, 'blender': {'handle': 'r-1', 'port': 9877}}, False))
        self.assertEqual(self.tool(srv, 'blender_exec', {'handle': 'r-1', 'code': 'c'}),
                         ({'ok': True, 'result': {'a': 1}, 'stdout': 'x'}, False))
        self.assertEqual(b.calls[-1], ('call', 'r-1', 'c', server.EXEC_S))
        self.tool(srv, 'blender_inspect', {'handle': 'r-1'})
        self.assertEqual(b.calls[-1], ('call', 'r-1', codes.INSPECT, server.INSPECT_S))
        self.tool(srv, 'blender_render', {'handle': 'r-1', 'engine': 'eevee', 'width': 64})
        self.assertEqual(b.calls[-1], ('render', 'r-1', None, 64, 720, 'eevee', None, server.RENDER_S))
        self.assertEqual(self.tool(srv, 'blender_list', {}),
                         ({'ok': True, 'open': [], 'ended': []}, False))
        self.assertEqual(self.tool(srv, 'blender_close', {'handle': 'r-1'})[0]['blender']['how'],
                         'done')

        b.replies['call'] = {'status': 'error', 'message': 'Traceback: boom'}
        self.assertEqual(self.tool(srv, 'blender_exec', {'handle': 'r-1', 'code': 'c'}),
                         ({'ok': False, 'message': 'Traceback: boom'}, True))
        b.replies['open'] = BridgeError('lock', 'no slot')
        out, is_error = self.tool(srv, 'blender_open', {'worktree': '/w'})
        self.assertTrue(is_error)
        self.assertEqual(out['error'], {'code': 'lock', 'message': 'no slot', 'field': None})
        b.replies['list'] = RuntimeError('a bug')
        out, is_error = self.tool(srv, 'blender_list', {})
        self.assertEqual((out['error']['code'], is_error), ('internal', True))

    def test_run_serves_with_the_watcher_on_and_closes_every_blender_at_the_end(self):
        b = StubBridge()
        rfile = io.BytesIO(b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n')
        wfile = io.BytesIO()
        server.run(rfile, wfile, server.Server(b))
        self.assertTrue(b.watching and b.shut)
        self.assertEqual(json.loads(wfile.getvalue()), {'jsonrpc': '2.0', 'id': 1, 'result': {}})


class EntryPoint(unittest.TestCase):
    """mcp/blender_server.py as a process: it boots, lists its tools and lists no Blender with no
    Blender and no lock (both are first touched by blender_open), and exits when stdin ends."""

    def test_it_lists_its_tools_and_exits_at_the_end_of_stdin_starting_no_blender(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        nowhere = os.path.join(tmp, 'no-blender.exe')       # a Blender looked for would be refused
        requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                                'clientInfo': {'name': 'test', 'version': '0'}}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                     'params': {'name': 'blender_list', 'arguments': {}}}]
        p = subprocess.run([sys.executable, ENTRY],
                           input=''.join(json.dumps(r) + '\n' for r in requests),
                           capture_output=True, text=True, encoding='utf-8', cwd=tmp, timeout=60,
                           env=clean_env(PM_KIT_BLENDER=nowhere),
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(p.returncode, 0, p.stderr)
        replies = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual([r.get('id') for r in replies], [1, 2, 3], p.stdout)
        self.assertEqual(replies[0]['result']['serverInfo']['name'], SERVER_NAME)
        self.assertEqual(tuple(t['name'] for t in replies[1]['result']['tools']), TOOL_NAMES)
        listed = replies[2]['result']
        self.assertFalse(listed['isError'], listed)
        self.assertEqual(listed['structuredContent'], {'ok': True, 'open': [], 'ended': []})
        self.assertFalse(os.path.exists(nowhere))

    def test_the_plugin_starts_this_server(self):
        with open(os.path.join(REPO, '.claude-plugin', 'plugin.json'), encoding='utf-8') as f:
            s = json.load(f)['mcpServers'][SERVER_NAME]
        self.assertEqual((s['command'], s['args']), ('python', ['${CLAUDE_PLUGIN_ROOT}/mcp/blender_server.py']))
        self.assertTrue(os.path.isfile(ENTRY))


class Codes(unittest.TestCase):
    def test_the_snippets_compile(self):
        for name, src in (('inspect', codes.INSPECT), ('stop', codes.STOP),
                          ('render', codes.render('/t/x.png', 64, 32, 'cycles', "it's")),
                          ('render', codes.render('C:\\t\\x.png', 64, 32))):
            with warnings.catch_warnings():
                warnings.simplefilter('error')      # a SyntaxWarning would show in Blender
                compile(src, name, 'exec')
        self.assertIn(repr('C:\\t\\x.png'), codes.render('C:\\t\\x.png', 64, 32))
        with self.assertRaises(ValueError):
            codes.render('/t/x.png', 64, 32, 'povray')

    def test_render_picks_the_engine_by_setting_it_and_puts_the_settings_back(self):
        """Blender's engine enum is dynamic: on the type it lists nothing (the live run's 5.2
        refused workbench that way), so the snippet tries each name on the scene's settings."""
        tmp = tempfile.mkdtemp(prefix='blendermcp-')
        self.addCleanup(shutil.rmtree, tmp, True)
        for engine, want in (('workbench', 'BLENDER_WORKBENCH'), ('eevee', 'BLENDER_EEVEE_NEXT')):
            with self.subTest(engine):
                bpy, r, sc = fake_bpy()
                path = os.path.join(tmp, engine + '.png')
                ns = {}
                with mock.patch.dict(sys.modules, {'bpy': bpy, 'mathutils': types.ModuleType('mathutils')}):
                    exec(compile(codes.render(path, 64, 32, engine, 'Cam'), 'render', 'exec'), ns)
                self.assertEqual((ns['result']['engine'], ns['result']['bytes']), (want, 3))
                self.assertEqual(r.rendered_with, (want, 64, 32, path, 'PNG', 'Cam'))
                self.assertEqual((r.engine, r.resolution_x, r.filepath, r.image_settings.file_format,
                                  sc.camera), ('CYCLES', 1920, '//out', 'JPEG', None))
        bpy, r, sc = fake_bpy(engines=('CYCLES',))
        with mock.patch.dict(sys.modules, {'bpy': bpy, 'mathutils': types.ModuleType('mathutils')}):
            with self.assertRaisesRegex(RuntimeError, 'none of the render engines'):
                exec(compile(codes.render(os.path.join(tmp, 'x.png'), 64, 32), 'render', 'exec'), {})
        self.assertEqual(r.engine, 'CYCLES')


def fake_bpy(engines=('BLENDER_WORKBENCH', 'BLENDER_EEVEE_NEXT', 'CYCLES')):
    """A bpy with just what the render snippet touches, for a scene with a camera named Cam: its
    render settings refuse an engine this Blender lacks, as Blender's do (TypeError)."""
    class Render:
        rendered_with = None

        def __init__(self):
            self._engine = 'CYCLES'
            self.resolution_x, self.resolution_y, self.resolution_percentage = 1920, 1080, 50
            self.filepath = '//out'
            self.image_settings = types.SimpleNamespace(file_format='JPEG')

        @property
        def engine(self):
            return self._engine

        @engine.setter
        def engine(self, value):
            if value not in engines:
                raise TypeError('bpy_struct: item.attr = val: enum "%s" not found' % value)
            self._engine = value

    r = Render()
    cam = types.SimpleNamespace(name='Cam', type='CAMERA')
    sc = types.SimpleNamespace(render=r, camera=None, objects=[cam])

    def render(write_still):
        r.rendered_with = (r.engine, r.resolution_x, r.resolution_y, r.filepath,
                           r.image_settings.file_format, sc.camera.name)
        with open(r.filepath, 'wb') as f:
            f.write(b'png')

    bpy = types.ModuleType('bpy')
    bpy.context = types.SimpleNamespace(scene=sc)
    bpy.data = types.SimpleNamespace(objects={'Cam': cam})
    bpy.ops = types.SimpleNamespace(render=types.SimpleNamespace(render=render))
    engine_prop = types.SimpleNamespace(enum_items=[])      # a dynamic enum, seen on the type
    bpy.types = types.SimpleNamespace(RenderSettings=types.SimpleNamespace(
        bl_rna=types.SimpleNamespace(properties={'engine': engine_prop})))
    return bpy, r, sc


if __name__ == '__main__':
    unittest.main()
