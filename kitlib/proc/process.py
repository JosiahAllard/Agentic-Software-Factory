"""Process information and verified kills, on the running platform: snapshots, creation times,
ancestry, verified kills, affinity, memory. A process is named by (pid, creation time) wherever a
reused pid could otherwise be hit. Each name is the Windows backend's
(kitlib.platform.windows.process); on Linux the process names are kitlib.platform.linux.process's
instead (the end of this file). The Windows-only handle, memory-counter and affinity-restore
functions are kitlib.platform.windows.process's alone.
"""
import kitlib.platform
from kitlib.platform.windows.process import (  # noqa: F401  (the face's names)
    _ancestor, affinity_of, alive, ancestors_of, ancestry, command_line, descendants, kill_tree,
    listed_ctime, listed_ctimes, memory_mb, own_ancestors, own_ctime, proc_ctime, process_parents,
    raise_to_normal, set_affinity, snapshot, terminate_verified)

# On Linux the process names read /proc instead (kitlib.platform.linux.process; docs/
# linux-support-plan.md section 8, slice 4), with the same meanings and (pid, ctime) identity,
# the ctime `(boot << 64) | starttime`. The affinity and memory functions above stay Windows's:
# off Windows they raise PlatformUnsupported through kitlib.platform.windows.win32.
if kitlib.platform.NAME == "linux":
    from kitlib.platform.linux import process as _procfs
    for _name in ("snapshot", "process_parents", "listed_ctimes", "listed_ctime", "own_ctime",
                  "proc_ctime", "alive", "_ancestor", "ancestry", "ancestors_of", "own_ancestors",
                  "descendants", "command_line", "terminate_verified", "kill_tree"):
        globals()[_name] = getattr(_procfs, _name)
    del _name
