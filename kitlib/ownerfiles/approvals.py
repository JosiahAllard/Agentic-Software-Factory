"""kitlib.ownerfiles.approvals: the owner's approval of an edit to an owner-only path, or of one
run of a delegated operation (the op kind, below).

An agent asks the owner to approve an exact change: the files it will commit, each with the blob
it starts from and the content it will write. The owner decides it on the hub page, only as the
strong owner (kitlib.hubsite.auth, "Strong sessions"; api.APPROVER). An approved request is
signed with the approval key (auth.read_approval_key), so a document edited in the store folder
by anyone without the key no longer verifies. The commit and land steps ask the server whether
their change is the approved one (Verifier) and fail closed on any doubt.

  PENDING_TTL_S, APPROVAL_LIFE_S  how long a request waits for a decision, and how long a file
                                  approval holds, used or not (the owner's choice: 7 days, 30
                                  days; APPROVED_TTL_S, 24 hours, for an op approval)
  DENIAL_PENDING_S                how long a denial stays pending owner input after its decision
                                  (24 hours; kitlib.hubsite.store.approval_decided)
  approval_end(doc)               when a decided approval stops counting: the one place its
                                  lifetime is computed
  blob_id(content)                git's blob id of the content (SHA-1 object format)
  repo_id(url)                    a remote URL as the repository's identity: credentials,
                                  scheme and `.git` dropped, the host lower-cased
  as_repo_id(value)               a repo_id kept as it is, a remote URL made one
  check_path(path)                why a path cannot be approved, or None
  new_doc(repo, title, head, files, now, reason=None, branch=None)
                                  the pending document of a request; ApprovalError
  canonical(doc_id, doc)          the bytes a decision's signature covers
  sign(key, doc_id, doc)          that signature: HMAC with SHA-256 under the approval key, hex
  expires_at(doc)                 when the document stops counting, or None (never or unknown)
  status_at(doc, now)             its status at `now`, supersede then expiry applied; None for a
                                  bad document
  supersede_fields(doc_id, doc, by, who, now)
                                  the `superseded` note marking a decided approval nobody
                                  consumed superseded by approval `by` or commit `by`;
                                  ApprovalError
  on_main(checkout, sha)          (True/False, None): whether main as last fetched reaches the
                                  commit `sha`, or (None, why)
  decide_fields(key, doc_id, doc, decision, now)
                                  the fields a decision writes; ApprovalError
  consume_fields(key, doc_id, doc, commit, now)
                                  the fields a consume writes, for a signed approval never
                                  consumed; ApprovalError
  check(key, doc_id, doc, repo, head, blobs, now, commit=None)
                                  (True, '') when this change is the approved one (given a
                                  commit, also consumed, by any commit), else (False, why):
                                  the server's verify, pure
  remote_id(checkout, remote='origin', run=None)
                                  (repo_id, None) of a checkout's remote, or (None, why)
  read_base(checkout, head, path, base_blob, cat=None)
                                  (text, None) of a file at head, read with git and checked to
                                  be base_blob, or (None, why)
  diff(path, base, content)       the unified diff lines the page shows for one file
  delta_result(checkout, base_blob, parent_blob, approved_blob, approved_bytes, call=None,
  cat=None)                       (blob or None, None): what one file's approved change gives
                                  on a parent, or (None, why) (the delta rule, below)
  delta_content(...)              the same arguments: (bytes or None, None), the content
                                  itself, or (None, why)
  delta_targets(checkout, doc, parent, call=None, cat=None)
                                  ({path: (parent entry, blob, bytes)}, None): what the approval
                                  gives on the commit `parent`, path by path, or (None, why)
  delta_error(checkout, doc, parent, commit, blobs, call=None, cat=None)
                                  ({path: approved blob}, None) when a commit makes the approved
                                  change on its parent, or (None, why)
  spent(checkout, doc_id, rev, call=None)
                                  (sha, None) of the newest commit `rev` reaches that carries the
                                  approval (TRAILER), (None, None), or (None, why)
  landed(checkout, base, tip, call=None)
                                  ({doc id: sha}, None) of the approvals the commits base..tip
                                  carry, as spent reads one, or (None, why): what a landing puts
                                  on main (kitlib.landing.lander.Lander.post_landing)
  Verifier(client)                .verify(doc_id, repo, head, blobs, commit=None) -> (ok, why),
                                  through the verify route; fails closed.
                                  .approved_doc(doc_id) -> (doc, why) from the snapshot
  request(client, repo, title, head, files, reason=None, record=None, branch=None)
                                  the agent's add call (a change approval is consumed only by
                                  the hub's own apply: there is no agent call for it)
  apply_approved(client, doc_id, if_version=None)
                                  the owner's call that has the hub apply an approved request
                                  again (kitlib.ownerfiles.apply)

The op kind: one run of a delegated operation (docs/delegations.md "Hub approvals").
  PENDING_OP_TTL_S                how long an op request waits for a decision (24 hours)
  line_sha256(line)               the hex SHA-256 of a run line's UTF-8 bytes
  new_op_doc(op, repo, line, title, now, reason=None, what=None)
                                  the pending document of an op request; ApprovalError
  check_op(key, doc_id, doc, op, repo, args_sha256, now)
                                  (True, '') when `doc` approves this run of `op` and was not
                                  consumed, else (False, why): the server's op verify, pure
  consume_op_fields(key, doc_id, doc, args_sha256, now)
                                  the fields an op consume writes; ApprovalError
  request_op(client, op, repo, line, title, reason=None, what=None) / verify_op(client, doc_id,
  op, repo, args_sha256) -> (ok, why, version) / consume_op(client, doc_id, args_sha256,
  if_version)                     the agent's calls; verify_op fails closed
check() and consume_fields() refuse an op document (WHY_KIND_OP), and check_op() and
consume_op_fields() a file one (WHY_KIND_FILES).

The document (collection `approvals`, ids A-<n>):
  status      pending, approved, denied, consumed or expired (STATUSES)
  repo        repo_id of the repository's remote (the owner's choice: the remote URL)
  title, reason
  head        the commit the change applies to (40 hex)
  files       [{path, base_blob, blob, content}] sorted by path: base_blob is the blob at
              `head` (None for a new file), blob is blob_id(content) (None with content None,
              a delete); the server computes blob, never takes it from the caller
  created_at  ISO time with offset
  decision, decided_at, sig      once decided (sig on an approval only)
  commit, consumed_at            once consumed
  superseded  {by, from, at}     once the PM or the repository's coordinator marked a decided,
                                 unconsumed approval superseded by approval `by` (A-<n>) or a
                                 commit `by` on main (supersede_fields); the stored status and
                                 the decision stay, and status_at reads SUPERSEDED; not covered
                                 by the signature
  record, park                   a file request bound to a backlog record {repo, id}, and the
                                 mark of the record's park (kitlib.hubsite.api park_item); not
                                 covered by the signature
  branch                         the branch the hub applies the change on, in its one linked
                                 worktree (kitlib.ownerfiles.apply); covered by the signature
                                 when present (canonical), and shown to the owner on the card
  apply {at, error}              the last apply that failed: the approval stays approved
  applied {by, branch, worktree} once the hub applied it (with commit and consumed_at)

An op document has no head, files or diff; it holds instead:
  kind        'op' (a document with no kind is a file approval)
  op          the delegation id D-nnn (OP_ID), and repo its repo name or `*` (OP_REPO)
  line        the run line the runner will spawn, the arguments filled in; what, the
              operation's `what`, shown beside it
  args_sha256 line_sha256(line), computed here, never taken from the caller
  consumed_at once consumed (an op consume writes no commit)
Its signature covers the id, kind, op, repo, args_sha256, the decision and decided_at; the line
is bound through args_sha256, and check_op refuses a line that no longer hashes to it. A pending
op request expires PENDING_OP_TTL_S after created_at, an approval APPROVED_TTL_S after
decided_at; only `approved` verifies: a consumed op approval was used once and is done.

A file approval's signature covers the id, repo, head, every file's (path, base_blob, blob), the
decision and decided_at (canonical): editing any of them, the expiry included, voids it. The content is left
out, as blob binds it. The status is not covered: only `approved` and `consumed` can verify, and
both need a signed approval.

The delta rule (docs/guard-hooks.md "Owner-only commits"; kitlib.ownerfiles.rules HubApprovals): an
approval is a change, not a commit. A commit C with one parent P makes it when C changes no path
the approval does not hold and each approved path p is at C what the change gives on P,
delta_result(base_blob, P:p, blob, content): a new file needs no file at P or the approved one; a
delete needs P:p at the base or absent; a modification needs P:p present, and gives the approved
blob where P:p is the base or the approved blob already, else the byte-level three-way merge
`git merge-file -p P:p base A` of the approved content A onto it, refused when it does not merge
cleanly. B0:p must be the signed base_blob and P:p a regular file; C:p keeps P's mode. Then the
server verifies the approved blobs for the signed base. A commit on main (as last fetched) that
carries the approval spends it: a later commit with it is refused.

The server's check compares `head` with the signed head, the approved base, exactly, and never
reads git: the client judges the parent and the commit with git (delta_error).

Expiry is read, never written by a timer: a pending request expires PENDING_TTL_S after
created_at; a file approval APPROVAL_LIFE_S after decided_at, consumed or not, and an op approval
APPROVED_TTL_S after it (approval_end; the owner's choice: a file approval holds until its change
is on main or for 30 days, whichever comes first, so a rebuilt branch and a land after the commit
can still check it).
A time that cannot be read, a decision dated after now and a naive now count as expired. Denied
and expired are final. Superseded is read too, from the `superseded` note, and before the expiry:
an approval superseded and past its life reads superseded, the act on record. It is final, never
consumed and never verified.

Routes (kitlib.hubsite.api): the helpers below call ROUTE (POST add), ROUTE/<id>/consume (POST,
an op approval's only) and ROUTE/<id>/verify (GET, query repo, head, files as JSON {path: blob}
and an optional commit; it answers {"ok": true, "valid": bool, "why": str} and never the key or
the sig); the owner's ROUTE/<id>/decide (POST) is the page's. No printing, no exit.
"""
import datetime
import difflib
import hashlib
import hmac
import json
import os
import re
import tempfile
import urllib.parse

