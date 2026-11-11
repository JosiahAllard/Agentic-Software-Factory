"""kitlib.hubsite.api: the hub's verbs as functions over the store, with no transport in them
(docs/modules/hub-design.md "Packages and files").

  ROUTES     the table every transport iterates: Route(method, pattern, role, handler). A pattern
             is a full path under BASE; a `{name}` segment takes one path segment. role is OWNER,
             AGENT, ANY, APPROVER or LOCK_WRITE: who may call it ("Roles", "Machine lock" in
             kitlib.hubsite.apilock).
  Api(store, views=None, clock=None, now=None, lock=None, config=None, **hooks)
             .call(method, path, role, body=None, query=None, via='', raise_unexpected=False,
                   agent_words=False, strong=False) -> (status, object)
             via names the transport (web, desktop-mcp, cli, tunnel, ...); every write records
             it as the document's `via` (`api` when none is given), an answer in `answer.via`
             too. A delete leaves no document to carry it. raise_unexpected: an exception
             that is no HubError is raised to the caller, not answered ("What an agent is
             told"). agent_words: an error answer is worded as for an agent whatever the role
             is, which stays the role for what the request may do.
  match(method, path)    (Route, {name: segment}) or None
  sample_path(route)     the pattern with every `{name}` filled in, for a test that walks ROUTES

Roles. The caller resolves who is asking and passes OWNER or AGENT; this module only enforces.
A role that is neither is refused before the path is looked at, a route whose role is not one of
ROLES is refused for everybody, and the check runs before the handler reads the body. So a route
added without a role answers `forbidden`, never the data.
APPROVER is a route role and never a caller: such a route answers only OWNER with strong=True,
the owner the transport saw log in through Cloudflare Access (kitlib.hubsite.tunnel's gate marks
that request, kitlib.hubsite.httpd passes it on). Every other caller is refused `forbidden`,
field `role`, with NOT_STRONG: the owner token, a browser session paired from it and an agent are
never strong. strong is a call argument, never a field of the body or the query, and it opens no
other route: on OWNER, AGENT and ANY routes the role alone decides. A handler reads it as
Request.strong. POST approvals/{id}/decide is an APPROVER route ("Approvals" in kitlib.hubsite.apiapprovals), and so are the
lock's reservation and division writes. LOCK_WRITE is a route role too: OWNER, and strong as
well when the call came over the tunnel ("Machine lock" in kitlib.hubsite.apilock).

Moved to the module that holds each route group: Approvals (apiapprovals), Pages (apipages),
Schedules (apisched), Findings (apifindings), Landings and Hand-offs (apilog), Skill edits
(apiskills), Machine lock (apilock), Fields and Creators (apibase).

Results. status is 200 with the route's object, or the status of STATUS[code] with
{"ok": false, "error": {"code", "message", "field"}}; both carry `revision`. The codes are the
store's HubError codes plus `forbidden`, `unavailable` (a view this server does not have, or one
that failed) and `internal` (anything unexpected). A write answers the revision after it (send:
after its last note), so a client that waits `since` it is not woken by its own write.

What an agent is told. A `store` refusal names files and the store's folder, and an unexpected
error may: the owner gets that text, any other caller gets the fixed phrase of AGENT_SAYS for a
`store` refusal and only the exception's class name for `internal`. The store's `invalid`,
`not_found` and `conflict` messages name a collection, an id and a field and go to both.
A transport whose answers leave this machine calls with agent_words=True: the owner then gets the
agent's wording too (the same status, code and field), so no answer of that transport names the
store's folder or a file path. kitlib.hubsite.httpd does for the tunnel listener.
A transport that has a log of its own calls with raise_unexpected=True: the unexpected error is
then raised to it and no answer is made here, so the transport can log the cause and send a
fixed message to every caller, the owner too (kitlib.hubsite.httpd does).

Views. The live backlog and the derived views are other modules; `views` is any object with some
of these methods, each one optional. A missing one answers `unavailable` on its own route, and
so does one that raises, with a short reason (a HubError's code, else the exception's class
name; never a message or a path). GET snapshot and GET health never fail for a view: the part
is null and `unavailable` maps its name to that same reason:
  backlog(repo='', state='', fresh=False)   [{repo, fetched_at, items}]
  completed(repo='', days=None)             [{repo, fetched_at, items}]
  notices()  repos()  usage()  agents()     a list each
  skills()                                  {available, skills|reason}: each skill's name and description
  skill(name)                               one skill with its body (GET skills/{name}); not_found
  findings()                                the lint's rows (GET findings; not in GET snapshot)
  lint_runs()                               [{repo, started, ledger, skip}]: each repo's last
                                            result's started and ledger, and its skip marker
                                            (kitlib.maintlint.slot.read_skip) or null
  health()                                  a dict merged into GET health (git, backlog, ...)
  record(repo, id)                          one record (GET records/{repo}/{id})
  record_template(repo)                     a new record's fields (GET records/{repo}/new)
The two record views differ: what they raise as a HubError is the answer (a 404 for a record or
repository that is not there, a 400 for a name that cannot be one), so their messages are fixed
phrases with no path, no id and nothing the caller sent; only anything else is `unavailable`.
Both are read now, never cached, and neither is part of GET snapshot.
  record_add(repo, id, summary, scopes, effort, ref)               the owner's four writes to
  record_edit(repo, id, version, changes, unset, body)             a repository's backlog store
  record_delete(repo, id, why)                                     (OWNER routes; kitlib.hubsite.
  record_unblock(repo, id, on, reason)                             recordwrite)
They raise HubErrors as the record views do; a `store` one carries the store's text, which only
the local owner is given. POST records/{repo}/{id}/delete takes `confirm`, which must repeat the id.
  record_answer(repo, id, qid, at, title, label, note, via, other, waiting)
                                            files a question's answer into the record it is
                                            bound to (recordwrite answer; file_answer)
An answer to a question bound to a record is filed by POST questions/{id}/answer as soon as it is
stored, and again by POST questions/file (AGENT, optional {id}) and at hub start for every
answered one not filed yet: file_answer. A filed question has status `filed` and stays pending,
with `filed` true, until its asker has it: the push worker then marks it done itself (done_by
`hub`, kitlib.hubsite.push "Done by the hub"), never in the filing's own write; the asker may
still log it or mark it done (POST questions/{id}/log or done; its done of one the hub marked
done is no conflict). So an asker between waits still reads the answer; the PM's list carries it
only when it is the PM's or routed to `pm` (Routes), or a standing rule. One the hub marked done
takes a new answer, which reopens it, and a promote, which files it again (HUB_DONE_CLEARED).
One that failed stays `answered` with `filing`,
for the PM. A filed question carries `filed_as`, what the answer did to its record (pulled,
freed, held: _filed_as, from the `record` view read before and after), which the push reads to
choose who hears it. `standing` {at, via} marks an answer the owner made a standing rule (the
answer's `standing` true, or later `standing` alone: _promote): the PM logs it.
POST questions/{id}/log and POST to_pm/{id}/reply take no ruling id (questions_log): a question
is marked logged with none. Documents an older hub logged keep their `ruling`; nothing reads it.

Nothing here opens a socket, starts a thread, prints, parses arguments or exits. GET wait (and GET
lock/wait) blocks in Store.wait for at most WAIT_MAX_S; how many may wait at once is the listener's
rule.

PM seen. GET snapshot carries `pm_seen`: {"waited", "pulled", "waiting"}. waited is the time
(the Api's clock) an AGENT-role caller last entered or left GET wait, pulled the time one last
read the pending work (GET pending, or GET wait with pending=1), waiting the number of agent
waits blocked in Store.wait right now. All three live in this Api object, not in the store: a
new Api (a server restart) starts at null, null and 0 until the next agent call. The owner's
own calls never count, nor does a call with `for` a key other than `pm` (another session's
wait for its own answers is not the PM listening). No lock is taken: each note is one assignment, and an open wait is one
key put into and popped from a dict, which the interpreter does as one step each.

Push status. GET snapshot carries `push`: null when this hub runs no push-notification worker,
else {"state", "last", "undelivered"} from PushWorker.status (kitlib.hubsite.push docstring
"Status"): state 'up' or 'down', last the latest delivery to the PM's state (null before the first),
undelivered the number of items some recipient has not yet had a written try of. The worker
sets Api.push_status when it is made; the snapshot reads it on the request's thread. GET health
carries the same `push` (Api.push_seen), so a hub with no worker or a dead one shows on health.

Watcher. GET snapshot and GET health carry `watcher`: null when this hub runs no push worker,
else {"state", "age"} from PushWorker.watcher_status (kitlib.hubsite.push docstring "Watcher"):
the PM's pm_watch heartbeat at the worker's latest read, state 'up', 'down' (older than
WATCHER_STALE_S) or 'none' (no heartbeat readable), age its seconds (null with none). A status
that raises is 'down' with a null age.

Code. GET snapshot and GET health carry `code`: null when this hub gives none, else {"stale",
"restart"} from Hub.code_state (kitlib.hubclient.codestale): stale is true once the hub code on disk
is newer than this process, restart the owner's to-do text then (null otherwise). The server sets
Api.code_status when it is built; a status that raises or is not an object gives null. The
tunnel's health holds fixed words only and carries none.

Jobs. GET health carries `jobs`: null when this hub runs no timed job, else {name: block} from
kitlib.hubsite.jobs Jobs.status, each block {"every_s", "runs", "failures", "running",
"last_at", "ok_at", "note", "error"} (kitlib.hubsite.jobs docstring). The server sets
Api.jobs_status when it is built; a status that raises or is not an object gives null. The
tunnel's health carries none.

Pending for one session. Every pending item carries `from`, the session key it is for
(Api.pending). GET pending and GET wait (with pending=1) take `for=<key>`: only that key's
items are listed (`for=pm`: see Routes), and such a wait is woken only by one of them, a reset or its timeout, so a
coordinator is not woken by another session's answer. A filtered read changes nothing: the
items it leaves out, and the ones it lists, stay pending for every reader until they are
handled (a question, answered or filed, until it is logged, or marked done when it needs no
ruling). Without `for` every item is listed, as before.
GET wait (with pending=1) also takes `except=<id>,<id>`: the ids the caller has already acted
on are left out, and such a wait is woken only by another item, so an answer that waits for the
PM's log is not returned again at once. It too only hides: nothing is marked.

Routes. Every pending item also carries `route`: who is to handle it now. It is its `from` key
while that session can be reached, else `pm`, so the PM is the fallback for a creator that is
gone. An item whose `from` is `pm` is the PM's. Otherwise, once a GET pending or GET wait `for`
its key has handed the item to that key (an agent call), the route is the key for
ACK_WINDOW_S after that first hand-over and `pm` from then on while it is still pending (the
creator clears a to-do with its ack). Before any hand-over the route is the key while the key is
reachable: a wait `for` it is blocked now, one started or ended within REACH_GRACE_S, or a push
written to it within ACK_WINDOW_S (Api.pushed, which the push worker calls for each written try
to a key other than `pm`), so a session told by push with no wait open keeps its items. `for=pm`
lists the items routed to `pm`, every answered question, whoever it is from (the PM logs each
ruling), and every decided approval; a filed question only when it is routed to `pm` (its asker
reads it). An approval has no `from`: it is for the coordinator of its
repository, the session key named as the configured repository whose origin remote is the
approval's `repo` (Api's repo_name), while that key is in sessions.json, else for `pm`. A
skill edit has no `from` either: it is for the kit's coordinator, the key Api's kit_key gives
(kit_session_key), on the same terms. A wait
`for=pm` is also woken when an item's route turns to `pm` with no write. All of
this lives in this Api object: a new Api (a server restart) routes every item to `pm` until its
creator waits again. As for PM seen, no lock is taken: each note is one dict step, a dict is
copied before it is walked, and a key's map of blocked waits is never removed, so a wait that
opens while another closes is not lost.

Revision. A read's `revision` is taken before the data is copied, so it is never later than the
data: a client that waits `since` it is told of any write the copy missed. GET health gives the
store's folder to the owner only.
"""
import contextlib
import contextvars
import dataclasses
import math
import re
import time

