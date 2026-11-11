"""The Windows TCP tables: GetExtendedTcpTable's raw rows, each with the pid that owns it. The
rows are parsed, and the Linux tables read, by the platform-neutral face that asks for them."""
import ctypes

from . import win32


def table(family):
    """The raw MIB_TCPTABLE_OWNER_PID (or its IPv6 form) of `family` (win32.AF_INET or
    AF_INET6), every state. Raises OSError when it cannot be read."""
    size = win32.DW(0)
    buf = None
    for _ in range(5):                  # the table can grow between the two calls
        err = win32.IPHLP.GetExtendedTcpTable(buf, ctypes.byref(size), False, family,
                                              win32.TCP_TABLE_OWNER_PID_ALL, 0)
        if err == 0 and buf is not None:
            return buf.raw[:size.value]
        if err not in (0, win32.ERROR_INSUFFICIENT_BUFFER):
            raise OSError("GetExtendedTcpTable: windows error %d" % err)
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = win32.DW(len(buf))
    raise OSError("GetExtendedTcpTable: the table kept growing")
