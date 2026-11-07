"""A merge's own resolution of an owner-only file that only combines the deletions its two sides
made (docs/commit.md "A merge that only deletes").

keeps_only_deletions(ours, theirs, result, bases)   the rule on the contents (pure)
deletion_merge(root, one, two, held)                the paths a merge of `one` and `two` may hold
still_judged(root, sha, parents, paths)             the owner-only paths a commit's author is
                                                    judged for (kitlib.guard.ownercommits.violations)
tree_entries(root, sha, paths)                      {path: (mode, object id)} at a commit

kitlib.guard.ownercommits judges a merge commit with still_judged; commit.py's merge in progress
judges its staged copies with deletion_merge (kitlib.commit). Git runs with replace refs off, as
in ownercommits.
"""
import collections

from kitlib.git import cmd
from kitlib.guard import ownercommits as oc


def tree_entries(root, sha, paths, env=None):
    """`({path: (mode, object id) or None}, None)` for `paths` at the commit `sha`, in one
    `git ls-tree`, or `(None, why)`. A path `sha` has no entry for maps to None."""
    r = cmd.call(oc.NOREPLACE + ['ls-tree', '-r', '-z', '--full-tree', sha, '--'] + list(paths),
                 root, env=env)
    if r.rc != 0:
        return None, 'git ls-tree failed: %s' % (r.err or 'exit %s' % r.rc)
    found = dict.fromkeys(paths)
    for row in cmd.text(r.out).split('\0'):
        meta, tab, name = row.partition('\t')
        cols = meta.split()
        if tab and name in found and len(cols) == 3:
            found[name] = (cols[0], cols[2])
    return found, None


def _lines(data):
    """The lines of `data` (bytes), each ending after its b'\\n' (a CRLF line keeps its b'\\r'),
    the last without one when `data` does not end in b'\\n'."""
    parts = data.split(b'\n')
    return [p + b'\n' for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def _subsequence(short, long):
    """True when the list `short` is `long` with items left out, in order."""
    rest = iter(long)
    return all(any(x == y for y in rest) for x in short)


def keeps_only_deletions(ours, theirs, result, bases):
    """True when a merge of `ours` and `theirs` (file contents, bytes) may hold `result` as its
    own resolution of an owner-only file (docs/commit.md "A merge that only deletes"): there is a
    merge base (`bases`, the file at each, bytes); no content holds a NUL byte (binary); `result`,
    as lines compared byte for byte, is `ours` and `theirs` each with lines left out, in order (it
    adds and moves none); it holds every line exactly as often as the parent holding it fewer
    times (it drops none both keep); and wherever the parents hold a line a different number of
    times, no parent holds it more often than any base (the difference is one side's deletion,
    never an addition the merge would drop)."""
    texts = [ours, theirs, result] + list(bases)
    if not bases or any(b'\0' in t for t in texts):
        return False
    one, two, res = _lines(ours), _lines(theirs), _lines(result)
    if not (_subsequence(res, one) and _subsequence(res, two)):
        return False
    c1, c2, cr = (collections.Counter(x) for x in (one, two, res))
    if any(cr[line] != min(c1[line], c2[line]) for line in set(c1) | set(c2)):
        return False
    for base in bases:
        cb = collections.Counter(_lines(base))
        if any(c1[line] != c2[line] and max(c1[line], c2[line]) > cb[line]
               for line in set(c1) | set(c2)):
            return False
    return True


def _read_blob(root, oid, env=None):
    """`(the bytes of the blob `oid`, None)`, or `(None, why)`."""
    r = cmd.call(oc.NOREPLACE + ['cat-file', 'blob', oid], root, env=env)
    if r.rc != 0:
        return None, 'git cat-file failed for %s: %s' % (oid[:10], r.err or 'exit %s' % r.rc)
    return r.out, None


def deletion_merge(root, one, two, held, env=None):
    """`(paths, None)`: those of `held` ({path: (mode, blob id) the merge holds, or None}) that a
    merge of the commits `one` and `two` may hold as its own resolution: a regular file of one
    mode at `one`, at `two`, in the merge and at every merge base (`git merge-base --all`; an
    add, a delete, a rename or a mode change is none), whose content passes
    keeps_only_deletions. `(None, why)` when git cannot tell."""
    paths = sorted(p for p, entry in held.items() if entry is not None)
    if not paths:
        return set(), None
    r = cmd.call(oc.NOREPLACE + ['merge-base', '--all', one, two], root, env=env)
    if r.rc not in (0, 1):
        return None, 'git merge-base failed for %s and %s: %s' % (
            one[:10], two[:10], r.err or 'exit %s' % r.rc)
    bases = cmd.text(r.out).split()
    at = []
    for rev in [one, two] + bases:
        found, why = tree_entries(root, rev, paths, env)
        if found is None:
            return None, why
        at.append(found)
    fit = set()
    for p in paths:
        entries = [found[p] for found in at] + [held[p]]
        if (not bases or None in entries or len({e[0] for e in entries}) != 1
                or entries[0][0] not in oc.REGULAR_MODES):
            continue
        texts = []
        for entry in entries:
            blob, why = _read_blob(root, entry[1], env)
            if blob is None:
                return None, why
            texts.append(blob)
        if keeps_only_deletions(texts[0], texts[1], texts[-1], texts[2:-1]):
            fit.add(p)
    return fit, None


def still_judged(root, sha, parents, paths, env=None):
    """`(paths, None)`: `paths` (owner-only paths the commit `sha` changes) without those a
    two-parent merge holds as a resolution that only deletes (deletion_merge, on its committed
    copies); every path for any other commit. `(None, why)` when git cannot tell."""
    if len(parents) != 2 or not paths:
        return paths, None
    found, why = tree_entries(root, sha, paths, env)
    if found is None:
        return None, why
    fit, why = deletion_merge(root, parents[0], parents[1], found, env)
    if fit is None:
        return None, why
    return [p for p in paths if p not in fit], None
