"""kitlib.hubsite.localask: one request to the hub server on this machine, and what it raises.

The light half of serve.py: the client (client.py) needs only this to ask the loopback listener,
so it does not import the server. serve.py imports every name here and re-exports it (serve.ask,
serve.ServeError, serve.NotRunning, serve.NoAnswer, serve.ASK_TIMEOUT_S, serve.VIA_CLI,
serve.VIA_MCP and the rest): the one class or function, reached by either module.

  ask(port, method, path, token=None, body=None, timeout=ASK_TIMEOUT_S, via=VIA_CLI)
      -> (status, object)
  ServeError, NotRunning, NoAnswer
"""
import http.client
import json
import socket

from . import httpd

ASK_TIMEOUT_S = 10
MAX_ANSWER = 16 << 20           # bytes of one answer ask() reads
VIA_CLI = 'cli'
VIA_MCP = 'mcp'                 # the hub MCP server's writes (kitlib.hubmcp, docs/hub-mcp.md)
USER_AGENT = 'hub-client'       # what ask() calls itself: it names no person and no machine


class ServeError(Exception):
    """The server cannot start, or a local request to it failed. The text is for the owner's
    console and may name a folder."""


class NotRunning(ServeError):
    """Nothing listens on the port: the hub server is not running there."""

    def __init__(self, port):
        ServeError.__init__(self, 'no hub server is listening on port %d' % port)
        self.port = port


class NoAnswer(ServeError):
    """The connection was made and the request sent, and no answer came in the call's time:
    a long-poll (client.Client.wait) asks again; any other caller reads it as a ServeError."""


def ask(port, method, path, token=None, body=None, timeout=ASK_TIMEOUT_S, via=VIA_CLI):
    """One request to the hub server on this machine's `port`: (status, the JSON object it
    answered). `token` is sent as a Bearer token; a POST carries `body` as JSON with the Origin
    and Content-Type the listener requires and names itself `via` (VIA_CLI by default; the hub
    MCP server passes VIA_MCP). NotRunning when nothing listens there, NoAnswer (a ServeError)
    when the connection was made and `timeout` passed with no answer, ServeError when what
    answers is not the hub or the connection could not be made."""
    headers = {'Accept': httpd.JSON, 'User-Agent': USER_AGENT}
    if token:
        headers['Authorization'] = 'Bearer %s' % token
    data = None
    if method == 'POST':
        data = json.dumps({} if body is None else body).encode('utf-8')
        headers.update({'Content-Type': httpd.JSON, httpd.VIA_HEADER: via,
                        'Origin': 'http://%s:%d' % (httpd.LOOPBACK, port)})
    conn = http.client.HTTPConnection(httpd.LOOPBACK, port, timeout=timeout)
    connected = False
    try:
        conn.connect()
        connected = True
        conn.request(method, path, body=data, headers=headers)
        answer = conn.getresponse()
        status, raw = answer.status, answer.read(MAX_ANSWER + 1)
    except ConnectionRefusedError:
        raise NotRunning(port)
    except (OSError, http.client.HTTPException) as e:
        why = 'port %d did not answer as the hub does: %s' % (port, type(e).__name__)
        if connected and isinstance(e, socket.timeout):
            raise NoAnswer(why)
        raise ServeError(why)
    finally:
        conn.close()
    try:
        obj = json.loads(raw.decode('utf-8')) if len(raw) <= MAX_ANSWER else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        raise ServeError('port %d answered %d with something that is not the hub\'s JSON'
                         % (port, status))
    return status, obj
