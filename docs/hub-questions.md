# Action Hub: questions

## questions/Q-NNN
| Field | Written by | Meaning |
|---|---|---|
| status | PM, owner, hub | `open`, then `answered` (owner), then `logged` (PM), or `done` (PM: no ruling needed, `questions done`), or `filed` (hub: the answer was filed into the bound record, "Filing the answer"), then `logged` or `done` (its asker), or `done` (hub, with `done_by` `hub`, once its asker had it: "Done by the hub") |
| repo, from, asked_at | PM, coordinator | where it came from; `from` is `pm` or the asking coordinator's launcher session key, the session its answer goes back to; asked_at is ISO time |
| title | PM | the question itself |
| context | PM | everything needed to answer; nothing is assumed from chat |
| options | PM | `[{label, description}]`, recommendation first |
| recommended | PM | index into options, normally 0 |
| urgent | PM | true sorts it to the top with an Urgent marker; only for items that block work or have a deadline |
| refs, story | PM | ids it refers to; the story a follow-up question belongs to |
| answer | owner | `{choice: index or "other", label, note, at, via}` |
| done_why, done_at | PM, asker, hub | why the answer needed no ruling, and when it was marked done (ISO time); the hub's is `filed into <repo>:<id>` |
| done_by | hub | `hub` when the hub marked it done ("Done by the hub"), for the PM's audit; null otherwise, and cleared by a re-answer or a promote |
| unfiled_why | PM | why an answer whose filing failed for good was logged or marked done anyway ("Filing the answer") |
| record | PM, coordinator | `{repo, id}`: the backlog record the question is about; absent or null on an unbound question |
| class | PM, coordinator | `rule`, `machine`, `cross-repo` or `go`: what kind of question one bound to no record is ("A question's class"); absent or null on a bound question |
| filing | hub | `{at, code, message}`: why the last filing of the answer failed; null once filed or answered again |
| filed_at | hub | when the answer was filed into the record (ISO time); null after a re-answer |
| filed_as | hub | what the filing did to the record: `pulled`, `freed`, `held`, or null when the record's state could not be read; null after a re-answer ("Filing the answer") |
| park | hub | `{at, code, message}`: what binding the question did to its record ("A question bound to a record"): `blocked`, `already`, `held`, `parked` or `gone`, or the code of a park that failed (`store`, `unavailable` and `internal` are tried again); `message` is a fixed phrase (`PARK_SAYS`); null after an unbind or a rebind; a rebind parks the new record |
| standing | owner | `{at, via}`: the owner made the answer a standing rule, for the PM to log ("A standing rule"); null after a re-answer without the box |

While a question is open its asker may change it (`POST /api/v1/questions/{id}/edit`, agent only:
`hub.py questions edit`, the MCP tool `hub_question_edit`) or withdraw it (`POST
/api/v1/questions/{id}/delete`: `questions withdraw`, `hub_question_withdraw`), which removes the
document. Both take `from` and `if_version`, both required, and are refused `forbidden` unless `from` is
the question's own (`pm` for a question with none), and `conflict`, field `status`, once it is no longer open
(`questions_edit`, `questions_delete`). An edit's fields replace the old ones; `options` replaces the
whole list. The owner withdraws an open question with the card's Withdraw button (the same delete route,
owner token, version optional) and never edits one. All agents share one token, so `from` is a claim
checked against the item: the check stops honest mistakes, not a hostile agent, and any agent claiming
`pm` can change the PM's items. An answer sent without `--if-version` (`hub.py answer`) after the asker
changed the options is taken against the options as they are now; the page always sends the version it
shows.

### A question's class
A question is asked with `record` (below) or with `class`, never both and never neither (`questions add
--class C`, MCP `hub_ask` `class`; `kitlib.hubvalidate` `QUESTION_CLASSES`): `rule` (a standing rule, a
delegation run line included), `machine` (this machine), `cross-repo` (work across repositories) or `go`
(a one-off landing or archive go). Either is refused `invalid`, field `class`, before any backlog is
read. An edit that unbinds a question (`--no-record`, a null `record`) needs `class` too unless it has
one; an edit that binds one drops its class; `class` on a question that stays bound is refused. A
question asked before the field existed has neither and still takes an edit of anything else. `GET
/pending` carries `class` on a classed, unbound question, and `hub.py pending --for pm` lists those
answers apart, after an `AUDIT` line: they have no record to be filed into, so the PM audits each when it
logs it or marks it done. The page shows a `class: C` chip on the card.

