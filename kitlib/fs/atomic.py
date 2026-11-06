"""kitlib.fs.atomic: write a whole text file in one step.

write_text puts the text in a uniquely named temp file in the target's own folder, then replaces
the target with it (kitlib.fs.replace, which waits out a reader holding the target open on
Windows). Two writers of one path never share a temp file; a reader sees the old file or the
new one, never half. The temp file is removed when the write or the replace fails. No fsync.
"""
import os
import tempfile

from kitlib.fs import replace as fsreplace


def write_text(path, text, *, encoding='utf-8', newline='\n', makedirs=False):
    """Write `text` to `path` through a temp file beside it, then replace `path` with it.
    makedirs=True makes the folder first."""
    folder = os.path.dirname(path) or '.'
    if makedirs:
        os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix='.%s.' % os.path.basename(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline=newline) as f:
            f.write(text)
        fsreplace.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
