"""The hub applies a change the owner approved (docs/hub-approvals.md "Applying"): it commits the
approved files on the branch the request named, in that branch's one linked worktree, and the
approval is consumed by that commit. kitlib.hubsite.apiapprovals runs it when the owner approves,
and again on `hub.py approvals apply A-<n>`. Nothing here prints, exits or raises a refusal: each
step answers why it refuses, or None.

  worktree_for(checkout, branch)    (path, None): the one linked worktree of `checkout`'s
                                    repository on refs/heads/<branch> (never the main checkout),
                                    or (None, why)
  apply(root, branch, did, doc, key, now, checks=())
                                    (Applied, None) once the approved change is committed on
                                    `branch` in the worktree `root` and its files are written;
                                    else (None, why) with the branch, files and index as they were
  undo(applied)                     the failures of putting an Applied back (the branch moved back
                                    by compare-and-swap, the files restored), as text
  hub_client(given) / fetch(client, doc_id) / state_error(doc_id, doc)
                                    the hub's copy of an approval and why it cannot be applied
                                    now (kitlib.worktree.new reads a base through them)
  prewrite(root, doc_id, doc, paths, head)
                                    (plan, None): what to write on HEAD, {path: (blob, bytes)}
                                    (None, None for a delete); or (None, why) when the change
                                    cannot be written: the paths are not exactly the approval's,
                                    a content is not its signed blob, a file is unchanged, the
                                    approved change does not apply on HEAD (the delta rule), HEAD
                                    holds it already, or a file in the work tree is not HEAD's copy
                                    (or a path not a plain file of the work tree)
  write(root, doc, plan=None) / restore(state)
                                    the plan (default: the approved content) into the work tree,
                                    and back

What is written on HEAD (the delta rule, kitlib.ownerfiles.approvals.delta_targets): for each
approved path, the approved content where HEAD holds the base's copy, and the clean merge of the
approved change onto HEAD's copy (`git merge-file`) where HEAD changed the path since the base. A
merge that conflicts, a path HEAD holds as something other than a regular file, or a file the
approval creates that HEAD holds with other content refuses.

apply, in order. Read-only checks first: the approval is signed under `key`, approved, never
consumed and not expired at `now`, for the worktree's repository (approvals.check), and `branch`
is the branch it names (the signature covers that name); HEAD is the branch, with no merge,
cherry-pick, revert or rebase in progress; every approved path is one an owner approval may
carry (kitlib.ownerfiles.request owner_paths_error, the request's own check, under the rules
below read at HEAD: a request posted past it is refused here); prewrite passes on HEAD; the
worktree's own filters (.gitattributes, core.autocrlf: `hash-object --path`) keep each approved
file as its approved blob; each approved path's index entry is HEAD's (_at_head) and nothing at
all is staged against HEAD (_staged_error); each probe in
`checks` ((root, paths) -> why or None) passes. Then the commit is made on a private index
(GIT_INDEX_FILE), never the worktree's: the approved bytes hashed as they are (--no-filters),
HEAD's tree with the approved entries, one parent, the message the approval's title, its reason
and `Owner-Approval: hub:A-<n>`, author and committer AUTHOR (never the owner, so the owner-file
rules judge it by its approval), no hook. kitlib.ownerfiles.rules.violations must find nothing on
it, under the rules the land check reads (_owner_rules: the config the commit holds merged with
main's, never the work tree's `.kit.json`; refused, as land refuses, when the main checkout is
not on its main branch), with the approval judged in process (the delta rule
and approvals.check, unconsumed). The branch is moved by
compare-and-swap (`update-ref <new> <old>`: the commit point), then the files are written and the
approved paths' index entries set to the commit's (git's index.lock, a bounded retry). A failure
after the branch moved puts back what was done: the branch moved back by compare-and-swap, the
files restored, the index entries HEAD's again; a put-back that fails is named in the why. The
caller consumes the approval (approvals.consume_fields) and, when that write fails, calls undo.
"""
import collections
import os
import shutil
import tempfile
import time

from kitlib.git import cmd
from kitlib.git import repo as gitrepo
from kitlib.ownerfiles import rules as oc

TRAILER = oc.TRAILER
VALUE = 'hub:A-<n>'
AUTHOR = ('PM kit hub', 'hub@pm-kit.invalid')   # the author and committer of every applied commit
OPERATIONS = ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply')
INDEX_TRIES = 10            # attempts at the worktree's index while another git holds index.lock
INDEX_WAIT_S = 0.2
NULL = '0' * 40