### A question bound to a record
`record` is given when the question is asked (`questions add --record REPO:ID`, MCP `hub_ask` `record`) or
by an edit while it is open (`questions edit --record REPO:ID`, `--no-record` or a null `record` to unbind
it). The hub reads the record then, through the record view the record page uses
(`kitlib.hubsite.backlog` `Views.record`: the configured repositories only, read now with no fetch), and
stores `repo` as the configured name. The kit's own records are bound under the PM repository's folder
name, as the hub names every configured repository (`<pm-folder>:<ID>`); `pm:<ID>` and `kit:<ID>` are
refused `not_found`. Each of these is refused, field `record`, and nothing is written (`_bound`):
- `invalid`: a `record` that is not exactly `{repo, id}` with two non-blank strings, a name that can be
  no repository or record id, a record that is completed (closed, retired or taken out of the store), or
  one in a repository whose `.kit.json` names no `"backlog".store` (the record view's `writable` false):
  its answer could never be filed (`no_store`), so it is asked unbound;
- `not_found`: a repo the hub has not configured, or an id that repo's backlog does not have (the refusal names another repo key whose backlog holds the id, e.g. a kit record is keyed on the PM repo's folder name);
- `unavailable`: a record the hub cannot check (a server with no backlog views, no checkout of the repo
  on this machine, a store that cannot be read). It fails closed: a binding nobody checked is never
  stored. Ask again, or ask unbound.

An edit checks its asker and its status first, so a refused edit reads no backlog.

Binding a question, when it is asked or by an edit, parks its record: the hub, as the owner's proxy,
blocks it on `owner` until the question is answered (`kitlib.hubsite.api` `park_item`,
`kitlib.hubsite.recordwrite` `park`, one store commit under `Session: hub-park`). An open record gets
the line `until: owner question:Q-n answered` and the body line `asked as Q-n on the Action Hub by
<asker>`. A pulled record is blocked the same way with its pull stopped and its branch kept and named;
the answer that ends the hold restores the pull while that branch still holds its tip. A blocked record
gains `owner` beside its other blockers. Several asks on one record are several lines, read as AND
(docs/backlog.md "block --until"). Nothing is written to a record parked by hand (`parked`: the hub
never touches a park), one held on the owner by hand with no owner line (`held`: that hold keeps its
own end, and the filed answer still ends it), one that has the line already (`already`) or one that is
gone (`gone`). The outcome is the question's `park`. The hub server parks off the request
(`Api.park_later`, a daemon thread, `serve._park_later`). A park that fails there is an `error` line
of the hub's log (`<qid>: record not parked: <code>`), and one whose code is `store`, `busy`,
`unavailable` or `internal` is made again by the hub's refile job while the ask is still asking
("Filing the answer").

Withdrawing or deleting an open bound question, or an edit that rebinds or unbinds it, drops its line
from the old record (`unpark_item`, `recordwrite` `withdraw`); the last owner line unblocks the
record with the reason `ask withdrawn`, and a rebind then parks the new record. A drop that fails is
logged (`<qid>: record not unparked: <code>`) and swept by the refile job's full pass: at hub start,
or after a `questions file` call without an id. A question asked with `class` whose `refs` name a pulled record is stored
with a `refs-pulled-record` warning, field `refs`: ask it with `--record` so the hub parks the record.

An agent's to-do and a file approval bind and park the same way, with the same checks and outcomes,
but only when they are added (`todos add --record`, `approvals add --record`; MCP `hub_todo_add` and
`hub_approval_request` `record`). The line is `until: owner todo:T-n done` or `until: owner
approval:A-n decided`, and the body line names the asker, `pm` for an approval. The owner's own to-do,
any to-do edit and an `op` approval take no `record` (`invalid`, field `record`). The hub's
`unblock-conditions` job ends the hold when the to-do is done or the approval is decided, approved or
denied (docs/hub-push.md "Push notifications"); withdrawing or deleting the item drops its line. An approval that
expires undecided keeps its line, and the job pushes the PM `approval-expired`; the requester may
then withdraw it. The refile job makes a failed park again and its full pass sweeps a gone item's
line, for all three kinds. Known limits: a hub restart pushes an expired approval's line again, and a reopened to-do
does not park its record again.

