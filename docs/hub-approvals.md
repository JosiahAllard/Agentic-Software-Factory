# Action Hub: approvals, landings, hand-offs

## approvals/A-NNN
A change an agent asks the owner to approve before it commits it. The rules (signature, expiry, what
verifies) are `kitlib/hubsite/approvals.py`, "The document"; the routes are `kitlib.hubsite.api`
("Approvals" in its docstring).

| Field | Written by | Meaning |
|---|---|---|
| (doc id) | server | `A-1`, `A-2`, ...: the document's id, not a field |
| status | server | `pending`, `approved`, `denied`, `consumed` or `expired` (`approvals.STATUSES`); expiry is read from the times, never written. `superseded` is read too, from `superseded`, and before the expiry: an approval superseded and past its life reads `superseded` (`approvals.status_at`) |
| repo | agent | the repository, as the repo_id of its remote URL (`approvals.repo_id`) |
| title, reason | agent | what the change is and why; reason is optional |
| head | agent | the commit the change applies to (40 hex) |
| files | agent, server | `[{path, base_blob, blob, content}]` sorted by path; the server computes `blob` from `content`. A null `base_blob` is a new file, a null `content` (and `blob`) a delete (`approvals.new_doc`) |
| diff | server | the unified diff lines the page shows, from the base read with git from this machine's checkout of `repo` to `content` (`approvals.diff`) |
| created_at | server | ISO time of the request |
| decision, decided_at, sig | server | once the owner decides; `sig` (the approval key's signature) on an approval only |
| commit, consumed_at | server | once an agent consumes the approval for a commit |
| spent, spent_at | server | the commit on main that carries the approval and when the hub learned it: set once, from a landing feed event's `approvals` ("landings/L-NNN"), on a consumed file approval of the event's repo only. Display only, for a later page (no page reads them yet): the status stays, and whether an approval is used up is read from main itself (`approvals.spent`). An approval that reached main some other way than `tools/land.py` has none |
| superseded | server | `{by, from, at}`: a decided approval nobody consumed, marked superseded by the PM or its repository's coordinator (`from`), by the approval `by` (`A-<n>`) or the commit `by` on main, at `at` (`approvals.supersede_fields`). The stored status and the decision stay; the status reads `superseded`, final: it is not pending, not consumed and not verified. Not covered by the signature |
| record | agent | `{repo, id}`: the backlog record the change is for; absent or null on an unbound request |
| park | server | `{at, code, message}`: what binding the request did to its record, as a question's `park` (docs/hub-questions.md "A question bound to a record") |

Routes, below `/api/v1`:

- POST `/approvals` (agent): body `repo`, `title`, `head`, `files`, optional `reason` and `record`; adds
  a pending request. A repository this machine has no checkout of is refused (`invalid`, field `repo`),
  and so is a `base_blob` that is not the file at `head` (field `files`). A `record` (`approvals add
  --record REPO:ID`) parks the record until the request is decided (docs/hub-questions.md "A question bound to a record"); an
  `op` approval takes none (`invalid`, field `record`).
- POST `/approvals/{id}/decide` (role `approver`: the strong owner alone, docs/hub-remote-access.md "The
  strong owner"): body `decision` (`approve` or `deny`) and `if_version`. Only a pending request is
  decided.
- POST `/approvals/{id}/consume` (agent): body `commit` and `if_version`; only an approval that is
  `approved` is consumed.
- POST `/approvals/{id}/delete` (agent): the requester withdraws a request (`hub.py approvals withdraw A-n
  --from KEY --if-version V`), as `questions withdraw` does: the body gives `from` and `if_version`, both
  required, and the document is deleted. Only an undecided request is withdrawn, pending or expired
  undecided (`conflict`, field `status` otherwise: a decided one is the owner's record); one bound to a
  record drops its line from the record. The `from` is a claim: an
  approval stores none, so it is `pm` (`forbidden`, field `from`, for another).
- POST `/approvals/{id}/supersede` (agent): the PM, or a coordinator session of the approval's
  repository, marks a decided approval nobody consumed superseded (`hub.py approvals supersede A-n
  --by A-m|SHA --from KEY --if-version V`, `SUPERSEDED A-n by A-m`). The body gives `from`, `by` and
  `if_version`, all required. The `from` is a claim, checked as `questions done` checks one: `pm`, the
  repository's configured name (`Api.repo_key`), or any sessions.json key whose entry sets `agent`
  and whose `repo` is that name, matched without case (`Api.coordinator_keys`; the kit's coordinator
  is keyed `kit`); `forbidden`, field `from`, for another. `by` is the
  approval that replaces it, which must exist and not be itself, or a commit id of 40 hex that main as
  last fetched (`refs/remotes/<remote>/<main>`) reaches in this machine's checkout of the repository
  (`approvals.on_main`); anything else, a git that cannot tell included, is `invalid`, field `by`. Only
  an approved or denied approval is superseded: a pending one is withdrawn instead, and a consumed,
  expired or superseded one is `conflict`, field `status`. It writes `superseded`; the approval leaves
  pending and `approvals show` gives its status and a line `superseded by <by> from <from> at <at>`.
- GET `/approvals/{id}/verify` (agent): query `repo`, `head`, `files` (JSON `{path: blob}`) and an
  optional `commit`; answers `{ok, valid, why}`, never the key or the sig. With `commit`, only a consumed
  approval is valid, consumed by that commit or another: a rebuilt branch keeps its approval (the
  approval's `commit` stays the first one consumed it).

An approval of kind `op` approves one run of a delegated operation (docs/delegations.md "Hub approvals"),
not a change. It has no `head`, `files` or `diff`, and holds instead:

| Field | Written by | Meaning |
|---|---|---|
| kind | agent | `op`; a document with no kind is a change |
| op, repo | agent | the operation's id `D-nnn` and its repo name, or `*` |
| line | agent | the run line `run D-nnn NAME=VALUE ...`, names sorted, each once (`approvals.new_op_doc`) |
| what | agent | the operation's `what`, shown above the line as the agent's note, not signed; optional |
| args_sha256 | server | the SHA-256 of `line`, computed by the server (`approvals.line_sha256`) |
| consumed_at | server | once the runner consumes it; an op approval has no `commit` |

Its signature covers the id, kind, op, repo, `args_sha256`, the decision and `decided_at`; the line is
bound through `args_sha256`. The signature does not cover `what`, `title` or `reason`: what the owner
approves is the operation id, the repo and the line. A pending op request expires after 24 hours
(`approvals.PENDING_OP_TTL_S`). Its routes:

- POST `/approvals` (agent): body `kind` `op`, `op`, `repo`, `line`, `title`, optional `reason` and
  `what`. No checkout is read. Any other `kind` is `invalid`, field `kind`.
- POST `/approvals/{id}/consume` (agent): body `args_sha256` (in place of `commit`) and `if_version`. Both
  `commit` and `args_sha256` is `invalid`.
- GET `/approvals/{id}/verify` (agent): query `kind=op`, `op`, `repo` and `args` (the line's SHA-256);
  answers `{ok, valid, why, version}`. The version pins the consume that follows. Only `approved`
  verifies: a consumed op approval was used once.

The change checks (`check`, `consume_fields`) refuse an op approval, and the op checks (`check_op`,
`consume_op_fields`) a change approval. The page draws an op card with the operation, its `what` labelled
as the agent's unsigned note, and its exact line, `run D-nnn` and then one `NAME=VALUE` a line; no head or
diff. `delegated.py request` asks for one, and `delegated.py run --approval` verifies and consumes it.

The agent's side of an approval is `commit.py --owner-approval hub:A-<n>` (docs/commit.md "Owner
approval"): it verifies the approval, writes what the approved change gives on HEAD (the approved
content where HEAD holds the base's copy, else its clean merge onto a newer HEAD, `approvals.delta_targets`)
and commits it, then consumes the approval for that commit and verifies it with the commit; `gate land` and the pre-push hook verify it again
(`kitlib.guard.ownercommits`). A file approval, used or not, holds until it is spent or 30 days after
the decision (`approvals.APPROVAL_LIFE_S`, through `approvals.approval_end`); an op approval for 24
hours (`approvals.APPROVED_TTL_S`). Spent: a commit that main as last fetched
(`refs/remotes/<remote>/<main>`) reaches carries its `Owner-Approval: hub:A-<n>` trailer
(`approvals.spent`), so the change is on main; the guard refuses any other commit that carries it then. The hub
learns of it from the landing feed (`spent` and `spent_at`, "landings/L-NNN"), for a later page only.
The guard judges a commit by its delta, not its exact parent: at the approval's base each approved path
is its base blob, and at the commit each is what the approved change gives on the commit's parent
(`approvals.delta_result`: the approved blob where the parent is the base or already the approved
blob, else a clean `git merge-file` of the parent, the base and the approved content), as a regular
file in the parent's mode (`approvals.delta_error`). So a branch rebuilt on a moved main still lands,
and a commit that undoes or adds to the approved change does not.

A decided approval that is not consumed is pending owner input (docs/hub-pm.md "Who handles a pending item"): for the
coordinator of its repository and for the PM, until it is consumed, superseded or expires, and a denied one for 24
hours after the decision. The server pushes it to both at the decision and, if it is still approved and
not consumed, once more 4 hours before it expires (docs/hub-push.md "Push notifications").

An agent asks for one with `hub.py approvals add --title T --file PATH CONTENT_FILE [--file ...]
[--checkout DIR] [--repo R] [--head SHA] [--reason TEXT]`, or the MCP tool `hub_approval_request`
(docs/hub-mcp.md); both build the body with `kitlib.hubapprove` and post it to POST `/approvals`, and
neither decides or consumes a request. Each PATH (relative to the repository top, with `/`) gets
CONTENT_FILE's exact bytes as its content, read as UTF-8 with its line endings kept, so the blob is the
one `commit.py --owner-approval` writes; the work tree is never read for it, since the consumer needs the
file still at its base. The base is PATH's blob at `--head` (default: the checkout's HEAD), none for a
file absent there; `--repo` defaults to the checkout's origin. It refuses before anything is posted, in
the builder's or `approvals.new_doc`'s words (`kitlib/hubapprove`'s docstring lists them): a checkout that
is not a git work tree or has no HEAD or origin it can name, a bad head, no file, a path that is not a
plain repository path or that an owner approval may not carry (a render source in a kit checkout, a path
the repository's owner rules do not lock: `hubapprove.owner_paths_error` says where each is committed
instead), a new file the work tree already has, a file at head whose work tree copy git does not hash to
its base, unreadable, non-UTF-8 or over-`approvals.MAX_CONTENT_BYTES` content, content that is the file at
head, content git would store as another blob (core.autocrlf or a text, eol or filter attribute changes it
on staging, which the consumer's staged check refuses), and what `new_doc` refuses (`MAX_FILES`,
`MAX_PATH`, `MAX_TITLE`, `MAX_REASON`). What the hub refuses comes back as the hub said it (`REFUSED
<code>: <message>`).

Known limits:
- `approvals add` (and `hub_approval_request`) cannot request a file delete: every file it names gets
  content. A delete is asked for by a direct POST `/approvals` with a null `content`.
- A request has no `from`: the document has no field for the session key that asked, so neither the verb
  nor the tool takes `--from`.

`GET /api/v1/snapshot` carries the collection whole, and `strong`: true only on the strong owner's call.
The page draws each request as a card in the Approvals section (`approvalCard`; Waiting and Decided tabs, docs/hub-using.md "Using the hub"), the pending ones counted in the Approvals
badge: its title, repo, head, reason and the stored diff as text. Approve and Deny post the decide route
with the version the page read; they are disabled unless the snapshot says `strong`, and a refusal (a 403
included) is shown on the card in the server's words.

## landings/L-NNN
The landing feed: one event per branch `tools/land.py` landed, posted by the landing itself after the
push is verified and the landing lock released (docs/gate-landing.md "Landing feed"). The event is the land
ledger line's fields (`result.LEDGER_FIELDS`, picked from the same land record) and the extras
(`kitlib.hubsite.landings.EXTRA_FIELDS`); the types are `schema.FIELDS['landings']`.

| Field | Written by | Meaning |
|---|---|---|
| (doc id) | server | `L-001`, `L-002`, ...: the document's id, not a field; a higher number is newer |
| branch, tip, run_id, started_at, finished_at, verdict, code, summary, gate_once, escalated | agent | the branch's land ledger line, field for field |
| repo | agent | the repository's name (`runner.repo_name`) |
| main_branch, main | agent | the main branch and the new main the landing pushed: one commit for every branch of a batch |
| records | agent | the backlog record ids the landing closed for this branch (a list, maybe empty) |
| mode | agent | the merged gate's mode, from its gate-OK record on the new main, or null |
| deploy | agent | the `Deploy:` trailer values of the tip's message, joined by `; `, or null |
| approvals | agent | `{A-<n>: sha}`: each hub approval a commit of main-before-the-landing..tip carries as its one `Owner-Approval: hub:A-<n>` value, with the newest such commit (`approvals.landed`); absent when there is none, so a hub older than the field still takes the event |
| posted_at | server | ISO time the hub took it |

Routes, below `/api/v1`: POST `/landings` (agent) adds one event: repo, branch, tip and main are required,
any other field is refused, and posted_at is the server's. Each file approval `approvals` names
that is `consumed`, is of the event's repo (the hub's repo name for its `repo` remote) and has no
`spent` yet is marked `spent` (its sha) and `spent_at` (the post's time); any other (not consumed,
another repo's or one the hub cannot name, an op approval, an unknown id) is skipped, since any agent
token may post an event, and the answer's `spent` lists the ids marked. GET `/landings` (any role) lists them newest
first, filtered by `repo`, at most `limit` (default `FEED_MAX`, 200). No route changes or deletes one.

Every event is kept; `GET /api/v1/snapshot` and the page carry the newest 200. An event is never pending
and never pushed: the feed is read, not answered. The post is best-effort with a 3 s timeout: a hub that
is down, has no agent token on this machine, does not answer, or is older than the route (a 404) is one
WARNING line in the landing's progress, and the event is dropped: no pending file, no retry. The landing's
outcome and exit code never change.

The page's Landings tab (`#landings`, under More on a phone) is a summary, drawn in the list with no side pane: one
card per repository (its landing count, and how many were not a clean pass), and in it one line per landing, newest
first. A line is the branch, the records it closed (each id with its title, looked up from the open and the closed
records the page already loads, by repo and id; the bare id when none is found), a result chip (Passed; Landed, not
gated; Failed, Refused and the other verdicts; a verdict the page does not know is shown as it is, and counts as not
clean), a Deploy chip when the tip carried a deploy note, and how long ago it was posted (read when the tab is drawn).
A landing that closed no records shows its gate summary where the records would be. A line opens in place to the
summary, tip, new main and gate (and the deploy note), with the event as posted in a "Raw event" fold; the event's data
format is unchanged. The filters are the tab's own and client side, over the newest 200 events the page holds: repo,
result (Passed, or Not a clean pass: any verdict but `pass`) and date (a quick range of 24 hours, 7 days or 30 days,
or from and to dates, which clear each other); Clear resets them, and they start fresh each time the tab is shown. A
date range that reaches past the oldest event held says the hub keeps only the newest 200.

## handoffs/C-NNN
A session's hand-off record: written beside its ready reply to the PM (skill session-handoff step 5),
with the reply's word and state note path, through the MCP tool `hub_handoff_ready` or `tools/hub.py
handoffs add`. The types are `schema.FIELDS['handoffs']`; the source is `kitlib/hubsite/api.py`
docstring "Hand-offs".

| Field | Written by | Meaning |
|---|---|---|
| (doc id) | server | `C-001`, `C-002`, ...: the document's id, not a field; a higher number is newer |
| from | agent | the session's key: a launcher session key (sessions.json, read on each add) or `pm`, checked as a question's `from` is |
| kind | agent | `handoff`, `restart` or `stop` (`api.HANDOFF_KINDS`): the ready reply's word; or `checkin`: a session started by a hand-off has read its note and taken over (the usage crossover stops the old session on it, docs/launcher.md "Usage crossover") |
| state_path | agent | the absolute path of the state note, on one line |
| at | server | ISO time the hub took it |

Routes, below `/api/v1`: POST `/handoffs` (agent) adds one record: all three fields are required and any
other is refused (`invalid`, naming the field: an unknown kind, a blank, relative or multi-line path, a key
the hub does not know). GET `/handoffs` (any role) gives `{items}`, the newest record of each session
(the highest id per `from`), sorted by key, or with `from=<key>` that key's alone (an unknown key: none).
Over the tunnel only, the state path is cut to `.../<its file name>`; every other caller gets it whole. Every record is
kept; none is pending or pushed, no route changes or deletes one, and `GET /api/v1/snapshot` leaves them
out. `hub.py handoffs [--from KEY]` prints `C-1 <kind> from=<key> at=<time> <path>` per session.