# What apply did: the commit, the branch tip it replaced, the branch and the worktree, and the
# files it wrote (write's state, for undo).
Applied = collections.namedtuple('Applied', 'commit old branch worktree written entries')


def doc_id(value):
    """The approval id of a `hub:A-<n>` value, exactly as the hub issues it (A-001), or
    None (oc.APPROVAL_RE)."""
    m = oc.APPROVAL_RE.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def trailer(value):
    return '%s: %s' % (TRAILER, value)


def _err(e):
    text = str(e)
    return '%s: %s' % (type(e).__name__, text) if text else type(e).__name__


def hub_client(given):
    """(client, None), or (None, why): `given` when not None, else a client of the machine's hub
    (kitlib.hubclient.settings)."""
    if given is not None:
        return given, None
    try:
        from kitlib.hubclient import client as hubclient, settings
        return hubclient.Client(settings.load().port), None
    except Exception as e:          # noqa: BLE001 - no client is no approval
        return None, 'the hub could not be asked (%s)' % _err(e)


def fetch(client, did):
    """(the document of the approval `did`, None) from the hub's snapshot, or (None, why)."""
    try:
        got = client.get('/snapshot', query={'collections': 'approvals'})
    except Exception as e:          # noqa: BLE001 - no answer is no approval
        return None, 'the hub could not be asked for hub:%s (%s)' % (did, _err(e))
    colls = got.get('collections') if isinstance(got, dict) else None
    docs = colls.get('approvals') if isinstance(colls, dict) else None
    if not isinstance(docs, dict):
        return None, 'the hub answered without its approvals'
    doc = docs.get(did)
    if doc is None:
        return None, 'the hub has no approval %s' % did
    files = doc.get('files') if isinstance(doc, dict) else None
    if not (isinstance(files, list) and files and all(
            isinstance(f, dict) and isinstance(f.get('path'), str) for f in files)
            and isinstance(doc.get('head'), str)):
        return None, 'hub:%s is malformed' % did
    return doc, None


def state_error(did, doc):
    if doc.get('commit'):
        return ('hub:%s was consumed already by %s: one approval is one commit; ask the owner '
                'again' % (did, str(doc['commit'])[:12]))
    status = doc.get('status')
    if status != 'approved':
        return 'hub:%s is %s: only an approved approval is committed' % (did, status or
                                                                        'malformed')
    return None


def approved_paths(doc):
    return sorted(f['path'] for f in doc['files'])


def _hash_object(root, path):
    r = cmd.call(['hash-object', '--', path], root)
    return cmd.text(r.out).strip() if r.rc == 0 else None


def _path_error(root, rel):
    """Why `rel` is not a plain file path of the work tree `root` to write, or None: no `..`, no
    absolute path, nothing in a `.git` folder, and no symbolic link or junction on the way."""
    parts = rel.split('/')
    if (not rel or rel.startswith('/') or '\\' in rel or ':' in rel
            or any(p in ('', '.', '..') or p.lower() == '.git' for p in parts)):
        return '%s is not a plain path in the work tree' % rel
    top = os.path.realpath(root)
    full = os.path.join(root, *parts)
    if os.path.islink(full):
        return '%s is a symbolic link: an approval writes plain files only' % rel
    parent = os.path.dirname(full)
    while not os.path.lexists(parent):
        parent = os.path.dirname(parent)
    real = os.path.realpath(parent)
    if os.path.normcase(real) != os.path.normcase(top) and not os.path.normcase(real).startswith(
            os.path.normcase(top) + os.sep):
        return '%s is reached through a link out of the work tree' % rel
    return None


def prewrite_error(root, did, doc, paths, head):
    """Why the approved change cannot be written into `root` for a commit of `paths` on `head`,
    or None (prewrite)."""
    return prewrite(root, did, doc, paths, head)[1]


