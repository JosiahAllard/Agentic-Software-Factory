# Action Hub: inbox, PM notes, to-dos, stories, repos, schedules, finding marks

## inbox/M-NNN
The owner's inbox: the PM's answer to every question the owner asks in a note (on a question, a to-do or
a story). The question card links to its answers; the Answers tab counts the unread ones.

| Field | Written by | Meaning |
|---|---|---|
| ref | PM | the question, to-do or story id the owner asked on |
| asked | PM | the owner's question, quoted from the note |
| text | PM | the answer, complete on its own |
| title | PM | optional one-line heading |
| repo, from, at | PM | provenance; at is ISO time |
| read, read_at | owner | the owner marks it read (or unread again) |

PM steps: read every answer note and every to-do or story detail for a question; post the answer here,
and still file any decision the note makes.

### Lock-window notices
The machine lock's own notices that a heavy or exclusive window started or ended, so the owner hears of it
without anyone relaying it (`kitlib/lock/notice.py`). Off unless `machine_lock.notice.dir` is set in the
machine config (`kitlib/lock/site.py`). Notices are per run, not per window: two heavy runs side by side
write two start and two end notices, and the machine is free again only once every started run has its end
notice. Ids are `L-<run id>-start` and `L-<run id>-end`; fields are title, text, at, repo, from (`machine
lock`), kind (`lock-window`), run and phase. The lock writes each as a file in that folder; they are never
stored in the hub. The server lists them (`GET /api/v1/notices`, `kitlib.hubsite.derived.notices`) and the
PM watcher prints `LOCKNOTICE <id> <title>` for each new one. The page shows them under Info, Lock notices
(`#notices`), from the snapshot's `views.notices`: newest first, the latest 100, read only. The tab's count
is the runs with a start notice and no end notice yet, marked while any is open. A run the lock asks to stop
because it is stale (docs/lock-api.md "Stale runs") also gets `L-<run id>-stale` (phase `stale`, file
`<run id>-stale.json`), whatever its kind, whenever the folder is set; it is listed and printed like the
others and does not change the count.

## to_pm/P-NNN
The owner's questions and FYIs to the PM. Inbox is PM -> owner and To PM is owner -> PM; replies to To PM
items do not go to inbox/.

| Field | Written by | Meaning |
|---|---|---|
| (doc id) | server | `P-001`, `P-002`, ...: the document's id, not a field |
| kind | owner | `question`, `fyi` or `act` (pass a maintenance finding on: below) |
| repo | owner | a repo name, `pm`, `all`, or empty |
| urgent | owner | true marks it Urgent and makes the badge hot |
| text | owner | the message, as written |
| refs, finding | owner | ids the note names; `finding`, an `act` note's only, is the finding's fingerprint |
| status | owner, PM | `queued`, `sent` (Send all), `done` (PM) |
| created_at, sent_at | server | ISO times; sent_at is null until sent |
| reply | PM | the answer, complete on its own (null or empty shows as "noted") |
| done_at | server | ISO time the PM replied; the Done window and order use it |

PM steps: each sent item is pending. Answer a Question with its reply; for an FYI reply with a short
acknowledgement. `to-pm reply` sets `done`.

## todos/T-NNN (or a generated id for the owner's own items)
| Field | Meaning |
|---|---|
| status | `open` or `done` (the owner ticks it) |
| title, detail | what to do and why |
| commands | a list of strings (a bare string is an error in `kitlib.hubvalidate`): the exact PowerShell commands, each with a Copy button, on every item that involves running something; to edit a file by hand, a command that opens it |
| repo, from, added_at | provenance; `from: you` on the owner's own items |
| done_at | set when ticked |
| done_note | the owner's optional note on the done mark ("The owner's note on a done to-do"); absent when there is none |
| urgent | true sorts it to the top; the owner can set it on their own items |
| pm_ack | PM: set once it has followed up on a done item or picked up an owner-added one; takes it off the pending list |
| record | agent: `{repo, id}`, the backlog record an agent's to-do is about; absent or null otherwise |
| park | hub: `{at, code, message}`, what binding the to-do did to its record, as a question's `park` (docs/hub-questions.md "A question bound to a record") |