from kitlib import hubvalidate
# at load, not on each read: a kit refresh can remove the folder this process imported from
from kitlib.launcher import settings as lsettings, state as lstate

from . import schema
from .store import HubError
from .apibase import (  # noqa: F401 - re-exported: callers and tests read these off api
    BASE, API_VERSION, OWNER, AGENT, ANY, APPROVER, LOCK_WRITE, ROLES, CALLERS, NOT_STRONG,
    NOT_LOCK_WRITER, WRITES_WHY, STATUS, FEED_MAX, LIMIT_DIGITS, LOCK_POLL_S,
    LOCK_DAYS_MAX, TUNNEL_VIA, ACK_WINDOW_S, REACH_GRACE_S, ROUTE_SLACK_S, VIEWS, OWNER_FROM,
    AGENT_FROM, ApiConfig, DAYS_MAX, AGENT_SAYS, Route, Request, _TRUE, _FALSE,
    _NAME, _SURROGATE, _REF_SHAPED, PAGE_HTML_MAX, error_body, _invalid, _natural,
    _version, _fields, _typed, _text, _flag, _pin, _q, _q_flag, _q_int, _when, _via, _reason, _timeout,
    _one_of, _SEGMENT, SEGMENT_SAYS, _record_view, _agent_from, _session_from)
from .apipark import (  # noqa: F401 - re-exported: callers and tests read these off api
    ASKS, _PARK_SAYS, PARK_SAYS, PARK_RETRY, _UNPARK_FAILED, UNPARK_FAILED, _parks, _ask_coll,
    _until, _park_says, _asking, park_item, unpark_item, _park_now, _park_failed, _parked,
    _later_said, _sweep_asks, _unpark_now)
from .apipages import (  # noqa: F401 - re-exported: callers and tests read these off api
    pages_list, pages_publish, _pages_write, PAGE_PLACE_FIELDS, pages_place, pages_get,
    pages_delete)
from .apilog import (  # noqa: F401 - re-exported: callers and tests read these off api
    inbox_add, inbox_read, LANDING_FIELDS, _newest, landings_list, landings_add, _mark_spent,
    _repo_name_of, _CONTROL, newest_handoffs, handoffs_list, handoffs_add)
from .apiskills import (  # noqa: F401 - re-exported: callers and tests read these off api
    SKILL_EDIT_CHANGE, SKILL_EDIT_WITHDRAW, _skill_name, _skill_text, _skill_source, _blob_field,
    skill_edits_list, _source_at, _main_blob, skill_edits_get, skill_edits_add, skill_edits_edit,
    skill_edits_withdraw, _submitted, skill_edits_done, skill_edits_fail)
from .apifindings import (  # noqa: F401 - re-exported: callers and tests read these off api
    _FINGERPRINT, FINDING_COPY, FINDING_PATHS, _PATH_HEAD, _PATH_WORD, _ABS_PATH, _unpathed, _seen,
    _fp_field, _finding, findings_list, LEDGER_COUNTS, _utc, _lint_runs, _unresolved_marks,
    finding_marks_list, finding_marks_set, finding_marks_delete, _act_note)
from .apisched import (  # noqa: F401 - re-exported: callers and tests read these off api
    SCHEDULE_TASKS, CADENCES, DAYS, _AT, _schedule_repo, launcher_checkouts, tick_last_seen,
    TICK_STALE_S, tick_every_s, _aware, _tick_status, tick_run_state, _next_due, schedules_list,
    _first_run, _one_checkout, schedules_set)
from .apitodos import (  # noqa: F401 - re-exported: callers and tests read these off api
    todos_list, todo_steps, todos_add, _ASKS_REPORT,
    TODO_ASKS_REPORT, _asks_report, TODO_NOTE_MAX, _NOTE_CONTROL, _todo_note, todos_done, todos_ack,
    _own, todos_edit, todos_delete)
from .apirecord import (  # noqa: F401 - re-exported: callers and tests read these off api
    RECORD_SHAPE, _holders, _bound, _no_record, _creator, _still_open, _withdrawn)
from .apiposts import (  # noqa: F401 - re-exported: callers and tests read these off api
    to_pm_list, _unpath_act, to_pm_add, _queued, to_pm_edit, to_pm_delete, _refusal, to_pm_send,
    to_pm_reply)
from .apiapprovals import (  # noqa: F401 - re-exported: callers and tests read these off api
    _approval, approvals_add, approvals_decide, approvals_consume, approvals_delete,
    approvals_supersede, approvals_verify)
from .apiread import (  # noqa: F401 - re-exported: callers and tests read these off api
    backlog, completed, _segment, skill_get, skill_source_get, record, record_new, _strings,
    record_add, record_edit, record_delete, record_unblock, _listing, pending, _unpath_pending,
    wait, _wait_for, SNAPSHOT_SKIP, _snapshot_colls, snapshot, health)
from .apiask import (  # noqa: F401 - re-exported: callers and tests read these off api
    questions_list, _class, questions_add, _pulled_refs, _asked, _cited,
    BINDS_FROM, questions_bind, questions_edit, questions_delete)
from .apilock import (  # noqa: F401 - re-exported: callers and tests read these off api
    _remote, _lock, _writes, _viewed, lock_view, _wrote, _confirmed, _strs, lock_hold_add,
    lock_hold_edit, lock_hold_end, lock_division, lock_run_end, lock_run_top, lock_game_mode,
    lock_games, lock_stats, lock_wait)


def _segments(path):
    return path.split('/') if isinstance(path, str) else []