def prewrite(root, did, doc, paths, head):
    """(plan, None): what to write into `root` for a commit of `paths` on `head`, {path: (blob,
    bytes)}, (None, None) for a path the change deletes; or (None, why) when the approved change
    cannot be written there (module docstring)."""
    from kitlib.ownerfiles import approvals as ap
    want = approved_paths(doc)
    if sorted(paths) != want:
        return None, ('the paths named are not the approval\'s: named %s; hub:%s holds %s. Name '
                      'exactly its files.' % (', '.join(sorted(paths)), did, ', '.join(want)))
    for p in want:
        why = _path_error(root, p)
        if why:
            return None, why
    unchanged = []
    for f in doc['files']:
        content = f.get('content')
        if content is not None and not isinstance(content, str):
            return None, 'the content of %s is not text' % f['path']
        if (None if content is None else ap.blob_id(content)) != f.get('blob'):
            return None, 'the content of %s is not the approved blob' % f['path']
        if f.get('blob') == f.get('base_blob'):
            unchanged.append(f['path'])
    if unchanged:
        return None, 'the approval changes nothing in: %s' % ', '.join(unchanged)
    targets, why = (None, 'HEAD is unborn: an approved commit has one parent') if head is None \
        else ap.delta_targets(root, doc, head)
    if targets is None:
        return None, ('the approval is for base %s, and its change does not apply on HEAD %s: %s. '
                      'Ask the owner again from this HEAD.'
                      % (doc['head'][:12], (head or 'unborn')[:12], why))
    plan = {}
    for p in want:
        par, blob, data = targets[p]
        if blob == (None if par is None else par[2]):
            return None, ('HEAD %s holds the approved change of %s already: there is nothing to '
                          'commit' % (head[:12], p))
        full = os.path.join(root, *p.split('/'))
        if par is None:
            drift = os.path.lexists(full) and 'a file is there where HEAD has none'
        else:
            drift = ((not os.path.isfile(full) or _hash_object(root, p) != par[2])
                     and 'it was changed')
        if drift:
            return None, ('%s in the work tree is not HEAD\'s copy, the one the approved change '
                          'is made on: %s. Put it back, or ask the owner again.' % (p, drift))
        plan[p] = (blob, data)
    return plan, None


def write(root, doc, plan=None):
    """((saved, made), None) once every approved file is written (the plan's bytes, else the
    approved content's UTF-8 bytes, no line-ending change) or deleted; else (None, why) with the
    work tree restored. `saved`: [(full path, its bytes before or None)]; `made`: the folders
    this call created."""
    saved, made = [], []
    try:
        for f in doc['files']:
            full = os.path.join(root, *f['path'].split('/'))
            old = None
            if os.path.lexists(full):
                with open(full, 'rb') as fh:
                    old = fh.read()
            saved.append((full, old))
            content = f.get('content')
            data = plan[f['path']][1] if plan else (None if content is None
                                                    else content.encode('utf-8'))
            if data is None:
                os.remove(full)
                continue
            missing, parent = [], os.path.dirname(full)
            while not os.path.isdir(parent):
                missing.append(parent)
                parent = os.path.dirname(parent)
            for folder in reversed(missing):
                os.mkdir(folder)
                made.append(folder)
            with open(full, 'wb') as fh:
                fh.write(data)
    except OSError as e:
        failed = restore((saved, made))
        return None, 'the approved content could not be written (%s)%s' % (
            _err(e), '; and could not be put back: %s' % '; '.join(failed) if failed else '')
    return (saved, made), None


def restore(state):
    """Put back every file `write` saved and remove the folders it made; the failures, as text."""
    saved, made = state
    failed = []
    for full, old in reversed(saved):
        try:
            if old is None:
                if os.path.lexists(full):
                    os.remove(full)
            else:
                with open(full, 'wb') as fh:
                    fh.write(old)
        except OSError as e:
            failed.append('%s (%s)' % (full, _err(e)))
    for folder in reversed(made):
        try:
            os.rmdir(folder)
        except OSError:
            pass                    # not empty: something else is in it now
    return failed


def worktree_for(checkout, branch):
    """(path, None): the one linked worktree of the repository `checkout` is in whose HEAD is
    refs/heads/<branch>; or (None, why): no branch, git cannot list the worktrees, the branch is
    the main checkout's (an approved change is never committed there), or no worktree has it."""
    if not isinstance(branch, str) or not branch:
        return None, 'the request names no branch to apply it on'
    found, why = gitrepo.worktrees(checkout)
    if found is None:
        return None, 'the worktrees of %s cannot be listed: %s' % (checkout, why)
    if found and found[0].branch == branch:
        return None, ("%s is the main checkout's branch: an approved change is applied in a "
                      "linked worktree only" % branch)
    on = [w.path for w in found[1:] if w.branch == branch and not w.bare]
    if len(on) != 1:
        return None, 'no linked worktree has %s checked out' % branch
    return on[0], None


