"""kitlib.hubsite: the Action Hub as an agent-neutral site.

  store    the hub store: one JSON file per document, one writer, ids, revision, events, pending
  schema   the fields each collection's documents carry, checked before every write
  auth     who a request on the local listener is: the owner token, the pairing code, the session cookie
  access   the Cloudflare Access token check for the tunnel listener: verify, KeyCache
  approvals the approvals a request waits on: asked, decided, verified
  api      the site's JSON API: routes, roles, and the writes behind them
  apiask   the api's question routes: list, ask, edit, withdraw and bind to a record
  apibase  what the api's handlers share: role and status constants, Route, Request, the request readers
  apilock  the api's machine-lock routes: the lock reads and owner writes, each a handler
  apifindings the api's finding routes: the maintenance lint's findings and the owner's marks
  apiskills the api's skill-edit routes: the owner's edits of a kit skill, draft to committed
  apirecord the checks a bound ask shares: the record it is bound to, an agent's claim
  apitodos the api's to-do routes: add, steps, done, edit and withdraw
  apiposts the api's routes for the owner's notes to the PM
  apiapprovals the api's approval routes: add, decide, consume, withdraw, supersede, verify
  apipark  the api's parking of a record on a bound ask, and the sweep of lines whose ask is gone
  apipages the api's page routes: publish, place, read and delete an HTML page and its card
  apilog   the api's log routes: the inbox, the landing feed and the hand-off records
  apisched the api's schedule routes: the owner's schedules for the kit's automatic tasks
  apiread  the api's read routes: backlog, records, pending, wait, snapshot and health, and the owner's record writes
  lockdoor the api's door to the machine lock, in-process: revision, view, stats, owner writes
  skilledits the checks of an owner's skill edit: its text, its blob, the branch it names
  derived  views read from files other tools write (notices, repos, usage, agents, skills), never stored
  repoweb  a repository's web address, main branch and root names, for the page's repo-path links
  backlog  the backlog views the site shows
  recordwrite the owner's add, edit and delete from the record page, through the backlog store
  gitsync  the store's commits, and its pull and push against the PM data remote
  httpd    one loopback HTTP listener in front of the api: the gate, the write rules, the waits
  tunnel   the listener a tunnel program points at: its Cloudflare Access gate, its write source
  push     the push notifications: an answer or a done to-do told to its session and the PM
  jobs     the timed jobs' runner (the jobs: hubjobs): thread, cadence, failures in health
  plugin_job the timed job that refreshes the pm-kit plugin export when kit main moves
  serve    the server: the store, the api, the views and the listeners, started and closed
  servesources the server's sources: the machine config's folders, sessions, sync and jobs
  serveviews the Api's views over the sources: backlog, completed, repos, usage, agents, skills
  servehealth GET health's fixed and owner answers, and the status lines hub.py status prints
  serverepos the kit checkout and repository names the skill and agent pages are given
  storecheck the store folder's findings as on disk, read only, no lock (hub.py validate)
  autostart the opt-in scheduled task that starts the server at sign-in (hub.py setup --autostart)
  control  hub.py stop and restart: ask the server to close, wait for it, start the task again
  selfrestart a task-run server's restart on new code: settle, self-check, wait for quiet, restart
"""
