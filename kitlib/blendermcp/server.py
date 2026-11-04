"""The Blender MCP server (docs/blender-mcp.md "Tools"): six tools over one Bridge (instance.py),
JSON-RPC 2.0 over stdio on the wire the kit's MCP servers share (kitlib.mcpwire).

A tool's failure comes back as a tool result with isError true and {ok: false, error: {code,
message}}, never as a crashed server. An add-on error (the code raised in Blender) is a result
with isError true that keeps the add-on's reply, its traceback included.

Server(bridge).handle(message) -> the response (or None for a notification); run(rfile, wfile)
serves until rfile ends, with the bridge's watcher running, and then closes every Blender.
"""
import json
import traceback

from kitlib import mcpwire

from . import IDLE_S, SERVER_NAME, TOOL_NAMES, addon, codes
from .instance import Bridge, BridgeError, WAIT_MAX_S

SERVER_VERSION = '0.1.0'
PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')     # newest first
CALL_MAX_S = 3600.0
EXEC_S = 120.0
RENDER_S = 300.0
INSPECT_S = 60.0
SIZE_MAX = 8192
UNKNOWN_TOOL = 'unknown_tool'

_HANDLE = {'type': 'string', 'description': 'The handle blender_open gave (its lock run id).'}
_TIMEOUT = {'type': 'number', 'description': 'Seconds the call may take, at most %d; past it the '
            'Blender is ended (the add-on cannot stop running code).' % CALL_MAX_S}


def _obj(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required),
            'additionalProperties': False}