def _git(root, args, env=None, stdin=None):
    """(stdout text, None) of `git <args>` in `root`, or (None, why)."""
    r = cmd.call(oc.NOREPLACE + list(args), root, stdin=stdin, env=env)
    if r.rc != 0:
        return None, 'git %s failed: %s' % (args[0], r.err or 'exit %s' % r.rc)
    return cmd.text(r.out), None


def _head_error(root, branch):
    """Why HEAD of `root` is not `branch` ready to commit on (or an operation is in progress
    there), or None."""
    out, _why = _git(root, ['symbolic-ref', '-q', 'HEAD'])
    if out is None or out.strip() != 'refs/heads/' + branch:
        return 'the worktree %s is not on %s' % (root, branch)
    for name in OPERATIONS:
        path, why = gitrepo.git_path(root, name)
        if path is None:
            return 'git cannot say whether an operation is in progress: %s' % why
        if os.path.lexists(path):
            return 'the worktree %s has %s: an operation is in progress there' % (root, name)
    return None


def _at_head(root, old, paths):
    """({path: (mode, oid) or None}, None) of `paths` in `old`, or (None, why) when git cannot say
    or a path is staged away from it (nothing may be staged on an approved path)."""
    got, staged = {}, []
    for p in paths:
        known, entry = oc.tree_entry(root, old, p)
        known_ix, entry_ix = oc.index_entry(root, p)
        if not (known and known_ix):
            return None, 'git cannot read the entry of %s in %s' % (p, root)
        got[p] = entry
        if entry_ix != entry:
            staged.append(p)
    if staged:
        return None, ('%s is staged in %s: an applied commit records the approved content only. '
                      'Unstage it there first' % (', '.join(staged), root))
    return got, None


def _staged_error(root, old):
    """Why the index of `root` holds something staged against `old`, or None: the hub applies on
    a clean index only, so nothing of anyone else's is left staged on its commit. It runs after
    _at_head, which refuses an approved path staged away from `old`."""
    out, why = _git(root, ['diff', '--cached', '--name-only', '--no-renames', '-z', old, '--'])
    if out is None:
        return why
    staged = sorted(n for n in out.split('\0') if n)
    if staged:
        return ('something else is staged in %s: %s. The hub applies an approved change on a '
                'clean index only: commit or unstage it there first' % (root, ', '.join(staged)))
    return None


def _filters_error(root, plan):
    """Why the filters of the worktree `root` (.gitattributes text and eol rules, core.autocrlf, a
    clean filter) would store an approved file as another blob than the approved one, or None.
    The commit holds the exact approved bytes; a work tree that cleans them to another blob would
    show the applied file changed, and its next commit would store other bytes."""
    for p, (blob, data) in sorted(plan.items()):
        if data is None:
            continue
        out, why = _git(root, ['hash-object', '--path', p, '--stdin'], stdin=data)
        if out is None:
            return why
        if out.strip() != blob:
            return ('%s would be stored as %s by the filters of %s (.gitattributes, '
                    'core.autocrlf), not as the approved blob %s: the hub commits the approved '
                    'bytes exactly. Ask again with content those filters keep as it is'
                    % (p, out.strip()[:12], root, blob[:12]))
    return None


def _paths_error(root, old, paths):
    """Why `paths` are not all ones an owner approval may carry, or None: the request's own
    check (kitlib.ownerfiles.request owner_paths_error: a render source of a kit checkout, a path
    the owner rules do not lock), under the owner rules _owner_rules reads at `old`. The hub's
    POST of a request does not run it, so a request posted past it is refused here."""
    from kitlib.ownerfiles import request
    rules, why = _owner_rules(root, old)
    if rules is None:
        return why
    return request.owner_paths_error(root, paths, rules=rules)