def match(method, path):
    """(Route, {name: segment}) for the route that method and path name, else None. A literal
    segment wins over a `{name}` one."""
    if not isinstance(method, str):
        return None
    want, best = _segments(path), None
    for route in ROUTES:
        have = _segments(route.pattern)
        if route.method != method.upper() or len(have) != len(want):
            continue
        params, literal = {}, 0
        for h, w in zip(have, want):
            if h.startswith('{') and h.endswith('}'):
                if not w:
                    break
                params[h[1:-1]] = w
            elif h != w:
                break
            else:
                literal += 1
        else:
            if best is None or literal > best[0]:
                best = (literal, route, params)
    return (best[1], best[2]) if best else None


def sample_path(route, value='X-1'):
    """route.pattern with every `{name}` segment replaced by value."""
    return re.sub(r'\{[^/}]*\}', value, route.pattern)


# launcher_keys' read inside one sessions_once(): a context variable, so each thread (each
# request) has its own
_ONCE = contextvars.ContextVar('kitlib.hubsite.api.sessions_once', default=None)


@contextlib.contextmanager
def sessions_once():
    """Within it, launcher_keys() reads sessions.json at most once in this thread's context, and
    every later call gives that read (or raises its error again): one pending call
    (Api._routed) reads the file once, however many of its items ask (the session keys, each
    approval's repository name, the kit coordinator's key). A scope inside another shares the
    outer one's read."""
    if _ONCE.get() is not None:
        yield
        return
    token = _ONCE.set({})
    try:
        yield
    finally:
        _ONCE.reset(token)


def launcher_keys():
    """The session keys of the launcher's sessions.json (kitlib.launcher, imported when this
    module loads), read now (inside sessions_once(), read once there). A launcher whose settings
    or that file cannot be read raises: the caller says so (Api.read_session_keys), never an
    empty set that reads as "no sessions"."""
    return {k for k, _ in _launcher_entries()}


def _launcher_entries():
    """[(key, entry)] of sessions.json, read now (inside sessions_once(), read once there and
    shared by launcher_keys and launcher_coordinators)."""
    memo = _ONCE.get()
    if memo is None:
        return list(lstate.entries(lstate.read_sessions(lsettings.load())))
    if 'entries' not in memo:
        try:
            memo['entries'] = (list(lstate.entries(lstate.read_sessions(lsettings.load()))), None)
        except Exception as e:      # noqa: BLE001 - kept, and raised to each caller in the scope
            memo['entries'] = (None, e)
    got, err = memo['entries']
    if err is not None:
        raise err
    return list(got)


def launcher_coordinators():
    """{session key: its `repo`} of the launcher's sessions.json entries that run a coordinator
    (`agent` set, as hubjobs.fill_watch.is_coordinator and kitlib.launcher.handoff read
    it), read now. Raises as launcher_keys does; Api.coordinator_keys says so."""
    return {k: e.get('repo') for k, e in _launcher_entries() if e.get('agent')}


def _for(d, for_key):
    """Whether pending item d (with `from`, `route` and `note`) is listed for for_key: every
    item with none; for AGENT_FROM, the ones routed to it, every question not filed (the PM logs
    each ruling; a filed answer is its asker's while the asker can be reached), every filed one
    the owner made a standing rule (`standing`: the PM logs it) and every approval (the owner's
    decision); for another key, its own."""
    if not for_key:
        return True
    if for_key == AGENT_FROM:
        return (d['route'] == AGENT_FROM or d['kind'] == 'approval'
                or (d['kind'] == 'question'
                    and (d.get('note') != 'filed' or d.get('standing') is True)))
    return d['from'] == for_key


