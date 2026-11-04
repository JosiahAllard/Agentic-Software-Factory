"""The Blender Lab MCP add-on's socket protocol, as observed (docs/blender-mcp.md "The add-on"):
raw TCP on 127.0.0.1, one request per connection, a JSON object and a NUL byte each way.

    request:  {"type": "execute", "code": <python>, "strict_json": true} NUL
    response: {"status": "ok", "result": ..., "stdout"?: ..., "stderr"?: ...} NUL
              {"status": "error", "message": ..., ...} NUL

The add-on serves one request at a time and puts no limit on how long the code runs, so every
call here has a deadline of its own: past it, CallTimeout, and the caller decides what happens to
the Blender (kitlib.blendermcp.instance ends it). An idle connection is dropped by the add-on
after about 10 s, so nothing here connects without sending at once.
"""
import json
import socket
import time

HOST = '127.0.0.1'
END = b'\0'
MAX_REPLY = 64 * 1024 * 1024        # a reply larger than this is refused, never buffered on
CONNECT_S = 5.0


class WireError(Exception):
    """The call did not get a reply: refused, closed early, or the reply was not one."""


class CallTimeout(WireError):
    """The call's deadline passed with no whole reply."""


def request(code, strict_json=True):
    """The request bytes for `code`, NUL included."""
    if not isinstance(code, str):
        raise TypeError('code must be a string')
    body = json.dumps({'type': 'execute', 'code': code, 'strict_json': bool(strict_json)},
                      ensure_ascii=True)
    return body.encode('ascii') + END


def parse(raw):
    """The reply object of the bytes before the NUL. WireError when it is not a JSON object with
    a status."""
    try:
        reply = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise WireError('the reply is not JSON: %s' % e)
    if not isinstance(reply, dict) or reply.get('status') not in ('ok', 'error'):
        raise WireError('the reply is not an add-on reply: %.200r' % (reply,))
    return reply


def call(port, code, timeout_s, host=HOST, connect_s=CONNECT_S, clock=time.monotonic):
    """The add-on's reply to `code` on `port`: one connection, one request, read up to the NUL.
    CallTimeout when the whole call (connect included) takes past timeout_s; WireError when the
    connection is refused or closed before a whole reply, or the reply is not one."""
    deadline = clock() + timeout_s
    try:
        s = socket.create_connection((host, port), timeout=min(connect_s, max(timeout_s, 0.01)))
    except socket.timeout:
        raise CallTimeout('no connection to %s:%d within %.1f s' % (host, port, connect_s))
    except OSError as e:
        raise WireError('no connection to %s:%d: %s' % (host, port, e))
    try:
        try:
            s.settimeout(max(deadline - clock(), 0.01))
            s.sendall(request(code))
            buf = bytearray()
            while True:
                left = deadline - clock()
                if left <= 0:
                    raise CallTimeout('no whole reply within %.1f s' % timeout_s)
                s.settimeout(left)
                chunk = s.recv(65536)
                if not chunk:
                    raise WireError('the connection closed before a whole reply (%d bytes)'
                                    % len(buf))
                buf.extend(chunk)
                end = buf.find(END)
                if end >= 0:
                    return parse(bytes(buf[:end]))
                if len(buf) > MAX_REPLY:
                    raise WireError('the reply passed %d bytes with no end' % MAX_REPLY)
        except socket.timeout:
            raise CallTimeout('no whole reply within %.1f s' % timeout_s)
        except OSError as e:
            raise WireError('the connection failed: %s' % e)
    finally:
        s.close()