def _owner_rules(root, new):
    """(rules, None): the owner rules the land check reads (kitlib.gate.land owner_refusal): the
    config the commit `new` holds (the defaults when it does not load) merged with main's
    (kitlib.config.owner.merge), main being <remote>/<main> as last fetched, else the main
    checkout's HEAD; never the work tree's `.kit.json`, so an uncommitted edit there cannot loosen
    them. (None, why) when main's config cannot be read, or when the main checkout is not on the
    main branch its HEAD's config names: as land refuses then (kitlib.gate.land config_refusal),
    since another branch's `.kit.json` would choose the rules."""
    from kitlib import config as kconfig
    found, why = gitrepo.worktrees(root)
    if not found:
        return None, 'the main checkout of %s cannot be found: %s' % (root, why or 'none listed')
    main_root, on = found[0].path, found[0].branch
    head, why = _git(main_root, ['rev-parse', '--verify', 'HEAD^{commit}'])
    if head is None:
        return None, why
    try:
        cfg = kconfig.load_at(main_root, head.strip())
        if on != cfg.main_branch:
            return None, ('the main checkout %s is on %s, not %s: the owner rules are read from '
                          'main, as gate land reads them' % (main_root, on or '(detached HEAD)',
                                                             cfg.main_branch))
        ref = 'refs/remotes/%s/%s^{commit}' % (cfg.remote, cfg.main_branch)
        tracking, _why = _git(main_root, ['rev-parse', '--verify', '-q', ref])
        if tracking:
            cfg = kconfig.load_at(main_root, tracking.strip())
    except kconfig.ConfigError as e:
        return None, 'the kit configuration of main is refused: %s' % e
    try:
        work = kconfig.load_at(root, new).owner_rules()
    except kconfig.ConfigError:
        work = kconfig.OwnerRules()
    return kconfig.merge_owner_rules(work, cfg.owner_rules()), None


def _info(entries, paths):
    """The `update-index --index-info` lines that set `paths` to `entries` ({path: (mode, oid) or
    None}; None removes the path)."""
    return ''.join('%s %s\t%s\n' % ((entries.get(p) or ('0', NULL)) + (p,))
                   for p in paths).encode('utf-8')


def _target(at_head, plan):
    """{path: (mode, oid) or None}: the approved paths' entries in the applied commit (HEAD's
    mode kept)."""
    return {p: ((at_head.get(p) or ('100644',))[0], blob) if blob is not None else None
            for p, (blob, _data) in plan.items()}


def _set_index(root, entries):
    """None once the worktree's index holds `entries` ({path: (mode, oid) or None}), else why;
    while another git holds index.lock it is tried INDEX_TRIES times."""
    data, why = _info(entries, sorted(entries)), None
    for _ in range(INDEX_TRIES):
        r = cmd.call(['update-index', '--index-info'], root, stdin=data)
        if r.rc == 0:
            cmd.call(['update-index', '-q', '--refresh'], root)    # stat data only: best effort
            return None
        why = 'git update-index failed: %s' % (r.err or 'exit %s' % r.rc)
        if 'index.lock' not in (r.err or ''):
            break
        time.sleep(INDEX_WAIT_S)
    return why


def _message(did, doc):
    title = ' '.join(str(doc.get('title') or '').split()) or 'Apply hub:%s' % did
    reason = str(doc.get('reason') or '').strip()
    return '%s\n\n%s%s\n' % (title, reason + '\n\n' if reason else '', trailer('hub:' + did))


class _Approved(object):
    """The approvals seam of rules.violations for the one approval being applied: the delta rule
    (approvals.delta_error) on the document in hand, then approvals.check under the key in
    process, unconsumed."""

    def __init__(self, key, did, doc, repo, now):
        self.key, self.did, self.doc, self.repo, self.now = key, did, doc, repo, now

    def check(self, root, doc_id, head, blobs, commit):
        from kitlib.ownerfiles import approvals as ap
        if doc_id != self.did:
            return False, 'the commit carries hub:%s, not hub:%s' % (doc_id, self.did)
        approved, why = ap.delta_error(root, self.doc, head, commit, blobs)
        if approved is None:
            return False, why
        return ap.check(self.key, doc_id, self.doc, self.repo, self.doc['head'], approved,
                        self.now)


def _checks(root, branch, did, doc, key, now, checks):
    """((old, plan, at_head, repo), None) once the read-only checks pass (module docstring), else
    (None, why)."""
    from kitlib.ownerfiles import approvals as ap
    if ap.approved_files(doc) is None or doc.get('kind') is not None:
        return None, 'hub:%s is not a well-formed file approval' % did
    why = state_error(did, doc)
    if why:
        return None, why
    repo, why = ap.remote_id(root)
    if repo is None:
        return None, 'the repository of %s has no remote to name it: %s' % (root, why)
    ok, why = ap.check(key, did, doc, repo, doc['head'],
                       {f['path']: f.get('blob') for f in doc['files']}, now)
    if not ok:
        return None, 'hub:%s cannot be applied: %s' % (did, why)
    if doc.get('branch') != branch:
        return None, ('hub:%s was approved for branch %s, not %s: it is applied only on the '
                      'branch its signature covers' % (did, doc.get('branch'), branch))
    why = _head_error(root, branch)
    if why:
        return None, why
    out, why = _git(root, ['rev-parse', '--verify', 'HEAD^{commit}'])
    if out is None:
        return None, why
    old, paths = out.strip(), approved_paths(doc)
    why = _paths_error(root, old, paths)
    if why:
        return None, why
    plan, why = prewrite(root, did, doc, paths, old)
    if plan is None:
        return None, why
    why = _filters_error(root, plan)
    if why:
        return None, why
    at_head, why = _at_head(root, old, paths)
    if at_head is None:
        return None, why
    why = _staged_error(root, old)
    if why:
        return None, why
    for probe in checks:
        why = probe(root, paths)
        if why:
            return None, why
    return (old, plan, at_head, repo), None


