"""Owner-only paths by commit author (docs/guard-hooks.md "Owner-only commits").

violations(root, rules, tip, exclude) judges the net change first: an owner-only path
(rules.is_locked) counts only when landing `tip` on an `exclude` commit (the target main) would
change it, that is, when the target differs for it from the clean merge of the target and `tip`
(`git merge-tree --write-tree`; the tip itself for a fast-forward), or that merge conflicts on it.
For each path that counts, it lists every commit reachable from `tip` and from none of `exclude`
that changes the path and whose AUTHOR email is not the owner's (rules.is_owner). So an agent's
change that a later commit undoes, a rename away and back, and an owner's change the branch brought
in by merging main do not count; a change that stays is still judged commit by commit. With no
`exclude` there is no target: every path a commit changes counts. `gate land` refuses a branch with
any (gate.land), and the pre-push hook refuses a push to main with any
(kitlib.guard.prepush), so a plain `git commit` cannot carry an owner-only change to main past
either.

What a commit changes:
  one parent or none   its diff against that parent (`git diff-tree -r --root`)
  two parents          what differs from the clean merge of the parents
                       (`git merge-tree --write-tree`), plus every path that merge conflicts on:
                       a merge that only joins two histories changes nothing of its own, and one
                       that edits an owner-only path (an "evil merge") or resolves a conflict in
                       one is judged by its author like any other commit; with `exclude`, when a
                       parent is an ancestor of an `exclude` commit, a path the merge holds
                       exactly as (mode and blob, or absent) every `exclude` commit does is not
                       counted: the merge holds main's current copy (a main sync), never an older
                       one, and the branch's own commits that changed the path are still judged
                       one by one; main moving the path again after the sync counts it again
  more parents         the union of its diffs against each parent (strict)

The author email is git's `%ae` (never mailmapped), read with replace refs off (pushes do not carry
them, so the commits judged are the ones that leave). Renames are not followed: a rename is a
delete and an add, and both paths count. Any git failure is `(None, why)`: the caller refuses.
A `"owner".shrink_only` path (rules.is_shrink_only) does not count for a one-parent commit
whose change to it only deletes lines (deletes_only); each commit is judged on its own.

The author email is what the committer says it is (`--author`, GIT_AUTHOR_EMAIL): this check
stops a plain agent commit, not an agent that forges the owner's identity. The guard's
owner-identity rule (kitlib.guard.rules) denies a session other than the owner's setting the
owner's email on the command line; an identity in the session's starting environment is not seen.

An owner approval (docs/commit.md "Owner approval"): a commit not authored by the owner that would
be a violation is not one when it carries exactly one `Owner-Approval: hub:A-<n>` trailer
(TRAILER; the id as the hub issues it, hub:A-001, APPROVAL_RE) and that approval holds for it
as a delta (HubApprovals; kitlib.ownerfiles.approvals): no commit on main as last fetched other
than this one carries it (approvals.spent: once the change is on main the approval is used up);
the commit has one parent P, any commit; it changes no path the approval does not hold; each
approved path is at the commit what the approved change gives on P (approvals.delta_result: the
approved content where P holds the base or the approved content, the clean `git merge-file` of
the change onto P where P moved on, a refusal on a conflict), a regular file in P's mode; and
the hub server verifies the approved blobs for the signed base, consumed (by any commit: there
is no exact binding). A merge, a root commit, a path the commit holds
as anything but a regular file (a symbolic link, a gitlink: blobs_at), a value that is not one
hub:A-<n>, and any doubt of git or the hub (not running, no answer) keep the violation, with why
in its `why`.
`approvals` is the seam: an object with check(root, doc_id, head, blobs, commit) -> (ok, why);
default_approvals() is the hub's.

A merge in progress (kitlib.commit asks; docs/commit.md): merge_owned names the staged paths the
merge put there itself, dropped_owner_changes the owner-only changes it would drop, and
merge_attribute_unset whether git would merge a path with no driver; they judge the merged
commits with violations above.
"""
import collections
import re

from kitlib.git import cmd, refs
from kitlib.git.refs import REGULAR_MODES, deletes_only     # named here too: callers use oc.<name>

