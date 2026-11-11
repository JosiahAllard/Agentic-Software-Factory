"""Tests for kitlib.platform.linux.tcp: the listening rows of /proc/net/tcp and tcp6, read from
files written here as a little-endian kernel writes them."""
import os
import shutil
import sys
import tempfile
import unittest

from kitlib.platform.linux import tcp

LO4 = b"\x7f\0\0\x01"
LO6 = b"\0" * 15 + b"\x01"
HEAD = "  sl  local_address                         rem_address                           st\n"


def proc_files(case, v4, v6=None):
    """(tcp, tcp6) paths in a folder case removes when it ends, each the header and its text; a
    table whose text is None is not written."""
    d = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, d, True)
    paths = []
    for name, text in (("tcp", v4), ("tcp6", v6)):
        path = os.path.join(d, name)
        if text is not None:
            with open(path, "w", encoding="ascii") as f:
                f.write(HEAD + text)
        paths.append(path)
    return tuple(paths)


@unittest.skipUnless(sys.byteorder == "little", "the rows below are /proc/net as a little-endian kernel writes it")
class ProcNet(unittest.TestCase):
    """Linux's listener table: /proc/net/tcp and tcp6, whose rows name no owner."""

    def test_the_listening_rows_are_their_ends_with_no_owner(self):
        paths = proc_files(self,
                           "   0: 0100007F:2685 00000000:0000 0A 00000000\n"
                           "   1: 0100007F:2686 0100007F:9C40 01 00000000\n"
                           "   2: 00000000:2687 00000000:0000 0A 00000000\n",
                           "   0: 00000000000000000000000001000000:2688 00000000000000000000000000000000:0000 0A 0\n"
                           "   1: 0000000000000000FFFF00000100007F:2689 00000000000000000000000000000000:0000 0A 0\n"
                           "   2: 00000000000000000000000001000000:268A 00000000000000000000000001000000:9C40 01 0\n")
        self.assertEqual(tcp.proc_net_listeners(paths),
                         [((4, LO4, 0x2685), None), ((4, b"\0" * 4, 0x2687), None),
                          ((6, LO6, 0x2688), None), ((4, LO4, 0x2689), None)])

    def test_no_tcp6_table_is_no_ipv6_and_no_tcp_table_raises(self):
        v4, v6 = proc_files(self, "   0: 0100007F:2685 00000000:0000 0A 00000000\n")
        self.assertEqual(tcp.proc_net_listeners((v4, v6)), [((4, LO4, 0x2685), None)])
        with self.assertRaises(OSError):
            tcp.proc_net_listeners((v6, v6))


if __name__ == "__main__":
    unittest.main()
