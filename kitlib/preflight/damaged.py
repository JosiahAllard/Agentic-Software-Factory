"""preflight's `damaged` lines: git's own files that a crash zero-filled (kitlib.fs.quarantine).

A power loss or an OS crash can leave a file git renamed into place at its full length holding
only NUL bytes. git then fails in ways that do not name the file (`fatal: not a git repository`,
a bad object, an unknown revision), so preflight names it itself: scan() walks up from a folder to
its `.git` and reads the files every git command needs. It runs no git itself, so it still works
when git cannot read the checkout at all (preflight runs it after its checkout lookup, whether or
not that found one), and writes nothing: the owner restores what it names.
"""
import os

from kitlib.fs import quarantine
from kitlib.git import defaults

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


def files(start):
    """The git files the checkout at or above start reads first, those that exist, in the order
    checked: the `.git` file and `commondir` (each a pointer), the git folder's HEAD and index,
    then the common folder's HEAD, index, packed-refs, every file under refs/ and each worktree's
    HEAD and index. The folders are read as kitlib.git.defaults.git_dirs reads them; when it finds
    none (a zeroed pointer among the reasons), only the two pointers are listed."""
    entry = _git_entry(start)
    if entry is None:
        return []
    top = os.path.dirname(entry)
    out = [entry] if os.path.isfile(entry) else []
    found = defaults.git_dirs(top)
    if found is None:
        gitdir = defaults.git_dir(top)
        cd = os.path.join(gitdir, 'commondir') if gitdir else None
        return out + ([cd] if cd and os.path.isfile(cd) else [])
    gitdir, common = found
    cd = os.path.join(gitdir, 'commondir')
    if os.path.isfile(cd):
        out.append(cd)
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