# ------------------------------------------------------------------ the Api
class Api(object):
    """The hub's verbs over `store` (a kitlib.hubsite.store.Store). `views` gives the derived
    views (module docstring) or is None; `clock` gives the time stamp of a write as an ISO
    string (default: the store's clock); `session_keys` gives the launcher's session keys as an
    iterable, called afresh on each use (default: launcher_keys); `now` gives monotonic seconds
    for the routes (default: time.monotonic). `approval_key()` gives the approval key or None,
    read on each decide and verify; `checkout(repo_id)` gives the root of this machine's checkout
    of that repository or None ("Approvals" in apiapprovals); both default to None, which no approval passes.
    `repo_name(repo_id)` gives the name of this machine's configured repository with that remote,
    or None (default), which routes a decided approval to the PM alone ("Routes").
    `lock` is the machine lock's door or None ("Machine lock" in apilock). `schedule_repos()` gives the
    machine config's launcher.repos as [(label, main checkout, name)] (default:
    launcher_checkouts), `tick_seen()` the ISO time the automatic tasks' tick was last seen or
    None (default: tick_last_seen), `tick_state()` the tick's records by schedule key (default:
    tick_run_state); all are read on each use ("Schedules" in apisched). `kit_key()` gives the session key
    of the kit repository's coordinator, '' when none is known (default), so a submitted skill
    edit is the PM's; `kit_blob(branch, path)` gives the blob id of `path` on `branch` in this
    machine's kit checkout, or None when git cannot read it; with none given (default) every
    read is None, so the done route is refused; `kit_text(blob)` gives the text of that blob in
    the kit checkout (CRLF read as LF), or None (default: always None) ("Skill edits" in apiskills).
    `coordinators()` gives {session key: repo} of sessions.json's coordinator sessions, read on
    each supersede (default: launcher_coordinators; coordinator_keys).
    The hooks (session_keys, approval_key, checkout, repo_name, schedule_repos, tick_seen,
    tick_state, kit_key, kit_blob, kit_text, coordinators) are the fields of an ApiConfig, given
    as `config` or as keywords; a keyword overrides the config's field."""

    def __init__(self, store, views=None, clock=None, now=None, lock=None, config=None, **hooks):
        # the hooks are an ApiConfig's fields; given as keywords they override `config`'s, and a
        # name that is not one is a TypeError, as for any unknown keyword
        config = ApiConfig(**hooks) if config is None else dataclasses.replace(config, **hooks)
        self.store = store
        self.config = config
        self.coordinators = config.coordinators or launcher_coordinators
        self.kit_text = config.kit_text or (lambda blob: None)
        self.kit_key = config.kit_key or (lambda: '')
        self.kit_blob = config.kit_blob or (lambda branch, path: None)
        self.tick_state = config.tick_state or tick_run_state
        self.schedule_repos = config.schedule_repos or launcher_checkouts
        self.tick_seen = config.tick_seen or tick_last_seen
        self.views = views
        self.lock = lock
        self.lock_poll_s = LOCK_POLL_S
        self.clock = clock or store.clock
        self.approval_key = config.approval_key or (lambda: None)
        self.checkout = config.checkout or (lambda repo: None)
        self.repo_name = config.repo_name or (lambda repo: None)
        self._session_keys = config.session_keys or launcher_keys
        self._seen = {'waited': None, 'pulled': None}
        self._waits = {}            # one key per agent wait that is blocked right now
        self.push_status = None     # PushWorker.status, set by the worker ("Push status")
        # routes (module docstring "Routes"): one dict step per note, no lock
        self.now = now or time.monotonic
        self.ack_window_s = ACK_WINDOW_S
        self.reach_grace_s = REACH_GRACE_S
        self._listening = {}        # session key -> {wait token: True} for its blocked waits
        self._heard = {}            # session key -> `now` its last wait started or ended
        self._handed = {}           # (kind, id) -> (session key, `now` of the first hand-over)
        self._pushed = {}           # session key -> `now` of the latest push written to it
        self.watcher_status = None  # PushWorker.watcher_status, set by the worker ("Watcher")
        self.code_status = None     # Hub.code_state, set by serve.build ("Code")
        self.jobs_status = None     # jobs.Jobs.status, set by serve.build ("Jobs")
        # run(qid, fn): fn, the filing of the owner's answer to qid, off the request
        # thread; set by serve.build. None files it in the answer's own call
        # (questions_answer)
        self.file_later = None
        # run(qid, fn): fn, the park (or its undoing) of the record a question is bound to, off
        # the request thread; set by serve.build. None runs it in the question's own call
        # (questions_add, questions_edit, questions_delete)
        self.park_later = None
        # held by every record write an ask drives (file_answer, park_item, unpark_item): each
        # reads the ask and writes the record as one step, so a park and the filing or
        # withdrawal of the same ask never interleave. serve.build sets a
        # threading.RLock with file_later and park_later; without them every such write runs in
        # its request's own call, so a context that holds nothing does
        self.record_lock = contextlib.nullcontext()

    def call(self, method, path, role, body=None, query=None, via='', raise_unexpected=False,
             agent_words=False, strong=False):
        """(status, object) for one request. role is OWNER or AGENT as the caller resolved it;
        body is the decoded JSON body or None; query is {name: value}; via names the transport
        (web, cli, ...) and is recorded on every write. With raise_unexpected an exception that
        is no HubError is raised, not answered: the caller logs it and words its own 500. With
        agent_words=True an error answer says what an agent is told (module docstring), to the
        owner too; only False leaves the owner the error's own text, so a value that is neither
        is read as True. The role decides what the request may do either way. strong=True says
        the caller is the owner logged in through Cloudflare Access (module docstring, "Roles");
        it counts only for OWNER, and any value but True is not strong."""
        # read before the handler copies any data: a write in between then has a later
        # revision, so a client waiting since this one is told of it
        revision = self.store.revision
        full = role == OWNER and agent_words is False       # the error's own text: paths and all
        strong = strong is True and role == OWNER
        try:
            status, obj = 200, self._call(method, path, role, body, query, via, strong,
                                          full and via != TUNNEL_VIA)
        except HubError as e:
            said = e.message if full else AGENT_SAYS.get(e.code, e.message)
            status, obj = STATUS.get(e.code, 500), error_body(e.code, said, e.field)
            extra = getattr(e, 'extra', None)   # what a route adds to its refusal (schedules_set's id)
            if isinstance(extra, dict):
                obj['error'].update((k, v) for k, v in extra.items()
                                    if k not in ('code', 'message', 'field'))
        except Exception as e:      # noqa: BLE001 - a transport gets an answer, never a traceback
            if raise_unexpected:
                raise
            said = '%s: %s' % (type(e).__name__, e) if full else type(e).__name__
            status, obj = 500, error_body('internal', said)
        obj.setdefault('revision', revision)
        return status, obj

    def _call(self, method, path, role, body, query, via, strong, full):
        if not isinstance(role, str) or role not in CALLERS:
            raise HubError('forbidden', 'this request carries no owner or agent credential',
                           'role')
        found = match(method, path)
        if found is None:
            raise HubError('not_found', 'there is no route %s %s' % (method, path), 'path')
        route, params = found
        if route.role not in ROLES:
            raise HubError('forbidden', 'the route %s %s names no role, so nobody may call it'
                           % (route.method, route.pattern), 'role')
        if route.role == APPROVER:
            if not (role == OWNER and strong is True):
                raise HubError('forbidden', NOT_STRONG % (route.method, route.pattern), 'role')
        elif route.role == LOCK_WRITE:
            if not (role == OWNER and (strong is True or via != TUNNEL_VIA)):
                raise HubError('forbidden', NOT_LOCK_WRITER % (route.method, route.pattern),
                               'role')
        elif route.role != ANY and route.role != role:
            raise HubError('forbidden', '%s %s is for the %s, not the %s'
                           % (route.method, route.pattern, route.role, role), 'role')
        if query is not None and not isinstance(query, dict):
            raise _invalid('the query is an object of names and values', 'query')
        out = route.handler(self, Request(role, params, body, query, via if isinstance(via, str)
                                          else '', strong, full is True))
        if not isinstance(out, dict):
            raise HubError('internal', 'the handler of %s gave no object' % route.pattern)
        return out

    # -------------------------------------------------------------- helpers for handlers
    def doc(self, coll, doc_id, b=None):
        """The document, HubError not_found when there is none, conflict when the body pins a
        version it is not at."""
        d = self.store.get(coll, doc_id) if schema.check_id(doc_id) is None else None
        if d is None:
            raise HubError('not_found', 'there is no %s/%s' % (coll, doc_id), 'id')
        pin = (b or {}).get('if_version')
        if pin is not None and pin != _version(d):
            raise HubError('conflict', '%s/%s is at version %d, not %d'
                           % (coll, doc_id, _version(d), pin), 'if_version')
        return d

    def add(self, coll, fields, req, extra=None):
        """A new document of fields, with the path the write came by (`via`)."""
        return self.written(self.store.add(coll, dict(fields, via=_via(req))), extra)

    def change(self, coll, doc_id, doc, changes, req, extra=None):
        """Merge changes into the document, pinned to the version of `doc` (the copy the
        handler judged), record the path the write came by (`via`), and give the write's
        result object."""
        r = self.store.update(coll, doc_id, dict(changes, via=_via(req)),
                              if_version=_version(doc))
        return self.written(r, extra)

    def written(self, r, extra=None):
        out = {'ok': True, 'id': r.id, 'version': r.version, 'revision': r.revision,
               'warnings': [f._asdict() for f in r.warnings]}
        out.update(extra or {})
        return out

    def items(self, coll, req, since=False):
        """The documents of coll as a list, each with its `id`, filtered by the query's status
        and repo (and since, a time, where the route takes it)."""
        allowed = hubvalidate.STATUSES.get(coll, ())
        status, repo = _q(req, 'status'), _q(req, 'repo').lower()
        if status == 'all':
            status = ''
        if status and status not in allowed:
            raise _invalid('status is one of %s, all; not %r' % (', '.join(allowed), status),
                           'status')
        after = _when(_q(req, 'since'), 'since') if since and _q(req, 'since') else None
        out = []
        for doc_id, d in self.store.docs(coll).items():
            if status and d.get('status') != status:
                continue
            if repo and str(d.get('repo') or '').strip().lower() != repo:
                continue
            if after is not None:
                at = _when(d.get('updated_at'), '') or _when(d.get('asked_at'), '')
                if at is None or at < after:
                    continue
            d['id'] = doc_id
            out.append(d)
        return sorted(out, key=lambda d: _natural(d['id']))

    def _view(self, name):
        fn = getattr(self.views, name, None) if self.views is not None else None
        if not callable(fn):
            raise HubError('unavailable', 'the %s view is not available on this server' % name,
                           'views')
        return fn

    def view(self, name, **kw):
        """What the view gives; whatever it raises is the caller's to answer for."""
        return self._view(name)(**kw)

    def shown(self, name, **kw):
        """view() for a route that is that one view: a view that raises is `unavailable` with
        the short reason, never its message."""
        fn = self._view(name)
        try:
            return fn(**kw)
        except Exception as e:      # noqa: BLE001 - a view is another module's code
            raise HubError('unavailable', 'the %s view failed: %s' % (name, _reason(e)), 'views')

    def session_keys(self):
        """The keys a question may come from: the launcher's session keys, read now, and
        AGENT_FROM (the PM's), which is known even when they cannot be read."""
        return self.read_session_keys()[0]

    def read_session_keys(self):
        """(session_keys(), why they could not be read or None): the reason is _reason's short
        one (an exception's class name, never its message or a path)."""
        try:
            return {k for k in self._session_keys() if isinstance(k, str)} | {AGENT_FROM}, None
        except Exception as e:      # noqa: BLE001 - unreadable: only the PM's key is known
            return {AGENT_FROM}, _reason(e)

    def kit_session_key(self):
        """The kit coordinator's session key ("Skill edits" in apiskills): kit_key() gives the kit repository's
        configured name, and the key is, by coordinator_keys' rule, a coordinator session of
        sessions.json whose `repo` is that name (the kit's `kit`); the name itself when there is
        none. '' when kit_key fails or gives no string. Whether that session exists is for the
        caller (the route checks session_keys, the push resolves it live). The first of
        kit_session_keys()."""
        return (self.kit_session_keys() or [''])[0]

    def kit_session_keys(self):
        """Every key kit_session_key may be, a session's key before the name, [] for none: the
        push tries each for a live session."""
        try:
            name = self.kit_key()
        except Exception:           # noqa: BLE001 - no kit coordinator known: the PM hears it
            name = ''
        if not isinstance(name, str) or not name:
            return []
        return self._keys_named(name)[1:] + [name]

    def repo_key(self, repo):
        """The session key of the coordinator of the repository whose remote is the repo_id
        `repo`: the configured repository's name (a repo's main coordinator is keyed by its name
        in sessions.json), '' when none is or repo_name fails. Not checked against the session
        keys: the caller does that."""
        try:
            name = self.repo_name(repo) if isinstance(repo, str) and repo else None
        except Exception:           # noqa: BLE001 - no name, no coordinator: the PM hears it
            name = None
        return name if isinstance(name, str) else ''

    def coordinator_keys(self, repo):
        """The session keys that may act as the coordinator of the repository whose remote is the
        repo_id `repo` (approvals supersede): repo_key(repo), and every coordinator session of
        sessions.json (coordinators()) whose `repo` is that repository's configured name, matched
        without case, so a coordinator keyed otherwise (the kit's `kit`) is one. [] when the
        repository has no name; only repo_key's when sessions.json cannot be read."""
        return self._keys_named(self.repo_key(repo))

    def _keys_named(self, name):
        """coordinator_keys' rule for a configured repository name: [name, then the coordinator
        sessions whose `repo` is it], [] for no name."""
        if not name:
            return []
        out = [name]
        try:
            got = self.coordinators()
        except Exception:           # noqa: BLE001 - unreadable: no coordinator beyond the name
            got = {}
        for key, of in (got.items() if isinstance(got, dict) else ()):
            if (isinstance(key, str) and key and isinstance(of, str)
                    and of.casefold() == name.casefold() and key not in out):
                out.append(key)
        return out

    def pending(self, for_key=''):
        """The pending items, each with the session key it is for as `from`: a question's or a
        to-do's own `from` when that is a session key, else AGENT_FROM (so the owner's to-dos,
        free-text names, stories and notes are the PM's), and the key that is to handle it now
        as `route` (module docstring "Routes"). With for_key, only the items for that key; for
        AGENT_FROM, the items routed to it, every question and every approval. An approval's
        `from` is its repository's coordinator key (repo_key). Reading never changes an item:
        what a filtered read leaves out stays pending for every other reader."""
        return [d for d in self._routed()[0] if _for(d, for_key)]

    def route_change_in(self):
        """Seconds until the route of a pending item next turns to AGENT_FROM with no write
        (a hand-over's window or a key's grace running out), or None when none will."""
        at = self._routed()[1]
        return None if at is None else max(0.0, at - self.now())

    def _routed(self):
        """([item], `now` of the next route change or None): the pending items with `from` and
        `route`. Hand-overs of items no longer pending are forgotten here. Each approval's
        repository is resolved (repo_key) once per call, however many approvals name it, the
        kit coordinator's key (kit_session_key) once for every skill edit, and sessions.json is
        read once for all of them (sessions_once)."""
        with sessions_once():
            return self._routed_once()

    def _routed_once(self):
        found, keys, out, repo_keys, kit = self.store.pending(), None, [], {}, None
        # the fields of the pending questions read below, in one hold of the store and with no
        # copy of a whole question
        asked = self.store.pick('questions', [p.id for p in found if p.kind == 'question'],
                                ('standing', 'record', 'class'))
        for p in found:
            d = p._asdict()
            sender = d.pop('sender')
            if d['kind'] == 'approval':
                # no `from`: its repository's coordinator
                repo = d['repo'] if isinstance(d['repo'], str) else ''
                if repo not in repo_keys:
                    repo_keys[repo] = self.repo_key(repo)
                sender = repo_keys[repo]
            elif d['kind'] == 'skill_edit':
                if kit is None:
                    kit = self.kit_session_key()    # the kit's coordinator commits it
                sender = kit
            mine = sender and sender not in (AGENT_FROM, OWNER_FROM)
            if mine and keys is None:
                keys = self.session_keys()          # read once, and only when it can matter
            d['from'] = sender if mine and sender in keys else AGENT_FROM
            if d['kind'] == 'question':
                q = asked.get(d['id']) or {}
                if d.get('note') == 'filed' and isinstance(q.get('standing'), dict):
                    d['standing'] = True        # the PM logs it too (_for)
                if not isinstance(q.get('record'), dict) and q.get('class') in hubvalidate.QUESTION_CLASSES:
                    d['class'] = q['class']     # the PM audits a classed answer (hub.py pending)
            out.append(d)
        now, flips = self.now(), []
        live = {(d['kind'], d['id']) for d in out}
        for gone in [k for k in list(self._handed) if k not in live]:
            self._handed.pop(gone, None)
        for d in out:
            d['route'], flip = self._route(d, now)
            if flip is not None:
                flips.append(flip)
        return out, (min(flips) if flips else None)

    def _route(self, d, now):
        """(route, `now` it turns to AGENT_FROM or None) of item d."""
        key = d['from']
        if key == AGENT_FROM:
            return AGENT_FROM, None
        handed = self._handed.get((d['kind'], d['id']))
        if handed is not None and handed[0] == key:
            until = handed[1] + self.ack_window_s
            return (key, until) if now < until else (AGENT_FROM, None)
        if self._listening.get(key):
            return key, None
        heard, pushed = self._heard.get(key), self._pushed.get(key)
        until = max(heard + self.reach_grace_s if heard is not None else -math.inf,
                    pushed + self.ack_window_s if pushed is not None else -math.inf)
        if now < until:
            return key, until
        return AGENT_FROM, None

    def pushed(self, key):
        """Note that a push to session key `key` was written: its items route to it for
        ack_window_s (module docstring "Routes"). One dict step; the push worker's thread."""
        if isinstance(key, str) and key not in ('', AGENT_FROM):
            self._pushed[key] = self.now()

    def listen(self, req, opened=None, closed=None):
        """Note that a wait `for` a session key other than AGENT_FROM started (`opened`, a
        token for a wait that now blocks), ended (`closed`, that token) or was answered at once
        (neither): the key is reachable (module docstring "Routes"). The owner's calls are not
        noted."""
        key = _q(req, 'for')
        if req.role != AGENT or key in ('', AGENT_FROM):
            return
        self._heard[key] = self.now()
        waits = self._listening.setdefault(key, {})
        if opened is not None:
            waits[opened] = True
        if closed is not None:
            waits.pop(closed, None)

    def handed(self, req, items):
        """Note that items went to the session key the request is `for` (an agent's call, a key
        other than AGENT_FROM): each of that key's own is handed over, the first time only. An
        item it now holds within the ack window gets the key as its `route` in `items` too, so
        the answer says who has it after this call, not before."""
        key = _q(req, 'for')
        if req.role != AGENT or key in ('', AGENT_FROM):
            return
        now = self.now()
        for d in items:
            if d.get('from') == key:
                got = self._handed.setdefault((d['kind'], d['id']), (key, now))
                if got[0] == key and now < got[1] + self.ack_window_s:
                    d['route'] = key

    def handed_to(self, kind, doc_id):
        """The session key item (kind, doc_id) was first handed over to by handed(), or None.
        Read-only, one dict read: kitlib.hubsite.push calls it from its own thread to tell that
        the session a push went to picked the item up."""
        got = self._handed.get((kind, doc_id))
        return got[0] if got is not None else None

    def seen(self, req, what, opened=None, closed=None):
        """Note that an agent (a PM session) just waited or pulled (`what`). `opened` is a key
        for a wait that now blocks, `closed` the key of one that ended. The owner's calls are
        not noted, nor another session's (`for` a key other than AGENT_FROM). An ended wait
        stops counting before the clock is read, and a clock that fails then leaves the earlier
        time: it never hides how the wait itself ended."""
        if req.role != AGENT or _q(req, 'for') not in ('', AGENT_FROM):
            return
        if closed is not None:
            self._waits.pop(closed, None)
            try:
                self._seen[what] = self.clock()
            except Exception:       # noqa: BLE001 - the wait's own answer or error stands
                pass
            return
        self._seen[what] = self.clock()
        if opened is not None:
            self._waits[opened] = True

    def pm_seen(self):
        return {'waited': self._seen['waited'], 'pulled': self._seen['pulled'],
                'waiting': len(self._waits)}

    def push_seen(self):
        """The push worker's status, or None when this hub runs none (module docstring "Push
        status"). A status that raises is 'down' with its class name as `last`."""
        fn = self.push_status
        if fn is None:
            return None
        try:
            got = fn()
        except Exception as e:      # noqa: BLE001 - the snapshot answers whatever the worker does
            return {'state': 'down', 'last': type(e).__name__, 'undelivered': None}
        return got if isinstance(got, dict) else None

    def watcher_seen(self):
        """The PM watcher's state as the push worker last read it, or None when this hub runs
        no push worker (module docstring "Watcher"). A status that raises is 'down'."""
        fn = self.watcher_status
        if fn is None:
            return None
        try:
            got = fn()
        except Exception:           # noqa: BLE001 - the snapshot answers whatever the worker does
            return {'state': 'down', 'age': None}
        return got if isinstance(got, dict) else None

    def code_seen(self):
        """The server's `code` object (module docstring "Code"), or None. Never raises."""
        fn = self.code_status
        if fn is None:
            return None
        try:
            got = fn()
        except Exception:           # noqa: BLE001 - the snapshot and health answer whatever it does
            return None
        return got if isinstance(got, dict) else None

    def jobs_seen(self):
        """The timed jobs' health blocks (module docstring "Jobs"), or None. Never raises."""
        fn = self.jobs_status
        if fn is None:
            return None
        try:
            got = fn()
        except Exception:           # noqa: BLE001 - health answers whatever the jobs do
            return None
        return got if isinstance(got, dict) else None


