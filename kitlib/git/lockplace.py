"""kitlib.git.lockplace: where a file's write lock (kitlib.fs.writelock) goes.

`lock_path(path, prefix)` names the lock file for the file at `path`:

- in a git checkout (kitlib.git.repo.classify of the file's folder, MAIN or LINKED):
  `<prefix>-<name>-<hash>.lock` in the repository's git common dir, never in the work tree, so a
  write leaves no untracked file in the checkout; the hash is of the file's real, case-folded path,
  so every spelling of one file names one lock;
- outside any checkout (NONE): `.<name>.lock` beside the file;
- git cannot tell (UNKNOWN): NoPlace.

`prefix` names the feature (`kit-ledger`, `kit-pending`), so two features' locks on one file differ.
Nothing here makes the lock file; kitlib.fs.writelock does, when it is taken.
"""
import hashlib
import os

from . import repo as gitrepo


class NoPlace(Exception):
    """git cannot tell whether the file is in a checkout; `.detail` is what git said."""

    def __init__(self, path, detail):
        Exception.__init__(self, 'cannot tell whether %s is in a git checkout: %s' % (path, detail))
        self.detail = detail


def lock_path(path, prefix):
    """The write lock's file for the file at `path` (the module docstring says where). NoPlace when
    git cannot tell."""
    folder, name = os.path.split(os.path.abspath(path))
    place = gitrepo.classify(folder)
    if place.kind == gitrepo.NONE:
        return os.path.join(folder, '.%s.lock' % name)
    if place.kind not in (gitrepo.MAIN, gitrepo.LINKED) or not place.common:
        raise NoPlace(path, place.detail)
    name = os.path.normcase(name)
    key = os.path.join(gitrepo.real(folder), name)
    tag = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in name) or 'file'
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]
    return os.path.join(place.common, '%s-%s-%s.lock' % (prefix, tag, digest))