from kitlib import hubvalidate
from kitlib.git import batch, cmd

from kitlib.hubclient.token import AGENT, ANY, OWNER


def _part(doc_id):
    """`doc_id` as one segment of a path, as client.part gives it: not imported from there, so
    this module loads without the client and the server it imports."""
    return urllib.parse.quote(str(doc_id), safe='')

# How long an approval counts, as the owner chose them (approval_end reads the last two).
PENDING_TTL_S = 7 * 86400       # a request not decided within 7 days is expired
PENDING_OP_TTL_S = 24 * 3600    # an op request not decided within 24 hours is expired
APPROVAL_LIFE_S = 30 * 86400    # a file approval, used or not, holds 30 days (or until spent)
APPROVED_TTL_S = 24 * 3600      # an op approval lasts 24 hours
DENIAL_PENDING_S = 24 * 3600    # a denial stays pending owner input this long after decided_at
                                # (kitlib.hubsite.store.approval_decided; the PM's ruling)
TRAILER = 'Owner-Approval'      # the trailer that carries one (kitlib.ownerfiles.rules)

PENDING, APPROVED, DENIED = 'pending', 'approved', 'denied'
CONSUMED, EXPIRED = 'consumed', 'expired'
STATUSES = tuple(hubvalidate.STATUSES['approvals'])     # the single source of stored ones
SUPERSEDED = 'superseded'       # read from the `superseded` note, never stored as a status
APPROVE, DENY = 'approve', 'deny'
DECISIONS = (APPROVE, DENY)
COLLECTION = 'approvals'
ROUTE = '/' + COLLECTION            # below api.BASE

MAX_PATH = 400
BRANCH = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}\Z')     # a request's branch
MAX_TITLE = 200
MAX_REASON = 4000
MAX_LINE = 4000                     # an op's run line

KIND_OP = 'op'
OP_ID = re.compile(r'D-[0-9]{3,}\Z')                     # kitlib.delegations.schema.ID
OP_REPO = re.compile(r'(?:[a-z0-9][a-z0-9-]*|\*)\Z')     # kitlib.delegations.schema.REPO
# One NAME=VALUE of a run line: kitlib.delegations.schema.ARG_NAME and ARG_CHARS
RUN_ARG = re.compile(r'(?:[a-z][a-z0-9_]{0,30})=(?:[A-Za-z0-9._:/\\-]{1,200})\Z')

# what check() and Verifier answer when the change is not the approved one
WHY_NO_KEY = 'no approval key'
WHY_UNKNOWN = 'no such approval'
WHY_BAD = 'the approval document is malformed'
WHY_UNSIGNED = 'the approval is not signed by the approval key'
WHY_STATUS = 'the approval is %s'               # % its status at that moment
WHY_REPO = 'the approval is for another repository'
WHY_HEAD = 'the approval is for another base commit'
WHY_FILES = 'the files differ from the approved ones'
WHY_COMMIT = 'the approval was not consumed'
WHY_SERVER = 'the hub server cannot say: %s'     # % the error's class
WHY_ANSWER = 'the hub server gave no verdict'
WHY_KIND_OP = 'the approval is for an operation, not a change'
WHY_KIND_FILES = 'the approval is for a change, not an operation'
WHY_OP = 'the approval is for another operation'
WHY_ARGS = 'the approval is for other arguments'