Violation = collections.namedtuple('Violation', 'sha author paths why', defaults=('',))
NOREPLACE = ['--no-replace-objects']
LIST_TIMEOUT = 120    # seconds
TRAILER = 'Owner-Approval'          # the one key; kitlib.config.owner refuses it as its own trailer
# The id exactly as the hub issues it ('A-%03d', kitlib.hubsite.store): A-001 to A-999, then
# A-1000 on; A-1, A-01 and A-000 are no id it writes (tests/ownerfiles/test_apply.py
# test_commit_passes_owner_rules takes one from a real hub).
APPROVAL_RE = re.compile(r'hub:(A-(?:(?!000)[0-9]{3}|[1-9][0-9]{3,}))\Z')
EXAMPLE = 'hub:A-001'
_SEP = '\x01'                       # between the trailer values of one commit in commits()
NOT_A_FILE = 'is not a regular file'    # blobs_at's text for any other entry


class HubApprovals(object):
    """The approvals seam of violations() that asks the hub server, judging the approval as a
    delta (module docstring): not spent on `main` (approvals.spent), the commit making the
    approved change on its parent `head` (approvals.delta_error, on the document the hub's
    snapshot gives), then the hub verifying the approved blobs for the signed base, consumed
    (approvals.Verifier.verify). `client` and `repo` default to the machine's hub
    (kitlib.hubclient.settings) and the checkout's origin remote, `main` to main as last fetched
    (refs/remotes/<remote>/<main branch> of the checkout's config); any failure to make one is a
    refusal, never a pass. One instance reads each approval's document and each spent answer
    once."""

    def __init__(self, client=None, repo=None, main=None):
        self.client, self.repo, self.main = client, repo, main
        self._docs, self._spent, self._main_sha = {}, {}, None

    def check(self, root, doc_id, head, blobs, commit):
        from kitlib.ownerfiles import approvals as ap      # here: a guard without the hub still loads
        repo = self.repo
        if repo is None:
            repo, why = ap.remote_id(root)
            if repo is None:
                return False, 'the repository has no remote to name it: %s' % why
        client = self.client
        if client is None:
            try:
                from kitlib.hubclient import client as hubclient, settings
                client = hubclient.Client(settings.load().port)
            except Exception as e:      # noqa: BLE001 - fail closed: no client is no approval
                return False, ap.WHY_SERVER % type(e).__name__
        why = self._spent_why(root, doc_id, commit)
        if why:
            return False, why
        verifier = ap.Verifier(client)
        if doc_id not in self._docs:
            self._docs[doc_id] = verifier.approved_doc(doc_id)
        doc, why = self._docs[doc_id]
        if doc is None:
            return False, why
        approved, why = ap.delta_error(root, doc, head, commit, blobs)
        if approved is None:
            return False, why
        return verifier.verify(doc_id, repo, doc['head'], approved, commit=commit)

    def _spent_why(self, root, doc_id, commit):
        """Why the approval `doc_id` is used up (a commit on main other than `commit` carries it),
        or '' when it is not; any doubt is a why."""
        from kitlib.ownerfiles import approvals as ap
        if self._main_sha is None:
            ref = self.main
            if ref is None:
                try:
                    from kitlib import config as kconfig
                    cfg = kconfig.load(root)
                    ref = 'refs/remotes/%s/%s' % (cfg.remote, cfg.main_branch)
                except Exception as e:  # noqa: BLE001 - fail closed: no main is no approval
                    return 'main cannot be named: %s' % e
            sha, why = refs.resolve(ref, root, known=True)
            if sha is None:
                return 'main %s cannot be read: %s' % (ref, why)
            self._main_sha = (ref, sha)
        ref, sha = self._main_sha
        if not sha:
            return ''                   # no main fetched: nothing has landed
        key = (sha, doc_id)
        if key not in self._spent:
            self._spent[key] = ap.spent(root, doc_id, sha)
        landed, why = self._spent[key]
        if why:
            return 'whether it is spent cannot be told: %s' % why
        if landed and landed != commit:
            return 'spent: landed in %s' % landed[:12]
        return ''


def default_approvals():
    """The approvals seam violations() uses when given none: the hub server's (HubApprovals)."""
    return HubApprovals()


def _git(root, args, env, timeout=cmd.GIT_TIMEOUT):
    r = cmd.call(NOREPLACE + list(args), root, timeout=timeout, env=env)
    if r.rc != 0:
        return None, 'git %s failed: %s' % (args[0], r.err or 'exit %s' % r.rc)
    return cmd.text(r.out), None