#### Binding the open questions
`hub.py questions bind --dry-run | --apply` (`POST /api/v1/questions/bind`, agent, body `{from: "pm",
apply}`; refused `forbidden`, field `from`, for any other `from`, as `questions done`) pairs the
questions that exist before the binding with their records. It reads every configured repository's
backlog (`--apply` reads it fresh, a fetch round; `--dry-run` the held snapshot): a parked record's `q:`
line, and an owner-blocked record's `owner_ask`, `q`, `blocked_on` and `Blocked on` lines, each give the
Q-nnn they cite (Q-012 is Q-12). A record citing one open hub question is a pair. It answers `{pairs,
misses, multi, skipped, failed}`: a `miss` is a record whose question is not in the hub or is neither
open nor answered (a filed, logged or done question is never bound), a parked record citing none, or a
repository or record that could not be read; `multi` are records citing several questions, left alone;
`skipped` is a question already bound or cited by two records. `--dry-run` writes nothing; `--apply` sets
`record` on each pair's open or answered, unbound question, checked as `_bound` checks it (a failure is
in `failed`), and files an answered one at once (`file_answer`), its pair carrying `filing` `{filed,
code, message}` (`FILED Q-1` or `NOT FILED Q-1: <why> (<code>)`), `{filed: false, moved: true}` when the
answer handler filed it first (`NOT FILED Q-1: moved (filed by the answer handler)`), or `{filed: false,
unanswered: true}` when it was no longer answered (`NOT FILED Q-1: no longer answered`).

#### Filing the answer
The hub files the owner's answer to a bound question into the record itself, in this order
(`kitlib.hubsite.api` `questions_answer`, `file_answer`):
1. The answer is stored as for any question: status `answered`, `answer`. A re-answer also clears
   `filing`, `filed_at` and `filed_as`, and a `filed` question takes a new answer like an answered one.
2. The record is written through the backlog library (`kitlib.hubsite.recordwrite` `answer`, the store's
   `plan_answer`, with the role check made: the hub files only the answer the owner gave, whoever's call
   starts it): an `answer: <qid> <at>` line, a `## Owner answer (Action Hub, <at>)` body section, and,
   when no other question bound to the record is still open, the record's park or owner blocker ends
   (an owner hold kept as `until` lines ends when its last line drops, docs/backlog.md `answer:`). The
   store's compare-and-swap plans the write again after a lost race. The same question and `at` again
   changes nothing, and an older `at` never overwrites a newer answer, so this step can be repeated after
   a crash.