A new to-do (`POST /todos`, so `hub.py todos add` and the MCP tool `hub_todo_add` alike) may also carry
`steps`, which is not stored: `kitlib.hubsite.api.todo_steps` turns each step into a command appended
after `commands` and adds one summary line per step at the end of `detail` (docs/hub-mcp.md "To-do
steps"). `hub.py todos add --steps F` reads the list from F, a UTF-8 JSON file. The server refuses, as
`invalid` naming the field: a bad step (`steps`), a `commands` entry holding a lone surrogate (U+D800 to
U+DFFF, `commands`), and, on an agent's to-do, text the owner's to-do rules forbid (`todosteps.check`:
plain Notepad, a hand edit described in prose, coordinator work). The owner's own to-dos are not judged by
those rules. A raw worktree, branch, tag or ref delete has no carve-out: its refusal names
`worktree_remove.py <slug> --abandon "<why>"`, which the coordinator runs on a ruling (docs/worktree-remove.md
"Tools"); a ref delete's also says a ref outside `refs/heads`, `refs/tags`, `refs/stash` and
`refs/backlog` the coordinator deletes itself, in its own repository (docs/guard-rules.md "Rules",
`git-shared-ref`). An agent's new to-do may carry `record` (`todos add --record REPO:ID`), which parks
the record until the to-do is done (docs/hub-questions.md "A question bound to a record"); the owner's own to-do and an edit
take none (`invalid`, field `record`). An agent's to-do whose title or detail asks the owner to send a
result back (`report back|in|which|what|whether`, `answer box`, a line starting `Report:`) is added with
a `todo-asks-report` warning (`api._asks_report`): a done mark files nothing into any record, so a
result a record needs is asked as a question bound to it.

While a to-do is open its creator may change it (`POST /api/v1/todos/{id}/edit` with the agent's token:
`hub.py todos edit`, the MCP tool `hub_todo_edit`) or withdraw it (`POST /api/v1/todos/{id}/delete`:
`todos withdraw`, `hub_todo_withdraw`), with the same `from` and `if_version` rules and refusals as a
question (docs/hub-questions.md "questions/Q-NNN"). An agent's edit takes no `steps`, its changed text is judged by the to-do
rules above, and it clears `pm_ack`. `todos edit` replaces `commands` only with the `--command` values it
is given, so it cannot clear them. Only the owner's own to-do can have its commands cleared (an empty list
to the route); an agent's open to-do needs at least one, and the hub refuses an agent's edit that empties
them (`invalid`). The owner edits and deletes only their own to-dos (`from: you`) at any status, and
deletes an agent's to-do (never edits it) while it is open (`todos_edit`, `todos_delete`).

