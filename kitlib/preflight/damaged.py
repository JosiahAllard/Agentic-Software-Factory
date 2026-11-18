"""preflight's `damaged` lines: git's own files that a crash zero-filled (kitlib.fs.quarantine).

A power loss or an OS crash can leave a file git renamed into place at its full length holding
only NUL bytes. git then fails in ways that do not name the file (`fatal: not a git repository`,
a bad object, an unknown revision), so preflight looks before it asks git anything: scan() walks
up from a folder to its `.git` and reads the files every git command needs. It runs no git, so
it still works when git cannot read the checkout at all, and writes nothing: the owner restores
what it names.
"""
import os

from kitlib.fs import quarantine

LINE = "damaged   FAIL %s all NUL bytes (a crash's zero-fill): restore it"
FILES = ('HEAD', 'index')       # a git folder's own: the main checkout's or a worktree's
COMMON = ('HEAD', 'index', 'packed-refs')


def _git_entry(start):
    """The `.git` (a folder, or a worktree's file) of the first folder at or above start, or
    None."""
    here = os.path.abspath(start)
    while True:
        p = os.path.join(here, '.git')
        if os.path.exists(p):
            return p
        up = os.path.dirname(here)
        if up == here:
            return None
        here = up


def _pointer(path):
    """What a `.git` file or a `commondir` file names, resolved against its own folder; None
    when it cannot be read or names nothing (a NUL byte in it among them)."""
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            text = f.read().strip()
    except OSError:
        return None
    if text.startswith('gitdir:'):
        text = text[len('gitdir:'):].strip()
    if not text or '\0' in text:
        return None
    return os.path.normpath(os.path.join(os.path.dirname(path), text))


def files(start):
    """The git files the checkout at or above start reads first, those that exist, in the order
    checked: the `.git` file and `commondir` (each a pointer), the git folder's HEAD and index,
    then the common folder's HEAD, index, packed-refs, every file under refs/ and each worktree's
    HEAD and index."""
    entry = _git_entry(start)
    if entry is None:
        return []
    out, gitdir = [], entry
    if os.path.isfile(entry):
        out.append(entry)
        gitdir = _pointer(entry)
        if gitdir is None:
            return out
    common = gitdir
    cd = os.path.join(gitdir, 'commondir')
    if os.path.isfile(cd):
        out.append(cd)
        common = _pointer(cd) or gitdir
    names = [os.path.join(gitdir, n) for n in FILES]
    names += [os.path.join(common, n) for n in COMMON]
    for folder, dirs, fs in os.walk(os.path.join(common, 'refs')):
        dirs.sort()
        names += [os.path.join(folder, n) for n in sorted(fs)]
    wt = os.path.join(common, 'worktrees')
    try:
        trees = sorted(os.listdir(wt))
    except OSError:
        trees = []
    names += [os.path.join(wt, t, n) for t in trees for n in FILES]
    seen = set(out)
    for p in names:
        if p not in seen and os.path.isfile(p):
            seen.add(p)
            out.append(p)
    return out


def scan(start):
    """The `damaged` lines for the checkout at or above start: one per git file of only NUL
    bytes (files), [] when there is none or no checkout is found."""
    return [LINE % p.replace('\\', '/') for p in files(start) if quarantine.all_nul(p)]