_HEX40 = re.compile(r'[0-9a-f]{40}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_HOST = re.compile(r'[a-z0-9]([a-z0-9.-]*[a-z0-9])?\Z')
_SCP = re.compile(r'(?:[^@/\s]+@)?([^:/\s]+):(?!//)(\S+)\Z')
_SCHEMES = {'https': 443, 'http': 80, 'ssh': 22, 'git': 9418}


class ApprovalError(Exception):
    """A request, decision or consume that cannot be made. `field` names what is wrong; `code`
    is the Api's error code it maps to (invalid or conflict)."""

    def __init__(self, message, field='', code='invalid'):
        Exception.__init__(self, message)
        self.message = message
        self.field = field
        self.code = code


def blob_id(content):
    """git's blob id (SHA-1 object format) of `content`: bytes as they are, a str as its UTF-8
    bytes, with no line-ending change."""
    data = content.encode('utf-8') if isinstance(content, str) else bytes(content)
    return hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest()


def repo_id(url):
    """The identity of the repository at remote `url`: `host[:port]/path`, the host lower-cased,
    a default port, credentials, the scheme, a trailing `/` and `.git` dropped. An https, http,
    ssh or git URL and the scp form (`user@host:path`) of one repository give the same id.
    ValueError for anything else (a local path, file://, a URL with a query); its message never
    holds the URL, which may carry a password."""
    if not isinstance(url, str) or not url.strip() or any(c.isspace() for c in url.strip()):
        raise ValueError('the remote is not a URL')
    url = url.strip()
    if '://' in url:
        scheme, _, rest = url.partition('://')
        scheme = scheme.lower()
        if scheme not in _SCHEMES:
            raise ValueError('the remote is not an https, http, ssh or git URL')
        if '?' in rest or '#' in rest:
            raise ValueError('the remote URL has a query or a fragment')
        authority, slash, path = rest.partition('/')
        host = authority.rpartition('@')[2]
        host, colon, port = host.partition(':')
        if colon:
            if not port.isdigit() or not 0 < int(port) < 65536:
                raise ValueError('the remote URL has a bad port')
            port = int(port)
        if not slash:
            raise ValueError('the remote URL names no repository')
    else:
        m = _SCP.match(url)
        if m is None or len(m.group(1)) < 2:        # one letter: a Windows drive, a local path
            raise ValueError('the remote is not a network URL')
        host, path, port, scheme = m.group(1), m.group(2), '', 'ssh'
    host = host.lower()
    if not _HOST.match(host):
        raise ValueError('the remote URL has a bad host')
    path = path.strip('/')
    if path.lower().endswith('.git'):
        path = path[:-4].rstrip('/')
    parts = path.split('/')
    if not path or any(p in ('', '.', '..') for p in parts):
        raise ValueError('the remote URL names no repository')
    where = host if port in ('', _SCHEMES[scheme]) else '%s:%d' % (host, port)
    return '%s/%s' % (where, '/'.join(parts))


def as_repo_id(value):
    """The repo_id of `value`: a repo_id (`host[:port]/path`, the host with a dot) read as an
    https URL's host and path, so a repo_id is kept as it is; anything else read by repo_id.
    ValueError as repo_id's."""
    if isinstance(value, str) and '://' not in value and '@' not in value \
            and '.' in value.partition('/')[0]:
        try:
            return repo_id('https://' + value)
        except ValueError:
            pass
    return repo_id(value)


def check_path(path):
    """Why `path` cannot be a file of an approval, or None: it must be relative to the repository
    top, with `/` between parts, none of them empty, `.`, `..` or `.git`, none ending in a dot or
    a space (Windows would read another name), no `\\`, `:` or control character."""
    if not isinstance(path, str) or not path:
        return 'a path is a non-empty string'
    if len(path) > MAX_PATH:
        return 'a path is at most %d characters' % MAX_PATH
    if any(ord(c) < 32 or ord(c) == 127 for c in path) or '\\' in path or ':' in path:
        return 'a path has no backslash, colon or control character'
    for part in path.split('/'):
        if part in ('', '.', '..') or part.lower() == '.git':
            return 'a path is relative to the repository top, with no empty, ., .. or .git part'
        if part.endswith(('.', ' ')):
            return 'no part of a path ends in a dot or a space'
    return None


def _iso(now):
    return now.isoformat(timespec='seconds')


def _when(text):
    """An ISO time with its offset as an aware datetime, or None."""
    if not isinstance(text, str):
        return None
    try:
        t = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def _text(value, field, limit, required):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ApprovalError('%s is %stext of at most %d characters'
                            % (field, 'non-empty ' if required else '', limit), field)
    return value


def new_doc(repo, title, head, files, now, reason=None, branch=None):
    """The pending document of a request (no id: the store gives it). `repo` is a remote URL or a
    repo_id; `files` is a list of {path, base_blob, content}: base_blob the file's blob at `head`
    (None: a new file), content its new text (None: a delete). Any `blob` a caller sends is
    ignored: blob_id(content) is computed here. `branch`, the branch the hub applies the change
    on, is a branch name (BRANCH) or None. `now` is an aware datetime. ApprovalError names the
    field that is wrong."""
    try:
        repo = as_repo_id(repo)
    except ValueError as e:
        raise ApprovalError(str(e), 'repo')
    title = _text(title, 'title', MAX_TITLE, True)
    reason = _text(reason, 'reason', MAX_REASON, False)
    if not isinstance(head, str) or not _HEX40.match(head):
        raise ApprovalError('head is a commit id of 40 lowercase hex digits', 'head')
    if not isinstance(files, list) or not files:
        raise ApprovalError('files is a non-empty list', 'files')
    if len(files) > hubvalidate.MAX_FILES:
        raise ApprovalError('one approval holds at most %d files' % hubvalidate.MAX_FILES, 'files')
    out, seen = [], set()
    for f in files:
        if not isinstance(f, dict):
            raise ApprovalError('each file is an object {path, base_blob, content}', 'files')
        path, base, content = f.get('path'), f.get('base_blob'), f.get('content')
        why = check_path(path)
        if why is not None:
            raise ApprovalError(why, 'files')
        if path.lower() in seen:            # Windows: one file, whatever the case
            raise ApprovalError('a path is named twice: %s' % path, 'files')
        seen.add(path.lower())
        if base is not None and (not isinstance(base, str) or not _HEX40.match(base)):
            raise ApprovalError('base_blob of %s is a blob id of 40 lowercase hex digits, or '
                                'null for a new file' % path, 'files')
        if content is not None:
            if not isinstance(content, str):
                raise ApprovalError('content of %s is text, or null for a delete' % path, 'files')
            try:
                size = len(content.encode('utf-8'))
            except UnicodeEncodeError:
                raise ApprovalError('content of %s is not valid text' % path, 'files')
            if size > hubvalidate.MAX_CONTENT_BYTES:
                raise ApprovalError('content of %s is over %d bytes' % (path, hubvalidate.MAX_CONTENT_BYTES),
                                    'files')
        elif base is None:
            raise ApprovalError('%s is neither in the base commit nor given content' % path,
                                'files')
        out.append({'path': path, 'base_blob': base,
                    'blob': None if content is None else blob_id(content), 'content': content})
    out.sort(key=lambda f: f['path'])
    doc = {'status': PENDING, 'repo': repo, 'title': title, 'head': head, 'files': out,
           'created_at': _iso(now)}
    if reason is not None:
        doc['reason'] = reason
    if branch is not None:
        if not isinstance(branch, str) or not BRANCH.match(branch) or '..' in branch \
                or '//' in branch or branch.endswith(('.lock', '/', '.')):
            raise ApprovalError('branch is a branch name (as kit/<slug>), not %r' % (branch,),
                                'branch')
        doc['branch'] = branch
    return doc


def line_sha256(line):
    """The hex SHA-256 of the run line `line` (text), as its UTF-8 bytes."""
    return hashlib.sha256(line.encode('utf-8')).hexdigest()


def _is_op(doc):
    """True for an op document, False for a file one (no kind); ValueError for another kind."""
    kind = doc.get('kind') if isinstance(doc, dict) else None
    if kind is None:
        return False
    if kind == KIND_OP:
        return True
    raise ValueError('the approval is of no known kind')


def new_op_doc(op, repo, line, title, now, reason=None, what=None):
    """The pending document of a request to run the delegated operation `op` (D-nnn) of `repo`
    (its repo name or `*`) once, as the run line `line`: `run D-nnn` and `name=value` for each
    argument, sorted by name, as kitlib.delegations.run.run_line writes it. No id: the
    store gives it. `now` is an aware datetime. args_sha256 is computed here. ApprovalError names
    the field that is wrong."""
    if not isinstance(op, str) or not OP_ID.match(op):
        raise ApprovalError('op is a delegation id D-nnn', 'op')
    if not isinstance(repo, str) or not OP_REPO.match(repo):
        raise ApprovalError("repo is the delegation's repo name or *", 'repo')
    line = _text(line, 'line', MAX_LINE, True)
    words = line.split(' ')
    names = [w.split('=', 1)[0] for w in words[2:]]
    if (words[:2] != ['run', op] or not all(RUN_ARG.match(w) for w in words[2:])
            or names != sorted(set(names))):
        raise ApprovalError('line is the run line `run %s NAME=VALUE ...`, one space apart, '
                            'each name once and sorted' % op, 'line')
    title = _text(title, 'title', MAX_TITLE, True)
    reason = _text(reason, 'reason', MAX_REASON, False)
    what = _text(what, 'what', MAX_REASON, False)
    doc = {'status': PENDING, 'kind': KIND_OP, 'op': op, 'repo': repo, 'title': title,
           'line': line, 'args_sha256': line_sha256(line), 'created_at': _iso(now)}
    if what is not None:
        doc['what'] = what
    if reason is not None:
        doc['reason'] = reason
    return doc


def canonical(doc_id, doc):
    """The bytes sign() covers: JSON of the id, repo, head, each file's [path, base_blob, blob]
    sorted by path, the decision, decided_at and, only when the document has one, the branch
    (so a signature made before branches stays valid), with sorted keys and no spaces; for an op
    document, of the id, kind, op, repo, args_sha256, the decision and decided_at. ValueError
    for a document that has not their shape, that names a path twice or is of no known kind."""
    if _is_op(doc):
        if not all(isinstance(doc.get(k), str) for k in ('op', 'repo', 'args_sha256')):
            raise ValueError('the op approval has no op, repo or args_sha256')
        body = {'id': doc_id, 'kind': KIND_OP, 'op': doc['op'], 'repo': doc['repo'],
                'args_sha256': doc['args_sha256'], 'decision': doc.get('decision'),
                'decided_at': doc.get('decided_at')}
        return json.dumps(body, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=True).encode('ascii')
    files = doc.get('files') if isinstance(doc, dict) else None
    if not isinstance(files, list) or not all(isinstance(f, dict) for f in files):
        raise ValueError('the approval has no files list')
    triples = sorted(([f.get('path'), f.get('base_blob'), f.get('blob')] for f in files
                      if isinstance(f.get('path'), str)), key=lambda t: t[0])
    if len(triples) != len(files):
        raise ValueError('a file of the approval has no path')
    if len({t[0] for t in triples}) != len(triples):
        raise ValueError('a path of the approval is named twice')
    body = {'id': doc_id, 'repo': doc.get('repo'), 'head': doc.get('head'), 'files': triples,
            'decision': doc.get('decision'), 'decided_at': doc.get('decided_at')}
    if 'branch' in doc:
        body['branch'] = doc['branch']
    return json.dumps(body, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True).encode('ascii')


def sign(key, doc_id, doc):
    """The signature of the decision in `doc`: HMAC with SHA-256 of canonical() under the
    approval key `key` (auth.read_approval_key), as 64 lowercase hex digits."""
    return hmac.new(key.encode('ascii'), canonical(doc_id, doc), hashlib.sha256).hexdigest()


def approval_end(doc):
    """When the decided approval `doc` stops counting, or None when its decided_at cannot be
    read: decided_at + APPROVED_TTL_S for an op approval, + APPROVAL_LIFE_S for a file approval,
    used or not (a file approval also ends once its change is on main: spent). The one place an
    approval's lifetime is computed, so a cutoff date would go here."""
    t = _when(doc.get('decided_at'))
    if t is None:
        return None
    life = APPROVED_TTL_S if doc.get('kind') == KIND_OP else APPROVAL_LIFE_S
    return t + datetime.timedelta(seconds=life)


def expires_at(doc):
    """When `doc` stops counting: created_at + PENDING_TTL_S (PENDING_OP_TTL_S for an op
    document) while pending, approval_end(doc) when approved or consumed; None when it never
    expires (denied, expired) or a time cannot be read (status_at then counts it expired)."""
    status = doc.get('status') if isinstance(doc, dict) else None
    if status == PENDING:
        t = _when(doc.get('created_at'))
        life = PENDING_OP_TTL_S if doc.get('kind') == KIND_OP else PENDING_TTL_S
        return None if t is None else t + datetime.timedelta(seconds=life)
    if status in (APPROVED, CONSUMED):
        return approval_end(doc)
    return None


def status_at(doc, now):
    """The status of `doc` at `now` (an aware datetime): SUPERSEDED for an approved or denied one
    with a `superseded` note, before any expiry (an approval superseded and past its life reads
    superseded: the act on record wins); EXPIRED for a pending, approved or consumed one past
    expires_at or with a time that cannot be read, for an approved or consumed one decided after
    `now`, and for any of them when `now` is naive; its stored status otherwise; None for a
    document with no known status."""
    status = doc.get('status') if isinstance(doc, dict) else None
    if status not in STATUSES:
        return None
    if status in (APPROVED, DENIED) and isinstance(doc.get('superseded'), dict):
        return SUPERSEDED
    if status in (PENDING, APPROVED, CONSUMED):
        end = expires_at(doc)
        try:
            if end is None or now >= end:
                return EXPIRED
            if status != PENDING and now < _when(doc.get('decided_at')):
                return EXPIRED
        except TypeError:           # a naive `now` cannot be compared: no time, no approval
            return EXPIRED
    return status


def decide_fields(key, doc_id, doc, decision, now):
    """The fields the owner's decision writes on `doc`: status, decision, decided_at and, for
    APPROVE, sig. Only a pending document that has not expired is decided; an approval needs the
    approval key. ApprovalError (code conflict for a document no longer pending)."""
    if decision not in DECISIONS:
        raise ApprovalError('decision is approve or deny', 'decision')
    status = status_at(doc, now)
    if status is None:
        raise ApprovalError(WHY_BAD, 'status', 'conflict')
    if status != PENDING:
        raise ApprovalError('only a pending approval is decided; this one is %s' % status,
                            'status', 'conflict')
    fields = {'status': APPROVED if decision == APPROVE else DENIED, 'decision': decision,
              'decided_at': _iso(now)}
    if decision == APPROVE:
        if doc.get('kind') == KIND_OP and not _line_holds(doc):
            raise ApprovalError(WHY_BAD, 'line', 'conflict')
        if not key:
            raise ApprovalError('there is no approval key to sign with', 'decision', 'conflict')
        signed = dict(doc, **fields)
        try:
            fields['sig'] = sign(key, doc_id, signed)
        except ValueError:
            raise ApprovalError(WHY_BAD, 'files', 'conflict')
    return fields


def withdraw_check(doc, now):
    """Only an undecided request is withdrawn by its requester: one pending, or stored pending
    and expired undecided (its withdrawal drops the line it parked on a record, which the expiry
    keeps: kitlib.hubsite.api approvals_delete). ApprovalError (code conflict, field status) for
    a decided one (approved, denied, consumed, or an approval expired after its decision), the
    owner's record by then."""
    status = status_at(doc, now)
    if status == EXPIRED and doc.get('status') == PENDING:
        return
    if status != PENDING:
        raise ApprovalError('only an undecided approval can be withdrawn, not one that is %s'
                            % (status or 'malformed'), 'status', 'conflict')


def supersede_fields(doc_id, doc, by, who, now):
    """The fields marking `doc` superseded: {'superseded': {by, from, at}}. Only a decided
    approval nobody consumed (status_at approved or denied) is superseded; a pending one is
    withdrawn instead (withdraw_check). `by` is the approval that replaces it (A-<n>, not
    `doc_id` itself) or a commit id (40 lowercase hex); the caller checks that the approval
    exists and that main reaches the commit (on_main), and who `who` is. ApprovalError (code
    conflict, field status; invalid, field by)."""
    status = status_at(doc, now)
    if status not in (APPROVED, DENIED):
        hint = ': withdraw a pending one' if status == PENDING else ''
        raise ApprovalError('only a decided approval nobody consumed can be superseded, not one '
                            'that is %s%s' % (status or 'malformed', hint), 'status', 'conflict')
    if not (isinstance(by, str) and (APPROVAL_ID.match(by) or _HEX40.match(by))):
        raise ApprovalError('by is the approval that replaces it (A-<n>) or a commit id of 40 '
                            'lowercase hex digits, not %s' % ascii(by), 'by')
    if by == doc_id:
        raise ApprovalError('an approval cannot supersede itself', 'by')
    return {'superseded': {'by': by, 'from': who, 'at': _iso(now)}}


def on_main(checkout, sha):
    """(True/False, None): whether main as last fetched (refs/remotes/<remote>/<main> of the
    checkout's config, as kitlib.ownerfiles.rules names it) reaches the commit `sha`; (None,
    why) when that cannot be told (no main, an unknown commit, git failing)."""
    from kitlib import config as kconfig
    from kitlib.git import refs
    try:
        cfg = kconfig.load(checkout)
        ref = 'refs/remotes/%s/%s' % (cfg.remote, cfg.main_branch)
    except Exception as e:      # noqa: BLE001 - fail closed: no main reaches nothing
        return None, 'main cannot be named: %s' % e
    return refs.is_ancestor(sha, ref, checkout)


def _line_holds(doc):
    """True when the op document `doc` holds a line that hashes to its args_sha256: the line the
    owner reads is the one the signature binds."""
    line = doc.get('line')
    return isinstance(line, str) and line_sha256(line) == doc.get('args_sha256')


def _signed(key, doc_id, doc):
    """'' when `doc` is an approval signed under `key`, else the WHY_ phrase of why not."""
    if not key:
        return WHY_NO_KEY
    if not isinstance(doc, dict):
        return WHY_UNKNOWN
    sig = doc.get('sig')
    if doc.get('decision') != APPROVE or not isinstance(sig, str):
        return WHY_UNSIGNED
    try:
        good = hmac.compare_digest(sign(key, doc_id, doc).encode('ascii'),
                                   sig.encode('ascii', 'replace'))
    except ValueError:
        return WHY_BAD
    return '' if good else WHY_UNSIGNED


def consume_fields(key, doc_id, doc, commit, now):
    """The fields consuming `doc` for `commit` writes: status, commit and consumed_at. Only an
    approval signed under `key` that is approved now and was never consumed is consumed (the
    status is not signed, so a stored commit counts too). ApprovalError (code conflict for one
    that cannot be consumed now)."""
    if not isinstance(commit, str) or not _HEX40.match(commit):
        raise ApprovalError('commit is a commit id of 40 lowercase hex digits', 'commit')
    why = _signed(key, doc_id, doc)
    if why:
        raise ApprovalError(why, 'sig', 'conflict')
    if doc.get('kind') is not None:      # signed, so of a known kind: an op
        raise ApprovalError(WHY_KIND_OP, 'kind', 'conflict')
    if doc.get('commit'):
        raise ApprovalError('the approval was consumed already by %s' % doc['commit'],
                            'commit', 'conflict')
    status = status_at(doc, now)
    if status != APPROVED:
        raise ApprovalError('only an approved approval is consumed; this one is %s'
                            % (status or 'malformed'), 'status', 'conflict')
    return {'status': CONSUMED, 'commit': commit, 'consumed_at': _iso(now)}


def check(key, doc_id, doc, repo, head, blobs, now, commit=None):
    """(True, '') when the change of `repo` (a remote URL or a repo_id) at base commit `head`
    with the files `blobs` ({path: blob, None for a delete}) is the one `doc` approved and the
    approval still counts at `now`; else (False, one of the WHY_ phrases). Given a `commit`, the
    approval must also be consumed (WHY_COMMIT), by that commit or another: a rebuilt branch
    keeps its approval until the change is on main (spent, judged by the client) or it expires.
    A document is trusted only once its signature checks under `key`, before any of its fields
    is compared."""
    why = _signed(key, doc_id, doc)
    if why:
        return False, why
    if doc.get('kind') is not None:      # signed, so of a known kind: an op
        return False, WHY_KIND_OP
    status = status_at(doc, now)
    if status not in (APPROVED, CONSUMED):
        return False, WHY_STATUS % (status or 'malformed')
    if commit is not None and status != CONSUMED:
        return False, WHY_COMMIT
    try:
        if as_repo_id(repo) != doc.get('repo'):
            return False, WHY_REPO
    except ValueError:
        return False, WHY_REPO
    if not isinstance(head, str) or head != doc.get('head'):
        return False, WHY_HEAD
    approved = {f['path']: f.get('blob') for f in doc['files']}
    if not isinstance(blobs, dict) or blobs != approved:
        return False, WHY_FILES
    return True, ''


def check_op(key, doc_id, doc, op, repo, args_sha256, now):
    """(True, '') when `doc` is an op approval signed under `key`, approved and never consumed at
    `now`, for the operation `op` of `repo` run as the line whose line_sha256 is `args_sha256`;
    else (False, one of the WHY_ phrases). The signature is checked before any field is
    compared."""
    why = _signed(key, doc_id, doc)
    if why:
        return False, why
    if doc.get('kind') != KIND_OP:
        return False, WHY_KIND_FILES
    if doc.get('consumed_at') is not None:
        return False, WHY_STATUS % CONSUMED
    status = status_at(doc, now)
    if status != APPROVED:
        return False, WHY_STATUS % (status or 'malformed')
    if not _line_holds(doc):
        return False, WHY_BAD
    if op != doc['op']:
        return False, WHY_OP
    if repo != doc['repo']:
        return False, WHY_REPO
    if not isinstance(args_sha256, str) or args_sha256 != doc['args_sha256']:
        return False, WHY_ARGS
    return True, ''


def consume_op_fields(key, doc_id, doc, args_sha256, now):
    """The fields using the op approval `doc` once writes: status and consumed_at. Only an op
    approval signed under `key`, approved now, never consumed and for the arguments
    `args_sha256` is consumed. ApprovalError (code conflict for one that cannot be consumed
    now)."""
    if not isinstance(args_sha256, str) or not _HEX64.match(args_sha256):
        raise ApprovalError('args_sha256 is 64 lowercase hex digits', 'args_sha256')
    why = _signed(key, doc_id, doc)
    if why:
        raise ApprovalError(why, 'sig', 'conflict')
    if doc.get('kind') != KIND_OP:
        raise ApprovalError(WHY_KIND_FILES, 'kind', 'conflict')
    if doc.get('consumed_at') is not None:
        raise ApprovalError('the approval was consumed already at %s' % doc['consumed_at'],
                            'status', 'conflict')
    status = status_at(doc, now)
    if status != APPROVED:
        raise ApprovalError('only an approved approval is consumed; this one is %s'
                            % (status or 'malformed'), 'status', 'conflict')
    if args_sha256 != doc.get('args_sha256'):
        raise ApprovalError(WHY_ARGS, 'args_sha256', 'conflict')
    if not _line_holds(doc):
        raise ApprovalError(WHY_BAD, 'line', 'conflict')
    return {'status': CONSUMED, 'consumed_at': _iso(now)}


def remote_id(checkout, remote='origin', run=None):
    """(repo_id, None) of `remote` of the git checkout `checkout`, or (None, why). `run` is
    kitlib.git.cmd.run_text or a stand-in. The why never holds the URL."""
    text, why = (run or cmd.run_text)(['remote', 'get-url', remote], checkout)
    if text is None:
        return None, why or 'git could not read the remote'
    try:
        return repo_id(text), None
    except ValueError as e:
        return None, str(e)


def read_base(checkout, head, path, base_blob, cat=None):
    """(text, None) of the file `path` at commit `head` in the git checkout `checkout` (the
    owner's choice: the server reads the base with git for the page's diff), or (None, why). The
    file must be the blob `base_blob` (40 hex) and UTF-8; with base_blob None (a new file) it must
    be absent at head, and the answer is (None, None). `head` must be a commit of the checkout.
    One `git cat-file --batch`: `cat` is kitlib.git.batch.cat_file or a stand-in."""
    if not isinstance(head, str) or not _HEX40.match(head):
        return None, 'not a commit id'
    if check_path(path) is not None:
        return None, 'not a path that can be approved'
    if base_blob is not None and (not isinstance(base_blob, str) or not _HEX40.match(base_blob)):
        return None, 'not a blob id'
    commit, spec = head + '^{commit}', head + ':' + path
    got, why = (cat or batch.cat_file)([commit, spec], checkout)
    if got is None:
        return None, why or 'git could not read the base'
    if got.get(commit) is None:
        return None, 'head is not a commit in the checkout'
    obj = got.get(spec)
    if base_blob is None:
        return (None, None) if obj is None else (None, 'the file exists at head')
    if obj is None:
        return None, 'the file is not at head'
    if obj[0] != 'blob' or blob_id(obj[1]) != base_blob:
        return None, 'the base differs from the file at head'
    try:
        return obj[1].decode('utf-8'), None
    except UnicodeDecodeError:
        return None, 'the base is not text'


def diff(path, base, content):
    """The unified diff lines of one file, from `base` (None: a new file) to `content` (None: a
    delete), named a/<path> and b/<path> (/dev/null for the side that is absent)."""
    old = [] if base is None else base.splitlines(keepends=True)
    new = [] if content is None else content.splitlines(keepends=True)
    return list(difflib.unified_diff(old, new, '/dev/null' if base is None else 'a/' + path,
                                     '/dev/null' if content is None else 'b/' + path))


APPROVAL_ID = re.compile(r'A-[0-9]+\Z')   # a file or op approval's doc id
SHA = re.compile(r'[0-9a-f]{40}\Z')       # a commit id, as git log gives it
_NOREPLACE = ['--no-replace-objects']     # replace refs do not travel: judge the real objects


def _entries(checkout, rev, paths, call):
    """({path: (mode, type, object id)} of `paths` at the commit `rev`, a path with no entry left
    out, None), or (None, why). One `git ls-tree` without -r: a path that is a folder shows as its
    tree entry."""
    r = call(_NOREPLACE + ['ls-tree', '-z', '--full-tree', rev, '--'] + list(paths), checkout)
    if r.rc != 0:
        return None, 'git ls-tree %s failed: %s' % (rev[:12], r.err or 'exit %s' % r.rc)
    wanted, found = set(paths), {}
    for row in cmd.text(r.out).split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name in wanted:
            found[name] = tuple(meta.split())
    return found, None


REGULAR_MODES = ('100644', '100755')    # the tree modes of a regular file


def delta_result(checkout, base_blob, parent_blob, approved_blob, approved_bytes, call=None,
                 cat=None):
    """(R, None): what the approved change of one file, from `base_blob` (None: the approval
    creates it) to `approved_blob` (None: it deletes it), gives on a parent that holds
    `parent_blob` (None: no file there); R is a blob id, or None for no file. (None, why) when it
    gives nothing (the delta rule, module docstring). `approved_bytes`: the approved content,
    which must be `approved_blob`'s, so the merge reads the bytes the owner saw. Fails closed on
    any git failure. `call` is kitlib.git.cmd.call and `cat` kitlib.git.batch.cat_file, or
    stand-ins."""
    data, why = delta_content(checkout, base_blob, parent_blob, approved_blob, approved_bytes,
                              call=call, cat=cat)
    if why:
        return None, why
    return (None if data is None else blob_id(data)), None


def delta_content(checkout, base_blob, parent_blob, approved_blob, approved_bytes, call=None,
                  cat=None):
    """delta_result with the bytes: (D, None), D the content the approved change gives on the
    parent (bytes, None for no file), or (None, why). What commit.py writes on a newer HEAD."""
    if approved_blob is not None and not (isinstance(approved_bytes, bytes)
                                          and blob_id(approved_bytes) == approved_blob):
        return None, 'the approved content is not its approved blob'
    if base_blob is None:
        if parent_blob is None or parent_blob == approved_blob:
            return (None if approved_blob is None else approved_bytes), None
        return None, 'the file the approval creates is already at the parent with other content'
    if approved_blob is None:
        if parent_blob is None or parent_blob == base_blob:
            return None, None
        return None, 'the file the approval deletes changed since the approved base'
    if parent_blob is None:
        return None, 'the file the approval changes is not at the parent'
    if parent_blob in (base_blob, approved_blob):
        return approved_bytes, None
    got, why = (cat or batch.cat_file)([parent_blob, base_blob], checkout)
    if got is None:
        return None, why or 'git could not read the blobs to merge'
    sides = [got.get(parent_blob), got.get(base_blob)]
    if any(s is None or s[0] != 'blob' for s in sides):
        return None, 'git could not read the blobs to merge'
    with tempfile.TemporaryDirectory(prefix='kit-approval-') as folder:
        names = []
        for name, data in (('parent', sides[0][1]), ('base', sides[1][1]),
                           ('approved', approved_bytes)):
            names.append(os.path.join(folder, name))
            with open(names[-1], 'wb') as f:
                f.write(data)
        r = (call or cmd.call)(['merge-file', '-p'] + names, checkout)
    if r.rc is None:
        return None, 'git merge-file failed: %s' % r.err
    if r.rc != 0:
        return None, ('the approved change does not merge cleanly onto the parent (git '
                      'merge-file exit %s)' % r.rc)
    return bytes(r.out), None


def spent(checkout, doc_id, rev, call=None):
    """(sha, None): the newest commit `rev` (main as last fetched) reaches that carries the trailer
    `Owner-Approval: hub:<doc_id>` exactly (TRAILER), so the approved change is on main and the
    approval is used up; (None, None) when none does; (None, why) when git cannot tell."""
    if not (isinstance(rev, str) and rev and not rev.startswith('-')):
        return None, 'not a rev: %r' % (rev,)
    want = 'hub:%s' % doc_id
    r = (call or cmd.call)(_NOREPLACE + [
        'log', '--no-use-mailmap', '-F', '--grep=%s' % want,
        '--format=%%H%%x00%%(trailers:key=%s,valueonly,separator=%%x01)%%x00' % TRAILER,
        rev, '--'], checkout)
    if r.rc != 0:
        return None, 'git log %s failed: %s' % (rev, r.err or 'exit %s' % r.rc)
    fields = cmd.text(r.out).split('\0')
    for i in range(0, len(fields) - 1, 2):
        sha = fields[i].strip()
        if sha and want in [v.strip() for v in fields[i + 1].split('\x01')]:
            return sha, None
    return None, None


def landed(checkout, base, tip, call=None):
    """({doc id: sha}, None): each approval a commit `tip` reaches and `base` does not carries, as
    the only `Owner-Approval: hub:<id>` value of that commit, with the newest such commit; else
    (None, why). What a landing from `base` (main before it) to `tip` puts on main, for the hub
    page (the landing feed); spent stays the check that counts."""
    for rev in (base, tip):
        if not (isinstance(rev, str) and rev and not rev.startswith('-')):
            return None, 'not a rev: %r' % (rev,)
    r = (call or cmd.call)(_NOREPLACE + [
        'log', '--no-use-mailmap',
        '--format=%%H%%x00%%(trailers:key=%s,valueonly,separator=%%x01)%%x00' % TRAILER,
        '%s..%s' % (base, tip), '--'], checkout)
    if r.rc != 0:
        return None, 'git log %s..%s failed: %s' % (base, tip, r.err or 'exit %s' % r.rc)
    out = {}
    fields = cmd.text(r.out).split('\0')
    for i in range(0, len(fields) - 1, 2):
        sha, values = fields[i].strip(), [v.strip() for v in fields[i + 1].split('\x01')]
        values = [v for v in values if v]
        if not (sha and len(values) == 1 and values[0].startswith('hub:')):
            continue
        doc_id = values[0][len('hub:'):]
        if APPROVAL_ID.match(doc_id):
            out.setdefault(doc_id, sha)          # git log lists the newest first
    return out, None


def approved_files(doc):
    """{path: (base_blob, blob, content bytes or None)} of the file approval `doc`, or None when
    it is malformed (no 40-hex head, no files, a path named twice, a field of the wrong type)."""
    if not isinstance(doc, dict):
        return None
    head, files = doc.get('head'), doc.get('files')
    if not (isinstance(head, str) and _HEX40.match(head) and isinstance(files, list) and files):
        return None
    out = {}
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get('path'), str) or f['path'] in out:
            return None
        base, blob, content = f.get('base_blob'), f.get('blob'), f.get('content')
        if any(v is not None and not (isinstance(v, str) and _HEX40.match(v))
               for v in (base, blob)) or not (content is None or isinstance(content, str)):
            return None
        out[f['path']] = (base, blob, None if content is None else content.encode('utf-8'))
    return out