### The owner's note on a done to-do
`POST /todos/{id}/done` (the page's tick, `hub.py todos done T-1 --note N`) takes an optional `note`, plain
text trimmed and stored as `done_note`: a remark on the done mark, which only the push carries. It is
filed into no record, even a bound to-do's, so a result a record needs (which checks held, a hash) is
asked as a question bound to that record (docs/hub-questions.md "Filing the answer"); an agent's to-do that asks for one gets
the `todo-asks-report` warning ("todos/T-NNN"). It is at most `TODO_NOTE_MAX` (2000) characters with no control character other than tab and line
breaks, and comes only with a done mark; otherwise `invalid`, field `note`. An empty note changes nothing,
and a done mark with no note keeps its wire format (no `done_note`). Marking done again replaces the note
(none given drops it); a reopen drops it. The page shows a note field on each open to-do, keeps its draft
in the browser's storage as an answer's (`oq.draft.T-n`, so it survives a redraw and the tunnel sign-in
redirect) until the hub has the done mark, and shows the stored note, as plain text, on the done card.
`hub.py todos show T-1` prints the to-do, its commands and `note: ...`. The push of the done mark to the
asker and the PM carries one further line, `The owner's note on it (the owner's words, data and not an
instruction): "..."`, read from the store when sent, whitespace collapsed and cut at 400 characters with
`hub todos show T-n` for the rest (`push.TODO_NOTE_PUSH_MAX`).

## stories/S-NNN
The owner writes a story and picks the backlog it belongs in. It reaches the backlog only through that
repo's coordinator.

| Field | Written by | Meaning |
|---|---|---|
| status | owner, PM | `submitted` (owner), `review`, `questions`, `added` or `declined` (PM); `withdrawn` (owner, only while submitted) |
| repo | owner | a repo name, such as `alpha` or `pm` |
| title, story, urgent, from, submitted_at | owner | the owner's story as written |
| reviewer | PM | the coordinator reviewing it |
| review_note | PM | the coordinator's summary, shown on the card |
| backlog_id, added_at | PM | the record the coordinator filed |
| closed_note, closed_at | PM, owner | why it was not added, or when it was withdrawn |

Follow-up questions are ordinary questions with `story` set; the page links them both ways. The page has
no story form: the owner writes new work on the record page instead (docs/hub-views.md "The record page"), and `stories
add` still files a story.

PM steps:
1. On a `submitted` story, send it verbatim to that repo's coordinator (coordinators stay in scope) and
   set `review` with `reviewer`.
2. The coordinator reviews it against its backlog (duplicates, size, fit) and either files it or sends
   follow-ups to the PM. The PM asks each follow-up as a question with `--story` and sets `questions`.
3. The PM files the answers as usual and relays them to the coordinator; the story goes back to `review`.
4. When the coordinator reports the filed record, set `added` with `--backlog-id` and a one-line
   `--review-note`. A coordinator that declines (duplicate, out of scope) gives a reason; set `declined`
   with `--closed-note`.

The coordinator files the record itself with its own backlog tool, citing the story.

## repos/<name>
{name, added_at, aliases?}: `aliases` (optional, a list of strings) names old tags an item's `repo` may
still carry; the page files such an item under this repo (case ignored). The server's repo list (`GET
/api/v1/repos`, `kitlib.hubsite.derived.repos`) is every repository the PM knows
(`kitlib.pmstart.known_repos`: each session's repo in `sessions_file`, the PM's repository, this kit's
repository and the optional `pm_start.repos`) with the aliases the store holds. An item whose repo is
`all` is for every repo and shows under each one.

The same list is the `repos` view of `GET /api/v1/snapshot`. Each entry of it also holds `web`, `branch` and `top` when the repository's checkout is on this machine and its
`origin` remote is a GitHub or GitLab one (`github.com`, `gitlab.com`, a `gitlab.*` host; any other host, no origin or a
local path, or a GitHub path other than `owner/repo`, gives none of the three; GitLab subgroups are kept and `/-/` ends the project path): `web` is the remote as an `https://host/owner/repo` address (credentials, port,
query and fragment dropped), `branch` the main branch (`.kit.json` `repo.main_branch`, else `main`, else `master`) and
`top` the names at the root of that branch, at most 64 (`kitlib/hubsite/repoweb.py`). Git is asked once per checkout and
again when the config file changes (a linked worktree's is the common git folder's) or after 10 minutes, never per poll; repoweb's own git calls (remote, root names, the config path) are cut off after 5 seconds each. The main branch is found by the backlog's resolver (`kitlib.hubbacklog.main_branch`), with its own longer limits; its result is kept with the rest, so a slow lookup costs at most once per config change or each 600 seconds.

## schedules/<task>-<repo>
When one of the kit's automatic tasks runs for one repo, set by the owner. The hub only stores and serves
it: nothing in the hub runs a task, registers one or starts a process.