# name -> (description, input schema, read only)
TOOLS = {
    'blender_open': (
        'Starts a headless Blender of your own for the worktree, booked through the machine lock '
        '(kind render, one gpu slot) and serving the Blender Lab MCP add-on on a port of its own '
        '(9877-9899). Returns its handle. It is closed by blender_close, after %d min with no call, '
        'or when this session ends. wait_s: how long to wait for the lock (default 0: refused at '
        'once when no slot is free).' % (IDLE_S // 60),
        _obj({'worktree': {'type': 'string', 'description': 'Your worktree, an absolute path; '
                           'Blender runs in it.'},
              'wait_s': {'type': 'number', 'description': 'Seconds to wait for the lock, at most '
                         '%d.' % WAIT_MAX_S}}, ['worktree']), False),
    'blender_list': (
        "This session's Blenders: open ones (handle, port, pid, lock run, calls, idle time) and "
        'the last ended ones with why they ended.',
        _obj({}), True),
    'blender_inspect': (
        'A summary of the Blender\'s open scene: objects (type, transform, dimensions, materials, '
        'modifiers, mesh counts; the first %d), camera, render engine and resolution, materials '
        'and collections.' % codes.OBJECT_LIMIT,
        _obj({'handle': _HANDLE}, ['handle']), True),
    'blender_render': (
        'Renders the scene to a PNG and returns its path (read it to see it). The scene\'s render '
        'settings are put back afterwards. With no camera named and none in the scene, a '
        'temporary camera frames every renderable object. engine: workbench (default, fast), '
        'eevee or cycles.',
        _obj({'handle': _HANDLE,
              'path': {'type': 'string', 'description': 'An absolute .png path (default: a new '
                       'file in the bridge\'s temp folder).'},
              'width': {'type': 'integer', 'description': 'Pixels, 16-%d (default 1280).' % SIZE_MAX},
              'height': {'type': 'integer', 'description': 'Pixels, 16-%d (default 720).' % SIZE_MAX},
              'engine': {'type': 'string', 'enum': sorted(codes.ENGINES)},
              'camera': {'type': 'string', 'description': 'A camera object to render from.'},
              'timeout_s': _TIMEOUT}, ['handle']), False),
    'blender_exec': (
        'Runs Python (bpy) in the Blender. Set `result` to a JSON-serialisable dict to get it '
        'back; print output comes back as stdout. An exception comes back as an error with its '
        'traceback. Default limit %d s.' % EXEC_S,
        _obj({'handle': _HANDLE, 'code': {'type': 'string', 'description': 'Python to run.'},
              'timeout_s': _TIMEOUT}, ['handle', 'code']), False),
    'blender_close': (
        'Stops the Blender and releases its lock booking (unsaved work is lost: save with '
        'blender_exec first).',
        _obj({'handle': _HANDLE}, ['handle']), False),
}
assert tuple(TOOLS) == TOOL_NAMES


class ToolError(Exception):
    def __init__(self, code, message, field=None):
        Exception.__init__(self, message)
        self.error = {'code': code, 'message': message, 'field': field}


def _is(v, t):
    if t == 'integer':
        return isinstance(v, int) and not isinstance(v, bool)
    if t == 'number':
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    py = {'string': str, 'boolean': bool, 'object': dict, 'array': list}.get(t)
    return py is not None and isinstance(v, py)


def _args(name, args):
    """Checks args against the tool's schema: an object, its required keys, no other keys, the
    simple types and enums."""
    props = TOOLS[name][1]['properties']
    if not isinstance(args, dict):
        raise ToolError('invalid', 'arguments must be an object')
    for k in args:
        if k not in props:
            raise ToolError('invalid', 'unknown argument %s' % k, k)
    for k in TOOLS[name][1]['required']:
        if k not in args:
            raise ToolError('invalid', '%s is required' % k, k)
    for k, v in args.items():
        if not _is(v, props[k]['type']):
            raise ToolError('invalid', '%s must be %s' % (k, props[k]['type']), k)
        if 'enum' in props[k] and v not in props[k]['enum']:
            raise ToolError('invalid', '%s must be one of %s' % (k, ', '.join(props[k]['enum'])), k)
    return args


def _timeout(a, default):
    t = a.get('timeout_s', default)
    if not 0 < t <= CALL_MAX_S:
        raise ToolError('invalid', 'timeout_s must be more than 0 and at most %d' % CALL_MAX_S,
                        'timeout_s')
    return float(t)


def _size(a, k, default):
    v = a.get(k, default)
    if not 16 <= v <= SIZE_MAX:
        raise ToolError('invalid', '%s must be 16 to %d' % (k, SIZE_MAX), k)
    return v


def _reply(reply):
    """The add-on's reply as a tool result: ok -> {ok: true, ...}; error -> isError."""
    out = {'ok': reply.get('status') == 'ok'}
    out.update({k: v for k, v in reply.items() if k != 'status'})
    return out, not out['ok']


class Server:
    """The tools over one Bridge (None: one on the machine's lock with the real Blender)."""

    def __init__(self, bridge=None):
        self.bridge = bridge or Bridge()

    def tool_list(self):
        return [{'name': n, 'description': d, 'inputSchema': s,
                 'annotations': {'readOnlyHint': ro, 'destructiveHint': n == 'blender_close',
                                 'openWorldHint': False}}
                for n, (d, s, ro) in TOOLS.items()]

    def call(self, name, args):
        """(payload, is_error) for one tool call. Never raises."""
        if name not in TOOLS:
            return {'ok': False, 'error': {'code': UNKNOWN_TOOL, 'message': 'unknown tool %s'
                                           % (name,), 'field': 'name'}}, True
        try:
            a = _args(name, args if args is not None else {})
            return getattr(self, '_t_' + name)(a)
        except ToolError as e:
            return {'ok': False, 'error': e.error}, True
        except BridgeError as e:
            return {'ok': False, 'error': {'code': e.code, 'message': e.message,
                                           'field': None}}, True
        except Exception as e:          # a bridge bug must not take the server down
            return {'ok': False, 'error': {'code': 'internal', 'message': '%s: %s' % (
                type(e).__name__, e), 'field': None, 'trace': traceback.format_exc(limit=5)}}, True

    def _t_blender_open(self, a):
        return {'ok': True, 'blender': self.bridge.open(a['worktree'], a.get('wait_s', 0))}, False

    def _t_blender_list(self, a):
        out = {'ok': True}
        out.update(self.bridge.list())
        return out, False

    def _t_blender_inspect(self, a):
        return _reply(self.bridge.call(a['handle'], codes.INSPECT, INSPECT_S))

    def _t_blender_render(self, a):
        return _reply(self.bridge.render(
            a['handle'], a.get('path'), _size(a, 'width', 1280), _size(a, 'height', 720),
            a.get('engine', 'workbench'), a.get('camera'), _timeout(a, RENDER_S)))

    def _t_blender_exec(self, a):
        return _reply(self.bridge.call(a['handle'], a['code'], _timeout(a, EXEC_S)))

    def _t_blender_close(self, a):
        return {'ok': True, 'blender': self.bridge.close(a['handle'])}, False

    # ---------------------------------------------------------------- JSON-RPC
    def handle(self, msg):
        """One JSON-RPC message (or a batch list) -> the response, or None for notifications."""
        if isinstance(msg, list):
            out = [r for r in (self.handle(m) for m in msg) if r is not None]
            return out or None
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or not isinstance(
                msg.get('method'), str):
            return _err(msg.get('id') if isinstance(msg, dict) else None, -32600,
                        'invalid request')
        mid, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
        if 'id' not in msg:
            return None
        if not isinstance(params, dict):
            return _err(mid, -32602, 'params must be an object')
        if method == 'initialize':
            want = params.get('protocolVersion')
            return _ok(mid, {'protocolVersion': want if want in PROTOCOLS else PROTOCOLS[0],
                             'capabilities': {'tools': {'listChanged': False}},
                             'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
                             'instructions': 'Headless Blenders of your own (docs/blender-mcp.md), '
                                             'each booked through the machine lock. Open one '
                                             'with blender_open, close it with blender_close. '
                                             'Blender Lab MCP add-on v%s.' % addon.ADDON_VERSION})
        if method == 'ping':
            return _ok(mid, {})
        if method == 'tools/list':
            return _ok(mid, {'tools': self.tool_list()})
        if method == 'tools/call':
            name = params.get('name')
            if name not in TOOLS:
                return _err(mid, -32602, 'unknown tool %s' % (name,))
            payload, is_error = self.call(name, params.get('arguments'))
            text = json.dumps(payload, ensure_ascii=False, default=str)
            return _ok(mid, {'content': [{'type': 'text', 'text': text}], 'isError': is_error,
                             'structuredContent': json.loads(text)})
        return _err(mid, -32601, 'method not found: %s' % method)


_ok, _err = mcpwire.ok, mcpwire.err


def run(rfile, wfile, server=None):
    """Serves `server` (default: Server()) on rfile/wfile until rfile ends, with its bridge's
    watcher running, then closes every Blender it opened."""
    server = server or Server()
    server.bridge.start_watcher()
    try:
        mcpwire.serve(server, rfile, wfile)
    finally:
        server.bridge.shutdown()
