"""A cache of `store.removed_records` (read by `find` through `store.removed`), so `find` does not
walk the whole store history on every call (KIT-BACKLOG-HISTORY-SLOW: that one
`git log --full-history` was about 0.6 s of a 1.4 s `find` on a 13k-commit store). A show of an
unknown id does not use it: `last_removal` stays a per-id log.

The cache is one JSON file in the repository's git dir, one per store folder, holding the exact
tip sha it was read at and the records `removed_records` returned there, in their order. Every
read verifies the key:
  - the stored tip is the tip now: the cached list is returned;
  - the stored tip is an ancestor of the tip now (`merge-base --is-ancestor`): `scan` runs over
    `<old>..<new>` only; when that range deleted no record file the list is unchanged and is
    kept under the new tip; when it deleted one the whole history is read again, so the order
    (git's own log order, merges included) is always the uncached one;
  - anything else (no file, another version or folder, a corrupt or unreadable file, a tip that
    is not a descendant, an unreadable tip): a full `scan`, as before the cache.
The file is written atomically (kitlib.fs.atomic). A failed write never fails the read: the
uncached result is returned. Nothing here raises for a cache problem; `scan` failures
(`Failure`) pass through as they did.
"""
import hashlib
import json
import os

from kitlib.fs import atomic
from kitlib.git import cmd

VERSION = 1


def _git_dir(root):
    out, _why = cmd.run_text(['rev-parse', '--git-dir'], root)
    if not out:
        return None
    return out if os.path.isabs(out) else os.path.join(root, out)


def _tip(root, rev):
    out, _why = cmd.run_text(['rev-parse', '--verify', '-q', rev + '^{commit}'], root)
    return out or None


def _is_ancestor(root, old, new):
    r = cmd.call(['merge-base', '--is-ancestor', old, new], root)
    return r.rc == 0


def _read(path, rel, make):
    """`(tip, items)` of a valid cache file, or None."""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if data['v'] != VERSION or data['rel'] != rel or not isinstance(data['tip'], str):
            return None
        items = [(s, make(sha, when, session, subject), text)
                 for s, sha, when, session, subject, text in data['items']]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return data['tip'], items


def _write(path, rel, tip, items):
    rows = [[s, r.sha, r.when, r.session, r.subject, text] for s, r, text in items]
    try:
        atomic.write_text(path, json.dumps({'v': VERSION, 'rel': rel, 'tip': tip, 'items': rows}))
    except (OSError, TypeError, ValueError):
        pass


def cached_removed(root, rev, rel, scan, make):
    """`scan(root, rev, rel)` (the uncached `[(stem, Removal, text)]`), served from the cache when
    it holds `rev`'s tip. `make(sha, when, session, subject)` builds a Removal."""
    tip = _tip(root, rev)
    gdir = _git_dir(root) if tip else None
    if not gdir:
        return scan(root, rev, rel)
    name = 'kit-removed-%s.json' % hashlib.sha1(rel.encode('utf-8')).hexdigest()[:12]
    path = os.path.join(gdir, name)
    held = _read(path, rel, make)
    if held and held[0] == tip:
        return held[1]
    if (held and _is_ancestor(root, held[0], tip)
            and not scan(root, '%s..%s' % (held[0], tip), rel)):
        _write(path, rel, tip, held[1])
        return held[1]
    items = scan(root, rev, rel)
    _write(path, rel, tip, items)
    return items