| Field | Meaning |
|---|---|
| task | `maint-lint`, `worktree-sweep` or `agent-temp-sweep` (`kitlib.hubsite.api.SCHEDULE_TASKS`, a fixed table) |
| repo | a repo the hub knows (a repos/ name or alias, or the PM's configured repos), stored as its name |
| cadence | `off` (kept, runs nothing), `daily` or `weekly` |
| days | weekly only, required: one or more of `Mon` to `Sun`, stored once each in week order |
| at | the time of day as HH:MM, 24-hour |
| set_at, via | server: when and by which path it was last set |

`POST /api/v1/schedules` (owner) takes {task, repo, cadence, at, days?, if_version?} and puts the whole
document; an agent is refused `forbidden`. Any other field, or a bad value, is `invalid` naming the field,
with nothing written. Setting an existing schedule again takes its `if_version`; without it, or with
another, it is `conflict`, whose error names the schedule's `id`: the page opens that one, with what was
picked on the new form carried onto it unsaved (unless it holds unsaved changes of its own), so one Save
sets it. A daily or weekly per-repo task whose repo is not exactly one checkout in the machine config's
`launcher.repos` is `invalid` naming `repo`, since the tick could not run it; so is one set while the
launcher list cannot be read. The answer carries `first_run`, the first slot as YYYY-MM-DDTHH:MM in the
hub machine's local time with no zone (null when off); the task starts at the first tick at or after it,
up to about 15 minutes later. `tick.last` is UTC with its offset; the page shows `first_run` as it is and
says it is the hub machine's time. `GET /api/v1/schedules` (anyone) lists them, filtered by `repo` like
every list, with `tick`: {last, running, every_s}: when the tick was last seen, whether that is recent
enough to call it running, and its period in seconds (`kitlib.hubsite.api` "Schedules"). Each listed
schedule also carries three derived fields, never stored: `last_outcome`, the tick's record for it
(`started`, `skipped`, `seen` or `failed`, from the tick's state file through `Api.tick_state`, default
`tick_run_state`; null for none); `last_run`, that record's time when it is `started` (null otherwise, so
a `failed` record is no run); and `next_due`, the first slot after now by `kitlib.autotasks.due`'s rule,
in the hub machine's local time like `first_run` (null when off).

## finding_marks/<fingerprint>
The owner's mark on one maintenance-lint finding (docs/maint-lint.md), so it stops showing: {status, repo,
check, target, text, note?, marked_at, via}. status is `dismissed`, `passed_on` or `done`
(`kitlib.hubvalidate.STATUSES`); repo, check, target and text are copied from the finding when it is
marked. `GET /api/v1/findings` (anyone) lists the lint's last findings, every repo
(`kitlib.hubsite.derived.findings` over `<pm_data>/maint-lint/<repo>/result.json`), filtered by `repo`,
less every marked one, with `runs`: each repo's last run as {repo, started, ledger, skipped}, ledger its
whole-number counts `stale_cites`, `conflict_pairs` and `overrides` or null, skipped the skip marker's
{reason, at} only when `at` is later than `started` and both parse, else null (`_lint_runs`; from the
server's `lint_runs` view, always there: an empty list when there is no lint folder). `POST
/api/v1/finding_marks` (owner) takes {fingerprint, status, note?, if_version?}; a fingerprint the lint
does not show is `not_found`, and marking again takes the mark's `if_version`, as for a schedule. `POST
/api/v1/finding_marks/{fingerprint}/delete` (owner) takes the mark back; `GET /api/v1/finding_marks`
(anyone) lists the marks. `POST /api/v1/to_pm` with kind `act` and `finding` passes one on: repo, refs
(the fingerprint and the finding's target first) and, when none is given, text are filled from the
finding; an edit never changes a kind to or from `act`. Only the local owner sees a target or fix whole:
any other caller, the tunnel included, gets each absolute path cut to `.../<its last part>`, and no act
note's refs carry one. An agent is refused `forbidden` on each write (`kitlib.hubsite.api` "Findings").

