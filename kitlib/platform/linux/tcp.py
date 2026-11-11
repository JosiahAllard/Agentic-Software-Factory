"""Linux's listening TCP sockets, read from /proc/net/tcp and tcp6 (kitlib.proc.tcp's listeners
on Linux). Those tables name no owner, so each listener's pid is None.

An end is (family, address bytes, port), as kitlib.proc.tcp names it: an IPv4-mapped IPv6
address (::ffff:a.b.c.d) is named as the IPv4 end it is.
"""
import struct

_MAPPED = b"\0" * 10 + b"\xff\xff"
PROC_NET = ("/proc/net/tcp", "/proc/net/tcp6")   # Linux: the IPv4 table, then the IPv6 one
_PROC_LISTEN = "0A"                     # TCP_LISTEN in a /proc/net row's st column


def _proc_end(text, family):
    """The end a /proc/net row names as `address:port` in hex: the address in 32-bit words, each
    in the kernel's own byte order, the port a plain hex number."""
    addr, port = text.rsplit(":", 1)
    raw = b"".join(struct.pack("=I", int(addr[i:i + 8], 16)) for i in range(0, len(addr), 8))
    if family == 6 and raw[:12] == _MAPPED:
        return (4, raw[12:], int(port, 16))
    return (family, raw, int(port, 16))


def proc_net_listeners(paths=None):
    """[(local, None)] of every listening row of Linux's /proc/net tables (paths: the IPv4 table,
    then the IPv6 one; default PROC_NET). They name no owner. A missing IPv6 table is a kernel with
    no IPv6, and is skipped; an IPv4 table that cannot be read raises OSError."""
    out = []
    for family, path in zip((4, 6), paths or PROC_NET):
        try:
            with open(path, encoding="ascii") as f:
                lines = f.read().splitlines()[1:]
        except OSError:
            if family == 4:
                raise
            continue
        for line in lines:
            cols = line.split()
            if len(cols) > 3 and cols[3] == _PROC_LISTEN:
                out.append((_proc_end(cols[1], family), None))
    return out
