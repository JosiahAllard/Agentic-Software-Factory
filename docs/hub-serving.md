# Action Hub: serving it

## Serving it

- `hub.py setup` makes the owner's and the agent's token files where they are absent and prints where
  they are; it never writes the machine config.
- `hub.py serve` serves until Ctrl+C or `hub.py stop`, either of which commits the last changes and
  releases the store lock. The port is `hub.port` of the machine config (default 8790); the store is
  `hub.store`, else `hub/` in the PM data worktree (docs/pm-data-branch.md). `--store PATH` serves a
  scratch copy with git sync off and no push notifications. The server needs no `hub` key: without one
  every default holds, so a trial run with `--store` never writes the key; the owner writes it only for
  the live store (docs/go-live.md "Switches"). The PM session-start checklist (docs/pm-start.md) runs its
  site checks with the key's settings or the defaults; writing the key does not switch it.
- `hub.py serve` raises itself to normal priority at start (`kitlib.proc.process.raise_to_normal`), never lowering it or going above normal: the autostart task runs at Task Scheduler's default priority 7, below normal, and the git calls it starts would inherit that and starve while runs keep every core busy. A raise that fails is one NOTE line.
- `hub.py status` prints the running server's health, one line each: `revision N`, `store ...`,
  `git: ...` (`running, branch B, nothing pending`, or what is pending, a failed pass, or `git sync is
  off on this server`), `backlog <repo>: fetched X ago`, `code: current` or `code: stale: ` and the
  restart ("Stale code" below), `jobs: ` with each timed job's `<name> runs R, failures F`, `, running`
  while it runs, and its error while it is failing or else its last note (cut to 60 characters,
  `serve.JOB_NOTE_MAX`), or `jobs: none` (docs/hub-push.md "Timed jobs" under push notifications), the tunnel line, and
  `unavailable: ...` for a view the server could not read. A server older than the `code` or `jobs`
  field prints no such line. The tunnel line is `tunnel: on` (the Access keys were fetched),
  `tunnel: bound, keys not fetched` (until the first request with a token; a failed fetch adds ` (reason)`,
  such as `keys-fetch`) or `tunnel: off: <why>`. `on` does not say that a tunnel program reaches the
  listener.
- **Stale code.** A running `hub serve` compares the `.py` files of `kitlib/hubsite`, of the timed
  jobs' `kitlib/hubjobs` and of the two packages it reads its machine settings through,
  `kitlib/pmdata` and `kitlib/config` (name, modified time, size; `serve.code_stamp`,
  `serve.code_folders`), with what they were at its start, at most every 30 seconds (`CODE_LOOK_S`).
  When they differ, a server run by its autostart task restarts itself
  (`kitlib.hubsite.selfrestart`):
  1. It logs a note that the code changed and asks, once per new code, whether it is the task's
     server: the machine config's `hub.port` and store are its own, and the scheduled task is
     `Running`. A server started by hand, with no task, or on a scratch store or another port only
     warns, as below.
  2. It waits until the code has stayed the same for 90 s (`SETTLE_S`), so a landing that is still
     writing the tree is not caught half way.
  3. It runs a self-check in a child python, at most 120 s: it imports the new hub code (a timed job's
     own module aside: the new server serves without a job that fails, with a start-up note), runs
     `tools/hub.py`'s top level, and builds, starts and asks GET health of a hub on a temp store and
     temp token folder.
  4. It waits until no request is in flight on its listeners (an answer being written is let finish;
     a long `wait` does not hold it) and no timed job is running.
  5. It starts a detached restarter, which runs `hub.py restart --port <port>`: the server's clean
     close, then the task started again (the `restart` bullet below). Before it starts it, the server
     logs `restarting itself: code <old> -> <new>` (the old and new code stamps) to the hub log;
     once it has started, it logs the same line with the restarter's pid (`...; restarter pid <pid>
     ...`).

  The restarter's output goes to `<PM repo>/.pm-kit/hub/self-restart.log`, beside the token
  files (`hub.py setup` prints that folder; git-ignored), overwritten by each attempt. Read it when
  a restart left no hub. A failed self-check or a restarter that cannot be started keeps the old
  server and shows the warning below; that code is never tried again by that server, so a broken
  tree does not loop, and only newer code is tried. Once a restarter has started, that server tries
  no more restarts at all, newer code included; one still running 6 minutes later keeps the old code
  and shows the warning, and the restart is then the owner's. While a restart is under way the warning
  stays hidden, so no restart to-do is filed for it; 15 minutes after the first change with no restart
  started (the code still changing, or a request always in flight) it shows, and the restart still
  happens when it can.

  The warning: one error line in the hub log and a `code` object, `{"stale", "restart"}`, in GET
  health (the local listener; the tunnel's fixed health carries none) and GET snapshot
  (`kitlib.hubsite.codestale`). `restart` is the owner's to-do: `python <kit>/tools/hub.py restart`,
  or, with no autostart task, `hub.py stop` then `hub.py serve`. Three places show it: the page's
  banner (`#codeerr`), a `hub: WARN` line in the PM session-start checklist (docs/pm-start.md), and a
  `HUB` event of pm_watch (docs/pm-watch.md), said once per stale episode. `hub.py status` prints it as
  its `code:` line. `web/index.html` is read from disk per request (`httpd.static_file`), so a
  page-only change needs no restart. Known limits: a restarter that stops the server and then cannot
  start the task, or whose new server never answers, leaves no hub (`self-restart.log` says why; start
  it with `hub.py restart`, or as that log says). The self-check proves the new code builds and answers
  on a scratch store, not that the machine's store and settings serve. The restarter runs with no
  console window, so its output is only in that log. `tools/hub.py` itself is not in the stamp (a
  change there alone neither warns nor restarts), nor are other packages the hub imports beyond those
  four folders.
