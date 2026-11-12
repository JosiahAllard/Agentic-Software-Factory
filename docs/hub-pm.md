# Action Hub: for the PM

## For the PM

The PM reads and writes the hub only through `tools/hub.py` or the hub MCP server (docs/hub-mcp.md),
both with the agent's token. A coordinator writes only its own questions: it posts each with `hub.py
questions add --from <its launcher session key>` (the `hub key:` line of its session-start context,
`kitlib.coordinatorstart.hub_key`) and `--record REPO:ID` or `--class C` (docs/hub-questions.md "A question's class"), reads
`hub.py pending --for <its key>` once at start, runs no background wait (the hub pushes the answer,
docs/hub-push.md "Push notifications"), and acts on the answer itself when it comes. A to-do for its own work it files
with `hub.py todos add --from <its key>` and acknowledges (`todos ack`) once it has acted on it; it
hands every other step (notes, stories) to the PM. Who handles each pending item: "Who handles a
pending item". For a question, the server takes `--from` only when it is `pm` or a key of the
launcher's sessions.json, read again on each add; any other value is refused, and a question without
`--from` is the PM's. The answer stays pending for everyone, the PM included, until the PM logs the
ruling in the rulings ledger and then marks the question logged (`questions log`, which takes no ruling
id: the ledger entry's Source names the question), or marks it done when it needs no ruling (`questions
done`; a bound question's filed answer the hub marks done itself once its asker has it, docs/hub-questions.md "Done by the
hub"), so each `pending` read lists it again and a plain wait would print it again at once: a caller
that does wait passes `--except` and every id it has acted on (`wait --pending --for <its key> --except
Q-7 T-3`). Every verb takes `--port N` and `--json` (the server's answer as one JSON line). The server
alone decides what a role may do: the owner's verbs (answer, todos done, stories add and withdraw, to-pm
add and send, inbox read) are refused to the agent. The creator of an open question or to-do changes it
with `edit` and removes it with `withdraw`, naming the `from` it was added with and the version it read;
the owner never edits an agent's item and only removes one (docs/hub-questions.md "questions/Q-NNN" and docs/hub-records.md "todos/T-NNN").

Verbs (the docstrings of `kitlib/hubsite/cli/` are the source), with the line each prints:

| Verb | Prints |
|---|---|
| `questions [--status S] [--repo R] [--since ISO]` | `Q-1 v2 open [repo] title`; a done one ends ` (done by the hub: <done_why>)` when the hub marked it done (docs/hub-questions.md "Done by the hub"), else ` (done: <done_why>)`, the why on one line (`questions show` too) |
| `questions show Q-1 [--json]` | the `Q-1 v2 answered [repo] title` line, then its options (the recommended one marked), context, answer and note |
| `questions add --title T [--repo R] [--context-file F] [--option "label::description" ...] [--recommended N] [--refs ID ...] [--urgent] [--story S-n] (--record REPO:ID \| --class rule\|machine\|cross-repo\|go) [--from KEY]` | `ASKED Q-2`, then any warning lines |
| `questions log Q-1 [--unfiled WHY] --if-version V` | `LOGGED Q-1` |
| `questions done Q-1 --why TEXT [--unfiled WHY] --if-version V` | `DONE Q-1` |
| `questions file [Q-1]` | `QUEUED Q-1` (`QUEUED all` with no id) from a running hub, whose refile job then files it; else `FILED Q-1` and `NOT FILED Q-2: <why> (<code>)` lines, or `nothing to file` (docs/hub-questions.md "Filing the answer") |
| `questions bind --dry-run \| --apply` (PM) | `BIND Q-1 repo:ID` (dry run) or `BOUND Q-1 repo:ID` lines (each answered one followed by `FILED Q-1` or `NOT FILED Q-1: <why> (<code>)`), `NOT BOUND Q-1: <why> (<code>)`, then `MISS repo:ID: <why>` (`MISS repo: <why>` when the whole repository could not be read), `MULTI repo:ID cites Q-1 Q-2`, `SKIP Q-1: <why>`, or `nothing to bind` (docs/hub-questions.md "Binding the open questions") |
| `questions edit Q-1 --from KEY --if-version V [--title T] [--context-file F] [--option "label::description" ...] [--recommended N] [--refs ID ...] [--urgent \| --no-urgent] [--record REPO:ID \| --no-record] [--class C]` | `EDITED Q-1` |
| `questions withdraw Q-1 --from KEY --if-version V` | `WITHDRAWN Q-1` |
| `answer Q-1 --choice N\|other [--note TEXT] [--if-version V]` (owner) | `ANSWERED Q-1 <label>` |
| `todos [--status S] [--repo R]` | `T-1 open [repo] title` |
| `todos add --title T [--detail D] [--command C ...] [--steps F] [--repo R] [--refs ID ...] [--urgent] [--from NAME \| --owner]` | `ADDED T-2` |
| `todos done T-1 [--undo] [--note N] [--if-version V]` (owner) | `DONE T-1` or `REOPENED T-1` |
| `todos show T-1` | the to-do line, then `detail:`, `command N:` and `note:` lines |
| `todos ack T-1` | `ACKED T-1` |
| `todos edit T-1 --from NAME --if-version V [--title T] [--detail D] [--command C ...] [--repo R] [--refs ID ...] [--urgent \| --no-urgent]` | `EDITED T-1` |
| `todos withdraw T-1 --from NAME --if-version V` | `WITHDRAWN T-1` |
| `stories [--status S] [--repo R]` | `S-1 v2 submitted [repo] title` |
| `stories add --repo R --title T --story-file F [--urgent]` (owner) | `SUBMITTED S-2` |
| `stories set S-1 --status review\|questions\|added\|declined [--reviewer NAME] [--review-note TEXT] [--backlog-id ID] [--closed-note TEXT] --if-version V` | `SET S-1 <status>` |
| `stories withdraw S-1` (owner) | `WITHDRAWN S-1` |
| `to-pm [--status S] [--repo R]` | `P-1 queued [repo] text` |
| `to-pm add --text T [--kind K] [--repo R] [--urgent]` (owner) | `QUEUED P-2` |
| `to-pm send` (owner) | `SENT P-1 P-2` or `SENT nothing` |
| `to-pm reply P-1 TEXT [--if-version V]` | `REPLIED P-1` |
| `inbox` | `M-1 unread [repo] title` |
| `inbox add --text T [--title T] [--repo R] [--ref ID] [--asked TEXT] [--refs ID ...] [--from NAME]` | `POSTED M-2` |
| `inbox read M-1 [--undo] [--if-version V]` (owner) | `READ M-1` or `UNREAD M-1` |
| `pages [--repo R]` | `ID v2 - [repo] title` |
| `pages publish ID --title T --html-file F [--repo R] [--summary S] [--refs ID ...] [--from NAME] [--if-version V]` | `PUBLISHED ID` (docs/hub-skill-pages.md "pages/<id> and page_html/<id>") |
| `pages show ID [--out F]` | the page's HTML, or `WROTE F` |
| `approvals [--status S] [--repo R]` | `A-1 pending [repo] title` |
| `approvals show A-1` | status, repo, head, title, reason, then `<path> <base_blob> -> <blob>` per file |
| `approvals add --title T --file PATH CONTENT_FILE [--file ...] [--checkout DIR] [--repo R] [--head SHA] [--reason TEXT]` | `REQUESTED A-001` (docs/hub-approvals.md "approvals/A-NNN") |
| `approvals withdraw A-1 --from KEY --if-version V` | `WITHDRAWN A-1` |
| `pending [--for KEY]` | `<kind> <id> [repo] to=<route> from=<key> [class=C] title`; with `--for pm` the classed answers come last, after an `AUDIT classed answers, no record to file into:` line (docs/hub-questions.md "A question's class") |
| `wait [--since REV] [--timeout S] [--pending [--for KEY] [--except ID ...]]` | `HUB <rev> <kind> <id> to=<route> from=<key> <title>` (pending) or `HUB <rev> <collection> <id> <op> <status>`; `HUB <rev> reset: the store is behind --since; read it again` after a reset |
| `validate [--store PATH]` | `VALIDATE <LEVEL> <document> <field>: <message> [<code>]` |
| `serve`, `status`, `setup`, `open`, `unpair`, `stop`, `restart` | see docs/hub-serving.md "Serving it" |

Notes on the table:
- `--status` also takes `all`. `todos` lines carry no version; `todos --json` gives each to-do's `version`.
- `questions add --story` is the story a follow-up belongs to; `stories set`, `questions log`,
  `questions done` and every `edit` and `withdraw` require `--if-version`. `--if-version V` is the
  version of the document the caller read and decided on (the `v2` of the `questions` and `stories`
  lines); a document that has moved on is refused (`REFUSED conflict`).
- `questions log` and `to-pm reply` take no ruling id: the hub stores none, and the API refuses a body
  with `ruling` (`invalid`, field `ruling`). `questions log` is refused (`invalid`, field `status`) for
  a question that is neither answered nor filed.
- `hub.py pending` lists the owner input not yet handled: questions answered and not logged or done,
  stories submitted, To PM items sent, to-dos done or added by the owner without `pm_ack`, and
  approvals the owner decided that are not consumed (`kitlib.hubsite.store.pending_items`; an approved
  one until it is consumed or expires, 30 days after the decision (24 hours for an op approval), a
  denied one for 24 hours after it).
  Each item carries `from`, the session key it is for, and `route`, the key that is to handle it now
  (the line's `from=` and `to=`). A question or to-do is for the launcher session key it was posted
  `--from` while that key is still in sessions.json; everything else (a to-do's free-text `from`, the
  owner's own items, stories, To PM items, and a question whose session has gone) is for `pm`
  (`kitlib.hubsite.api.Api.pending`). An approval has no `from`: it is for the coordinator of its
  repository, the configured repository whose origin remote is the approval's `repo`, keyed by that
  repository's name while the name is in sessions.json, and for `pm` otherwise. `pending --for KEY`
  lists only KEY's items, and `pending --for pm` the items routed to `pm`, every answered question and
  every decided approval; reading it marks nothing, so the item stays pending for every reader until it
  is logged, acknowledged, consumed or past its window.
- `hub.py wait --pending` waits until an item is pending (one already pending ends it at once) and
  prints a `HUB` line for each; with `--for KEY` other sessions' items do not end it. `--except ID ...`
  leaves out the items with those ids (ones already acted on), so only another item ends the wait; it
  marks nothing. `wait [--since REV]` waits for any change; `--for` or `--except` without `--pending` is
  a usage error. `--timeout S` exits 4 with no line when nothing came. No session keeps a wait running:
  the PM and each coordinator read `pending --for <key>` once at start and the hub pushes the rest
  (docs/hub-push.md "Push notifications"); the verb stays for a caller that wants it. The page still shows a PM wait as
  listening, so with push alone it shows no PM listening. A plain `wait --pending` would end at once,
  again and again, on an item a creator holds and has not acknowledged.

At each start, resume and compact the PM session-start checklist (docs/pm-start.md) says whether the
server is up, how many items are pending, and the push and session-watch state from `GET /api/v1/health`.

### Who handles a pending item

Each pending item goes to the session that created it while that session can be reached, and to the PM
otherwise, so the PM is the fallback for a creator that is gone (`route`, "Routes" in the
`kitlib/hubsite/api.py` docstring):
- An item for `pm` is the PM's.
- An item for a session key, before it is handed over, is that key's while the key can be reached: a
  push was written to it within `ACK_WINDOW_S` (`Api.pushed`, which the push worker calls for each
  written try to a key other than `pm`), the usual case since no session keeps a wait open; or a `wait
  --pending --for <key>` is open now, or one started or ended within `REACH_GRACE_S` (the 55-second
  wait limit plus 30 seconds). Otherwise it is the PM's.
- A `pending` or `wait` `--for <key>` that returns one of the key's items hands it over. The item is
  then the key's for `ACK_WINDOW_S` (30 minutes) from the first hand-over, and the PM's after that while
  it is still pending. The creator ends it by acting on it: `todos ack` for a to-do; an answered
  question stays pending until the PM logs it (`questions log`) or, when it needs no ruling (a one-off
  go such as a landing or archive go, which the ledger and the rulebooks keep out), marks it done
  (`questions done <id> --why <text> --if-version V`; status `done`, with `done_why` and `done_at`).
  Only the asker (the question's `from`) or the PM (`pm`) marks it done: the body's optional `from`
  (default `pm`) must be one of the two, else `forbidden` (field `from`) and the question is unchanged;
  like the edit check it is a claim, since agents share one token. A done question is no longer pending
  and its push ends (`questions_done`). A question bound to a record whose answer the hub filed (status
  `filed`, docs/hub-questions.md "Filing the answer") stays pending, with `filed` true on the item, until its asker has it: the
  hub then marks it done itself (docs/hub-questions.md "Done by the hub"). Until then its asker may log it or mark it done:
  both take a filed question as they take an answered one. `questions done` of a question the hub
  already marked done writes nothing and answers ok, with its version and `done_by` `hub`; the `from`
  check comes first, so another key is still `forbidden`.
- `--for pm` lists the PM's items and every answered question, whoever asked it, because the PM logs
  each ruling, and every decided approval. A filed question is on it only while it is the PM's: the PM
  asked it, or its asker cannot be reached, so an answer filed while its asker is gone still reaches
  someone. A PM wait is also woken when an item turns to the PM with no write.
- A decided approval is for its repository's coordinator and listed for the PM too. It stops being
  pending with no write: once consumed, at its expiry, or 24 hours after a denial.
- A submitted skill edit is for the kit's coordinator (docs/hub-skill-pages.md "skill_edits/E-NNN") on the same terms as any
  other key, else the PM's; it stops being pending when it is committed, failed, superseded or withdrawn.
- This state lives in the server's memory only: after a restart every item is the PM's until its
  creator waits again. To-dos whose `from` is a free-text name are the PM's.

