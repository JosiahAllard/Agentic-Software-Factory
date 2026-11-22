"""kitlib.fs.quarantine: a crash's zero-filled file, told apart and set aside.

A file written through a temp file and a rename, with no fsync before the rename, can come back
from a power loss or an OS crash at its full length holding only NUL bytes: the rename reached the
disk and the data did not. No writer ever leaves that at a final path while it runs (its temp
file has another name), so a file of nothing but NUL bytes at a final path means its writer is
dead and the content is lost. all_nul() is that test. quarantine() moves such a file to
`<root>/_corrupt/<its path under root>`, never over an earlier copy, so the store reads on without
it and the bytes stay for the owner to look at; listed() names what is there.

read_json() reads a JSON object file that may be damaged: one that is not a JSON object (not
UTF-8, not JSON, all NUL bytes, another JSON type) is set aside by quarantine() and read as absent,
so its reader goes on and the bytes are kept. With retry_s, a file that fails and is not all NUL is
read once more after retry_s first (a write in flight); a NUL file is a dead writer's, never
retried. The hub store's open and the launcher's cache files (the fill watch's fill-watch.json, the
hub's push-outbox.json) read through it.
"""
import json
import os
import time

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


class SetAsideFailed(OSError):
    """read_json: the file will not parse (`why`) and could not be moved; it is left in place."""

    def __init__(self, path, why, err):
        super(SetAsideFailed, self).__init__('cannot read %s (%s) nor set it aside in %s: %s'
                                             % (path, why, FOLDER, err))
        self.path, self.why = path, why


def _parse(path):
    """(object, '') or (None, why) for the file at `path`; FileNotFoundError and any other
    OSError raised."""
    with open(path, 'rb') as f:
        data = f.read()
    try:
        obj = json.loads(data.decode('utf-8-sig'))
    except ValueError as e:
        return None, str(e)
    return (obj, '') if isinstance(obj, dict) else (None, 'not a JSON object')


def read_json(path, root, retry_s=0):
    """(object, None) for the JSON object in `path` (UTF-8, with or without a BOM); (None, None)
    when there is no file; (None, where) once a file that is not a JSON object has been moved by
    quarantine(path, root) to `where`. With retry_s > 0, a failing file that is not all NUL is read
    once more after retry_s first. OSError when the file cannot be read; SetAsideFailed (an
    OSError) when it will not parse and cannot be moved."""
    try:
        doc, why = _parse(path)
        if doc is None and retry_s > 0 and not all_nul(path):
            time.sleep(retry_s)         # once: a file that still fails is not a write in flight
            doc, why = _parse(path)
    except FileNotFoundError:
        return None, None
    if doc is not None:
        return doc, None
    try:
        return None, quarantine(path, root)
    except OSError as e:
        raise SetAsideFailed(path, why, e)
