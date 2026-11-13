"""A connection to a Unix stream socket served by one known process (pid, ctime), on Linux: the
peer half of Claude Code's cross-session messaging, whose sessions listen on a socket under a
cc-socks folder (hosts/claude/adapter/peer.py).

  connect(path, pid, ctime, timeout)   a connected socket whose server is that process, or
                                       ProcError(code) with nothing sent

Before the connect: the path is absolute, holds no NUL and fits sun_path (MAX_PATH bytes); its
folder (lstat, never followed) is a directory this user owns that no group or other may write, so
nobody else can swap the socket inside it; the socket itself (lstat) is a socket this user owns.
After it: SO_PEERCRED names the server's pid and uid, which must be the expected pid and this user,
and that pid's ctime now (kitlib.platform.linux.process.proc_ctime) must be the expected ctime, so a
pid that changed after the caller read that ctime is refused. It does not catch a pid reused within
the second an lstart procStart names: the caller's ctime is then already the reused process's
(kitlib.launcher.pipes). The peer credentials and the ctime are the proof;
the folder and owner checks only keep the path from naming another user's socket.

The codes: invalid-socket-path, invalid-socket-peer, socket-missing, socket-folder-unsafe,
socket-not-owned, socket-connect-failed, socket-peer-unreadable, socket-owner-mismatch,
socket-process-identity-mismatch. This module imports on any platform; connect needs Linux.
"""
import os
import socket
import stat
import struct

from kitlib.platform import ProcError
from kitlib.platform.linux import process

MAX_PATH = 107                  # sun_path is 108 bytes with its NUL
_CRED = struct.Struct('3i')     # struct ucred: pid, uid, gid


def check_path(path):
    """ProcError('invalid-socket-path') unless path is an absolute str with no NUL whose bytes fit
    sun_path."""
    if type(path) is not str or not path.startswith('/') or '\0' in path:
        raise ProcError('invalid-socket-path')
    try:
        raw = os.fsencode(path)
    except (UnicodeError, ValueError):
        raise ProcError('invalid-socket-path') from None
    if len(raw) > MAX_PATH:
        raise ProcError('invalid-socket-path')


def _check_files(path, uid):
    try:
        folder = os.lstat(os.path.dirname(path))
        target = os.lstat(path)
    except OSError:
        raise ProcError('socket-missing') from None
    if not stat.S_ISDIR(folder.st_mode) or folder.st_uid != uid or folder.st_mode & 0o022:
        raise ProcError('socket-folder-unsafe')
    if not stat.S_ISSOCK(target.st_mode) or target.st_uid != uid:
        raise ProcError('socket-not-owned')


def _check_peer(sock, pid, ctime, uid):
    try:
        peer_pid, peer_uid, _gid = _CRED.unpack(
            sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CRED.size))
    except (OSError, struct.error):
        raise ProcError('socket-peer-unreadable') from None
    if peer_uid != uid:
        raise ProcError('socket-owner-mismatch')
    if peer_pid != pid or process.proc_ctime(pid) != ctime:
        raise ProcError('socket-process-identity-mismatch')


def connect(path, pid, ctime, timeout):
    """A connected AF_UNIX stream socket on path (its timeout set to `timeout`) whose server is
    the process (pid, ctime) of this user; ProcError(code) otherwise, the socket closed and no byte
    sent. The caller closes the socket it gets."""
    check_path(path)
    if (type(pid) is not int or not 0 < pid <= 0xffffffff or type(ctime) is not int or ctime <= 0
            or type(timeout) not in (int, float) or not 0 < timeout):
        raise ProcError('invalid-socket-peer')
    uid = os.getuid()
    _check_files(path, uid)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        try:
            sock.connect(path)
        except OSError:
            raise ProcError('socket-connect-failed') from None
        _check_peer(sock, pid, ctime, uid)
    except BaseException:
        sock.close()
        raise
    return sock