3. The question is marked `filed`, with `filed_at` and `filed_as`, pinned to the version of step 1.
   `filed_as` is what the answer did to the record, from its `state` read through the record view just
   before step 2 and again after it (`_record_state`, `_filed_as`): `pulled` when it was pulled before
   (someone is working on it) or the answer ended a hold that had stopped its pull, which is back,
   `freed` when it was parked or blocked and is now open (a free-text Other
   answer that ends the wait frees it too), `held` otherwise (it still waits on more, or it was open), and
   null when a read that decides it cannot tell (no record view, a view that raised, no fields, a
   completed record). The push reads it to choose who hears. A filed question stays pending for its
   asker, with `filed` true, until the asker has it, and the hub then marks it done ("Done by the
   hub"); until then the asker may log it (`questions log`, once its ruling is in the ledger) or mark it
   done (`questions done`), so an asker busy when the owner answers still finds the answer in the push
   and on its next `pending --for KEY`. It is the PM's only when the PM asked it or its asker
   cannot be reached (docs/hub-pm.md "Who handles a pending item"). The page shows a filed card as `filed in <record id>
   · waiting for <asker> to read it` (the PM for its own question) and keeps its Change answer button: a
   re-answer files again. A delegation run line answered on a bound question waits for the asker's log
   step too: a filed question is refused until it is logged (`kitlib.delegations.run` `hub_order`).

When step 2 fails the question stays `answered`, so it stays pending for the PM, and gains `filing` `{at,
code, message}`: `not_found` (the record is completed or gone), `invalid` (the store refused the answer)
and `no_store` (the repository's `.kit.json` names no `"backlog".store`, so the hub may write none) leave
the answer to the PM, who handles it as an unbound one; `store`, `busy` (another local writer held
the backlog store's write lock past the hub's 5 s wait, docs/backlog.md "The store write lock") and
`unavailable` say the hub tries again, and the refile job below does so on its own. A final code is
tried again only by the job's full pass and by a `questions file` call, which file the answer once what
made it fail is mended (a record reopened, a store named). `message` is a fixed phrase (`FILING_SAYS`),
never the store's text, which can name a path; the refile job writes that text to the hub's log (the
owner's), and an answer route that files in its own call (below) carries it, to the local owner, in its
`filing`. A retry that fails the same way (the code and phrase `filing` already has) writes nothing and
keeps the first `at`, so it moves no version an asker's `--if-version` holds. A mark (step 3, or
`filing`) whose pin no longer holds writes nothing: the question was answered again, logged or marked
done meanwhile, and the call that moved it does its own filing.

The hub's refile job (`kitlib.hubsite.refile`, job `refile`, every 30 s, its first run 5 s after
start, docs/hub-push.md "Push notifications", timed jobs) files again. Its first run is the full pass: every answered, bound question
not filed yet is filed, whatever code its last filing failed with, and every open bound ask whose park
is not made yet is parked (a question, a to-do, an approval). It also sweeps every repository's owner
`question:Q-n answered` lines (`_sweep_asks`, read with no fetch): a line the hub wrote (the body says
`asked as Q-n on the Action Hub`) whose question is gone, or now bound to another record or none, is
dropped as a withdraw drops it (a drop that fails is logged, `<qid>: record not unparked: <why>`). A
line a person wrote is never dropped: one whose question is gone is logged (`<repo>:<ID>: owner line
cites missing Q-n`), for the PM to clear by hand. A crash after step 1 or after step 2 is mended there.
An unbound question is never filed: the PM logs it as before.

Each later run does only what is due: a filing or park that failed with a code to retry (`store`,
`busy`, `unavailable`, `internal`), on an ask still asking, and an answered bound question with no
`filing` and no `filed_at` whose answer is 5 minutes old (its filing off the request never marked it).
An id that failed waits 30 s, doubled after each failure up to 15 minutes, before it is due again; the
wait is kept in memory, keyed on the answer's `at`, so a new answer starts it afresh and a hub restart
forgets it. Once a write on a repository answers `busy`, the rest of that repository's ids wait for the
next run. Each failure is a `note` line of the hub's log with the store's own text (`<qid> not filed
(<code>): <text>`, `<qid>: record not parked (<code>): <phrase>`); the question keeps the fixed phrase.
A run with nothing due is skipped.

`hub.py questions file [Q-1]` (`POST /api/v1/questions/file`, agent, optional `{id}`) queues work for
that job and returns at once: `{ok: true, queued: [id]}` (`QUEUED Q-1`) or `{ok: true, queued: "all"}`
(`QUEUED all`); an id that names no question is refused `not_found` and queues nothing. A queued id is filed and parked whatever its wait; `all` makes the job run its whole
full pass again, the withdraw sweep included. The job runs at once on a thread of its own; when a run is
going already, that run makes one more pass. A request that lands after a running pass's last check
waits for the next run, up to 30 s: a delay, never a loss. The outcome is in the question's `filing`
and `park` and in the hub's log, never in the reply. An `Api` with no refile job (the library's callers,
the tests) files in the call and answers `{filed, failed, parked, unparked, park_failed,
orphan_hand_lines}` as before: `filed` lists the ids filed, `failed` and `park_failed` `[{id, code,
message}]`, `unparked` `[{id, record, unblocked}]`, `orphan_hand_lines` `[{repo, id, q}]`.

