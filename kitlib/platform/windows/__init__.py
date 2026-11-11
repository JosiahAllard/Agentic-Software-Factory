"""The Windows backend of the seam: the Win32 bindings (win32) and every Windows primitive (cpu,
process, job, console, tcp, pipe, shield, winwatch). The contract's names
(kitlib.platform.api.NAMES) are cpu's and process's own functions, unchanged
(docs/linux-support-plan.md section 2.1, rule 2), asked for through kitlib.platform.backend().

A name is imported when first asked for, never on import: importing a submodule (console, which
`import kitlib` loads) imports this package, and that stays free of ctypes."""
import importlib

_FROM = {'cpu': ('threads', 'all_mask', 'l3_domains'),
         'process': ('alive', 'proc_ctime', 'own_ctime', 'ancestry', 'descendants', 'command_line',
                     'kill_tree', 'memory_mb', 'affinity_of', 'set_affinity')}

__all__ = ['alive', 'proc_ctime', 'own_ctime', 'ancestry', 'descendants', 'command_line',
           'kill_tree', 'threads', 'all_mask', 'l3_domains', 'memory_mb', 'affinity_of',
           'set_affinity']


def __getattr__(name):
    for module, names in _FROM.items():
        if name in names:
            return getattr(importlib.import_module(__name__ + '.' + module), name)
    raise AttributeError('module %r has no attribute %r' % (__name__, name))