def delta_error(checkout, doc, parent, commit, blobs, call=None, cat=None):
    """({path: approved blob}, None) when the commit `commit`, whose one parent is `parent`, makes
    the approved change of `doc` (as the hub's snapshot gives it, not yet verified) on it, `blobs`
    being every path the commit changes with its blob there; else (None, why). The delta rule,
    module docstring: the commit changes no path the approval does not hold; at the approval's
    base each approved path is its base_blob; and at the commit each is what delta_result gives
    on the parent, a regular file in the parent's mode. The caller then asks the hub to verify
    the approved blobs for the signed base, which binds every field read here."""
    files = approved_files(doc)
    if files is None:
        return None, WHY_BAD
    extra = sorted(p for p in (blobs if isinstance(blobs, dict) else ()) if p not in files)
    if extra:
        return None, '%s: %s is not in the approval' % (WHY_FILES, extra[0])
    call = call or cmd.call
    targets, why = delta_targets(checkout, doc, parent, call=call, cat=cat)
    if targets is None:
        return None, why
    paths = sorted(files)
    at_commit, why = _entries(checkout, commit, paths, call)
    if at_commit is None:
        return None, why
    for p in paths:
        par, want, _data = targets[p]
        c = at_commit.get(p)
        if want is None:                # the approved change leaves no file there
            if c is not None:
                return None, '%s: %s at %s is not the approved change' % (
                    WHY_FILES, p, commit[:12])
            continue
        if c is None or not _regular(c, want):
            return None, '%s: %s at %s is not the approved change' % (WHY_FILES, p, commit[:12])
        if par is not None and c[0] != par[0]:
            return None, '%s: %s at %s changes its mode from %s to %s' % (
                WHY_FILES, p, commit[:12], par[0], c[0])
    return {p: files[p][1] for p in paths}, None