def _paths(out):
    return [p for p in out.split('\0') if p]


def commits(root, tip, exclude=(), env=None):
    """`([(sha, [parents], author email, [Owner-Approval values])], None)` reachable from `tip`
    and from no `exclude` revision, or `(None, why)`."""
    for rev in [tip] + list(exclude):
        why = refs.bad_rev(rev)
        if why:
            return None, why
    args = ['log', '--no-use-mailmap',
            '--format=%%H %%P%%x00%%ae%%x00%%(trailers:key=%s,valueonly,separator=%%x01)%%x00'
            % TRAILER, tip]
    if exclude:
        args += ['--not'] + list(exclude)
    out, why = _git(root, args + ['--'], env, timeout=LIST_TIMEOUT)
    if out is None:
        return None, why
    fields = out.split('\0')
    found = []
    for i in range(0, len(fields) - 2, 3):
        ids = fields[i].split()
        if not ids:
            continue
        values = [v.strip() for v in fields[i + 2].split(_SEP) if v.strip()]
        found.append((ids[0], ids[1:], fields[i + 1].strip().lower(), values))
    return found, None


def plain_changes(root, shas, env=None):
    """`({sha: sorted paths}, None)` for commits with one parent or none, in one
    `git diff-tree --stdin` (a commit that changes nothing prints nothing), or `(None, why)`."""
    if not shas:
        return {}, None
    r = cmd.call(NOREPLACE + ['diff-tree', '--stdin', '-r', '--root', '--name-only', '--no-renames',
                              '-z'], root, stdin=('\n'.join(shas) + '\n').encode('ascii'),
                 timeout=LIST_TIMEOUT, env=env)
    if r.rc != 0:
        return None, 'git diff-tree failed: %s' % (r.err or 'exit %s' % r.rc)
    order = {sha: i for i, sha in enumerate(shas)}
    found = {sha: set() for sha in shas}
    current, at = None, -1
    for token in _paths(cmd.text(r.out)):
        i = order.get(token)
        if i is not None and i > at:          # git answers in the order it was asked
            current, at = token, i
        elif current is None:
            return None, 'git diff-tree printed a path before any commit: %r' % token
        else:
            found[current].add(token)
    return {sha: sorted(paths) for sha, paths in found.items()}, None


def _against_merge(root, rev, one, two, env=None):
    """`(set of paths, None)`: what differs between `rev` and the clean merge of `one` and `two`
    (`git merge-tree --write-tree`), plus every path that merge conflicts on; or `(None, why)`."""
    r = cmd.call(NOREPLACE + ['merge-tree', '--write-tree', '--name-only', '--no-messages',
                              '--allow-unrelated-histories', '-z', one, two], root, env=env)
    tree, conflicted = parse_merge_tree(cmd.text(r.out)) if r.rc in (0, 1) else ('', set())
    if not refs.SHA_RE.fullmatch(tree or ''):
        return None, 'git merge-tree failed for %s and %s (rc %s): %s' % (
            one[:10], two[:10], r.rc, r.err or 'no output')
    out, why = _git(root, ['diff', '--name-only', '--no-renames', '-z', tree, rev], env)
    return (None, why) if out is None else (set(_paths(out)) | set(conflicted), None)


def net_change(root, tip, targets, env=None):
    """`(set of paths, None)`: every path that landing `tip` on any of `targets` would change (the
    target against the clean merge of the target and `tip`, plus that merge's conflicts); or
    `(None, why)`. For a target `tip` descends from, the merge is `tip`'s own tree."""
    paths = set()
    for target in targets:
        found, why = _against_merge(root, target, target, tip, env)
        if found is None:
            return None, why
        paths |= found
    return paths, None


def _synced(root, parents, targets, env=None):
    """`(True, None)` when a parent is an ancestor of a target, `(False, None)` when none is, or
    `(None, why)`."""
    for parent in parents:
        for target in targets:
            r = cmd.call(NOREPLACE + ['merge-base', '--is-ancestor', parent, target], root, env=env)
            if r.rc not in (0, 1):
                return None, 'git merge-base failed for %s and %s: %s' % (
                    parent[:10], target[:10], r.err or 'exit %s' % r.rc)
            if r.rc == 0:
                return True, None
    return False, None