def _build(root, did, doc, old, plan, at_head):
    """(the commit of the approved change on `old`, None), made on a private index, or (None,
    why)."""
    tmp = tempfile.mkdtemp(prefix='kit-apply-')
    try:
        env = dict(cmd.scrubbed_env(), GIT_INDEX_FILE=os.path.join(tmp, 'index'),
                   GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                   GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])
        for p, (blob, data) in sorted(plan.items()):
            if data is None:
                continue
            out, why = _git(root, ['hash-object', '-w', '--no-filters', '--stdin'], env, data)
            if out is None:
                return None, why
            if out.strip() != blob:
                return None, '%s hashed as %s, not %s' % (p, out.strip()[:12], blob[:12])
        for args, stdin in ((['read-tree', old], None),
                            (['update-index', '--index-info'],
                             _info(_target(at_head, plan), sorted(plan)))):
            out, why = _git(root, args, env, stdin)
            if out is None:
                return None, why
        tree, why = _git(root, ['write-tree'], env)
        if tree is None:
            return None, why
        sha, why = _git(root, ['commit-tree', tree.strip(), '-p', old], env,
                        _message(did, doc).encode('utf-8'))
        return (sha.strip(), None) if sha is not None else (None, why)
    finally:
        shutil.rmtree(tmp, True)


def apply(root, branch, did, doc, key, now, checks=()):
    """(Applied, None) once the approved change of `doc` (the approval `did`, the hub's stored
    document) is committed on `branch` in the worktree `root` and written there; else (None, why),
    with what was done put back (module docstring). `key`: the approval key; `now`: an aware
    datetime; `checks`: probes (root, paths) -> why or None, run before anything is written."""
    got, why = _checks(root, branch, did, doc, key, now, checks)
    if got is None:
        return None, why
    old, plan, at_head, repo = got
    new, why = _build(root, did, doc, old, plan, at_head)
    if new is None:
        return None, 'the commit could not be made: %s' % why
    rules, why = _owner_rules(root, new)
    if rules is None:
        return None, why
    bad, why = oc.violations(root, rules, new, (old,),
                             approvals=_Approved(key, did, doc, repo, now))
    if bad is None or bad:
        return None, 'the commit would not pass the owner-file rules: %s' % (
            why or oc.describe(rules, bad))
    out, why = _git(root, ['update-ref', '-m', 'hub: apply hub:%s' % did,
                           'refs/heads/' + branch, new, old])
    if out is None:
        return None, '%s could not be moved from %s: %s' % (branch, old[:12], why)
    done = Applied(new, old, branch, root, None, at_head)
    written, why = write(root, doc, plan)
    if written is None:
        return None, why + _undone(undo(done))
    done = done._replace(written=written)
    why = _set_index(root, _target(at_head, plan))
    if why:
        return None, 'the index of %s could not be set: %s%s' % (root, why, _undone(undo(done)))
    return done, None


def _undone(failed):
    if failed:
        return '; and it could not all be put back: %s' % '; '.join(failed)
    return '; the branch, files and index are as they were'


def undo(applied):
    """Put back what `applied` did: the branch moved back from the commit by compare-and-swap,
    the files restored, the approved paths' index entries HEAD's again. The failures, as text
    ([] when all is back)."""
    failed = []
    out, why = _git(applied.worktree, ['update-ref', '-m', 'hub: put back hub apply',
                                       'refs/heads/' + applied.branch, applied.old,
                                       applied.commit])
    if out is None:
        failed.append('%s is still at %s: %s' % (applied.branch, applied.commit[:12], why))
    if applied.written is not None:
        failed.extend(restore(applied.written))
        why = _set_index(applied.worktree, applied.entries)
        if why:
            failed.append(why)
    return failed
