# Action Hub: lock routes, views and the record page

## Machine lock routes
The hub server reads and changes the machine lock in-process through `kitlib.lock.api` (the owner's
decision), for the schedule page. `kitlib/hubsite/apilock.py` docstring "Machine lock" is the source; the
server's door to the lock is `kitlib/hubsite/lockdoor.py`, made by `servesources.machine_sources` and first
opening the lock on its first call.
- Three reads, all the owner's: `GET /api/v1/lock/view`, `GET /api/v1/lock/stats` and `GET
  /api/v1/lock/wait`. An agent token, or no token, is refused every one with 403 `forbidden` (field
  `role`) on the local and the tunnel listener alike, before the lock is read. The lock MCP server keeps
  no write path, by the owner's decision.
- `lock/view` answers `{lock_revision, view, writer, writes}`: the schedule window's view as JSON
  (`kitlib.lockui.model.to_json`): holds, runs, the strip, lanes, details, cores, memory, warnings, the
  game slots and the CPU division. `queue_items` is the line behind the runs (id, position, repo, slug,
  kind, what it needs, estimated start, why it waits) and each finished run in the strip carries its
  `slug`. One allow-list of keys serves both listeners: no pid, process tree, log path, session, store
  folder or run note is in it.
- `lock/stats?days=N&since=T&until=T` is `kitlib.lock.api.stats`: `days` 1 to 366, the times ISO with
  their offset; a bad one is 400 `invalid` naming the field.
- `lock/wait?since=REV&timeout=S` answers the view plus `changed` once the lock's revision differs from
  `since` (default: the revision now), or after `timeout` (at most 55 seconds) with `changed: false`. It
  looks at the revision every half second. It takes one of the listener's wait slots like `GET wait`: past
  `max_waits` it is 429 `busy` with a Retry-After. An agent takes no slot: it gets the 403.
- Through the tunnel the view is the same, except that a warning that could name a file on this machine
  (an unreadable `queue.json`, holds file or kinds file, a layout problem) is a fixed phrase
  (`model.REMOTE_SAYS`). Only the tunnel listener's own `via` asks for that; a local client that sends the
  tunnel's word gets the local view.
- With no lock door, or a lock that cannot be read, a route answers 503 `unavailable` (field `lock`),
  naming only the error's class, never its text.
- `writer` is `owner` when the lock takes the hub's own process for the owner, else `agent` (a hub started
  under an agent session). `writes` is `{lock, strong, why}` for this caller: `lock` true when it may make
  the `lock-write` writes, `strong` true when it may make every write, and `why` the reason it may not,
  for the page to show.

Eight writes, by the owner's decision. Each answers `{ok, result}` plus the view after the write
(`lock_revision`, `view`, `writer`, `writes`):

| Route (POST `/api/v1/...`) | Body | Role |
|---|---|---|
| `lock/holds` | `{kind: reserve, scope, start, end, note}` or `{kind: hold, scope, start, end, note, cpu, gpu}` | approver |
| `lock/holds/{id}/edit` | `{start, end, note}`, each optional | approver |
| `lock/holds/{id}/end` | `{}` | approver |
| `lock/division` | `{fields}` or `{defaults: true}` | approver |
| `lock/runs/{id}/end` | `{confirm}`, which repeats the run id | lock-write |
| `lock/runs/{id}/top` | `{}` | lock-write |
| `lock/game_mode` | `{enabled}` | lock-write |
| `lock/games` | `{text}`, the Games dialog's lines | lock-write |

- `lock-write` (`kitlib.hubsite.api.LOCK_WRITE`) is the owner on the PC's listener, and only the strong
  owner through the tunnel. `approver` is the strong owner on either listener (a browser paired as the
  strong owner, or the Access login; docs/hub-remote-access.md "The strong owner"). An agent may make no
  write. Warning: the local listener takes the owner token for a lock writer, so a future relay that
  forwards remote requests to the local listener must close that path.
- The page makes owner holds only; it may edit or end any hold. A reserve's `start` and `end` are ISO
  times with their offset. A hold's and an edit's typed times (`HH:MM`, `YYYY-MM-DD HH:MM`, `+2h`) are
  read in the PC's local zone, whatever zone the browser is in (`kitlib.lockui.actions`); an edit's
  missing note keeps the hold's own.
- The lock records the writer as `hub`, or `hub-tunnel` for the tunnel's calls. A hub whose `writer` is
  `agent` refuses every write with 403 `forbidden` (field `lock`) and the reason, before the lock is
  asked.
- The hub's writes run one at a time. The lock's refusals are the hub's codes: `invalid` 400 (the lock's
  field), `forbidden` 403 (field `lock`), `not_found` 404 (field `id`), `conflict` and the lock's `busy`
  409 (busy says to try again), a store failure 503 `unavailable`. Through the tunnel a store failure, and
  any message or note that names a folder, is a fixed phrase (`lockdoor.REMOTE_SAYS`).

## Views read live
Never stored; read from their sources (`kitlib.hubsite.derived`, `kitlib.hubsite.backlog`):
- backlog and completed: each repository's backlog store (`.kit.json` backlog.store), every open record
  and every record closed in the last 90 days, fetched every `hub.backlog_refresh_s`. Parked is the
  backlog items whose state is parked. A repository's folder is `pm_start.repo_paths[name]`, else the PM
  repository's sibling. While that timer runs, `GET /api/v1/snapshot` (the page's load) answers these two
  from what the timer's reads left, however old, and starts no git. A repository the first round has not
  reached shows with error "not read yet"; before any read the snapshot lists the view in `unavailable` as
  `NotReadYet`, which the page shows as "The backlog view is unavailable: NotReadYet". `GET
  /api/v1/backlog?fresh=1` runs a fetch round and reads live. With no timer (`backlog_refresh_s` unset) the
  snapshot reads as the routes do.
- usage: `launch.py usage` (docs/launcher.md "Usage").
- agents: the rendered agent definitions of the host the server runs for (`<host shell>/agents`,
  `servesources.agents_dir`).
- skills: the kit's skill sources, `<kit>/content/skills/<name>/SKILL.md` (`servesources.skills_dir`): each one's
  name (the folder's) and description (its front matter). `GET /api/v1/skills` and the snapshot carry
  just those; `GET /api/v1/skills/<name>` gives one skill's body, the name checked against the listed folders
  (an unknown or malformed name is a 404 or 400). Never a host's rendered copy.
- findings: the maintenance lint's last result of each repo, never in the snapshot
  (docs/hub-records.md "finding_marks/<fingerprint>").

### Documents named in a question

A question's context and its options' labels and descriptions show a named document as a link that opens in a new tab
(`target=_blank`, `rel="noopener noreferrer"`, so the tunnel origin is not sent as the Referer): an `http(s)` URL (any other scheme stays text), and a word that is a listed page's id (as a whole word, not inside a path),
which links to the hub at `#doc=<id>` (the page opens that document's reader once the lists are read). The links are
made as elements from the parsed URL or the encoded id, never from markup; everything else is a text node. A repo
file path is a link to the repository's own host, `<web>/blob/<branch>/<path>` (the repos entry's `web` and `branch`,
docs/hub-records.md "repos/<name>"), when the question's repo has them and the path has a `/`, one of the known extensions (`md`, `py`, `json`,
`html`, `css`, `js`, `txt`, `yml`, `yaml`, `toml`, `ps1`, `sh`), no empty, `.` or `..` part, and a first part that is a
name in `top`; otherwise it stays text. The link text is the path as written. The hub serves no file by path. Known
limits: the link is to the main branch, so a file that is not on it (a new file on an unlanded branch) is a 404 there,
and a private repository asks the viewer to sign in on the host; the hub cannot tell the two cases apart without a
request to the host.