def changed(root, sha, parents, env=None, targets=()):
    """`(sorted paths the commit itself changes, None)` (module docstring; `targets`: violations'
    `exclude`), or `(None, why)`."""
    if len(parents) <= 1:
        out, why = _git(root, ['diff-tree', '-r', '--root', '--no-commit-id', '--name-only',
                               '--no-renames', '-z', sha], env)
        return (None, why) if out is None else (sorted(set(_paths(out))), None)
    if len(parents) == 2:
        paths, why = _against_merge(root, sha, parents[0], parents[1], env)
        if paths is None:
            return None, why
        if paths and targets:
            synced, why = _synced(root, parents, targets, env)
            if synced is None:
                return None, why
            if synced:
                differs = set()
                for target in targets:
                    out, why = _git(root, ['diff', '--name-only', '--no-renames', '-z', target,
                                           sha], env)
                    if out is None:
                        return None, why
                    differs.update(_paths(out))
                paths &= differs        # kept: held otherwise than some target holds it
        return sorted(paths), None
    paths = set()
    for parent in parents:
        out, why = _git(root, ['diff', '--name-only', '--no-renames', '-z', parent, sha], env)
        if out is None:
            return None, why
        paths.update(_paths(out))
    return sorted(paths), None


def blobs_at(root, sha, paths, env=None):
    """`({path: blob id at `sha`}, None)` for `paths`, in one `git ls-tree`, or `(None, why)`. A
    path `sha` has no entry for maps to None (a deletion); one whose entry is not a regular file
    (mode 100644 or 100755: a symbolic link 120000, a gitlink 160000) maps to a text starting
    with NOT_A_FILE, which equals no blob id and no deletion (irregular() names them)."""
    out, why = _git(root, ['ls-tree', '-r', '-z', '--full-tree', sha, '--'] + list(paths), env)
    if out is None:
        return None, why
    found = dict.fromkeys(paths)
    for row in _paths(out):
        meta, tab, name = row.partition('\t')
        cols = meta.split()
        if tab and name in found and len(cols) == 3:
            regular = cols[0] in REGULAR_MODES and cols[1] == 'blob'
            found[name] = cols[2] if regular else '%s (mode %s)' % (NOT_A_FILE, cols[0])
    return found, None


def irregular(blobs):
    """Why a path of `blobs` (blobs_at) is in the commit but not as a regular file, or None."""
    odd = sorted('%s %s' % (p, b[len(NOT_A_FILE) + 1:]) for p, b in blobs.items()
                 if isinstance(b, str) and b.startswith(NOT_A_FILE))
    if not odd:
        return None
    return '%s %s: an approval holds regular files only' % (', '.join(odd), NOT_A_FILE)


def approval_refusal(root, sha, parents, values, paths, approvals, env=None):
    """`(refusal, None)` for the commit `sha` (`parents`, its Owner-Approval `values`, the
    `paths` it changes): refusal None when it carries no approval, '' when the hub verifies its
    approval (module docstring), else why the approval does not count. `(None, why)` when git
    cannot read the commit's blobs."""
    if not values:
        return None, None
    if len(values) != 1 or not APPROVAL_RE.match(values[0]):
        return ('its Owner-Approval %s is not one hub:A-<n> (as %s)'
                % (', '.join(values), EXAMPLE)), None
    doc_id = APPROVAL_RE.match(values[0]).group(1)
    name = 'owner approval hub:%s' % doc_id
    if len(parents) != 1:
        return '%s refused: %s' % (name, 'a merge cannot carry an owner approval' if parents
                                   else 'a root commit has no approved base'), None
    blobs, why = blobs_at(root, sha, paths, env)
    if blobs is None:
        return None, why
    odd = irregular(blobs)
    if odd:
        return '%s refused: %s' % (name, odd), None
    ok, why = (approvals or default_approvals()).check(root, doc_id, parents[0], blobs, sha)
    return ('' if ok else '%s refused: %s' % (name, why or 'no reason given')), None


