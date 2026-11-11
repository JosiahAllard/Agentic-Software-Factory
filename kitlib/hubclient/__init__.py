"""kitlib.hubclient: everything another package needs to talk to the hub server on this machine
(docs/modules/hub-design.md "The split"). It never imports the server (kitlib/hubsite/).

  settings    the machine config's `hub` key: port, store, access (load, settings)
  token       where the token files live and reading one (folder, read_token)
  localask    one request to the loopback listener (ask)
  errors      what a call raises (ServeError, NotRunning, NoAnswer, Refused, NoHubToken,
              NoHubPointer)
  client      the client (Client, hub_client, exit_code, part)
  push        the words of a push notification (Sent, WRITTEN, UNCERTAIN, PM_KEY, field)
  codestale   whether the running server's code is older than the checkout's (notice, text)
"""