# ------------------------------------------------------------------ handlers
def questions_answer(api, req):
    """The owner's answer: `choice`, an optional `note`, and `standing` true when the answer is
    to be a standing rule (a question bound to a record only: the PM logs it, docs/hub-questions.md
    "A standing rule"); a re-answer with no `standing` drops the mark. `standing` true with no
    `choice` is the promote of an answer already given (_promote). A question the hub marked
    done (_hub_done) takes a new answer too: it is reopened, its done fields cleared."""
    allowed = ('choice', 'note', 'standing', 'if_version')
    b = _fields(req, allowed, flags=('standing',))
    standing = _flag(b, 'standing', False)
    doc_id = req.params['id']
    if standing and 'choice' not in b:
        _pin(b)
        if 'note' in b:
            raise _invalid('a note comes with a choice: standing alone keeps the answer given',
                           'note')
        return _promote(api, req, doc_id, b)
    _fields(req, allowed, required=('choice',))
    _pin(b)
    note = (_text(b, 'note') or '').strip()
    d = api.doc('questions', doc_id, b)
    if d.get('status') not in ('open', 'answered', 'filed') and not _hub_done(d):
        raise _invalid('%s is %s: only an open, an answered or a filed question takes an answer'
                       ' (or one the hub marked done)' % (doc_id, d.get('status')), 'status')
    if standing and not isinstance(d.get('record'), dict):
        raise _invalid('%s is bound to no record: an answer the PM handles is logged by the PM '
                       'already, so it takes no standing flag' % doc_id, 'standing')
    choice = b['choice']
    options = d.get('options') if isinstance(d.get('options'), list) else []
    if choice == 'other':
        if not note:
            raise _invalid('the choice "other" needs a note saying what', 'note')
        label = 'Other'
    elif isinstance(choice, int) and not isinstance(choice, bool) and 0 <= choice < len(options):
        o = options[choice]
        label = o.get('label') if isinstance(o, dict) and isinstance(o.get('label'), str) else ''
    else:
        raise _invalid('choice is an index into the %d option(s) or "other", not %r'
                       % (len(options), choice), 'choice')
    answer = {'choice': choice, 'label': label, 'note': note, 'at': api.clock(),
              'via': _via(req)}
    changes = {'status': 'answered', 'answer': answer}
    if (d.get('filing') is not None or d.get('filed_at') is not None
            or d.get('filed_as') is not None):
        changes.update(filing=None, filed_at=None, filed_as=None)   # a re-answer is filed afresh
    if _hub_done(d):
        changes.update(HUB_DONE_CLEARED)    # a new answer reopens it
    if standing:
        changes['standing'] = {'at': answer['at'], 'via': answer['via']}
    elif d.get('standing') is not None:
        changes['standing'] = None      # a re-answer without the box is no standing rule
    out = api.change('questions', doc_id, d, changes, req,
                     {'status': 'answered', 'answer': answer})
    if _hub_done(d):
        # pending afresh: the old answer's hand-over must not pass for a pick-up of this one when
        # no read ran in between (_routed forgets only what it sees gone; todos_done the same)
        api._handed.pop(('question', doc_id), None)
    if isinstance(d.get('record'), dict):
        if api.file_later is not None:
            # the record write fetches and pushes (seconds, a minute when the remote is slow):
            # the answer returns now, stored, and its filing marks the question later, which
            # the page reads through its wait and snapshot (docs/hub-questions.md "Filing the answer")
            via = _via(req)
            api.file_later(doc_id, lambda: file_answer(api, doc_id, via))
            return out
        got = file_answer(api, doc_id, _via(req), full=req.full)
        if got is not None:
            out['filing'] = got
            if 'version' in got:        # the mark is the last write: its version and revision
                out.update(version=got['version'], revision=got['revision'])
            if got.get('filed'):
                out['status'] = 'filed'
    return out