def delta_targets(checkout, doc, parent, call=None, cat=None):
    """({path: (P entry or None, R blob or None, R bytes or None)}, None): what the approved
    change of `doc` (the hub's snapshot document, not yet verified) gives on the commit `parent`
    P, path by path, R by delta_content; else (None, why). At the approval's base each approved
    path must be its base_blob, a regular file (or absent for a new file), and at P a regular
    file or absent. P's entry is (mode, type, object id). The one reading of the delta rule's
    base and parent: delta_error judges a commit by it, and commit.py writes R on a newer HEAD."""
    files = approved_files(doc)
    if files is None:
        return None, WHY_BAD
    paths, head, call = sorted(files), doc['head'], call or cmd.call
    found = []
    for rev in (head, parent):
        got, why = _entries(checkout, rev, paths, call)
        if got is None:
            return None, why
        found.append(got)
    at_base, at_parent = found
    out = {}
    for p in paths:
        base, blob, content = files[p]
        b, par = at_base.get(p), at_parent.get(p)
        if (b is None) != (base is None) or (b is not None and not _regular(b, base)):
            return None, '%s: %s at the approved base %s is not the approved base blob' % (
                WHY_HEAD, p, head[:12])
        if par is not None and not _regular(par):
            return None, '%s: %s at %s is not a regular file' % (WHY_FILES, p, parent[:12])
        data, why = delta_content(checkout, base, None if par is None else par[2], blob, content,
                                  call=call, cat=cat)
        if why:
            return None, '%s: %s: %s' % (WHY_FILES, p, why)
        out[p] = (par, None if data is None else blob_id(data), data)
    return out, None


