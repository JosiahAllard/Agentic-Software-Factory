"""kitlib.fs.quarantine: a crash's zero-filled file, told apart and set aside.

A file written through a temp file and a rename, with no fsync before the rename, can come back
from a power loss or an OS crash at its full length holding only NUL bytes: the rename reached the
disk and the data did not. No writer ever leaves that at a final path while it runs (its temp
file has another name), so a file of nothing but NUL bytes at a final path means its writer is
dead and the content is lost. all_nul() is that test. quarantine() moves such a file to
`<root>/_corrupt/<its path under root>`, never over an earlier copy, so the store reads on without
it and the bytes stay for the owner to look at; listed() names what is there.
"""
import os

from kitlib.fs import replace as fsreplace

FOLDER = '_corrupt'     # under a store's root
BLOCK = 65536           # bytes read at a time


def all_nul(path):
    """True when `path` is a non-empty file whose every byte is NUL; False for anything else,
    an empty file, a folder or a path that cannot be read."""
    try:
        with open(path, 'rb') as f:
            seen = False
            while True:
                block = f.read(BLOCK)
                if not block:
                    return seen
                if block.count(0) != len(block):
                    return False
                seen = True
    except OSError:
        return False


def quarantine(path, root):
    """Move `path` (a file under `root`) to `<root>/_corrupt/<its relative path>`, with `.1`,
    `.2`, ... added when that name is taken, and return where it went. An OSError (the move
    refused, the folder not made) is raised to the caller with `path` left where it was."""
    rel = os.path.relpath(path, root)
    target = os.path.join(root, FOLDER, rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    dst, n = target, 0
    while not fsreplace.place_new(path, dst):
        n += 1
        dst = '%s.%d' % (target, n)
    return dst


def listed(root):
    """The files under `<root>/_corrupt/`, as sorted paths relative to it with '/' separators;
    [] when there are none or the folder cannot be read."""
    base = os.path.join(root, FOLDER)
    out = []
    for folder, _dirs, files in os.walk(base):
        for name in files:
            out.append(os.path.relpath(os.path.join(folder, name), base).replace(os.sep, '/'))
    return sorted(out)