# what a question's `filing` says, by the code its filing failed with: fixed phrases, since the
# store's own text can name a path and the document goes over the tunnel (GET snapshot)
FILING_SAYS = {
    'not_found': 'the record is completed or gone: the PM handles this answer',
    'invalid': 'the backlog store refused this answer: the PM handles it',
    'store': 'the backlog store could not be written now: the hub tries again',
    'unavailable': 'this hub cannot write a backlog store: the hub tries again',
    'no_store': 'this repository names no backlog store the hub may write (.kit.json '
                '"backlog".store): the PM handles this answer',
}


def _waiting(api, doc_id, record):
    """The other questions bound to `record` that are still open, in id order."""
    return sorted((i for i, q in api.store.docs('questions').items()
                   if i != doc_id and q.get('status') == 'open' and q.get('record') == record),
                  key=_natural)


def _record_state(api, record):
    """The `state:` of the open record `record` ({repo, id}) read now through the `record`
    view ('open' when it has no state line), or None when it cannot be told: no view, a view
    that raised, no fields, or a completed record."""
    try:
        got = api._view('record')(record.get('repo'), record.get('id'))
    except Exception:               # noqa: BLE001 - a view is another module's code
        return None
    if not isinstance(got, dict) or got.get('where') != 'open':
        return None
    fields = got.get('fields')
    if not isinstance(fields, list):
        return None
    for pair in fields:
        if isinstance(pair, (list, tuple)) and len(pair) == 2 and pair[0] == 'state':
            return pair[1] if isinstance(pair[1], str) and pair[1] else 'open'
    return 'open'


def _filed_as(before, after):
    """What a filed answer did to its record, from the record's state before and after the
    filing (_record_state): `pulled` (someone is working on it: it was, or the answer ended a
    hold that had stopped its pull, which is back, plan_claim.restore_pull), `freed` (it was parked or
    blocked and now is open: work can start), `held` (it waits on more), or None when a state
    could not be read (the push then treats it as before this field: docs/hub-push.md "Push
    notifications")."""
    if before == 'pulled':
        return 'pulled'
    if before is None or after is None:
        return None
    if after == 'pulled':
        return 'pulled'
    if before in ('parked', 'blocked') and after == 'open':
        return 'freed'
    return 'held'


def file_answer(api, doc_id, via='api', full=False):
    """File the stored answer of question doc_id into the record it is bound to, through the
    `record_answer` view, then mark the question (docs/hub-questions.md "A question bound to a
    record"): status `filed` with `filed_at`, or, when the view refused, status `answered` kept
    with `filing` {at, code, message} (message a fixed phrase of FILING_SAYS), so the PM still
    sees it pending. Both marks are pinned to the version read first: a question that moved
    meanwhile (answered again, logged, done) is left as it is, and the answer that moved it is
    filed by its own call. None for a question that is not answered or is bound to no record;
    else {id, filed, version, revision} or {id, filed: false, code, message} (message the
    view's own text when `full`) and the version and revision of each mark written, or {id,
    filed: false, moved: true}. A mark the hub store refuses otherwise is given as a failure with
    that code, nothing marked. A failure with the code and phrase the question's `filing` has
    already writes nothing: its version and revision are those it has. The record write is
    idempotent (kitlib.hubsite.recordwrite answer), so a crash anywhere in between is mended by
    filing again. It runs under Api.record_lock, as park_item and unpark_item do."""
    with api.record_lock:
        return _file_answer(api, doc_id, via, full)


def _file_answer(api, doc_id, via, full):
    d = api.store.get('questions', doc_id)
    if d is None or d.get('status') != 'answered':
        return None
    record, ans = d.get('record'), d.get('answer')
    if not isinstance(record, dict) or not isinstance(ans, dict):
        return None
    version = _version(d)
    before = _record_state(api, record)
    try:
        api._view('record_answer')(
            record.get('repo'), record.get('id'), doc_id, ans.get('at'), d.get('title'),
            ans.get('label'), ans.get('note'), ans.get('via'), ans.get('choice') == 'other',
            _waiting(api, doc_id, record))
    except HubError as e:
        code, said = e.code, e.message
    except Exception as e:          # noqa: BLE001 - a view is another module's code
        code, said = 'internal', _reason(e)
    else:
        code = said = None
    if code is None:
        changes = {'status': 'filed', 'filed_at': api.clock(), 'filing': None,
                   'filed_as': _filed_as(before, _record_state(api, record))}
    else:
        message = FILING_SAYS.get(code, 'the answer could not be filed (%s)' % code)
        was = d.get('filing')
        if isinstance(was, dict) and (was.get('code'), was.get('message')) == (code, message):
            # failed as it failed before: the mark it has stays, its first `at` kept, so a retry
            # moves no version an asker's --if-version holds
            now = api.store.get('questions', doc_id)
            if now is None or _version(now) != version:
                return {'id': doc_id, 'filed': False, 'moved': True}
            return {'id': doc_id, 'filed': False, 'code': code, 'version': version,
                    'revision': api.store.revision, 'message': said if full else message}
        changes = {'filing': {'at': api.clock(), 'code': code, 'message': message}}
    try:
        r = api.store.update('questions', doc_id, dict(changes, via=via), if_version=version)
    except HubError as e:
        if e.code == 'conflict':
            return {'id': doc_id, 'filed': False, 'moved': True}
        return {'id': doc_id, 'filed': False, 'code': e.code,
                'message': e.message if full else AGENT_SAYS.get(e.code, e.message)}
    marked = {'id': doc_id, 'version': r.version, 'revision': r.revision}
    if code is None:
        return dict(marked, filed=True)
    return dict(marked, filed=False, code=code,
                message=said if full else changes['filing']['message'])


