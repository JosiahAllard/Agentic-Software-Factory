"""kitlib.staging.revert: the revert of one landing as a commit object, and the local branch that
carries it to the coordinator (docs/modules/staging-design.md "Culprit and revert").

  make(root, merge, base)              (Made, None) or (None, why): no ref moves, no tree changes
  prepare(root, merge, base, made)     (Prepared, None) or (None, why): the local branch

The revert of `merge` on `base` is the three-way merge of `base` and `merge`'s first parent with
`merge` as their base (`git merge-tree --write-tree --merge-base <merge> <base> <merge>^1`: mainline
1, git revert -m 1's result), written as objects only, then one `commit-tree -p <base>`. Its
message names the landing and ends with `Reverts-landing: <merge sha>` (kitlib.backlog.at_landing
REVERTS_KEY), so the landing that lands it reopens what `merge` closed (kitlib.backlog.reopen).
Author and committer are AUTHOR, set in the call's environment, never the machine's git identity.

A merge-tree conflict is a Made with no commit, status `conflict`, and its paths; a result whose
tree is `base`'s (the change is already undone there) is `empty`. The branch is REVERT_BRANCH, made
only in the local repository: nothing here pushes, lands or touches a work tree. A branch that is
there already at the same commit is kept as it is; at another commit it is left alone, and the note
says so. Every git call goes through kitlib.git.cmd.call; nothing here prints.
"""
import collections

from kitlib.git import cmd, refs

AUTHOR = ('PM kit promotion', 'promote@pm-kit.invalid')     # author and committer of a revert
REVERT_BRANCH = 'revert/%s'         # % the landing's sha, cut to 10
REVERTS_KEY = 'Reverts-landing:'    # kitlib.backlog.at_landing REVERTS_KEY: staging is below it
CONFLICT, EMPTY, MADE = 'conflict', 'empty', 'made'

Made = collections.namedtuple('Made', 'status commit paths')       # paths: a conflict's
Prepared = collections.namedtuple('Prepared', 'branch commit note')


def branch_of(merge):
    return REVERT_BRANCH % merge[:10]


def _env():
    return dict(cmd.scrubbed_env(), GIT_AUTHOR_NAME=AUTHOR[0], GIT_AUTHOR_EMAIL=AUTHOR[1],
                GIT_COMMITTER_NAME=AUTHOR[0], GIT_COMMITTER_EMAIL=AUTHOR[1])


def _out(r, what):
    if r.rc != 0:
        return None, 'git %s failed (rc %s): %s' % (what, r.rc, r.err or 'no output')
    return cmd.text(r.out).strip(), None


def by_hand(root, landing):
    """The `git revert` that undoes `landing` by hand: `-m 1` for a merge, none for a commit
    pushed straight to staging (git refuses `-m` there). Git failing to say: the merge form."""
    parents, _ = _out(cmd.call(['rev-list', '--parents', '-1', landing], root), 'rev-list')
    if parents is not None and len(parents.split()) == 2:
        return 'git revert %s' % landing
    return 'git revert -m 1 %s' % landing


def message(merge, subject):
    """The revert commit's message."""
    return 'Revert landing %s: %s\n\nThis undoes landing %s (mainline 1).\n\n%s %s\n' % (
        merge[:10], subject, merge, REVERTS_KEY, merge)


def make(root, merge, base):
    """`(Made, None)`: the revert of `merge` on `base` in the repository at `root` (the module
    docstring), or `(None, why)` when git cannot make it."""
    r = cmd.call(['merge-tree', '--write-tree', '--name-only', '--no-messages', '-z',
                  '--merge-base', merge, base, merge + '^1'], root)
    tree, conflicted = refs.parse_merge_tree(cmd.text(r.out)) if r.rc in (0, 1) else ('', set())
    if not refs.is_sha(tree):
        return None, 'git merge-tree could not revert %s on %s (rc %s): %s' % (
            merge[:10], base[:10], r.rc, r.err or 'no output')
    if conflicted:
        return Made(CONFLICT, None, sorted(conflicted)), None
    had, why = refs.tree_of(base, root)
    if had is None:
        return None, why
    if had == tree:
        return Made(EMPTY, None, []), None
    subject, why = _out(cmd.call(['log', '-1', '--format=%s', merge], root), 'log')
    if subject is None:
        return None, why
    sha, why = _out(cmd.call(['commit-tree', tree, '-p', base], root,
                             stdin=message(merge, subject).encode('utf-8'), env=_env()),
                    'commit-tree')
    if sha is None:
        return None, why
    return Made(MADE, sha, []), None


def prepare(root, merge, base, made=None):
    """`(Prepared, None)`: the local branch REVERT_BRANCH at the revert of `merge` on `base`
    (`made`: one make() already gave on that base), or `(None, why)`: a conflict, an empty
    revert, or git."""
    if made is None:
        made, why = make(root, merge, base)
        if made is None:
            return None, why
    if made.status == CONFLICT:
        return None, ('the revert of %s does not apply cleanly on %s: it conflicts in %s; revert '
                      'it by hand on a branch from %s: %s' % (
                          merge[:10], base[:10], ', '.join(made.paths), base[:10],
                          by_hand(root, merge)))
    if made.status == EMPTY:
        return None, '%s already holds no change of %s: there is nothing to revert' % (
            base[:10], merge[:10])
    branch = branch_of(merge)
    there, why = refs.resolve('refs/heads/' + branch, root, known=True)
    if there is None:
        return None, why
    if there == made.commit:
        return Prepared(branch, made.commit, 'already there'), None
    if there:
        return Prepared(branch, there, 'left as it is: %s is at %s, not at the new revert %s' % (
            branch, there[:10], made.commit[:10])), None
    _, why = _out(cmd.call(['branch', branch, made.commit], root), 'branch')
    if why:
        return None, why
    return Prepared(branch, made.commit, None), None