- Remote access is only through the tunnel listener: with `hub.access` set (an Access `team`, the
  application's audience tag `aud` and the allowed `emails`; all three), the server also listens on
  `hub.tunnel_port` (default 8791, loopback too) for a Cloudflare tunnel, and every request there needs a
  Cloudflare Access token in the header `Cf-Access-Jwt-Assertion` (`kitlib.hubsite.tunnel`). The tool
  never runs the tunnel program. Both listeners bind the loopback address only, and `hub.port` and
  `hub.tunnel_port` must differ. If `hub.access` is incomplete the tunnel stays off and the local
  listener keeps serving; `status` says why. The owner's guide to reach the hub from a phone:
  docs/hub-remote-access.md. A valid Access token also makes the caller the strong owner, the only
  caller a route of role `approver` (`kitlib.hubsite.api.APPROVER`) answers; from it the tunnel's
  `/api/v1/pair/strong` makes a one-time link that pairs the PC's browser as the strong owner on the
  local listener, a session kept on disk under a key derived from the owner token and `approval.key`,
  which the server makes beside the tokens at start (docs/hub-remote-access.md "The strong owner"). The
  health answer through the tunnel holds fixed words only: `ok`, `version`, `store` (`ok`), `git` (`ok`,
  `failed`, `off` or `unknown`) and `tunnel` (`on` or `bound, keys not fetched`); no folder, branch,
  reason or revision. A health answer the server cannot give leaves as its status and error code with
  the message `health could not be answered`.
- Autostart (opt-in): `python tools/hub.py setup --autostart` registers a scheduled task that runs `hub
  serve` at logon with no console window (on Linux, a systemd user service, else an XDG autostart entry);
  `--whatif` shows what it would do and changes nothing. Without the flag nothing is registered. It is
  refused, with nothing registered, when no store folder is set, from a kit worktree or a plugin export
  folder (it must be the main checkout's `tools/hub.py`), or when no `pythonw.exe` sits beside the
  python. Ctrl+C does not apply to the windowless server: `hub.py restart` stops it cleanly and starts the
  task again, and `hub.py stop` stops it (docs/hub-remote-access.md "8. Restart the hub"). A server cut off
  without a clean close leaves its store lock, which the next start takes over with a note.
- `hub.py stop [--port N] [--timeout S]` and `hub.py restart [--port N] [--timeout S]`
  (`kitlib.hubsite.control`) post `POST /api/v1/stop` (`httpd.STOP_ROUTE`) with the owner's Bearer
  token. The route takes that token only, never a browser session (403 `forbidden` otherwise), and only
  on the local listener: through the tunnel it answers 404 (403 `access` first without a valid Access
  token). It answers `ok`, `stopping`, and the server's `pid` and `ctime`, then the server closes as on
  Ctrl+C. `stop` waits until the port is free and that process has ended: `STOPPED: ...`, or `nothing to
  stop: ...` (exit 0) when nothing answers on the port. A server stopped a moment ago may still be
  closing then: it frees its port before its last git pass and the store's close. `restart` reads the
  scheduled task's state first and starts the task again only when the task ran the server it stopped
  (`Running` or `Queued`), or when nothing ran and the task is ready; then it waits until the new server
  answers on `hub.port` (`RESTARTED: ...`). A task that cannot be read or started after the stop prints
  the `STOPPED` line, why, and how to start the task (exit 1). A `restart` that starts nothing points at
  the task (`setup --autostart`, `Enable-ScheduledTask`, `Start-ScheduledTask`), never at `hub.py serve`:
  a server started in a console ends with that shell, the task's does not. Neither ever kills a process:
  past `--timeout` (default 300 s, approximate: one request in flight may run past it) they print `TIMED
  OUT: ...` and exit 4.
- `python tools/hub.py setup --check` writes nothing: it says whether `hub.access` is unset, set or
  cannot serve, fetches the Access keys once when it can serve (`access keys: ...`), and lists each kit
  copy on the machine that would refuse the `hub` key (`kitlib.config.copies`). It ends `READY: ...`
  (exit 0) or `NOT READY: ...` (exit 1). Run it before writing the `hub` key (docs/hub-remote-access.md
  "Before you start").
- `hub.py validate [--store PATH]` checks the store folder against the field tables in the other hub topic docs (docs/action-hub.md lists them), with no
  server, and prints `VALIDATE <LEVEL> <document> <field>: <message> [<code>]`.

The machine config's `hub` key: docs/kit-json.md "Machine config keys". Its settings are `port`,
`tunnel_port`, `store` (an absolute path), `commit_every_s`, `push`, `backlog_refresh_s` and `access`;
an unknown key is refused, except a leftover `relay`, which is ignored.

Exit codes of every verb: 0 ok, 1 refused (also a Ctrl+C other than the one that closes a running
server, or a server that did not close cleanly), 2 usage error or an unreadable machine config, 3 the
server is not running (the start command is printed), or a `restart` that started nothing (no task, a
disabled one, or a server started by hand: it says which and prints the start command), 4 a `wait
--timeout` that ran out, or a `stop` or `restart` that ran out of time (nothing was killed). A `wait`
whose server goes down after one of its polls was answered (a restart: the port refuses the
connection, or the hub answers `REFUSED store`) asks again each second (`client.RETRY_PAUSE_S`) from the
revision it last had, within its `--timeout`, or for up to 600 s down in a row (`client.RESTART_WAIT_S`)
when it has none; then it ends with that reason: 3 and the start command, or 1 (`REFUSED store: ...`). A
server already down at the wait's first poll ends it at once the same way. A connection dropped
mid-request still ends it with 1 (`REFUSED: port N did not answer as the hub does: ...`).

