"""Owner-only paths by commit author (docs/guard.md "Owner-only commits").

violations(root, rules, tip, exclude) judges the net change first: an owner-only path
(rules.is_locked) counts only when landing `tip` on an `exclude` commit (the target main) would
change it, that is, when the target differs for it from the clean merge of the target and `tip`
(`git merge-tree --write-tree`; the tip itself for a fast-forward), or that merge conflicts on it.
For each path that counts, it lists every commit reachable from `tip` and from none of `exclude`
that changes the path and whose AUTHOR email is not the owner's (rules.is_owner). So an agent's
change that a later commit undoes, a rename away and back, and an owner's change the branch brought
in by merging main do not count; a change that stays is still judged commit by commit. With no
`exclude` there is no target: every path a commit changes counts. `gate land` refuses a branch with
any (kitlib.gate.land), and the pre-push hook refuses a push to main with any
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

The author email is what the committer says it is (`--author`, GIT_AUTHOR_EMAIL): this check
stops a plain agent commit, not an agent that forges the owner's identity. The guard's
owner-identity rule (kitlib.guard.rules) denies a session other than the owner's setting the
owner's email on the command line; an identity in the session's starting environment is not seen.

An owner approval (docs/commit.md "Owner approval"): a commit not authored by the owner that would
be a violation is not one when it carries exactly one `Owner-Approval: hub:A-<n>` trailer
(TRAILER; the id as the hub issues it, hub:A-001, APPROVAL_RE) and that approval holds for it
as a delta (HubApprovals; kitlib.hubsite.approvals): no commit on main as last fetched other
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
"""
import collections
import re

from kitlib.git import cmd, refs

Violation = collections.namedtuple('Violation', 'sha author paths why', defaults=('',))
NOREPLACE = ['--no-replace-objects']
LIST_TIMEOUT = 120    # seconds
TRAILER = 'Owner-Approval'          # the one key; kitlib.config.owner refuses it as its own trailer
# The id exactly as the hub issues it ('A-%03d', kitlib.hubsite.store): A-001 to A-999, then
# A-1000 on; A-1, A-01 and A-000 are no id it writes (tests/commit/test_commit_approval.py
# TheIdTheHubIssues takes one from a real hub).
APPROVAL_RE = re.compile(r'hub:(A-(?:(?!000)[0-9]{3}|[1-9][0-9]{3,}))\Z')
EXAMPLE = 'hub:A-001'
_SEP = '\x01'                       # between the trailer values of one commit in commits()
REGULAR_MODES = ('100644', '100755')    # the tree modes of a regular file
NOT_A_FILE = 'is not a regular file'    # blobs_at's text for any other entry


class HubApprovals(object):
    """The approvals seam of violations() that asks the hub server, judging the approval as a
    delta (module docstring): not spent on `main` (approvals.spent), the commit making the
    approved change on its parent `head` (approvals.delta_error, on the document the hub's
    snapshot gives), then the hub verifying the approved blobs for the signed base, consumed
    (approvals.Verifier.verify). `client` and `repo` default to the machine's hub
    (kitlib.hubsite.settings) and the checkout's origin remote, `main` to main as last fetched
    (refs/remotes/<remote>/<main branch> of the checkout's config); any failure to make one is a
    refusal, never a pass. One instance reads each approval's document and each spent answer
    once."""

    def __init__(self, client=None, repo=None, main=None):
        self.client, self.repo, self.main = client, repo, main
        self._docs, self._spent, self._main_sha = {}, {}, None

    def check(self, root, doc_id, head, blobs, commit):
        from kitlib.hubsite import approvals as ap      # here: a guard without the hub still loads
        repo = self.repo
        if repo is None:
            repo, why = ap.remote_id(root)
            if repo is None:
                return False, 'the repository has no remote to name it: %s' % why
        client = self.client
        if client is None:
            try:
                from kitlib.hubsite import client as hubclient, settings
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
        from kitlib.hubsite import approvals as ap
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


def _parse_merge_tree(raw):
    """`(tree, conflicted paths)` from `git merge-tree --write-tree --name-only --no-messages -z`."""
    from kitlib.commit import parse_merge_tree
    return parse_merge_tree(raw)


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
    tree, conflicted = _parse_merge_tree(cmd.text(r.out)) if r.rc in (0, 1) else ('', set())
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
    parts = ['%s by %s changes %s%s' % (v.sha[:10], v.author or '(no email)',
                                        ', '.join(v.paths[:5]) + (' ...' if len(v.paths) > 5
                                                                  else ''),
                                        ' (%s)' % v.why if v.why else '')
             for v in bad[:limit]]
    more = ' (and %d more)' % (len(bad) - limit) if len(bad) > limit else ''
    line = ('owner-only paths changed by a commit not authored by the owner: %s%s'
            % ('; '.join(parts), more))
    problem = rules.identity_problem()
    if problem:
        line += '; %s' % problem
    return line + ('. Only the owner commits these paths (author email = "owner".email in '
                   '.kit.json); ask the owner to make the change')