## The record page
One backlog record, read live and never part of the snapshot (`kitlib/hubsite/apiread.py` `record`,
`record_new` and the record writes block; `kitlib/hubsite/serveviews.py` `SiteViews`). `{repo}` only picks a
configured repository (`Views._named` in `kitlib/hubsite/backlog.py`); its folder never comes from the
caller.
- `GET /api/v1/records/{repo}/{id}` (owner and agents): `read_record` in `kitlib/hubsite/backlog.py`,
  whose docstring lists the keys: every field, the body, the blockers, the hold with its ruling or
  question, the branch for a pulled or parked record, and `version`. `GET /api/v1/records/{repo}/new` is
  the blank record (`record_template`).
- `POST /api/v1/records/{repo}` (owner only), body `id`, `summary`, `scope` (a list), `effort`, `ref`: adds
  an unpriced record through the backlog library's add (`kitlib/hubsite/recordwrite.py` `add`). It is
  written `found_on: none`, with the store's `Session: owner-hub` trailer and a `review: owner-hub <date>`
  mark, so `pull` refuses it and `next` never offers it until a coordinator runs `clear-review`
  (docs/backlog.md "Records").
- `POST /api/v1/records/{repo}/{id}/edit` (owner only), body `version` (the one the read answered),
  `changes` (`{key: [values]}`), `unset`, `body`: the backlog library's set. A record that changed since
  that read is `conflict` on field `version`; read it again.