def violations(root, rules, tip, exclude=(), env=None, approvals=None):
    """`([Violation], None)`: the commits from `tip` not in `exclude` that change an owner-only path
    that landing `tip` on `exclude` would change (module docstring), are not authored by the
    owner and carry no owner approval the hub verifies (`approvals`: the seam, default
    default_approvals(), made once per call so its reads are cached across the commits);
    `(None, why)` when git cannot tell."""
    found, why = commits(root, tip, exclude, env)
    if found is None:
        return None, why
    net = None
    if exclude:
        net, why = net_change(root, tip, exclude, env)
        if net is None:
            return None, why
        if not any(rules.is_locked(p) for p in net):
            return [], None
    judged = [c for c in found if not rules.is_owner(c[2])]
    plain, why = plain_changes(root, [c[0] for c in judged if len(c[1]) <= 1], env)
    if plain is None:
        return None, why
    bad = []
    if approvals is None:
        approvals = default_approvals()
    for sha, parents, author, values in judged:
        paths, why = (plain[sha], None) if sha in plain else changed(root, sha, parents, env,
                                                                     exclude)
        if paths is None:
            return None, why
        locked = [p for p in paths if rules.is_locked(p) and (net is None or p in net)]
        for p in [p for p in locked if len(parents) == 1 and rules.is_shrink_only(p)]:
            shrinks, why = deletes_only(root, p, parents + [sha], env)  # merges, roots never shrink
            if shrinks is None:
                return None, why
            locked = [q for q in locked if q != p or not shrinks]
        if not locked:
            continue
        refusal, why = approval_refusal(root, sha, parents, values, paths, approvals, env)
        if why:
            return None, why
        if refusal != '':
            bad.append(Violation(sha, author, locked, refusal or ''))
    return bad, None


def describe(rules, bad, limit=5):
    """One line naming the offending commits (at most `limit`), their authors and paths, why an
    owner approval a commit carries did not count, and why no author counts as the owner when
    that is the case."""
    parts = ['%s by %s changes %s%s%s' % (v.sha[:10], v.author or '(no email)',
                                          ', '.join(v.paths[:5]), ' ...' if len(v.paths) > 5 else '',
                                          ' (%s)' % v.why if v.why else '') for v in bad[:limit]]
    more = ' (and %d more)' % (len(bad) - limit) if len(bad) > limit else ''
    line = ('owner-only paths changed by a commit not authored by the owner: %s%s'
            % ('; '.join(parts), more))
    problem = rules.identity_problem()
    if problem:
        line += '; %s' % problem
    return line + ('. Only the owner commits these paths (author email = "owner".email in '
                   '.kit.json); ask the owner to make the change')


def run_git(root, *args, timeout=None):
    """`(rc, stdout text unstripped, stderr text)`; rc None when git did not run."""
    kw = {} if timeout is None else {'timeout': timeout}
    r = cmd.call(list(args), root, **kw)
    return r.rc, r.out.decode('utf-8', 'replace'), r.err


def parse_merge_tree(raw):
    """`(tree, conflicted paths)` from `git merge-tree --write-tree --name-only --no-messages -z`
    (the tree OID, then each conflicted path once, NUL-terminated). A newline after the OID is
    tolerated as well as a NUL."""
    tokens = raw.split('\0')
    head = tokens[0]
    if '\n' in head:
        head, rest = head.split('\n', 1)
        tokens = [rest] + tokens[1:]
    else:
        tokens = tokens[1:]
    return head.strip(), {t for t in tokens if t not in ('', '\n')}


def _entries(out, path):
    """The `(meta fields, name)` rows of a `-z` ls-files/ls-tree listing whose name is exactly
    `path` (a pathspec may match more: a directory's contents, or a glob)."""
    rows = []
    for row in out.split('\0'):
        meta, tab, name = row.partition('\t')
        if tab and name == path:
            rows.append(meta.split())
    return rows


def index_entry(root, path):
    """`(known, entry)`: `path`'s staged index entry as `(mode, oid)`, or None when the index holds
    none (a staged deletion). `known` is False when git cannot say, or when the path is unmerged
    (a conflict stage is in the index): nothing is proven then."""
    rc, out, _ = run_git(root, 'ls-files', '--stage', '-z', '--', path)
    if rc != 0:
        return False, None
    rows = _entries(out, path)
    if not rows:
        return True, None
    if len(rows) != 1 or len(rows[0]) != 3 or rows[0][2] != '0':
        return False, None
    return True, (rows[0][0], rows[0][1])


