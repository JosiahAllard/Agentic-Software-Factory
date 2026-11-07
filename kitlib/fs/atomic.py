"""kitlib.fs.atomic: write a whole file in one step.

write_text and write_bytes put the content in a uniquely named temp file in the target's own folder,
then replace the target with it (kitlib.fs.replace, which waits out a reader holding the target open
on Windows). Two writers of one path never share a temp file; a reader sees the old file or the new
one, never half. The temp file is removed (kitlib.fs.replace.remove) when the write or the replace
fails. fsync=True flushes the temp file to disk before the replace; the default does not.
"""
import os
import tempfile

from kitlib.fs import replace as fsreplace


def _write(path, mode, data, fsync, makedirs, prefix, **open_kw):
    folder = os.path.dirname(path) or '.'
    if makedirs:
        os.makedirs(folder, exist_ok=True)
    if prefix is None:
        prefix = '.%s.' % os.path.basename(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=prefix, suffix='.tmp')
    try:
        with os.fdopen(fd, mode, **open_kw) as f:
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        fsreplace.replace(tmp, path)
    except BaseException:
        fsreplace.remove(tmp)
        raise


def write_text(path, text, *, encoding='utf-8', newline='\n', makedirs=False, fsync=False, prefix=None):
    """Write `text` to `path` through a temp file beside it, then replace `path` with it.
    makedirs=True makes the folder first; `prefix` names the temp file's start (default
    '.<file name>.')."""
    _write(path, 'w', text, fsync, makedirs, prefix, encoding=encoding, newline=newline)


def write_bytes(path, data, *, makedirs=False, fsync=False, prefix=None):
    """write_text for bytes: written as given, no encoding or newline translation."""
    _write(path, 'wb', data, fsync, makedirs, prefix)
