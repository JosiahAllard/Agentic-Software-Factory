"""The one process listing (docs/modules/base-design.md "Contract"): every process as a dict. The
OS half is the seam's process_rows (kitlib.platform); this starts nothing itself but hands the
backend kitlib.proc.spawn.run to start PowerShell with."""
from kitlib import platform
from kitlib.proc import spawn

TIMEOUT = 4


def list_processes(timeout=TIMEOUT):
    """All processes as dicts with Name, CommandLine, ExecutablePath, ProcessId, ParentProcessId, Created.
    subprocess.TimeoutExpired after `timeout` seconds (Windows: PowerShell Get-CimInstance; Linux
    reads /proc with no timeout and raises OSError when /proc cannot be listed)."""
    return platform.process_rows(timeout, spawn.run)