def questions_file(api, req):
    """File every answered question bound to a record (or only `id`) that is not filed yet:
    {ok, filed: [ids], failed: [{id, code, message}], parked, unparked, park_failed}; a
    question that moved meanwhile is in neither. An unknown `id` is `not_found`; one that is not
    answered or has no record files nothing. Each open bound question not parked yet is parked
    (`parked` [ids]); without `id`, so is each open bound to-do and pending bound approval
    (park_item), the lines of asks gone or bound elsewhere are dropped (`unparked` [{id, record,
    unblocked}], _sweep_asks), and a hand-written line whose ask is gone is left and listed
    (`orphan_hand_lines` [{repo, id, q}]). A park or a drop not made is in `park_failed` [{id,
    code, message}]."""
    b = _fields(req, ('id',))
    one = _text(b, 'id')
    if one is not None:
        api.doc('questions', one)
        ids = [one]
    else:
        ids = sorted(api.store.docs('questions'), key=_natural)
    filed, failed, parked, park_failed = [], [], [], []
    for doc_id in ids:
        got = file_answer(api, doc_id, _via(req), full=req.full)
        if got is None or got.get('moved'):
            pass
        elif got['filed']:
            filed.append(doc_id)
        else:
            failed.append({'id': doc_id, 'code': got['code'], 'message': got['message']})
        # an open bound question whose park has not happened (the hub stopped first, or the
        # store could not be written) is parked now
        if _parks(api):
            _parked(park_item(api, doc_id, _via(req)), doc_id, parked, park_failed)
    unparked, hand = [], []
    if one is None and _parks(api):
        for coll in ('todos', 'approvals'):     # a bound to-do's or approval's park, the same
            docs = api.store.docs(coll)
            for doc_id in sorted(docs, key=_natural):
                if isinstance(docs[doc_id], dict) and isinstance(docs[doc_id].get('record'), dict):
                    _parked(park_item(api, doc_id, _via(req)), doc_id, parked, park_failed)
        unparked, more, hand = _sweep_asks(api)
        park_failed += more
    return {'ok': True, 'filed': filed, 'failed': failed, 'parked': parked,
            'unparked': unparked, 'park_failed': park_failed, 'orphan_hand_lines': hand}


# The statuses a question is logged or marked done from: an answer the PM handles, and one filed
# into its record that its asker has not yet read and closed.
HANDLED_FROM = ('answered', 'filed')
# A question's done_by when the hub's push worker marked it done once its asker had the filed
# answer (kitlib.hubsite.push "Done by the hub"), and what a new answer or a promote clears.
HUB_DONE_BY = 'hub'
HUB_DONE_CLEARED = {'done_by': None, 'done_why': None, 'done_at': None}


def _hub_done(d):
    """True when question d is done because the hub marked it so (HUB_DONE_BY): it still takes
    a new answer and a promote, and its asker's own done is no conflict."""
    return d.get('status') == 'done' and d.get('done_by') == HUB_DONE_BY
# The filing codes no retry mends by itself (FILING_SAYS: the PM handles the answer); any other
# code, or no filing mark yet, may still file on the next filing call (questions_file).
FINAL_FILING = ('not_found', 'invalid', 'no_store')


def _unfiled(d, doc_id, b, verb):
    """The changes `unfiled` adds to log or done (`verb`) of question d, which an answer bound to
    a record and not filed must carry (docs/hub-questions.md "Filing the answer"): refused
    `invalid`, field `filing`, while its filing may still succeed (a code not in FINAL_FILING, or
    none yet: file it first); field `unfiled` for a final code with no or a blank `unfiled`, and
    for `unfiled` on any question whose filing did not fail. Else {unfiled_why} or {}."""
    given = b.get('unfiled')
    record = d.get('record')
    if d.get('status') != 'answered' or not isinstance(record, dict):
        if given is not None:
            raise _invalid('%s has no answer left unfiled: only an answer whose filing failed '
                           'takes unfiled' % doc_id, 'unfiled')
        return {}
    named = '%s:%s' % (record.get('repo'), record.get('id'))
    filing = d.get('filing') if isinstance(d.get('filing'), dict) else {}
    code = filing.get('code')
    if code not in FINAL_FILING:
        raise _invalid('%s is bound to %s and its answer is not filed yet (%s): file it first '
                       '(hub.py questions file %s), then %s it'
                       % (doc_id, named, filing.get('message') or 'no filing has run', doc_id,
                          verb), 'filing')
    why = (given or '').strip()
    if not why:
        raise _invalid('%s\'s answer could not be filed into %s (%s): %s; unfiled says why it '
                       'is handled without its record' % (doc_id, named, code,
                                                          filing.get('message')), 'unfiled')
    return {'unfiled_why': why}


def questions_log(api, req):
    """POST questions/{id}/log: mark an answered or filed question logged (status `logged`), the
    PM having logged its answer in the ledger. No ledger id is taken or stored (an owner
    decision): a body with `ruling` is refused as an unknown field. Any other status, `logged`
    included, is refused (`invalid`, field status). An answer bound to a record and not filed is
    refused or needs `unfiled` (_unfiled), which is kept as `unfiled_why`."""
    b = _fields(req, ('if_version', 'unfiled'))
    _pin(b, required=True)
    _text(b, 'unfiled')
    doc_id = req.params['id']
    d = api.doc('questions', doc_id, b)
    if d.get('status') not in HANDLED_FROM:
        raise _invalid('%s is %s: only an answered or filed question is logged'
                       % (doc_id, d.get('status')), 'status')
    changes = _unfiled(d, doc_id, b, 'log')
    changes.update(status='logged')
    return api.change('questions', doc_id, d, changes, req, {'status': 'logged'})


def questions_done(api, req):
    """The PM, or the asker of a filed question, marks an answered or filed question handled
    with no ruling (a one-off go the ledger keeps out; an answer the record already holds):
    status `done`, terminal, so it is no longer pending and its push ends. `from` is the
    caller's session key (AGENT_FROM, the PM, when none): refused `forbidden` (field from)
    unless it is the question's own `from` or the PM's, and before anything else about the
    question is said or written. Like _creator it is a claim checked against the item, not
    proof (all agents share one token). One the hub marked done already (_hub_done: once its
    asker had the filed answer, maybe after the version the call pins) is done: ok, nothing
    written, its version kept, `done_by` given. Refused (`invalid`) for a question that is
    neither answered nor filed, for a blank why and for a standing rule (the PM logs it). An
    answer bound to a record and not filed is refused or needs `unfiled` (_unfiled), kept as
    `unfiled_why`."""
    b = _fields(req, ('why', 'from', 'if_version', 'unfiled'), required=('why',))
    _pin(b, required=True)
    _text(b, 'from')
    _text(b, 'unfiled')
    why = _text(b, 'why').strip()
    if not why:
        raise _invalid('why says why the answer needs no ruling: it cannot be blank', 'why')
    doc_id = req.params['id']
    d = api.doc('questions', doc_id)
    claimed = (b.get('from') or '').strip() or AGENT_FROM
    asker = d.get('from') or AGENT_FROM
    if claimed not in (asker, AGENT_FROM):
        raise HubError('forbidden', '%s is from %s: only its asker or %s can mark it done '
                       '(%s was given)' % (doc_id, asker, AGENT_FROM, ascii(claimed)), 'from')
    if _hub_done(d):
        # the hub marked it done once its asker had it (kitlib.hubsite.push "Done by the hub"),
        # maybe after the version this call pins: done already, nothing written
        return {'ok': True, 'id': doc_id, 'version': _version(d),
                'revision': api.store.revision, 'warnings': [], 'status': 'done',
                'done_by': HUB_DONE_BY}
    d = api.doc('questions', doc_id, b)
    if d.get('status') not in HANDLED_FROM:
        raise _invalid('%s is %s: only an answered or filed question is marked done'
                       % (doc_id, d.get('status')), 'status')
    if isinstance(d.get('standing'), dict):
        # the asker's routine done of a filed answer would end the PM's push unlogged
        raise _invalid('%s is a standing rule the owner made: the PM logs it in the ledger '
                       '(hub questions log), it is not marked done' % doc_id, 'standing')
    changes = _unfiled(d, doc_id, b, 'mark done')
    changes.update(status='done', done_why=why, done_at=api.clock())
    return api.change('questions', doc_id, d, changes, req, {'status': 'done'})


def _promote(api, req, doc_id, b):
    """POST questions/{id}/answer with `standing` true and no `choice`: the owner makes the
    answer already given to a question bound to a record a standing rule (the filed card's
    promote button), the answer kept as it is and not filed again: `standing` {at, via}, so the
    question is pending for the PM, who logs it into the ledger, as well as for its asker
    (docs/hub-questions.md "A standing rule"). One the hub marked done (_hub_done) is put back to
    `filed`, its done fields cleared, so it is pending for the PM. Refused (`invalid`) for a
    question that is not answered, filed or marked done by the hub, one bound to no record (the
    PM logs every such answer already) and one already standing."""
    d = api.doc('questions', doc_id, b)
    if d.get('status') not in HANDLED_FROM and not _hub_done(d):
        raise _invalid('%s is %s: only an answered or filed question is made a standing rule'
                       ' (or one the hub marked done)' % (doc_id, d.get('status')), 'status')
    if not isinstance(d.get('record'), dict):
        raise _invalid('%s is bound to no record: the PM logs its answer already' % doc_id,
                       'record')
    if isinstance(d.get('standing'), dict):
        raise _invalid('%s is a standing rule already' % doc_id, 'standing')
    standing = {'at': api.clock(), 'via': _via(req)}
    changes = {'standing': standing}
    if _hub_done(d):
        # filed again, so pending for the PM, who logs it: the hub's done is undone
        changes.update(HUB_DONE_CLEARED, status='filed')
    return api.change('questions', doc_id, d, changes, req, {'standing': standing})


