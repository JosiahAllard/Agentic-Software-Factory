"""kitlib.blendermcp.server: the tools, their argument checks and results, over a stand-in bridge;
and the snippets of kitlib.blendermcp.codes compile."""
import io
import json
import unittest
import warnings

from kitlib.blendermcp import TOOL_NAMES, codes, server
from kitlib.blendermcp.instance import BridgeError


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


if __name__ == '__main__':
    unittest.main()
