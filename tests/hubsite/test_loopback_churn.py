"""The listener tests' loopback churn (record KIT-TEST-LOOPBACK-CHURN): httpfix's send()
leaves no connection in TIME_WAIT, the TIME_WAIT tables are read on Windows and Linux, and the
module guard (httpfix.ChurnGuard) counts only its own listeners' ports and skips when it cannot
read the table."""
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

from kitlib.hubsite import httpd
from kitlib.proc import tcp
from tests.hubsite import httpfix
from tests.platforms import windows_only
from tests.proc.test_tcp import LO4, LO6, v4_row, v6_row

REQUESTS = 200                  # a sweep's worth: one listener asked this many times
LEFT = 5                        # at most this many of them in TIME_WAIT: closed the ordinary
#                                 way they left about one each (214 of 200 on 11-04)


def on_port(ends, port):
    return sum(1 for l, r in ends if l[2] == port or r[2] == port)


class SendChurnTest(httpfix.ListenerCase):
    def test_a_sweep_of_requests_leaves_its_listener_no_time_wait(self):
        try:
            before = httpfix.time_wait_ends()
        except OSError as e:
            self.skipTest('the TCP table cannot be read: %s' % e)
        lis = self.listener()
        bodies = {httpfix.get(lis.port, '/', self.agent).body for _ in range(REQUESTS)}
        self.assertEqual(len(bodies), 1)                # every request was answered, alike
        self.assertNotEqual(bodies, {b''})
        self.close(lis)
        left = on_port(httpfix.time_wait_ends() - before, lis.port)
        self.assertLessEqual(left, LEFT, '%d of %d connections left in TIME_WAIT'
                             % (left, REQUESTS))
        self.assertEqual(self.errors(), [])

    def test_the_reader_sees_the_time_wait_raw_leaves(self):
        # every other live check is an upper bound, which a reader that returned nothing would
        # pass: raw() closes first, so each call leaves its connection in TIME_WAIT (3 of 3 on
        # Windows, 11-04), and the reader must see them
        try:
            before = httpfix.time_wait_ends()
        except OSError as e:
            self.skipTest('the TCP table cannot be read: %s' % e)
        lis = self.listener()
        for _ in range(3):
            self.assertNotEqual(httpfix.raw(lis.port, b'GET / HTTP/1.1\r\nHost: %s:%d\r\n\r\n'
                                            % (httpd.LOOPBACK.encode(), lis.port)), b'')
        self.close(lis)
        self.assertGreaterEqual(on_port(httpfix.time_wait_ends() - before, lis.port), 1)


@unittest.skipUnless(sys.byteorder == 'little', 'the rows below are as a little-endian kernel '
                     'writes /proc/net')
