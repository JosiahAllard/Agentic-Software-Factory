"""The machine's established TCP connections, each with the pid that owns it: which local process
is at the other end of a connection on this machine (kitlib.proc.daemons asks it of a build
daemon). And its listening sockets, each with its owner: which processes listen on a port
(kitlib.blendermcp asks it of its Blender's port). On Linux the listeners come from /proc/net/tcp
and tcp6, which name no owner (kitlib.platform.linux.tcp); the connections are Windows only.

An end is (family, address bytes, port): family 4 or 6, the address in network order, the port
a number. An IPv4-mapped IPv6 address (::ffff:a.b.c.d) is named as the IPv4 end it is, so both
ends of one connection are named alike whichever table each sits in.
"""
import struct

import kitlib.platform
from kitlib.platform.windows import tcp as _wintcp, win32 as _win32   # the tables: Windows only
from kitlib.platform.linux import tcp as _linuxtcp                   # /proc/net: Linux

LISTEN = 2                              # MIB_TCP_STATE_LISTEN
ESTABLISHED = 5                         # MIB_TCP_STATE_ESTAB
_V4_ROW = struct.Struct("<6I")          # MIB_TCPROW_OWNER_PID
_V6_ROW = struct.Struct("<16sII16sIIII")  # MIB_TCP6ROW_OWNER_PID
_MAPPED = b"\0" * 10 + b"\xff\xff"


def _port(raw):
    """A table port: network byte order in the low 16 bits of a DWORD."""
    raw &= 0xFFFF
    return ((raw & 0xFF) << 8) | (raw >> 8)


def _end6(addr, port):
    if addr[:12] == _MAPPED:
        return (4, addr[12:], _port(port))
    return (6, addr, _port(port))


def parse_v4(buf, want=ESTABLISHED):
    """[(local, remote, pid)] of the rows of a MIB_TCPTABLE_OWNER_PID buffer in state `want`
    (default the established ones)."""
    n = struct.unpack_from("<I", buf, 0)[0]
    out = []
    for i in range(n):
        state, la, lp, ra, rp, pid = _V4_ROW.unpack_from(buf, 4 + i * _V4_ROW.size)
        if state == want:
            out.append(((4, struct.pack("<I", la), _port(lp)),
                        (4, struct.pack("<I", ra), _port(rp)), int(pid)))
    return out


def parse_v6(buf, want=ESTABLISHED):
    """[(local, remote, pid)] of the rows of a MIB_TCP6TABLE_OWNER_PID buffer in state `want`
    (default the established ones)."""
    n = struct.unpack_from("<I", buf, 0)[0]
    out = []
    for i in range(n):
        la, _ls, lp, ra, _rs, rp, state, pid = _V6_ROW.unpack_from(buf, 4 + i * _V6_ROW.size)
        if state == want:
            out.append((_end6(la, lp), _end6(ra, rp), int(pid)))
    return out


def is_loopback(end):
    family, addr = end[0], end[1]
    if family == 4:
        return addr[:1] == b"\x7f"
    return addr == b"\0" * 15 + b"\x01"


def connections():
    """[(local, remote, pid)] of every established TCP connection now, IPv4 and IPv6. Raises
    OSError when a table cannot be read."""
    return parse_v4(_wintcp.table(_win32.AF_INET)) + parse_v6(_wintcp.table(_win32.AF_INET6))


def listeners():
    """[(local, pid)] of every listening TCP socket now, IPv4 and IPv6: local is the end it
    listens on (an any-address listener has the all-zero address). Two sockets that both set
    SO_REUSEADDR can listen on one port, so a port can have more than one row. Raises OSError
    when a table cannot be read. On Linux each row's pid is None (kitlib.platform.linux.tcp)."""
    if kitlib.platform.NAME == "linux":
        return _linuxtcp.proc_net_listeners()
    rows = (parse_v4(_wintcp.table(_win32.AF_INET), LISTEN)
            + parse_v6(_wintcp.table(_win32.AF_INET6), LISTEN))
    return [(local, pid) for local, _remote, pid in rows]


def peers(pids, conns):
    """[(pid, peer)] for each established connection of a process in `pids` whose other end is
    on this machine: peer is the pid owning that other end, or None when no row owns it (the
    connection is closing, or the table cannot tell). The other end is on this machine when a
    row owns it, when its address is a loopback one, or when its address is the local address
    of any row in `conns` (one of this machine's own interface addresses): a missing row there
    is None, never another host. Only a connection to an address no row has as its local one
    is to another host, and it is not listed."""
    pids = set(pids)
    owner, here = {}, set()
    for local, remote, pid in conns:
        owner[(local, remote)] = pid
        here.add(local[:2])                 # (family, address): this machine's own
    out = []
    for local, remote, pid in conns:
        if pid not in pids:
            continue
        peer = owner.get((remote, local))
        if peer is None and not is_loopback(remote) and remote[:2] not in here:
            continue
        out.append((pid, peer))
    return out
