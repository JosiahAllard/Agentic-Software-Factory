# Action Hub: using the hub

## Using the hub

Start the server with `python tools/hub.py serve`, then `python tools/hub.py open`: it opens the
page signed in as you (a one-time pairing link sets the browser's session cookie; `--print` prints
the link instead). `serve` prints the full `open` command on its start line. A paired browser stays
paired across a server restart: the server keeps each session, as a keyed hash and never the cookie,
in `browser-sessions-<port>.json` beside the token files (`kitlib/hubsite/auth.py`, "Sessions").
`serve --port 0` binds a new port each run and keeps no such file, so its browsers are paired again
after a restart. A session lasts 30 days (`auth.SESSION_TTL_S`) and is renewed to a full 30 when the
browser uses it with less than half left. `python tools/hub.py unpair` ends every browser's session
(the owner's token only; with no server running it empties that file itself); a new `owner.token`
ends them all too. A browser that is not paired is told on the page to run `python tools/hub.py
open` in the kit folder. The server's health is `GET /api/v1/health`.

The sidebar lists the sections in groups: Home on its own; Your work (Questions, To-dos, Approvals);
Projects (Backlog, Schedule, Docs, Landings); More (Message the PM, Usage, Agents, Skills); Upkeep (Lint findings,
Automatic tasks). A count sits at the right edge of an entry and is highlighted for what waits on
you: any pending approval, urgent question or to-do. Lint findings shows a dash until the page has
read `GET /api/v1/findings` once (at the start and after each wait wake). On a phone the sidebar
becomes a bar of five tabs: Home, Questions, Approvals, Schedule and More (`#more`, a page of the
other sections with their counts). Between 761 and 1100 pixels the detail pane stacks under the list.

Home is titled "Today <date>" and lists what waits on the owner in four groups: Decisions (open
questions), Approvals (pending), Hand steps (open to-dos) and Replies from the PM (`to_pm` notes with
status `done` and a `reply`, whose `done_at` is within `HOME_REPLY_DAYS` (7) days, newest first; the
snapshot has no read marker). Each row opens its card in the detail pane. The machine strip sits
under the title.

- **Questions.** Two tabs. Questions: what the PM and the coordinators ask you, each with context,
  options and a recommendation; pick one and add a note if you like. Its own tabs are Open,
  Submitted (answered, not yet logged by the PM), Logged, Filed (only when a filed question exists;
  a filed answer sits there until its asker logs it), and Other (only when a question has another
  status). Answers: the PM's replies to what you asked in a note, in Unread and Read tabs.
- **Message the PM** (`#topm`). Write the PM a question or an FYI. Add keeps it queued; Send all
  sends every queued item at once. Tabs: Queued (editable, deletable), Sent (waiting on the PM) and
  Done (the PM's reply; the last 30 days, with Show older). A row shows its first line with Urgent,
  its type and its repository as chips, and opens the whole message in the detail pane.
- **Approvals.** What an agent asked to run or change. Waiting (pending, each with Approve and Deny,
  which need a strong session) and Decided (approved, denied, used or expired, newest first, no
  buttons). The count is the pending ones.
- **To-dos.** Hand steps for you. A card shows the detail as short paragraphs and bullets, and each command as a numbered step in its own block with its own Copy button (a `;` outside quotes breaks the line in the display only; Copy sends the command as stored). A step has its own tick box, view state for this page load only. Tick the card when done.
  You can add, edit and delete your own. Tabs: To do and Done.
- **Backlog.** Open (each repository's open work under a heading per priority, with priority, status
  and text filters), Parked (items a coordinator parked waiting on you, with their age) and Completed
  (1, 7, 30 or 90 days). A row opens its detail beside the list: priority, status, branch, session,
  question, and Edit fields, Edit body and Retire, which open the record page (docs/hub-views.md "The record page"; Retire
  opens it with the delete form open, the only retire route, which still asks a reason and the typed
  id). The backlog view carries no effort, scope or body, so the detail reads `GET /records/<repo>/<id>`
  when it opens and adds them; if that read fails it says so and the buttons stay. A completed row's
  detail has Open the record. Open also shows your stories: in progress above the table, filed or closed
  below it, with Withdraw while one is still submitted. The New story button opens a blank record page;
  `stories add` still files a story.
- **Docs.** The agents' documents (a plan, research, a visual choice), from `pages/` and `page_html/`.
  The left pane is a tree, All documents > repo > kind, with a count on each node. A card with a repo
  but no kind is under that repo's Unsorted, and a card with no repo is under a top-level Unsorted.
  Selecting a node shows its cards newest first (This week / Earlier, by last change; one with no time
  is Earlier) under a heading with the count and size. The id/title box filters tree and cards, and the
  repo menu still narrows both. The selected node and the open repos are kept in the browser's
  localStorage. The existing pages were given a kind and a repo (`pages place`), so none is Unsorted. A row opens
  its detail: the summary, refs and buttons. Ask the PM about it adds a line about the document to the
  Message the PM form and sends nothing. Read full screen reads the body from the server into a reader;
  Close or Esc (focus outside the document) shuts it. A ref on any card that names a listed document
  opens it the same way. Delete (pressed twice) removes a document; agents publish them, the page
  writes none.
  Above the cards a cluster map draws the selected tree node's children as a treemap (repos under All
  documents, kinds under a repo, months under a kind), sized by document count and coloured by kind; a
  repo or kind cell selects that node, as the tree does, and a tree click redraws the map. Month cells are not clickable, and an Unsorted node shows months too.
- **Usage** (`#info`). Usage (each account's 5-hour and weekly percentages) and Lock notices. A usage
  row opens its detail in the pane.
- **Agents** (`#agents`, its own entry in More). Each agent's model and effort; a row opens its detail.
- **Skills** (`#skills`, its own entry in More). The kit's skills, read only: a row is a skill's name and
  description, and its detail is the header and the body of `content/skills/<name>/SKILL.md` as plain
  text, read from `GET /skills/<name>` when the card opens (the snapshot carries no body). The source only, never a host's rendered copy; the page edits nothing.
- **Schedule** (`#schedule`, the sidebar's Projects group and the phone's Schedule tab), drawn from
  `GET /lock/view` (docs/hub-views.md "Machine lock routes"), with four tabs:
  - Now: the runs now (each with a progress line against the time expected, and a Late chip past it),
    the line behind them (Next up, from `queue_items`), today's holds and a History row that opens the
    finished runs (1, 7, 30 or 90 days; filterable to failed or stopped). A run's detail has End run
    (asked twice); a waiting run's has Move to top. The sidebar count is the runs now.
  - Week: the week of half hours (back two weeks, on four). Reserve time on a card opens pick mode: the
    half hour a window starts in, then the one it ends in, then Reserve the whole machine or half, with
    a note.
  - Holds: the owner's holds and the agents'. New hold opens a form (the whole machine, half, or named
    slots, with typed times); Edit changes a hold's window or note and Release (asked twice) ends it,
    whoever made it.
  - Settings: the Game mode card (on or off, and the games list) and the Division card (what is in
    force, what the owner set, the default for each key; change it or use the defaults).

  One watcher reads the lock for every view (the Home machine strip, the count, this section): it waits
  on `GET /lock/wait`, stops while the browser tab is hidden and reads again when shown. Home's machine
  strip is one link to `#schedule`. `/lock.html` answers 404. Times are the PC's local time and a
  reserved window is sent in UTC.

  A control this login may not use is disabled with a "Read only" reason: holds and the division need
  the strong owner; a hub started under an agent session changes nothing. An agent is refused the lock
  routes.

The sidebar's foot (when the hub was read, then the PM line and the push line) says whether a PM is
listening: "A PM is listening." while a PM holds a `wait` open, else "Push delivery reaches the PM."
while the snapshot's `push` state is `up` and its `last` is `written`, else when one last asked;
another session's wait `--for` its own key does not count (`Api.seen`). With a push worker running,
the line ends with the PM watcher's heartbeat from the snapshot's `watcher` field: "The PM's watcher is
running.", "The PM's watcher has stopped: its last heartbeat is <age> old." or "No PM watcher
heartbeat is found." (`pmSeenText`). On a phone the header also shows a "PM listening" or "No PM
listening" pill. A second line gives the push status from the snapshot's `push` field: "Push delivery
is up" or "is down" (with the state of the latest try to the PM), how many items are undelivered, or
"Push notifications are off on this hub server." when no worker runs. The page reads everything at once
(`GET /api/v1/snapshot`), then waits for the next change (`GET /api/v1/wait`, about 55 seconds on the
server), so what the PM writes shows without a reload. After a failed wait it reads again after a
back-off (2 s doubling to 60 s, or the server's `Retry-After`). When the snapshot read before the
wait answered, a failure of the wait (a tunnel or proxy cutting it, a 502 or 524) or of the read
after it is tried again within 15 s, so the page still follows the hub by polling (`hubNextMs`). The tab
shown again, the browser back online or the page restored from the back-forward cache ends the
wait or back-off and reads at once. The tab hidden ends the wait too, and the page waits for it to be
shown again: each wait holds one of the few connections a browser keeps to the hub, and hidden tabs
holding theirs can stall the owner's writes behind them (a tab opened hidden and never shown still reads once and
waits). Shown again, the page reads once. An answer sent shows on its card at once ("Your answer was sent. Reading it back from the hub
server…") while the lists are read again behind it. A redirect is the tunnel's sign-in (Cloudflare Access) running
out: the page says to reload it to sign in again, and tries every 45 s until then. A view the
server cannot give says so at the top. `GET /api/v1/snapshot?collections=a,b` (comma-separated) answers
`{revision, collections}` with those collections alone, cut as the whole snapshot cuts them, and no
views, pending or status; the hub client reads its inbox so (`collections=inbox`). With no
`collections` (or an empty one) the whole snapshot answers. A name that is not a collection of the
snapshot (`page_html` and `handoffs` are never in it) is `invalid`, field `collections`. An older hub
ignores the query and answers whole.

Links ending `#home`, `#questions`, `#inbox`, `#approvals`, `#more`, `#todo`, `#topm`, `#backlog` (or
`#stories`), `#parked`, `#docs`, `#schedule`, `#autotasks`, `#findings` or `#info` open that section;
`#agents` opens Agents, `#skills` Skills, `#landings` Landings; `#usage` and `#notices` open that Info tab. The Questions, Answers, To do and Docs lists
filter by repository; Questions, To do and Docs also by id or title. Automatic tasks lists the
schedules ("schedules/") under "Schedules", and the two PC-set sweeps (`worktree-sweep`,
`agent-temp-sweep`, which machine setup registers with Windows) under "Set on the PC": read-only, with
a lock, and a plain row with no times where the hub holds no schedule doc for one. A schedule's card
shows its last run and next due (`last_run`, `next_due`; a failed start is a chip, and a `skipped`
record reads "skipped", not "never"). It is edited with an Off / Daily / Weekly control, seven day chips
(weekly only) and a 24-hour time; the sweeps are not offered by the New schedule form.

Lint findings (the Upkeep group, and the More page on a phone) lists the maintenance lint's findings,
read from `GET /api/v1/findings` and `GET /api/v1/finding_marks` each time the section is shown and
after each mark. A row gives the check, the repo, the target, the text and its age. The rows come in
three groups, each headed with its count and left out when empty: New this run (the lint's `new` flag:
its fingerprint was not in that repo's previous run, so false on a repo's first run and true again for
a finding that went away and came back), Still open, and Handled (the marked ones, muted, each tagged
Passed on, Dismissed or Done; a mark whose repo's last run no longer lists the finding is hidden, in
`GET finding_marks` and the snapshot alike, and stays stored: a finding that comes back with the same
fingerprint is shown as handled again, a known limit). Above the rows, each repo's last run gives its ledger as counts, and
"last run skipped: <reason>, <time>" only when the server names a skip later than that run
(docs/hub-records.md "finding_marks/<fingerprint>"). The card of an unmarked finding offers:

- for a check that names a record, a button named for what is done, which runs in the detail pane:
  Set priority (`unpriced`), Unblock (`keyword-hold`, `dead-blocker`, `answered-question`), Edit scope
  (`scope-path-gone`, `body-path-gone`, `file-moved`) or Retire (`duplicate`, one per record,
  `dead-pull`, `landed-still-pulled`) (`FINDING_ACTS`). The panel reads `GET records/{repo}/{id}` for
  the fields and version, then calls the owner routes: Set priority and Edit scope `POST .../edit` with
  the version read (`changes` = `priority`, or `scope` one path per line); Unblock `POST .../unblock`
  with the reason (and `on`, when the record has several blockers); Retire `POST .../delete` with the
  reason and the id typed again. Unblock and Retire take no version, so the panel reads the record
  again first and refuses when its version moved. No ruling id is asked for or sent, and a refusal
  shows in the store's words. A done action leaves the finding listed until the next lint run; an
  "Open <id>" button opens the record page;
- Pass on: an act note to the PM with the note field's text (prefilled as the server would write it;
  unchanged, the server writes it), queued in To PM, then the mark `passed_on`. A mark that fails after
  the note was taken is retried by Pass on without a second note;
- Dismiss: the mark `dismissed`.

A target or fix shows as the server gives it. In To PM an act note shows as Act and its edit offers
no kind.

