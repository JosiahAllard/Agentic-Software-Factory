"""kitlib.fs.atomic: write a whole file in one step.

write_text and write_bytes put the content in a uniquely named temp file in the target's own folder,
then replace the target with it (kitlib.fs.replace, which waits out a reader holding the target open
on Windows). Two writers of one path never share a temp file; a reader sees the old file or the new
one, never half. The temp file is removed (kitlib.fs.replace.remove) when the write or the replace
fails. fsync=True flushes the temp file to disk before the replace; the default does not. Without
it a power loss or an OS crash can leave the target at its new length holding only NUL bytes (the
rename reached the disk, the data did not; kitlib.fs.quarantine). `wait` bounds the replace's
retries (default kitlib.fs.replace.WAIT).

The temp file comes from tempfile.mkstemp, so on Linux the written file has mode 0600 (owner
read and write only), not the umask's usual 0644 that open() gives; Windows has no such bits.
"""
import os
import tempfile

from kitlib.fs import replace as fsreplace


def _write(path, mode, data, fsync, makedirs, prefix, wait, **open_kw):
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
        fsreplace.replace(tmp, path, wait=wait)
    except BaseException:
        fsreplace.remove(tmp)
        raise


def write_text(path, text, *, encoding='utf-8', newline='\n', makedirs=False, fsync=False, prefix=None,
               wait=None):
    """Write `text` to `path` through a temp file beside it, then replace `path` with it.
    makedirs=True makes the folder first; `prefix` names the temp file's start (default
    '.<file name>.'); `wait` is the seconds a refused replace is retried."""
    _write(path, 'w', text, fsync, makedirs, prefix, wait, encoding=encoding, newline=newline)


def write_bytes(path, data, *, makedirs=False, fsync=False, prefix=None, wait=None):
    """write_text for bytes: written as given, no encoding or newline translation."""
    _write(path, 'wb', data, fsync, makedirs, prefix, wait)