def tree_entry(root, rev, path):
    """`(known, entry)`: `path`'s entry in commit `rev` as `(mode, oid)`, or None when the commit
    has none (it deleted the path). `known` is False when git cannot say."""
    rc, out, _ = run_git(root, 'ls-tree', '-z', rev, '--', path)
    if rc != 0:
        return False, None
    rows = _entries(out, path)
    if not rows:
        return True, None
    if len(rows) != 1 or len(rows[0]) != 3:
        return False, None
    return True, (rows[0][0], rows[0][2])


def merge_bases(root, head):
    """Every merge base of HEAD and `head`, or None when git cannot say (or finds none)."""
    rc, out, _ = run_git(root, 'merge-base', '--all', 'HEAD', head)
    bases = out.split() if rc == 0 else []
    return bases or None


def carries_merged_copy(root, path, head, bases):
    """True when `path`'s staged state is exactly the merged-in commit `head`'s (the same mode and
    blob, or absent from both: the merged branch deleted it and the deletion is staged) AND `head`
    changed the path from every merge base in `bases`, so the change came in with the merged
    branch rather than being the merge undoing this branch's own. An unmerged path, one git cannot
    list, or no known merge base is False."""
    if not bases:
        return False
    known_i, staged = index_entry(root, path)
    known_t, theirs = tree_entry(root, head, path)
    if not (known_i and known_t and staged == theirs):
        return False
    for base in bases:
        known_b, at_base = tree_entry(root, base, path)
        if not known_b or at_base == theirs:
            return False
    return True


def dropped_owner_changes(root, rules, heads):
    """The owner-only paths a merge in progress would commit as HEAD's own copy although the merged
    branch changed them from every merge base (a `git checkout HEAD -- <path>` dropped that
    change). Only a single merge; a path git cannot list, or an unmerged one, is left out."""
    if len(heads) != 1:
        return []
    bases = merge_bases(root, heads[0])
    rc, out, _ = run_git(root, 'diff', '--name-only', '--no-renames', '-z', 'HEAD', heads[0])
    if not bases or rc != 0:
        return []
    dropped = []
    for p in sorted(x for x in out.split('\0') if x and rules.is_locked(x)):
        known_i, staged = index_entry(root, p)
        known_h, ours = tree_entry(root, 'HEAD', p)
        known_t, theirs = tree_entry(root, heads[0], p)
        if not (known_i and known_h and known_t) or staged != ours or ours == theirs:
            continue
        at_bases = [tree_entry(root, b, p) for b in bases]
        if all(known and at != theirs for known, at in at_bases):
            dropped.append(p)
    return dropped


def merge_owned(root, rules, staged, heads, landed=(), join=True):
    """`(paths, why)`: the staged paths a merge in progress put there itself. An ordinary path
    counts only when its staged content equals the clean merge result (`git merge-tree
    --write-tree`) and merge-tree does not list it as conflicted. An owner-only path counts only
    when its staged state is exactly the merged branch's own copy, which that branch changed from
    the merge base (carries_merged_copy: same mode and blob as `<MERGE_HEAD>:<path>`, or a deletion
    of a path the merged branch deleted), whether the merge took it cleanly or a conflict was
    resolved to it, and every commit the merge brings that changes it, other than those reachable
    from `landed` (landed_main), is the owner's or carries an approval the hub verifies
    (non_owner_paths). An owner-only path both sides changed counts too when it passes the
    ordinary test and clean_owner_merge (same name and regular file throughout, no merge driver,
    every commit on both sides approved), unless `join` is False. Any other staged state of an owner-only path is never
    the merge's. No merge, or an octopus one, gives `(set(), None)`; a git failure gives
    `(set(), why)` (or the paths found so far and `why`, when only the authors could not be
    checked)."""
    candidates = set(staged)
    if len(heads) != 1 or not candidates:
        return set(), None
    rc, out, err = run_git(root, 'merge-tree', '--write-tree', '--name-only', '--no-messages', '-z',
                           'HEAD', heads[0])
    tree, conflicted = parse_merge_tree(out) if rc in (0, 1) else ('', set())
    if not tree:
        return set(), 'git merge-tree failed (rc %s): %s' % (rc, err or out.strip() or 'no output')
    rc, out, err = run_git(root, 'diff', '--cached', '--name-only', '--no-renames', '-z', tree)
    if rc != 0:
        return set(), 'git diff --cached against the merge result failed: %s' % (err or 'no output')
    differs = {p for p in out.split('\0') if p}
    locked = {p for p in candidates if rules.is_locked(p)}
    owned = (candidates - locked) - differs - conflicted
    if locked:
        foreign, why = non_owner_paths(root, rules, heads[0], landed)
        if foreign is None:
            return owned, why
        bases = merge_bases(root, heads[0])
        owned |= {p for p in locked - foreign if carries_merged_copy(root, p, heads[0], bases)}
        joined = (locked - foreign - owned) - differs - conflicted
        if joined and join:
            joined, why = clean_owner_merge(root, rules, joined, heads[0], bases, landed)
            if joined is None:
                return owned, why
            owned |= joined
    return owned, None


