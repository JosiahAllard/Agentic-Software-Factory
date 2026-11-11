"""kitlib.fs.writelock: one writer of a file at a time, across processes and threads.

A caller holds `locked(lock, ...)` from its read of the file to its write, so two writers never
both read the same text and neither replace loses the other's change. The lock is
kitlib.fs.treelock's OS lock (a byte-range lock on Windows, a flock on Linux) on the lock file
`lock`, which the caller names (git's lockplace module says where a file's lock goes);
writelock makes it and its folder, and asks git nothing.

The system drops the lock when its holder exits, so a dead holder never blocks. A busy lock is
tried every `pause` seconds for `wait` seconds, then Busy. The lock nests within one thread (the
same lock file taken again by its holder, under any spelling, just counts) and excludes every other
thread. The lock file is never removed (a remove and a re-create would give two files, each with a
holder).

Callers word their own refusals: every error here carries what they need (`.lock`, `.detail`).
"""
import contextlib
import os
import threading
import time

from .treelock import TreeBusy, TreeLock, TreeLockError

PAUSE = 0.05    # seconds between tries of a busy lock

_held = threading.local()    # .locks: {lock file identity: [TreeLock, depth]} of this thread


class WriteLockError(Exception):
    """The write lock could not be found, made or taken; `.lock` is its file (None: not known)."""

    def __init__(self, message, lock=None):
        Exception.__init__(self, message)
        self.lock = lock


class CannotMake(WriteLockError):
    """The lock file (or its folder) could not be created; `.detail` is the OSError."""

    def __init__(self, lock, detail):
        WriteLockError.__init__(self, 'cannot make %s: %s' % (lock, detail), lock)
        self.detail = detail


class CannotTake(WriteLockError):
    """The OS lock failed for a reason other than another holder; `.detail` is the TreeLockError."""

    def __init__(self, lock, detail):
        WriteLockError.__init__(self, str(detail), lock)
        self.detail = detail


class Busy(WriteLockError):
    """Another holder kept the lock for the whole wait; `.holder` is `held by pid N since T`,
    `.wait` the seconds waited."""

    def __init__(self, lock, holder, wait):
        WriteLockError.__init__(self, '%s is %s; waited %s s' % (lock, holder, wait), lock)
        self.holder = holder
        self.wait = wait


def _identity(lock):
    """The lock file's identity (volume, file index), so every spelling of one path is one key;
    None when the file does not exist."""
    try:
        st = os.stat(lock)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _locks():
    if not hasattr(_held, 'locks'):
        _held.locks = {}
    return _held.locks


def held(lock):
    """True when this thread holds the write lock on the lock file `lock`. False when it does not
    and when the lock file does not exist yet."""
    key = _identity(lock)
    return key is not None and key in _locks()


@contextlib.contextmanager
def locked(lock, wait, what='write', pause=None):
    """Hold the write lock on the lock file `lock` (made, with its folder, when missing) for the
    block; yields `lock`. Taken already by this thread: nests. Busy: tried every `pause` (default
    PAUSE) for `wait` seconds, then Busy. `what` is the holder's record's `what`, for another
    taker's message."""
    try:
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        with open(lock, 'ab'):          # exists before its identity is read; a holder's record stays
            pass
    except OSError as e:
        raise CannotMake(lock, e)
    key = _identity(lock)
    mine = _locks()
    if key in mine:
        mine[key][1] += 1
        try:
            yield lock
        finally:
            mine[key][1] -= 1
        return
    pause = PAUSE if pause is None else pause
    deadline = time.monotonic() + wait
    tl = TreeLock(lock, what=what)
    while True:
        try:
            tl.acquire()
            break
        except TreeBusy as e:
            if time.monotonic() >= deadline:
                raise Busy(lock, str(e), wait)
        except TreeLockError as e:
            raise CannotTake(lock, e)
        time.sleep(pause)
    mine[key] = [tl, 1]
    try:
        yield lock
    finally:
        del mine[key]
        tl.release()