class TablesTest(unittest.TestCase):
    """Both platforms' TIME_WAIT rows, from fixed tables, so each is tested on either."""
    HEAD = '  sl  local_address                         rem_address                           st\n'

    def proc_files(self, v4, v6):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        paths = []
        for name, text in (('tcp', v4), ('tcp6', v6)):
            path = os.path.join(d, name)
            with open(path, 'w', encoding='ascii') as f:
                f.write(self.HEAD + text)
            paths.append(path)
        return tuple(paths)

    def test_linux_reads_the_loopback_time_wait_rows_of_both_tables(self):
        paths = self.proc_files(
            '   0: 0100007F:2685 0100007F:9C40 06 00000000\n'     # loopback, TIME_WAIT
            '   1: 0100007F:2686 0100007F:9C41 01 00000000\n'     # established
            '   2: 0A000001:2687 0A000002:9C42 06 00000000\n',    # TIME_WAIT, not loopback
            '   0: 00000000000000000000000001000000:2688 00000000000000000000000001000000:9C43 06 0\n'
            '   1: 0000000000000000FFFF00000100007F:2689 0000000000000000FFFF00000100007F:9C44 06 0\n')
        with mock.patch('kitlib.platform.NAME', 'linux'), mock.patch.object(tcp, 'PROC_NET', paths):
            self.assertEqual(httpfix.time_wait_ends(), {
                ((4, LO4, 0x2685), (4, LO4, 0x9C40)),
                ((6, LO6, 0x2688), (6, LO6, 0x9C43)),
                ((4, LO4, 0x2689), (4, LO4, 0x9C44))})

    @windows_only('the Windows TCP table is read through iphlpapi')
    def test_windows_reads_the_loopback_time_wait_rows_of_both_tables(self):
        from kitlib.platform.windows import win32
        other = b'\x0a\0\0\x01'
        tables = {
            win32.AF_INET: struct.pack('<I', 3) + v4_row(11, LO4, 5000, LO4, 6000, 0)
            + v4_row(5, LO4, 5001, LO4, 6001, 10) + v4_row(11, other, 5002, other, 6002, 0),
            win32.AF_INET6: struct.pack('<I', 1) + v6_row(11, LO6, 5003, LO6, 6003, 0)}
        with mock.patch('kitlib.platform.NAME', 'windows'), \
                mock.patch('kitlib.platform.windows.tcp.table', lambda family: tables[family]):
            self.assertEqual(httpfix.time_wait_ends(), {
                ((4, LO4, 5000), (4, LO4, 6000)), ((6, LO6, 5003), (6, LO6, 6003))})


class GuardTest(unittest.TestCase):
    """httpfix.ChurnGuard on a stand-in table: what it counts, when it fails, when it skips."""

    def setUp(self):
        env = mock.patch.dict(os.environ)       # these guards add no line to a run's churn log
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(httpfix.CHURN_LOG, None)

    def end(self, port):
        return (4, LO4, port)

    def test_it_counts_only_new_rows_on_its_listeners_ports(self):
        table = {(self.end(7000), self.end(50000))}         # in TIME_WAIT before the module
        guard = httpfix.ChurnGuard('m', 2, read=lambda: set(table))
        start = httpd.Listener.start
        guard.start()
        self.addCleanup(setattr, httpd.Listener, 'start', start)
        self.assertIsNot(httpd.Listener.start, start)
        guard.ports.update((7000, 7001))
        table |= {(self.end(7001), self.end(50001)),          # the listener's end
                  (self.end(50002), self.end(7000)),          # the client's end
                  (self.end(9000), self.end(50003))}          # another program's
        self.assertEqual(guard.check(), 2)
        self.assertIs(httpd.Listener.start, start, 'check() puts Listener.start back')

    def test_more_than_the_bound_fails_the_module(self):
        table = set()
        guard = httpfix.ChurnGuard('m', 1, read=lambda: set(table))
        guard.start()
        guard.ports.add(7000)
        table |= {(self.end(7000), self.end(p)) for p in (50000, 50001)}
        with self.assertRaisesRegex(AssertionError, 'm left 2 .*bound 1'):
            guard.check()

    def test_a_table_it_cannot_read_skips_never_passes(self):
        def broken():
            raise OSError('no table')
        for reads in ([broken], [set, broken]):
            with self.subTest(fails_on=len(reads)):
                calls = list(reads)
                guard = httpfix.ChurnGuard('m', 0, read=lambda: calls.pop(0)())
                guard.start()
                with self.assertRaisesRegex(unittest.SkipTest, 'no table'):
                    guard.check()

    def test_the_listener_start_it_wraps_notes_the_port(self):
        guard = httpfix.ChurnGuard('m', 0, read=set)
        guard.start()
        try:
            case = httpfix.ListenerCase('run')
            case.setUp()
            try:
                lis = case.listener()
                self.assertIn(lis.port, guard.ports)
            finally:
                case.doCleanups()
        finally:
            self.assertEqual(guard.check(), 0)


if __name__ == '__main__':
    unittest.main()
