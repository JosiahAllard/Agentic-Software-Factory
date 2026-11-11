"""kitlib.hubsite.cli: the work of each tools/hub.py verb. The tool parses (tools/_hub_args.py),
calls the verb here with the parsed arguments and `out`, and exits with the code it returns.

  common    what the verbs share: the hub client, the printed line, a write's answer, START
  server    serve, status, open, unpair, stop and restart, and the server's log lines
  task      the scheduled task or Linux job that starts the server, in words and commands
  setup     setup (--autostart, --whatif, --check) and validate
  asks      questions, answer, todos, stories, to-pm, inbox, handoffs, pending and wait
  pages     pages and skill-edit
  approvals approvals: list, show, add, apply, withdraw and supersede

Each verb is `verb(a, out, stop=None)`: `a` the parsed arguments, `out` the tool's writer of its
lines, `stop` the event that ends a serving server. `out` has four forms: line(text, err=False)
a plain print (stdout, or stderr with err=True); put(text) a print whose character the console
cannot show is written as its escape (common.put, the lines of a verb's answer); raw(text)
`pages show`'s write of a page; say(text, err=False) serve's form, flushed, which loses the line
to a closed console and nothing else. It returns the exit code; the tool maps a refusal it
raises to its line and code. Nothing here prints or exits itself. Each module's docstring
says what its verbs print.

The verbs of asks, pages and approvals ask the running server through kitlib.hubclient.client
(common.hub). Each takes --port N
(default: hub.port) and --json, which prints the server's answer as one JSON line in place of
the lines named here. A verb marked (owner) carries the owner's token, the others the agent's;
the server alone decides what a role may do.

`--choice N` and `--recommended N` are the option's place in the question's list, counted from
0 as the api counts it. `questions log`, `questions done`, `stories set` and each `edit` and
`withdraw` (`approvals withdraw` too) need --if-version V: the version of the document the caller
read and decided on, which the `questions` and `stories` lines show as `v2` and --json
(`todos --json` too) as `version`. Without it the verb is a usage error and asks nothing; with a
version the document no longer has, the server refuses the write (`REFUSED conflict`).

Exit codes: 0 ok; 1 refused (`REFUSED: ...`, or `REFUSED <code>: <message>` when the server
refused the call), a store with an error finding, a server that did not close cleanly, or a
Ctrl+C other than the one that closes a running server; 2 usage error or a machine config that
cannot be read; 3 the server is not running (the start command is printed), or `restart`
started nothing; 4 `wait --timeout` ran out with nothing to say, or `stop` or `restart` ran out
of time (nothing was killed).
"""