def _regular(entry, oid=None):
    """True when the ls-tree entry (mode, type, object id) is a regular file (with blob `oid`)."""
    return (len(entry) == 3 and entry[0] in REGULAR_MODES and entry[1] == 'blob'
            and (oid is None or entry[2] == oid))


class Verifier(object):
    """Asks the hub server whether a change is the approved one. `client` is a
    kitlib.hubclient.client.Client (or a stand-in with its get()); the call is the agent's.
    verify() fails closed: any error of the call (NotRunning, Refused, ServeError, anything
    else) or an answer without a verdict is (False, why)."""

    def __init__(self, client):
        self.client = client

    def approved_doc(self, doc_id):
        """(the document the hub's snapshot gives the approval `doc_id`, None), or (None, why):
        WHY_SERVER on a failed call, WHY_ANSWER for a snapshot without approvals, WHY_UNKNOWN when
        it holds none of that id, WHY_BAD for one that is not an object. Not trusted as it stands:
        verify() checks what is read from it against the signature."""
        try:
            got = self.client.get('/snapshot', ANY, {'collections': 'approvals'})
        except Exception as e:      # noqa: BLE001 - fail closed: no answer is no approval
            return None, WHY_SERVER % type(e).__name__
        colls = got.get('collections') if isinstance(got, dict) else None
        docs = colls.get('approvals') if isinstance(colls, dict) else None
        if not isinstance(docs, dict):
            return None, WHY_ANSWER
        if doc_id not in docs:
            return None, WHY_UNKNOWN
        doc = docs[doc_id]
        return (doc, None) if isinstance(doc, dict) else (None, WHY_BAD)

    def verify(self, doc_id, repo, head, blobs, commit=None):
        """(True, '') only when the server answers valid true for this change (and, given a
        `commit`, the approval was consumed, by it or another commit: check's commit); else
        (False, why)."""
        if commit is not None and not commit:   # the client drops an empty value: unbound
            return False, WHY_COMMIT
        try:
            files = json.dumps(blobs, sort_keys=True, separators=(',', ':'))
            query = {'repo': repo, 'head': head, 'files': files}
            if commit is not None:
                query['commit'] = commit
            got = self.client.get('%s/%s/verify' % (ROUTE, _part(doc_id)), AGENT, query)
        except Exception as e:      # fail closed: no answer is no approval
            return False, WHY_SERVER % type(e).__name__
        if not isinstance(got, dict) or got.get('valid') is not True:
            why = got.get('why') if isinstance(got, dict) else None
            return False, why if isinstance(why, str) and why else WHY_ANSWER
        return True, ''


