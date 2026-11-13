"""kitlib.platform.linux.unixsock: the refusals of connect before and after the connect. The path
and file checks run on scripted lstat results on any platform; the peer check on a scripted
SO_PEERCRED; Real runs them on real files, symlinks and a socket this process serves."""
import os
import shutil
import socket
import stat
import tempfile
import types
import unittest
from unittest import mock

from tests.platforms import posix_only

from kitlib.platform import ProcError
from kitlib.platform.linux import unixsock

ME = 1000
FOLDER = '/run/user/1000/cc-socks'
PATH = FOLDER + '/9.sock'
CTIME = 7 << 64 | 360037


def st(kind, uid=ME, mode=0o700):
    return types.SimpleNamespace(st_mode=kind | mode, st_uid=uid)


class Path(unittest.TestCase):
    def code(self, path):
        with self.assertRaises(ProcError) as caught:
            unixsock.check_path(path)
        return caught.exception.code

    def test_a_path_that_fits_sun_path_passes(self):
        unixsock.check_path('/' + 'a' * (unixsock.MAX_PATH - 1))

    def test_a_path_past_sun_path_relative_or_with_a_nul_is_refused(self):
        for bad in ('/' + 'a' * unixsock.MAX_PATH, 'run/9.sock', '/run/\0/9.sock', b'/run/9.sock', None):
            self.assertEqual(self.code(bad), 'invalid-socket-path', bad)


class Files(unittest.TestCase):
    """_check_files on scripted lstat results: lstat never follows a link, so a symlink is seen
    as one."""

    def code(self, folder=None, target=None):
        stats = {FOLDER: folder, PATH: target}

        def lstat(p):
            if stats.get(p) is None:
                raise FileNotFoundError(2, 'missing', p)
            return stats[p]
        with mock.patch.object(unixsock.os, 'lstat', lstat), self.assertRaises(ProcError) as caught:
            unixsock._check_files(PATH, ME)
        return caught.exception.code

    def test_this_users_socket_in_this_users_private_folder_passes(self):
        stats = {FOLDER: st(stat.S_IFDIR), PATH: st(stat.S_IFSOCK, mode=0o755)}
        with mock.patch.object(unixsock.os, 'lstat', stats.__getitem__):
            unixsock._check_files(PATH, ME)

    def test_a_missing_folder_or_socket_is_missing(self):
        self.assertEqual(self.code(None, st(stat.S_IFSOCK)), 'socket-missing')
        self.assertEqual(self.code(st(stat.S_IFDIR), None), 'socket-missing')

    def test_a_symlinked_folder_or_one_another_may_write_or_own_is_unsafe(self):
        for folder in (st(stat.S_IFLNK, mode=0o777), st(stat.S_IFDIR, mode=0o720),
                       st(stat.S_IFDIR, mode=0o702), st(stat.S_IFDIR, uid=ME + 1)):
            self.assertEqual(self.code(folder, st(stat.S_IFSOCK)), 'socket-folder-unsafe', folder)

    def test_a_symlinked_socket_a_file_or_another_users_socket_is_not_owned(self):
        for target in (st(stat.S_IFLNK, mode=0o777), st(stat.S_IFREG), st(stat.S_IFSOCK, uid=ME + 1)):
            self.assertEqual(self.code(st(stat.S_IFDIR), target), 'socket-not-owned', target)


class PeerSock(object):
    def __init__(self, value):
        self.value = value

    def getsockopt(self, level, name, size):
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class Peer(unittest.TestCase):
    """_check_peer on a scripted SO_PEERCRED (socket.SO_PEERCRED exists only on Linux)."""

    def code(self, value, pid=9, now=CTIME):
        with mock.patch.object(unixsock.socket, 'SO_PEERCRED', 17, create=True), \
                mock.patch.object(unixsock.process, 'proc_ctime', lambda p: now), \
                self.assertRaises(ProcError) as caught:
            unixsock._check_peer(PeerSock(value), pid, CTIME, ME)
        return caught.exception.code

    def cred(self, pid=9, uid=ME):
        return unixsock._CRED.pack(pid, uid, ME)

    def test_the_expected_process_of_this_user_passes(self):
        with mock.patch.object(unixsock.socket, 'SO_PEERCRED', 17, create=True), \
                mock.patch.object(unixsock.process, 'proc_ctime', lambda p: CTIME):
            unixsock._check_peer(PeerSock(self.cred()), 9, CTIME, ME)

    def test_credentials_that_cannot_be_read_are_unreadable(self):
        for value in (OSError(92, 'no option'), b'\0' * 4):
            self.assertEqual(self.code(value), 'socket-peer-unreadable', value)

    def test_a_server_of_another_user_is_an_owner_mismatch(self):
        self.assertEqual(self.code(self.cred(uid=ME + 1)), 'socket-owner-mismatch')

    def test_another_pid_or_the_pid_at_another_ctime_is_an_identity_mismatch(self):
        self.assertEqual(self.code(self.cred(pid=10)), 'socket-process-identity-mismatch')
        self.assertEqual(self.code(self.cred(), now=CTIME + 1), 'socket-process-identity-mismatch')
        self.assertEqual(self.code(self.cred(), now=None), 'socket-process-identity-mismatch')


@posix_only('a Unix socket and its symlinks: Windows CPython has no AF_UNIX')
class Real(unittest.TestCase):
    """connect on real files: a socket this process serves in a 0700 temp folder, reached through
    a symlinked socket, a symlinked folder, a plain file and a missing name. Each refusal leaves
    no connection on the server."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='kit-us-')
        self.addCleanup(shutil.rmtree, self.root, True)
        self.folder = os.path.join(self.root, 'socks')
        os.mkdir(self.folder, 0o700)
        os.chmod(self.folder, 0o700)
        self.path = os.path.join(self.folder, 's.sock')
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.server.close)
        self.server.bind(self.path)
        self.server.listen(2)
        self.server.setblocking(False)
        from kitlib.proc.process import proc_ctime
        self.ctime = proc_ctime(os.getpid())

    def code(self, path):
        with self.assertRaises(ProcError) as caught:
            unixsock.connect(path, os.getpid(), self.ctime, 2)
        with self.assertRaises(BlockingIOError):
            self.server.accept()
        return caught.exception.code

    def test_the_socket_itself_connects_to_this_process(self):
        sock = unixsock.connect(self.path, os.getpid(), self.ctime, 2)
        sock.close()

    def test_a_symlink_to_the_socket_is_not_owned(self):
        link = os.path.join(self.folder, 'link.sock')
        os.symlink(self.path, link)
        self.assertEqual(self.code(link), 'socket-not-owned')

    def test_the_socket_through_a_symlinked_folder_is_unsafe(self):
        link = os.path.join(self.root, 'linkdir')
        os.symlink(self.folder, link)
        self.assertEqual(self.code(os.path.join(link, 's.sock')), 'socket-folder-unsafe')

    def test_a_plain_file_is_not_owned_and_a_missing_name_is_missing(self):
        plain = os.path.join(self.folder, 'plain.sock')
        open(plain, 'w').close()
        self.assertEqual(self.code(plain), 'socket-not-owned')
        self.assertEqual(self.code(os.path.join(self.folder, 'none.sock')), 'socket-missing')


if __name__ == '__main__':
    unittest.main()