- `POST /api/v1/records/{repo}/{id}/delete` (owner only), body `why`, `confirm`: `confirm` must repeat the
  id, or the call is `invalid` on field `confirm` and nothing is written. The store's delete rules apply
  unchanged: a live pull is refused until it is released.
- `POST /api/v1/records/{repo}/{id}/unblock` (owner only), body `on`, `reason`: the backlog library's
  unblock (`recordwrite.unblock`). One blocker opens the record; with several, `on` names the one to clear
  and `reason` is required, and a call with no `on` is `invalid` on field `on`. Clearing an owner blocker
  takes nothing more.
- These routes are the owner's, so they meet the store's owner gates (docs/backlog.md "Owner gates";
  recordwrite passes `role_checked`). None takes a ruling id: a body with `ruling` is `invalid` on that
  field, as any field a route does not take.
- Each write answers `lines`, what the store printed (its notes included), and an add or edit also answers
  the record as it reads now. A store refusal is `invalid` in the store's words; a record not there is
  `not_found`. A repository whose `.kit.json` names no backlog store, or whose store cannot be written
  from the hub's checkout (a folder store outside a linked worktree), is `store`: the owner sees the
  store's text, an agent or the tunnel the fixed phrase.

On the page (the record page block in `kitlib/hubsite/web/index.html`), a backlog row's detail in Open,
Parked or Completed opens the record (Edit fields, Edit body, Retire or Open the record), and Back to the
backlog returns. The record shows its id, repository and where it is (`open` or `completed`), then four
sections:
- Fields: every header field except the hold keys, one `key: value` line per value.
- Blocker or hold: each `blocked_on` (with a button that opens the record it names), `owner_ask`,
  `hold_ruling` and `q`, or "Not blocked or held."
- Body: the record's text.
- Branch: the branch, its tip, date, worktree and how far it is ahead of and behind main. Read only.

On an open record, Fields, Blocker or hold and Body each have an Edit button that opens the section as
text. In the two field sections a key left out is unset, a changed key is sent with all its values, and a
line that is not `key: value` is refused on the page. Save sends only what changed, at the version the
page read, and draws the record the answer carries; a refusal shows as "Not saved: " and the store's text.
The page has no ruling id box.

Delete… (open records only) opens a panel with a reason and a box to type the id again; the Delete button
stays disabled until both are filled and the typed id matches. After the delete the page reads the record
again, which now shows as removed and has no Edit or Delete. A record the store cannot read ("A header
line of this record is not key: value") says so above its sections.

The New story button (Backlog, beside the backlog menu) opens a blank record for the backlog that menu
picks, else the first repository: a backlog menu, then id, summary, scope (one path per line) and effort,
defaulting to the first effort in the template. Create is enabled once id, summary and scope are filled;
it adds the record and opens it. "Not added: " shows the store's text, such as a repository whose
`.kit.json` names no backlog store (this kit's repository, on the implicit store, is one).
