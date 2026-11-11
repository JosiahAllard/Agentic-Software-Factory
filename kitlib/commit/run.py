"""One commit call (kitlib.commit, whose docstring holds the rules): commit(Request) ->
Outcome(code, lines), in order the checks (kitlib.commit.checks), the render, the stage, the
commit from a private index, and the put-back on a refusal; the message is kitlib.commit.message.
tools/commit.py calls this module."""
import collections
import hashlib
import os
import shutil
import subprocess
import sys
import uuid

from kitlib import config as kconfig
from kitlib import attribution, verdict
from kitlib.docs import callers as doc_callers
from kitlib.config import user as kuser
from kitlib.content import oncommit
from kitlib.git import cmd, repo
from kitlib.proc import spawn

from kitlib.commit.checks import (
    OK, REFUSED, USAGE, _Stop, author_email, deletion_merge_paths, foreign_refusal, landed_main,
    locked_paths, machine_path_error, main_checkout_error, merge_heads, model_requirement_error,
    owner_identity_error, owner_only_refusal, repo_path, shrunk_paths, staged_deletion_merges,
    staged_machine_path_error, staged_status, unapproved_locked, unstage_added)
from kitlib.commit.message import (
    build_message, build_trailers, canonical_approved, message_body, session_lines)
from kitlib.ownerfiles import rules as ownerrules


KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


DOC_BUDGET_TIMEOUT = 600     # seconds


HUB_APPLIES = ('the hub applies a change the owner approved, on the branch the request names, in '
               'its worktree: an agent never commits it. Ask with hub.py approvals add; one the '
               'hub could not apply, the owner applies with hub.py approvals apply A-<n> '
               '(docs/hub-approvals.md "Applying")')


# root: the work tree's top level; cwd: the caller's directory (a relative --body-file); env: the
# environment the host's model variable is read from (None: os.environ); provider: the host name
# (None: the hosts.json default); allow_empty: --allow-empty, a commit of no paths (module
# docstring).
Request = collections.namedtuple(
    'Request', 'root subject b body_file model owner_authored owner_approved '
               'lander_main_checkout paths cwd env provider allow_empty',
    defaults=(None, False))


Outcome = collections.namedtuple('Outcome', 'code lines')


def request(root, subject, paths, b='', body_file=None, model=None, owner_authored=False,
            owner_approved=(), lander_main_checkout=False, cwd=None, env=None, provider=None,
            allow_empty=False):
    return Request(root, subject, b, body_file, model, owner_authored, list(owner_approved),
                   lander_main_checkout, list(paths), cwd or os.getcwd(), env, provider,
                   allow_empty)


