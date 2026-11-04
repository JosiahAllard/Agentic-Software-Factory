"""A stand-in for a background Blender serving the add-on, for the bridge's tests: it listens on
127.0.0.1:--port (SO_REUSEADDR, as the add-on does), serves one request at a time (a JSON object
and a NUL each way), runs each request's code with exec and replies with its `result`, its
stdout, or the error and its traceback. It registers the add-on's server module, whose stop() ends
it with exit code 0 after the reply, as the add-on's does.

Options: --port N; --ready-delay S (wait S seconds before listening); --ignore-stop (stop() does
nothing); --exit-at-start CODE (exit at once with CODE); --pidfile PATH (write this pid there).
Standard library only; it imports nothing of the kit.
"""
import contextlib
import io
import json
import os
import socket
import sys
import time
import traceback
import types

SERVER_MODULE = 'bl_ext.user_default.mcp.mcp_to_blender_server'


def options(argv):
    o = {'port': None, 'ready_delay': 0.0, 'ignore_stop': False, 'exit_at_start': None,
         'pidfile': None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--ignore-stop':
            o['ignore_stop'] = True
            i += 1
            continue
        key = a[2:].replace('-', '_')
        if key not in o or i + 1 >= len(argv):
            raise SystemExit('fakeblender: unknown option %s' % a)
        o[key] = argv[i + 1]
        i += 2
    return o


def register(state, ignore):
    def stop():
        if not ignore:
            state['stop'] = True
    parts = SERVER_MODULE.split('.')
    for n in range(1, len(parts) + 1):
        sys.modules.setdefault('.'.join(parts[:n]), types.ModuleType('.'.join(parts[:n])))
    sys.modules[SERVER_MODULE].stop = stop


def run(code):
    ns = {'__name__': '__fake_blender__'}
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(code, '<mcp>', 'exec'), ns)
        reply = {'status': 'ok', 'result': ns.get('result')}
    except Exception as e:
        reply = {'status': 'error', 'message': '%s: %s\n%s' % (type(e).__name__, e,
                                                               traceback.format_exc())}
    if out.getvalue():
        reply['stdout'] = out.getvalue()
    return reply


def serve(port, state):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(5)
    while not state['stop']:
        conn, _ = srv.accept()
        with conn:
            conn.settimeout(10)
            buf = b''
            try:
                while b'\0' not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except OSError:
                continue
            if b'\0' not in buf:
                continue
            req = json.loads(buf[:buf.index(b'\0')].decode('utf-8'))
            reply = run(req.get('code', ''))
            conn.sendall(json.dumps(reply, default=str).encode('utf-8') + b'\0')
    srv.close()


def main(argv):
    o = options(argv)
    if o['pidfile']:
        with open(o['pidfile'], 'w') as f:
            f.write(str(os.getpid()))
    if o['exit_at_start'] is not None:
        return int(o['exit_at_start'])
    time.sleep(float(o['ready_delay']))
    state = {'stop': False}
    register(state, o['ignore_stop'])
    serve(int(o['port']), state)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