The hub server files off the answer's request (`Api.file_later`, set by `serve.build`: steps 2 and 3 on a
daemon thread, `serve._file_later`), since step 2 fetches and pushes the backlog store, which can take
seconds, up to a minute a try when the remote is slow. The answer route returns once step 1 is written,
with status `answered` and no `filing`. The page's card then says `waiting to be filed in <record id>`
until its wait and snapshot bring the `filed` mark, and the card says `filed in <record id>`. The page
shows no `filing`: an answer whose filing failed keeps saying `waiting to be filed in <record id>`, and
the PM, who has it pending, handles it. A raise on that thread is an `error` line of the
hub's log (`filing an answer to <qid> failed: <type>`) and the question stays `answered` for the retries above.
An `Api` with no `file_later` (the library's callers, the tests) files in the answer's own call: its answer
then carries `filing` (`{id, filed, version, revision}`, `{id, filed: false, code, message}`, or `{id,
filed: false, moved: true}`), and its `status`, `version` and `revision` are those of the last write.

An answered, bound question not filed is neither logged nor marked done (`questions log`, `questions
done`, `_unfiled`): while its last code may still file (every code outside `FINAL_FILING`, such as
`store`, `busy`, `unavailable` and `internal`, or no filing yet) both are refused `invalid`, field `filing`: file
it first. For a final code (`not_found`, `invalid`, `no_store`) they need `--unfiled "<why>"` (body
`unfiled`), refused `invalid`, field `unfiled`, when absent or blank; the why is kept as `unfiled_why`.
`unfiled` on any other question is refused the same way.

Known limits:
- A filed question has its own Filed tab on the page (no attention chip), and `hub.py pending` and `wait
  --pending` mark it ` (filed)` after the title.
- After the store reports an Uncommitted error the line is already in the record, so the retry changes
  nothing and marks the question `filed` before the store's catch-up applies it.

#### Done by the hub
A filed answer is the asker's to act on, and it needs no ruling: the record already carries it. So the
hub marks the question done itself once its asker has it, and nobody has to (`kitlib.hubsite.push`
`auto_done_ok`, `_hub_done`):
- Which: a question with status `filed`, a known `filed_as` (`pulled`, `freed` or `held`) and a record,
  that is not a standing rule and whose options and answer carry no delegation run line (`run D-NNN`,
  which waits for its asker's log step, `kitlib.delegations.run` `hub_order`). A standing rule, an
  unbound question (a classed one too), an answer whose filing failed and one filed with no `filed_as`
  stay as they are, for the PM or their asker.
- When: never in the filing's own write. The push worker writes it once the asker has the answer: its
  pick-up (`pending --for` or `wait --pending --for` hands it over), or, when the PM asked, the first
  written try of the PM's push; an asker that picked it up before the push fired has it at once. When
  the push goes to a stand-in (the record's coordinator, or the PM's `freed` ping), the asker is still
  owed it, unsent: it is done once the asker reads it, and the stand-in's push and the `freed` ping run
  their course first. An asker that never reads it leaves it `filed` (no gave-up line for it).
- How: status `done`, `done_by` `hub`, `done_why` `filed into <repo>:<id>`, `done_at`, via `hub-push`,
  pinned to the version the question had when its push fired: a new answer, a promote, the asker's own
  log or done, or any other write since wins, the question stays as it is and the server logs `push: Q-n
  not marked done: conflict`. The entry survives a server restart (`push-outbox.json`).
- Afterwards: it is no longer pending. Its card says `filed in <record id> · done by the hub once <asker>
  had it` and keeps Change answer (a re-answer reopens it: `answered`, `done_by`, `done_why` and
  `done_at` cleared, filed again, and pushed to its asker afresh) and Make it a standing rule (a promote
  files it again: status `filed`, for the PM to log). The PM start's digest counts it apart
  (docs/pm-start.md "Filed answers").

#### A standing rule
An answer to a bound question goes to its asker, so the PM would not log it as a ruling. When the owner
wants the answer to hold beyond this record, so the same question is not asked again, the owner makes it a
standing rule, which the PM logs in the ledger (`questions log`, docs/ledger.md).
- On the answer: `POST /api/v1/questions/{id}/answer` with `standing` true beside `choice` sets `standing`
  `{at, via}` (the answer's own). A re-answer without it clears `standing`. It is refused `invalid`, field
  `standing`, on a question bound to no record: the PM logs every such answer already.
- Afterwards (promote): the same route with `standing` true and no `choice` keeps the answer as it is and
  files nothing again; it sets `standing` `{at, via}` at the time of the promote (`_promote`). It takes an
  optional `if_version`; a `note` is refused `invalid`, field `note`. It is refused `invalid`, field
  `status`, unless the question is `answered`, `filed`, or done by the hub (`done_by` `hub`; the promote
  makes it `filed` again) (an answer whose filing failed can be promoted),
  field `record` for a question bound to no record, and field `standing` for one already standing. There
  is no separate route.
- Who reads it: a filed standing rule stays pending for its asker and is also pending for the PM
  (`pending --for pm`, with `standing` true), and the push tells the PM to log it until it is logged.
  `questions done` is refused `invalid`, field `standing`: the asker's routine done would end the PM's push
  with nothing logged.
- The page: a bound question's card has a "Make this a standing rule" box beside the options, ticked
  again on a re-answer of a standing one; it sends `standing` true only when ticked. A filed card, or one
  the hub marked done, that is not standing has a "Make it a standing rule" button (the promote, with the version it shows), and a
  standing card says `a standing rule, for the PM to log`.

Known limits:
- Until the PM logs it, a filed standing rule stays in its asker's pending list too: the asker cannot
  mark it done.
- The page offers the promote only on a filed card or one the hub marked done: an answer whose filing
  failed is promoted through the route, or by answering again with the box ticked.