def doc_budget_check(root, kit_root=None):
    """`(verdict line, ok)` from the kit's tools/doc_budget.py run with `-C root`, or a NOT
    CHECKED line when the kit has none. Judged conjunctively (kitlib.verdict): exit 0 AND the OK
    line AND no FAILED line. `kit_root` defaults to KIT_ROOT, read at call time."""
    tool = os.path.join(kit_root or KIT_ROOT, 'tools', 'doc_budget.py')
    if not os.path.isfile(tool):
        return 'docs      NOT CHECKED - no tools/doc_budget.py in the kit, so nothing was measured', True
    try:
        r = spawn.run([sys.executable, tool, '-C', root], capture_output=True, text=True,
                      encoding='utf-8', errors='replace', timeout=DOC_BUDGET_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'docs      FAILED (doc_budget.py did not finish in %d s)' % DOC_BUDGET_TIMEOUT, False
    except OSError as e:
        return 'docs      FAILED (doc_budget.py could not start: %s)' % e, False
    out = (r.stdout or '') + (r.stderr or '')
    return verdict.line('docs', r.returncode, out, keep=4), verdict.judge(r.returncode, out)[0]


def doc_index_check(root, kit_root=None):
    """`(verdict line, ok)` from the kit's tools/doc_index.py run with `-C root` (the index
    checks doc_budget no longer makes), `(None, True)` when `.kit.json` "doc_index".enabled is
    false, or a NOT CHECKED line when the kit has no tool. Judged as doc_budget_check judges."""
    try:
        if doc_callers.switched_off(kconfig.load(root)):
            return None, True
    except kconfig.ConfigError:
        pass                                  # the tool reports a bad config itself
    tool = doc_callers.tool(kit_root or KIT_ROOT)
    if not os.path.isfile(tool):
        return doc_callers.not_checked(), True
    try:
        r = spawn.run([sys.executable, tool, '-C', root], capture_output=True, text=True,
                      encoding='utf-8', errors='replace', timeout=doc_callers.TIMEOUT)
    except subprocess.TimeoutExpired:
        return 'doc index FAILED (doc_index.py did not finish in %d s)' % doc_callers.TIMEOUT, False
    except OSError as e:
        return 'doc index FAILED (doc_index.py could not start: %s)' % e, False
    line, failed = doc_callers.line(r.returncode, (r.stdout or '') + (r.stderr or ''))
    return line, not failed


def left_staged_line(others):
    """The warning for files another writer staged during this call, which it left staged."""
    return ('WARNING: staged by another writer during this call, left staged and not committed: '
            '%s' % ', '.join(sorted(others)))


def head_commit(root):
    """HEAD's commit id, or '' on an unborn branch (HEAD names a branch that does not exist yet).
    Any other failure to read HEAD raises _Stop: an empty tree is never guessed."""
    rc, out, err = ownerrules.run_git(root, 'rev-parse', '-q', '--verify', 'HEAD^{commit}')
    if rc == 0 and out.strip():
        return out.strip()
    src, ref, _e = ownerrules.run_git(root, 'symbolic-ref', '-q', 'HEAD')
    if src == 0 and ref.strip():
        rrc, rout, _e = ownerrules.run_git(root, 'rev-parse', '-q', '--verify', ref.strip())
        if rrc == 1 and not rout.strip():
            return ''
    raise _Stop(REFUSED, 'refused: git could not read HEAD (rev-parse HEAD: %s). Nothing was '
                'committed.' % (err.strip() or 'exit %s' % rc))


def index_snapshot(root, keep):
    """`{path: (mode, oid), or None for a deletion}`: the real index's entry for each path of
    `keep` ({path: status letter}), read in one `git ls-files --stage`. Raises _Stop when git cannot
    list the index or a kept path is not one stage-0 entry (unmerged, or changed meanwhile)."""
    rc, out, err = ownerrules.run_git(root, 'ls-files', '--stage', '-z')
    if rc != 0:
        raise _Stop(REFUSED, 'git ls-files --stage failed: %s. Nothing was committed.'
                    % (err or 'exit %s' % rc))
    entries = {}
    for row in out.split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name in keep:
            entries.setdefault(name, []).append(meta.split())
    snap = {}
    for path in sorted(keep):
        metas = entries.get(path, [])
        if keep[path] == 'D' and not metas:
            snap[path] = None
        elif len(metas) == 1 and len(metas[0]) == 3 and metas[0][2] == '0':
            snap[path] = (metas[0][0], metas[0][1])
        else:
            raise _Stop(REFUSED, 'refused: %s is not one staged entry in the index (unmerged, or '
                        'changed during this call). Nothing was committed; run this again.' % path)
    return snap


def private_index_file(tmp):
    """The private index's path in the folder `tmp`. Its name starts `next-index-`, the name
    git gives the index of a partial commit, so the kit's pre-commit hook
    (kitlib.githooks.render.partial) treats this commit as one."""
    return os.path.join(tmp, 'next-index-%d' % os.getpid())


def private_index(root, tmp, head, snap):
    """`(env, None)` for a `git commit` that records exactly `snap` (index_snapshot): a private
    index (private_index_file) holding `head`'s tree (empty for '', an unborn branch) with each
    snapshot entry set, a deletion (None) removed; or `(None, why)`. Every other entry of the real
    index, staged by anyone, is left out of the commit and left as it is.

    The private index starts as a copy of the real index (_copied_index), with every other path
    that differs from `head` (staged, unmerged, or absent) put back to `head`'s entry, so the
    entries `git commit` refreshes keep their stat data. A read-tree index has none, and `git
    commit` then hashes every tracked work-tree file, past cmd.GIT_TIMEOUT in a large tree.
    When the copy cannot be used, and on an unborn branch, the
    private index is read-tree'd (`read-tree -m` would refuse an unmerged real index)."""
    env = dict(cmd.scrubbed_env(), GIT_INDEX_FILE=private_index_file(tmp))
    zero = '0' * (len(head) or 40)
    back = _copied_index(root, env, head, snap) if head else None
    if back is None:
        r = cmd.call(['read-tree', head] if head else ['read-tree', '--empty'], root, env=env)
        if r.rc != 0:
            return None, 'git read-tree into a private index failed: %s' % (
                r.err or 'exit %s' % r.rc)
        back = []
    lines = back + ['0 %s\t%s' % (zero, p) if e is None else '%s %s\t%s' % (e[0], e[1], p)
                    for p, e in sorted(snap.items())]
    if lines:
        r = cmd.call(['update-index', '--index-info'], root, env=env,
                     stdin=('\n'.join(lines) + '\n').encode('utf-8'))
        if r.rc != 0:
            return None, 'git update-index on the private index failed: %s' % (
                r.err or 'exit %s' % r.rc)
    return env, None


EMPTY_BLOB_SHA1 = hashlib.sha1(b'blob 0\0').hexdigest()


EMPTY_BLOB_SHA256 = hashlib.sha256(b'blob 0\0').hexdigest()


def _copied_index(root, env, head, snap):
    """The real index copied to env's GIT_INDEX_FILE, and the --index-info lines that put every
    path outside `snap` back to `head`'s entry there: removed first (every stage), then `head`'s
    mode and blob. None, with the copy removed, when the copy cannot be used: no real index, a
    copy whose trailing checksum does not match (torn), git cannot read it or `head`, or a path
    this cannot carry exactly (not UTF-8, so it decodes with U+FFFD; a newline; a leading quote).
    An entry holding the empty blob is always put back: `ls-files --stage` does not show an
    intent-to-add entry's flag, and one would match a file empty at `head`, then be left out of
    the commit's tree. The copy keeps the real index's mtime (copy2), which git's racy-clean
    check compares entries against."""
    dest = env['GIT_INDEX_FILE']
    real, _why = repo.git_path(root, 'index')
    try:
        if not real:
            return None
        shutil.copy2(real, dest)
        if _torn(dest, len(head)):
            raise OSError('torn copy')
    except OSError:
        _remove(dest)
        return None
    r = cmd.call(['ls-files', '--stage', '-z'], root, env=env)
    t = cmd.call(['ls-tree', '-r', '-z', '--full-tree', head], root, env=env) if r.rc == 0 else r
    if r.rc != 0 or t.rc != 0:
        _remove(dest)
        return None
    ours, theirs = {}, {}
    for out, into in ((r.out, ours), (t.out, theirs)):
        for row in cmd.text(out).split('\0'):
            meta, tab, path = row.partition('\t')
            f = meta.split()
            if not (tab and len(f) == 3):
                continue
            if '�' in path or '\n' in path or path.startswith('"'):
                _remove(dest)
                return None
            if into is ours:
                ours.setdefault(path, set()).add((f[0], f[1], f[2]))
            else:
                theirs[path] = (f[0], f[2])
    empty = EMPTY_BLOB_SHA256 if len(head) == 64 else EMPTY_BLOB_SHA1
    drop, put = [], []
    for path in sorted((set(ours) | set(theirs)) - set(snap)):
        want = theirs.get(path)
        if (want is not None and want[1] != empty
                and ours.get(path) == {(want[0], want[1], '0')}):
            continue
        if path in ours:
            drop.append('0 %s\t%s' % ('0' * len(head), path))
        if want is not None:
            put.append('%s %s\t%s' % (want[0], want[1], path))
    return drop + put


def _torn(path, oid_len):
    """True when the index file at `path` does not end in the checksum of what precedes it
    (an all-zero trailer, index.skipHash, is not checked)."""
    algo, size = ('sha256', 32) if oid_len == 64 else ('sha1', 20)
    with open(path, 'rb') as fh:
        data = fh.read()
    if len(data) < 12 + size or data[:4] != b'DIRC':
        return True
    body, tail = data[:-size], data[-size:]
    return tail != bytes(size) and hashlib.new(algo, body).digest() != tail


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


MERGE_STATE = ('MERGE_HEAD', 'MERGE_MSG', 'MERGE_MODE')


def merge_state(root):
    """`{path: bytes}` of the merge-state files that exist now (MERGE_STATE), to put back when a
    commit is undone."""
    saved = {}
    for name in MERGE_STATE:
        path, _why = repo.git_path(root, name)
        try:
            with open(path, 'rb') as fh:
                saved[path] = fh.read()
        except (OSError, TypeError):
            pass
    return saved


def head_moved_line(head, now, made=False):
    """The refusal's first line; `made`: this call's commit was made on `now` and still exists."""
    return ('refused: HEAD moved during this call (another writer committed %s on top of %s); '
            '%s' % (now[:12] or 'nothing', head[:12] or 'an unborn branch',
                    'this call\'s commit was made on theirs.' if made
                    else 'this commit would have reverted their change.'))


def undo_commit(root, new, parent, saved, merged):
    """Point HEAD back at `parent` if it is still `new` (update-ref with the old value), and, when
    `new` is a merge commit (`merged`: it consumed the merge state), write back the merge-state
    files `saved` that it removed. The refusal lines either way."""
    rc, _out, err = ownerrules.run_git(root, 'update-ref', '-m',
                                       'commit.py: undo a commit made on a moved HEAD',
                                       'HEAD', parent, new)
    if rc != 0:
        return ['refused, and NOT undone: commit %s was made on the moved HEAD and lacks the other '
                'writer\'s change; git update-ref failed: %s. Undo it by hand: git update-ref HEAD '
                '%s %s' % (new[:12], err.strip() or 'exit %s' % rc, parent, new)]
    for path, data in (saved.items() if merged else ()):
        if not os.path.exists(path):
            try:
                with open(path, 'wb') as fh:
                    fh.write(data)
            except OSError as e:
                return ['Undone: HEAD is back at %s, but %s could not be written back (%s).'
                        % (parent[:12], path, e.strerror)]
    return ['Undone: HEAD is back at %s; nothing was committed, and the files this call named '
            'are still staged. Run this again.' % parent[:12]]


READ_BACK_FORMAT = '--format=%H %T %P%n%B'     # the read-back of HEAD after `git commit`


REFLOG_TAIL = 1 << 20      # bytes of logs/HEAD read for this call's entry: it is among the last


def message_lines(text):
    """`text` as git's whitespace cleanup leaves it, near enough to compare: each line's trailing
    whitespace dropped, and blank lines dropped."""
    return [l.rstrip() for l in text.splitlines() if l.strip()]


def reflog_tag():
    """This call's GIT_REFLOG_ACTION for `git commit`: git writes it, then ': <subject>', as the
    message of the reflog entry for the commit, so the entry is this call's alone."""
    return 'commit (kit %d %s)' % (os.getpid(), uuid.uuid4().hex)


def tagged_entry(root, tag):
    """`(old, new)` of the one entry in HEAD's reflog (logs/HEAD of the work tree at `root`) whose
    message starts with `tag`: the HEAD this call's `git commit` read and the commit it made; old is
    '' on an unborn branch. None when there is no reflog, no such entry, or more than one (the fake
    git writes no reflog)."""
    path, _why = repo.git_path(root, 'logs/HEAD')
    if not path:
        return None
    try:
        with open(path, 'rb') as fh:
            fh.seek(max(0, os.path.getsize(path) - REFLOG_TAIL))
            data = fh.read()
    except OSError:
        return None
    want, hits = (tag + ':').encode('utf-8'), []
    for line in data.split(b'\n'):
        meta, tab, message = line.partition(b'\t')
        fields = meta.split()
        if tab and message.startswith(want) and len(fields) >= 2:
            old, new = (f.decode('ascii', 'replace') for f in fields[:2])
            hits.append(('' if not old.strip('0') else old, new))
    return hits[0] if len(hits) == 1 else None


def commit_only(root, head, snap, msg, allow_empty=False):
    """`git commit` of exactly `snap` (index_snapshot) on `head` (head_commit) through
    private_index; `(rc, out, err, commit id, note lines, trees)`. `allow_empty` (--allow-empty):
    git commits `head`'s tree unchanged; this call's commit whose tree a hook changed is undone
    (undo_commit) and refused. `trees` is None when the commit
    holds exactly the tree built, else `(tree built, tree committed)`: a hook changed it, and
    align_index brings the real index's entries for the paths that differ into line. Every other
    staged entry stays staged.
    HEAD must not move under the call: checked just before `git commit` (refused), and read back
    after it. A HEAD read back whose first parent is `head` and that holds the tree and message
    built is this call's commit. When a hook may have changed it, the reflog entry tagged with this
    call's GIT_REFLOG_ACTION (tagged_entry) decides: with no entry, or one naming that HEAD, it is
    this call's (git commit moves HEAD only from the HEAD it read). Otherwise this call's commit
    and the HEAD it was made on come from that entry:
    - this call's commit at HEAD, made on a HEAD another writer moved: undone (undo_commit) and
      refused;
    - this call's commit made on `head`, another writer's on top of it: it stands (a NOTE line);
      refused, NOT undone, when HEAD no longer contains it;
    - this call's commit made on a moved HEAD, another writer's on top: refused, NOT undone, naming
      the commit it may revert.
    With no such entry (no reflog, the fake git), the commit at HEAD is this call's only when its
    tree is the private index's and its message is `msg`; then it is undone as above. Anything
    else is refused without undoing anything: a commit that is not this call's is never undone."""
    tag = reflog_tag()
    tmp = kuser.mkdtemp('kit-commit-')
    try:
        env, why = private_index(root, tmp, head, snap)
        if env is None:
            return 1, '', why, '', [], None
        r = cmd.call(['write-tree'], root, env=env)
        if r.rc != 0:
            return r.rc, '', 'git write-tree of the private index failed: %s' % (
                r.err or 'exit %s' % r.rc), '', [], None
        tree = r.out.decode('utf-8', 'replace').strip()
        now = head_commit(root)
        if now != head:
            raise _Stop(REFUSED, head_moved_line(head, now), 'Nothing was committed; the files '
                        'this call named are still staged. Run this again.')
        saved = merge_state(root)
        r = cmd.call(['commit', '-q'] + (['--allow-empty'] if allow_empty else []) + ['-m', msg],
                     root, env=dict(env, GIT_REFLOG_ACTION=tag))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gout = r.out.decode('utf-8', 'replace')
    if r.rc != 0:
        return r.rc, gout, r.err, '', [], None
    rc, out, err = ownerrules.run_git(root, 'log', '-1', READ_BACK_FORMAT, 'HEAD')
    first, _nl, body = out.partition('\n')
    ids = first.split()
    if rc != 0 or len(ids) < 2:
        raise _Stop(REFUSED, 'git commit ran, but HEAD could not be read back to check it was '
                    'made on %s: %s. Check `git log -3` by hand.'
                    % (head[:12] or 'the unborn branch', err.strip() or 'exit %s' % rc))
    now, now_tree, parents = ids[0], ids[1], ids[2:]
    base = parents[0] if parents else ''
    as_built = now_tree == tree and message_lines(body) == message_lines(msg)
    if base == head and as_built:
        return 0, gout, r.err, now, [], None
    entry = tagged_entry(root, tag)
    if base == head and (entry is None or entry[1] == now):
        if allow_empty and now_tree != tree:
            raise _Stop(REFUSED, 'refused: --allow-empty, but a hook changed the commit\'s tree '
                        '(it staged or removed a file), so it is not an empty commit.',
                        *undo_commit(root, now, base, saved, len(parents) > 1))
        return 0, gout, r.err, now, [], (tree, now_tree)
    if not parents:
        raise _Stop(REFUSED, 'refused: after git commit, HEAD is %s, a commit with no parent, '
                    'which this call cannot tell for its own commit on %s.'
                    % (now[:12], head[:12] or 'an unborn branch'),
                    'NOT undone: check `git log` and `git reflog` by hand.')
    if entry is None:
        if not as_built:
            raise _Stop(REFUSED, 'refused, and NOT undone: git commit ran, but HEAD is now %s, '
                        'which this call cannot tell for its own commit (another writer committed '
                        'after it, or a hook changed it). Check `git log -3` by hand: a commit of '
                        'this call made on another writer\'s commit lacks their change.' % now[:12])
        return _undo_moved(root, head, now, base, saved, len(parents) > 1)
    ours_base, ours = entry
    if ours == now:
        if ours_base != base:
            raise _Stop(REFUSED, 'refused, and NOT undone: HEAD %s has parent %s, but git logged '
                        'this call\'s commit on %s. Check `git log -3` by hand.'
                        % (now[:12], base[:12], ours_base[:12] or 'an unborn branch'))
        return _undo_moved(root, head, now, base, saved, len(parents) > 1)
    if ours_base == head:
        rc, _out, _err = ownerrules.run_git(root, 'merge-base', '--is-ancestor', ours, now)
        if rc == 0:
            trc, made, _err = ownerrules.run_git(root, 'rev-parse', '%s^{tree}' % ours)
            return 0, gout, r.err, ours, [
                'NOTE: another writer has since committed %s on top of this commit.' % now[:12]], (
                    tree, made.strip() if trc == 0 else '')
        raise _Stop(REFUSED, 'refused, and NOT undone: this call committed %s on %s, but HEAD is '
                    'now %s, which does not contain it. Check `git log -3` and `git reflog` by '
                    'hand.' % (ours[:12], head[:12] or 'an unborn branch', now[:12]))
    raise _Stop(REFUSED, head_moved_line(head, ours_base, made=True),
                'NOT undone: this call\'s commit %s was made on %s, so it may undo changes from %s; '
                'another writer has since committed %s on top of it. Check `git log -3` and '
                'restore their change by hand.'
                % (ours[:12], ours_base[:12] or 'an unborn branch',
                   ours_base[:12] or 'that writer', now[:12]))


def _undo_moved(root, head, new, parent, saved, merged):
    """Refuse this call's commit `new`, made on `parent` after HEAD moved from `head`, undoing it
    (undo_commit)."""
    raise _Stop(REFUSED, head_moved_line(head, parent),
                *undo_commit(root, new, parent, saved, merged))


def _tree_entry(mode, oid):
    return None if not mode.strip('0') else (mode, oid)


def align_index(root, trees, ours):
    """`(note lines, refusal lines)` after bringing the real index into line with this call's
    commit `ours` for each path a hook changed in it; `trees` is commit_only's `(tree built, tree
    committed)`. A hook's `git add` reaches only the private index, so without this the real index
    keeps the copy from before the hook and the next commit reverts the hook's change. A path whose
    real-index entry is still the built tree's is set to the committed entry. One holding another
    copy (staged before or during the call) is left as staged, and refused (the commit stands),
    naming the command that takes the commit's copy. The read and the write of the index are two git calls:
    a path staged between them is overwritten."""
    built, made = trees
    if made == built:
        return [], []
    rc, out, err = ownerrules.run_git(root, 'diff-tree', '-r', '-z', '--no-renames', built,
                                      made) if made else (
        None, '', 'its tree could not be read')
    if rc != 0:
        return [], ['refused: commit %s stands, but a hook may have changed it, and it could not be '
                    'compared with what this call built (%s). Check `git diff --cached` against it '
                    'by hand.' % (ours[:12], err.strip() or 'exit %s' % rc)]
    cols, changed = out.split('\0'), {}
    for i in range(0, len(cols) - 1, 2):
        meta = cols[i].lstrip(':').split()
        if len(meta) >= 4:
            changed[cols[i + 1]] = (_tree_entry(meta[0], meta[2]), _tree_entry(meta[1], meta[3]))
    rc, out, err = ownerrules.run_git(root, 'ls-files', '--stage', '-z')
    if rc != 0:
        return [], ['refused: commit %s stands, but a hook changed %s in it, and the index could '
                    'not be read to bring it into line (%s). Run: git restore --staged --source=%s '
                    '-- %s' % (ours[:12], ', '.join(sorted(changed)), err.strip() or 'exit %s' % rc,
                               ours[:12], ' '.join(sorted(changed)))]
    staged = {}
    for row in out.split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name in changed:
            staged.setdefault(name, []).append(tuple(meta.split()))
    fit, kept = [], []
    for path in sorted(changed):
        old, new = changed[path]
        rows = staged.get(path, [])
        same = (not rows and old is None) or (
            len(rows) == 1 and len(rows[0]) == 3 and rows[0][2] == '0' and rows[0][:2] == old)
        (fit if same else kept).append(path)
    if fit:
        zero = '0' * len(ours)
        info = ['0 %s\t%s' % (zero, p) if changed[p][1] is None
                else '%s %s\t%s' % (changed[p][1][0], changed[p][1][1], p) for p in fit]
        r = cmd.call(['update-index', '--index-info'], root,
                     stdin=('\n'.join(info) + '\n').encode('utf-8'))
        if r.rc != 0:
            kept, fit = sorted(kept + fit), []
    notes = ['index     set to the commit\'s copy of what a hook changed: %s' % ', '.join(fit)
             ] if fit else []
    if not kept:
        return notes, []
    return notes, ['refused: commit %s stands, but a hook changed %s in it, and the index holds '
                   'a copy other than the one this call committed (someone staged it before or '
                   'during this call, or the index could not be written): the next commit of it '
                   'would replace the hook\'s change. To '
                   'take the commit\'s copy: git restore --staged --source=%s -- %s'
                   % (ours[:12], ', '.join(kept), ours[:12], ' '.join(kept))]


def render_sources(root, paths):
    """The repo-relative rendered paths the kit's renderer wrote or removed in `root`, when `root`
    has the kit's layout and a path in `paths` is a render source (kitlib.content.oncommit); else
    None (no render ran). A render that fails raises _Stop, naming the command to run."""
    if not (oncommit.wants_render(paths) and oncommit.is_kit(root)):
        return None
    got, why = oncommit.render(root)
    if got is None:
        raise _Stop(REFUSED, 'refused: a render source is in this commit, and the render failed: %s. '
                    'Fix it and render by hand, then commit again: %s. Nothing was committed.'
                    % (why, oncommit.command(root)))
    return got


def _stage(req, root, paths, rendered, named, merged, status_before, msg_warnings, body, trailers,
           lines, exempt=(None, (), (), ())):
    """`(head, snap, status_after, others, message)`: the named paths staged and checked, for
    commit_only; raises _Stop on a refusal. `exempt`: (rules, the owner-only paths exempt as
    work-tree shrinks, those exempt as a merge of deletions, [the merge's MERGE_HEAD] for them),
    each judged again here on its staged copy against `head`."""
    # A listed path is always added again. The one skip: a listed path staged as a deletion and
    # absent from the work tree (`git add` would fail "pathspec did not match"); its deletion is
    # committed because it was named. Nothing unnamed is ever added.
    to_add = [p for p in paths
              if not (status_before.get(p) == 'D' and not os.path.lexists(os.path.join(root, p)))]
    if to_add:
        rc, _out, err = ownerrules.run_git(root, 'add', '--', *to_add, timeout=None)
        if rc != 0:
            raise _Stop(REFUSED, 'git add failed: %s' % (err or 'exit %s' % rc))
    err = oncommit.stage(root, rendered) if rendered else None
    if err:
        raise _Stop(REFUSED, *unstage_added(root, sorted(named), status_before, [
            'refused: the rendered files could not be staged: %s.' % err]))
    # Only what this call named, rendered or the merge staged is committed: anything another
    # writer staged since the check above stays staged, out of this commit.
    staged_now = staged_status(root)
    status_after = {p: s for p, s in staged_now.items() if p in named or p in merged}
    others = set(staged_now) - set(status_after)
    if not status_after and not req.allow_empty:
        raise _Stop(REFUSED, 'nothing to commit in: %s' % ', '.join(paths))
    # What is committed is this one read of HEAD and of the index; the index must still hold it
    # after the machine-path check, so the blobs checked are the blobs committed.
    head = head_commit(root)
    snap = index_snapshot(root, status_after)
    rules, shrunk, joined, merge_head = exempt
    grown = sorted(set(shrunk) - (shrunk_paths(root, rules, shrunk, ['--cached', head])
                                  if shrunk and head else set()))
    unjoined, why = (staged_deletion_merges(root, joined, head, merge_head[0])
                     if joined and head and merge_head else (sorted(joined), None))
    if grown or unjoined:   # the staged copy, the one committed, is what counts
        note = ['the staged merge of %s could not be judged: %s.' % (', '.join(unjoined), why)
                ] if why else []
        raise _Stop(REFUSED, *unstage_added(root, sorted(named), status_before,
                                            [owner_only_refusal(sorted(grown + unjoined))] + note))
    refusal, warnings = staged_machine_path_error(root, status_after, status_before)
    # The message check printed the same machine-config WARNING already: once is enough.
    if refusal:
        raise _Stop(REFUSED, *[l for l in refusal if l not in msg_warnings])
    changed = sorted(p for p, e in index_snapshot(root, status_after).items() if snap[p] != e)
    if changed:
        raise _Stop(REFUSED, 'refused: %s was staged again during this call, after its staged copy '
                    'was checked. Nothing was committed; run this again.' % ', '.join(changed))
    lines.extend(l for l in warnings if l not in msg_warnings)
    return head, snap, status_after, others, build_message(req.subject, body, trailers)


def commit(req):
    """Outcome(code, lines) for one commit request (module docstring)."""
    lines = []
    try:
        _commit(req, lines)
    except _Stop as stop:
        return Outcome(stop.code, lines + stop.lines)
    return Outcome(OK, lines)


def _usage(text):
    return _Stop(USAGE, 'USAGE: %s' % text)


def _commit(req, lines):
    root = req.root
    try:
        cfg = kconfig.load(root)
        opts = kconfig.commit_settings(cfg)
        rules = cfg.owner_rules()
    except kconfig.ConfigError as e:
        raise _Stop(USAGE, 'REFUSED: the kit configuration is refused: %s. Nothing was committed.'
                    % e)
    if req.allow_empty and req.paths:
        raise _usage('--allow-empty takes no paths: it commits only the message and its trailers')
    model, source, err = attribution.resolve_model(req.owner_authored, req.model, req.env, req.provider)
    if err:
        raise _usage(err)
    if source not in (None, '--model'):
        lines.append('model     %s (from %s)' % (model, source))

    body, err = message_body(req.b, req.body_file, root, req.cwd)
    if err:
        raise _usage(err)
    # The owner's own commit is exempt: the owner may keep a path literal on purpose.
    err, msg_warnings = ((None, []) if req.owner_authored
                         else machine_path_error(req.subject, body, req.b))
    if err:
        raise _Stop(REFUSED, err, *msg_warnings)
    lines.extend(msg_warnings)

    err = main_checkout_error(req.lander_main_checkout, root, opts['main_branch'],
                              opts['rule_pointer'])
    if err:
        raise _Stop(USAGE, err)

    paths = []
    for p in req.paths:
        rel = repo_path(p, root)
        if rel is None:
            raise _usage('"%s" is outside this worktree (%s); name a file inside it' % (p, root))
        paths.append(rel)
    for p in paths:
        if p in ('.', '-A', '--all') or '*' in p or os.path.isdir(os.path.join(root, p)):
            raise _usage('"%s" is not an explicit file path; name each file' % p)

    err = model_requirement_error(rules, req.owner_authored, req.owner_approved, paths)
    if err:
        raise _usage(err)

    ran = render_sources(root, paths)
    rendered = ran or []
    named = set(paths) | set(rendered)
    status_before = staged_status(root)
    heads = merge_heads(root) if opts['merge_fold'] else []
    landed = landed_main(root, opts['remote'], opts['main_branch']) if heads else []
    merged, merge_why = ownerrules.merge_owned(root, rules, set(status_before) - named, heads,
                                               landed)
    foreign = set(status_before) - named - merged
    for p in ownerrules.dropped_owner_changes(root, rules, heads or merge_heads(root)):
        lines.append("WARNING: this merge drops the merged branch's change to owner-only %s (it "
                     "commits HEAD's copy); if that is not meant, restore it from the merged "
                     "branch" % p)
    if req.allow_empty and status_before:
        raise _Stop(REFUSED, 'refused: --allow-empty commits nothing staged, but this is staged: '
                    '%s. Commit it by name, or unstage it. Nothing was committed.'
                    % ', '.join(sorted(status_before)))
    if req.allow_empty and (heads or merge_heads(root)):
        raise _Stop(REFUSED, 'refused: --allow-empty during a merge in progress; commit the merge '
                    'by naming its resolved files. Nothing was committed.')
    if req.allow_empty and not head_commit(root):
        raise _Stop(REFUSED, 'refused: --allow-empty on an unborn branch: there is no HEAD to keep, '
                    'so it would make an empty root commit. Nothing was committed.')
    if foreign:
        raise _Stop(REFUSED, foreign_refusal(foreign, merge_why, len(heads) == 1))
    # A path given to another record by a narrowing pull (docs/commit.md "A narrowed record"):
    # every named path, both sides of a rename included; merge-owned paths are main's, unchecked.
    # Imported here: kitlib.backlog is heavy, and most commits hold no narrowed record.
    from kitlib.backlog import narrowed as narrowmod
    refusal, warnings = narrowmod.check(root, sorted(named))
    lines.extend(warnings)
    if refusal:
        raise _Stop(REFUSED, *(refusal + ['Nothing was committed.']))
    approved = canonical_approved(rules, req.owner_approved, paths)
    locked = locked_paths(rules, paths + [p for p in rendered if p not in paths])
    shrunk, joined = set(), set()       # each judged again on the staged copy, in _stage
    if locked and not req.owner_authored:
        if len(heads) == 1:             # a merge of deletions only (docs/commit.md)
            joined = deletion_merge_paths(root, rules, locked, heads[0], landed)
        elif not merge_heads(root):     # a shrink: never a merge
            shrunk = shrunk_paths(root, rules, locked)
        locked = [p for p in locked if p not in shrunk | joined]
    if locked and not req.owner_authored:
        # The hint only for a path the merge would carry unnamed, as it is staged now.
        carried = sorted(ownerrules.merge_owned(root, rules, set(locked) & set(status_before),
                                                heads, landed)[0])
        raise _Stop(REFUSED, owner_only_refusal(locked, carried))
    for p in unapproved_locked(rules, locked, approved):
        raise _Stop(REFUSED, 'refused: %s is owner-only and no --owner-approved value names it.' % p)
    if req.owner_authored:
        err = owner_identity_error(rules, author_email(root))
        if err:
            raise _Stop(REFUSED, err)
    body, session = session_lines(body, req.owner_authored, req.env, req.provider)
    trailers = build_trailers(rules, approved, req.owner_authored, model, opts['coauthor_families'],
                              req.provider, session)
    head, snap, status_after, others, msg = _stage(
        req, root, paths, rendered, named, merged, status_before, msg_warnings, body,
        trailers, lines, (rules, shrunk, joined, heads[:1]))
    rc, out, err, new, notes, trees = commit_only(root, head, snap, msg,
                                                  allow_empty=req.allow_empty)
    if rc != 0:
        raise _Stop(REFUSED, 'commit failed: %s' % (err or out.strip() or 'exit %s' % rc))
    _rc, short, _err = ownerrules.run_git(root, 'rev-parse', '--short', new)
    lines.append('committed %s  %s' % (short.strip() or new[:12], req.subject))
    lines.append('files     %s' % (', '.join(
        paths + sorted((merged & set(status_after)) - set(paths))) or 'none (--allow-empty)'))
    lines.extend(notes)
    aligned, misaligned = align_index(root, trees, new) if trees else ([], [])
    lines.extend(aligned)
    try:
        others = set(staged_status(root))    # against the new HEAD: what is still staged
    except _Stop:
        pass                                 # the commit stands; the earlier read says enough
    if others:
        lines.append(left_staged_line(others))
    if ran is not None:
        lines.append('rendered  %s' % (', '.join(p for p in rendered if p not in paths)
                                       or 'no rendered file changed'))
    if any(p.endswith('.md') for p in status_after) or locked:
        line, ok = doc_budget_check(root)
        lines.append(line)
        iline, iok = doc_index_check(root)
        if iline:
            lines.append(iline)
        if not (ok and iok):
            lines.append('          fix it in a follow-up commit before this branch can land')
    if misaligned:
        raise _Stop(REFUSED, *misaligned)
