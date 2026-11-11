"""kitlib.hubclient.localask: one request to the hub server on this machine.

What the client (client.py) needs to ask the loopback listener, so it does not import the
server. kitlib/hubsite/serve.py re-exports ask and its constants (serve.ask, serve.ASK_TIMEOUT_S,
serve.VIA_CLI, serve.VIA_MCP and the rest), and kitlib/hubsite/httpd.py takes LOOPBACK, JSON and
VIA_HEADER from here: the one object, reached by either module.

  ask(port, method, path, token=None, body=None, timeout=ASK_TIMEOUT_S, via=VIA_CLI)
      -> (status, object); raises the errors of kitlib.hubclient.errors
"""
import http.client
import json
import socket

from .errors import NoAnswer, NotRunning, ServeError

LOOPBACK = '127.0.0.1'          # the one address the hub's local listener binds
JSON = 'application/json'
VIA_HEADER = 'X-Hub-Via'        # where a caller names its transport (kitlib/hubsite/httpd.py)
ASK_TIMEOUT_S = 10
MAX_ANSWER = 16 << 20           # bytes of one answer ask() reads
VIA_CLI = 'cli'
VIA_MCP = 'mcp'                 # the hub MCP server's writes (kitlib.hubmcp, docs/hub-mcp.md)
USER_AGENT = 'hub-client'       # what ask() calls itself: it names no person and no machine


def ask(port, method, path, token=None, body=None, timeout=ASK_TIMEOUT_S, via=VIA_CLI):
    """One request to the hub server on this machine's `port`: (status, the JSON object it
    answered). `token` is sent as a Bearer token; a POST carries `body` as JSON with the Origin
    and Content-Type the listener requires and names itself `via` (VIA_CLI by default; the hub
    MCP server passes VIA_MCP). NotRunning when nothing listens there, NoAnswer (a ServeError)
    when the connection was made and `timeout` passed with no answer, ServeError when what
    answers is not the hub or the connection could not be made."""
    headers = {'Accept': JSON, 'User-Agent': USER_AGENT}
    if token:
        headers['Authorization'] = 'Bearer %s' % token
    data = None
    if method == 'POST':
        data = json.dumps({} if body is None else body).encode('utf-8')
        headers.update({'Content-Type': JSON, VIA_HEADER: via,
                        'Origin': 'http://%s:%d' % (LOOPBACK, port)})
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
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