def stories_list(api, req):
    return {'items': api.items('stories', req)}


def stories_add(api, req):
    b = _fields(req, ('repo', 'title', 'story', 'urgent'), required=('repo', 'title', 'story'))
    for name in ('repo', 'title', 'story'):
        _text(b, name)
    b.update({'status': 'submitted', 'from': OWNER_FROM, 'submitted_at': api.clock()})
    return api.add('stories', b, req, {'status': 'submitted'})


def stories_set(api, req):
    b = _fields(req, ('status', 'reviewer', 'review_note', 'backlog_id', 'closed_note',
                      'if_version'), required=('status',))
    _pin(b, required=True)
    for name in ('status', 'reviewer', 'review_note', 'backlog_id', 'closed_note'):
        _text(b, name)
    status = b['status']
    if status not in hubvalidate.AGENT_STORY_STATUSES:
        raise _invalid('status is one of %s, not %r (submitted and withdrawn are the owner\'s)'
                       % (', '.join(hubvalidate.AGENT_STORY_STATUSES), status), 'status')
    doc_id = req.params['id']
    d = api.doc('stories', doc_id, b)
    if d.get('status') == 'withdrawn':
        raise _invalid('%s was withdrawn by the owner' % doc_id, 'status')
    changes = {k: v for k, v in b.items() if k != 'if_version'}
    if status == 'added':
        changes['added_at'] = api.clock()
    if status == 'declined':
        changes['closed_at'] = api.clock()
    return api.change('stories', doc_id, d, changes, req, {'status': status})


def stories_withdraw(api, req):
    b = _fields(req, ('if_version',))
    _pin(b)
    doc_id = req.params['id']
    d = api.doc('stories', doc_id, b)
    if d.get('status') != 'submitted':
        raise _invalid('%s is %s: only a submitted story can be withdrawn'
                       % (doc_id, d.get('status')), 'status')
    return api.change('stories', doc_id, d, {'status': 'withdrawn', 'closed_at': api.clock()},
                      req, {'status': 'withdrawn'})


def _r(method, path, role, handler):
    return Route(method, BASE + path, role, handler)


ROUTES = (
    _r('GET', '/questions', ANY, questions_list),
    _r('POST', '/questions', AGENT, questions_add),
    _r('POST', '/questions/{id}/answer', OWNER, questions_answer),
    _r('POST', '/questions/file', AGENT, questions_file),
    _r('POST', '/questions/bind', AGENT, questions_bind),
    _r('POST', '/questions/{id}/log', AGENT, questions_log),
    _r('POST', '/questions/{id}/done', AGENT, questions_done),
    _r('POST', '/questions/{id}/edit', AGENT, questions_edit),
    _r('POST', '/questions/{id}/delete', ANY, questions_delete),
    _r('GET', '/todos', ANY, todos_list),
    _r('POST', '/todos', ANY, todos_add),
    _r('POST', '/todos/{id}/done', OWNER, todos_done),
    _r('POST', '/todos/{id}/ack', AGENT, todos_ack),
    _r('POST', '/todos/{id}/edit', ANY, todos_edit),
    _r('POST', '/todos/{id}/delete', ANY, todos_delete),
    _r('GET', '/stories', ANY, stories_list),
    _r('POST', '/stories', OWNER, stories_add),
    _r('POST', '/stories/{id}', AGENT, stories_set),
    _r('POST', '/stories/{id}/withdraw', OWNER, stories_withdraw),
    _r('GET', '/to_pm', ANY, to_pm_list),
    _r('POST', '/to_pm', OWNER, to_pm_add),
    _r('POST', '/to_pm/send', OWNER, to_pm_send),
    _r('POST', '/to_pm/{id}/reply', AGENT, to_pm_reply),
    _r('POST', '/to_pm/{id}/edit', OWNER, to_pm_edit),
    _r('POST', '/to_pm/{id}/delete', OWNER, to_pm_delete),
    _r('POST', '/inbox', AGENT, inbox_add),
    _r('POST', '/inbox/{id}/read', OWNER, inbox_read),
    _r('GET', '/landings', ANY, landings_list),
    _r('POST', '/landings', AGENT, landings_add),
    _r('GET', '/handoffs', ANY, handoffs_list),
    _r('POST', '/handoffs', AGENT, handoffs_add),
    _r('GET', '/pages', ANY, pages_list),
    _r('POST', '/pages', AGENT, pages_publish),
    _r('GET', '/pages/{id}', ANY, pages_get),
    _r('POST', '/pages/{id}/delete', OWNER, pages_delete),
    _r('POST', '/pages/{id}/place', ANY, pages_place),
    _r('GET', '/schedules', ANY, schedules_list),
    _r('POST', '/schedules', OWNER, schedules_set),
    _r('POST', '/approvals', AGENT, approvals_add),
    _r('POST', '/approvals/{id}/decide', APPROVER, approvals_decide),
    _r('POST', '/approvals/{id}/consume', AGENT, approvals_consume),
    _r('POST', '/approvals/{id}/delete', AGENT, approvals_delete),
    _r('POST', '/approvals/{id}/supersede', AGENT, approvals_supersede),
    _r('GET', '/approvals/{id}/verify', AGENT, approvals_verify),
    _r('GET', '/backlog', ANY, backlog),
    _r('GET', '/completed', ANY, completed),
    _r('GET', '/records/{repo}/new', ANY, record_new),
    _r('GET', '/records/{repo}/{id}', ANY, record),
    _r('POST', '/records/{repo}', OWNER, record_add),
    _r('POST', '/records/{repo}/{id}/edit', OWNER, record_edit),
    _r('POST', '/records/{repo}/{id}/delete', OWNER, record_delete),
    _r('POST', '/records/{repo}/{id}/unblock', OWNER, record_unblock),
    _r('GET', '/findings', ANY, findings_list),
    _r('GET', '/finding_marks', ANY, finding_marks_list),
    _r('POST', '/finding_marks', OWNER, finding_marks_set),
    _r('POST', '/finding_marks/{id}/delete', OWNER, finding_marks_delete),
    _r('GET', '/notices', ANY, _listing('notices')),
    _r('GET', '/repos', ANY, _listing('repos')),
    _r('GET', '/usage', ANY, _listing('usage')),
    _r('GET', '/agents', ANY, _listing('agents')),
    _r('GET', '/skills', ANY, _listing('skills')),
    _r('GET', '/skills/{name}', ANY, skill_get),
    _r('GET', '/skills/{name}/source', ANY, skill_source_get),
    _r('GET', '/skill_edits', ANY, skill_edits_list),
    _r('POST', '/skill_edits', OWNER, skill_edits_add),
    _r('GET', '/skill_edits/{id}', ANY, skill_edits_get),
    _r('POST', '/skill_edits/{id}/edit', OWNER, skill_edits_edit),
    _r('POST', '/skill_edits/{id}/withdraw', OWNER, skill_edits_withdraw),
    _r('POST', '/skill_edits/{id}/done', AGENT, skill_edits_done),
    _r('POST', '/skill_edits/{id}/fail', AGENT, skill_edits_fail),
    _r('GET', '/pending', ANY, pending),
    _r('GET', '/wait', ANY, wait),
    _r('GET', '/snapshot', ANY, snapshot),
    _r('GET', '/health', ANY, health),
    _r('GET', '/lock/view', OWNER, lock_view),
    _r('GET', '/lock/stats', OWNER, lock_stats),
    _r('GET', '/lock/wait', OWNER, lock_wait),
    _r('POST', '/lock/holds', APPROVER, lock_hold_add),
    _r('POST', '/lock/holds/{id}/edit', APPROVER, lock_hold_edit),
    _r('POST', '/lock/holds/{id}/end', APPROVER, lock_hold_end),
    _r('POST', '/lock/division', APPROVER, lock_division),
    _r('POST', '/lock/runs/{id}/end', LOCK_WRITE, lock_run_end),
    _r('POST', '/lock/runs/{id}/top', LOCK_WRITE, lock_run_top),
    _r('POST', '/lock/game_mode', LOCK_WRITE, lock_game_mode),
    _r('POST', '/lock/games', LOCK_WRITE, lock_games),
)
# the handlers that block: the listener counts each call to one as a wait (kitlib.hubsite.httpd)
WAIT_HANDLERS = (wait, lock_wait)