def merge_attribute_unset(root, path):
    """True when git says the `merge` attribute of `path` is unspecified, read from the work tree
    and from the index (`git check-attr`), and no `merge.default` is configured (git applies that
    driver to every path whose attribute is unspecified); anything else, or a git that cannot
    say, is False: a merge driver (merge=union, -merge, a custom one) makes a result
    merge-tree's alone does not prove."""
    rc, _, _ = run_git(root, 'config', '--get', 'merge.default')
    if rc != 1:                     # 0: a driver is set; any other code: git could not say
        return False
    for extra in ((), ('--cached',)):
        rc, out, _ = run_git(root, 'check-attr', '-z', *extra, 'merge', '--', path)
        fields = out.split('\0') if rc == 0 else []
        if len(fields) < 3 or fields[0] != path or fields[2] != 'unspecified':
            return False
    return True


def clean_owner_merge(root, rules, paths, head, bases, landed=()):
    """`(paths, None)`: those of the owner-only `paths` (staged exactly as the clean merge of HEAD
    and `head` has them, and not conflicted there: merge_owned's test) that the merge in progress
    carries as the join of both sides' changes: each is a regular file of one mode (as staged)
    under the same name at HEAD, at `head` and at every merge base in `bases` (no rename, add,
    delete or mode change), its `merge`
    attribute is unspecified (merge_attribute_unset), and no commit on either side that changes it,
    other than those reachable from `landed`, lacks the owner as author or an approval the hub
    verifies (non_owner_paths for `head`'s side; the same check of HEAD's against `head` for
    HEAD's). `(None, why)` when git cannot tell (docs/commit.md "Owner approval")."""
    if not bases:
        return set(), None
    fit = set()
    for p in paths:
        known, staged = index_entry(root, p)
        entries = [tree_entry(root, rev, p) for rev in ['HEAD', head] + list(bases)]
        if (known and staged and staged[0] in REGULAR_MODES
                and all(k and e and e[0] == staged[0] for k, e in entries)
                and merge_attribute_unset(root, p)):
            fit.add(p)
    if not fit:
        return set(), None
    ours, why = _unapproved_paths(root, rules, 'HEAD', [head] + list(landed), 'this branch\'s')
    return (None, why) if ours is None else (fit - ours, None)


def non_owner_paths(root, rules, head, landed=()):
    """`(paths, None)`: the owner-only paths that a commit the merged-in `head` brings (one
    reachable neither from HEAD nor from `landed`) changes without the owner as its author or an
    approval the hub verifies, judged as `gate land` judges a branch
    (violations); or `(None, why)` when git cannot tell. A commit already
    on main is not asked of the hub again: its approval was verified when it landed, and stops
    verifying once past its lifetime or while the hub is down. A merged copy one of those paths carries is not the owner's,
    so it is never the merge's."""
    return _unapproved_paths(root, rules, head, ['HEAD'] + list(landed), 'merged')


def _unapproved_paths(root, rules, tip, exclude, which):
    """`(paths, None)`: the owner-only paths of violations(tip, exclude), or
    `(None, why)` naming `which` commits could not be checked."""
    bad, why = violations(root, rules, tip, list(exclude))
    if bad is None:
        return None, 'the authors of the %s commits could not be checked: %s' % (which, why)
    return {p for v in bad for p in v.paths}, None