def request(client, repo, title, head, files, reason=None, record=None, branch=None):
    """Add a request for the owner (POST ROUTE, the agent's) and answer the server's reply; the
    client's errors propagate. `files` as new_doc takes them; `record` the {repo, id} it binds."""
    body = {'repo': repo, 'title': title, 'head': head, 'files': files}
    if reason is not None:
        body['reason'] = reason
    if record is not None:
        body['record'] = record
    if branch is not None:
        body['branch'] = branch
    return client.post(ROUTE, AGENT, body)


def apply_approved(client, doc_id, if_version=None):
    """The owner has the hub apply the approved file request `doc_id` again (POST
    ROUTE/<id>/apply, the owner's; docs/hub-approvals.md "Applying"), pinned to `if_version`
    when one is given, and answer the server's reply: `applied` true with `commit`, or false
    with `why`. The client's errors propagate."""
    body = {} if if_version is None else {'if_version': if_version}
    return client.post('%s/%s/apply' % (ROUTE, _part(doc_id)), OWNER, body)


def request_op(client, op, repo, line, title, reason=None, what=None):
    """Add a request to run `op` once as `line` (POST ROUTE with kind op, the agent's) and answer
    the server's reply; the client's errors propagate."""
    body = {'kind': KIND_OP, 'op': op, 'repo': repo, 'line': line, 'title': title}
    if reason is not None:
        body['reason'] = reason
    if what is not None:
        body['what'] = what
    return client.post(ROUTE, AGENT, body)


def verify_op(client, doc_id, op, repo, args_sha256):
    """(True, '', version) only when the server answers valid true for this run of `op` and
    gives the document's version (a whole number); else (False, why, None). Fails closed as
    Verifier does."""
    try:
        got = client.get('%s/%s/verify' % (ROUTE, _part(doc_id)), AGENT,
                         {'kind': KIND_OP, 'op': op, 'repo': repo, 'args': args_sha256})
    except Exception as e:      # fail closed: no answer is no approval
        return False, WHY_SERVER % type(e).__name__, None
    if not isinstance(got, dict) or got.get('valid') is not True:
        why = got.get('why') if isinstance(got, dict) else None
        return False, why if isinstance(why, str) and why else WHY_ANSWER, None
    version = got.get('version')
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        return False, WHY_ANSWER, None
    return True, '', version


def consume_op(client, doc_id, args_sha256, if_version):
    """Mark the op approval `doc_id` used (POST ROUTE/<id>/consume, the agent's), pinned to
    `if_version`; the client's errors propagate."""
    return client.post('%s/%s/consume' % (ROUTE, _part(doc_id)), AGENT,
                       {'args_sha256': args_sha256, 'if_version': if_version})
