"""kitlib.hubclient.errors: what a call to the hub server on this machine raises.

  ServeError    the server cannot start, or a local request to it failed
  NotRunning    nothing listens on the port (a ServeError)
  NoAnswer      the request was sent and no answer came in the call's time (a ServeError)
  Refused       the hub answered with an error status (a ServeError)
  NoHubToken    neither token file (agent or owner) is there to read (client.hub_client)
  NoHubPointer  no PM-repo pointer is set, so there is no token folder (client.hub_client)

kitlib/hubsite/serve.py re-exports ServeError: the one class, reached by either module.
"""

REFUSAL_UNKNOWN = 'not the expected answer'   # a refusal's message when the body gives none


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


class Refused(ServeError):
    """The server answered a call with a refusal: its `code`, `message`, `field`, HTTP `status`
    and whole `body` are attributes."""

    def __init__(self, status, body):
        err = body.get('error') if isinstance(body, dict) else None
        err = err if isinstance(err, dict) else {}
        self.status = status
        self.body = body
        self.code = str(err.get('code') or status)
        self.message = str(err.get('message') or REFUSAL_UNKNOWN)
        self.field = err.get('field')
        ServeError.__init__(self, '%s: %s' % (self.code, self.message))


class NoHubToken(Exception):
    """Neither token file of the hub (agent or owner) is there to read."""


class NoHubPointer(Exception):
    """No PM-repo pointer is set, so the hub has no token folder to look in."""
