# Base design

Low-level design of the base layer: `platform`, `proc`, `fs`, `schedtask`, the wires (`mcpwire`, `hostrpc`), `verdict` and the root files. The high-level design is ARCHITECTURE.md ("Layers", base) and wins on a conflict.

## Contract

- **`platform` is the only place that knows the OS.** It imports nothing from the kit. `platform.NAME` is `windows`, `linux` or `unsupported`, read at call time. Every primitive named in `platform.api.NAMES` exists in both backends with one meaning; one with no backend raises `PlatformUnsupported` (an `OSError`). A Windows DLL binds at first use, never at import.
- **No OS test above `platform`.** Code that differs by OS calls a primitive, or reads `platform.NAME`; `sys.platform` and `os.name` appear only here.
- **Every kit child starts through `proc`**: `proc.spawn` (no console window) or `proc.job.Job` (contained: pinned to cores, memory measured, ended as a tree). A process is named by pid and creation time together. Nothing kills by name.
- **The console guard** is installed when `kitlib` loads: no start in a kit process opens a console window. An incident goes to the folder its provider gives (`config` registers one); with none, to the OS temp folder.
- **`proc.list_processes()`** is the one process listing.
- **`fs`** gives whole-file atomic write, a replace that waits out a reader, a forced rmtree, a tree lock (one OS lock on a file, one process at a time) and a write lock (one writer of a file across processes and threads, nesting in one thread). The caller names the lock file; `fs` asks git nothing.
- **`schedtask`** is the one way to register, read and remove a kit scheduled task: a Task Scheduler task on Windows, run windowless through `pythonw` and the no-window carrier; a systemd user unit, else cron, on Linux. The feature that needs a task names it and gives its command.
- **The wires** carry no policy: `mcpwire` is JSON-RPC 2.0 over stdio, one message per line; `hostrpc` is bounded JSON-lines RPC.
- **Root files**: `kitroot` names this kit's tools in a printed or run command; `clock` is the time fence; `ident` bounds ids carried between processes. `kitlib/__init__.py` installs the console guard and nothing else.

## Packages and files

| Path | Holds |
|---|---|
| `platform/__init__.py`, `api.py`, `dll.py`, `mask.py`, `jobparts.py` | `NAME`, `backend()`, `ProcError`, the contract's names, lazy DLLs, the processor-mask arithmetic, the job parts both backends share |
| `platform/windows/` | The Win32 bindings and every Windows primitive: process, cpu topology, job object, pipes, the TCP table, the pid shield, the console hook, the window watch |
| `platform/linux/` | `contain` (cgroup or fallback containment: the tiers, the probe, the carrier and the Jobs), `contain_cgroup` (its cgroup v2 and systemd calls), `contain_proc` (its /proc reads), `contain_scripts` (its stub, watchdog and starter scripts), `cpu`, `process`, `hardware`, `locks`, `paths`, `detach`, `schedule`, `tcp` (the /proc/net listeners), `unixsock` (a Unix socket connect checked by SO_PEERCRED and ctime) |
| `proc/` | The OS-neutral face: `spawn`, `job`, `process`, `listing`, `daemons`, `bash`, `tcp`, `console` (installs the hook) |
| `fs/` | `atomic`, `replace`, `rmtree`, `treelock`, `writelock` |
| `schedtask/` | The interpreter a task runs, the task command line, register, read, remove; the fake-able host object |
| `mcpwire/`, `hostrpc/`, `verdict/` | One file each |
| `clock.py`, `ident.py`, `kitroot.py` | The root files |

## State

No stored data. The console hook is process-wide and installed once under a lock. A tree lock and a write lock are OS locks: the kernel drops them when the holder dies.

## Errors

`PlatformUnsupported` for a missing backend. `ProcError` (kitlib.platform) for pipe and process-identity failures. A write lock that cannot be made, taken or is busy raises its own named error; the caller turns it into its refusal. Containment that cannot be set up fails the start: nothing runs uncontained.

## Adding to it

A new OS primitive: add its name to `platform.api.NAMES`, implement it in both backends, test each. A new scheduled task: the feature calls `schedtask`; it adds no task code of its own. A helper that knows a repository, a host or a config key does not belong in this layer.

## Tests

`tests/platform`, `tests/proc`, `tests/fs`, `tests/schedtask`. Tests that need one OS carry a platform marker; the ratchet holds the other OS's failures to a baseline that only shrinks (docs/test-tools.md "Platform markers and the ratchet").

## Reference

docs/linux-support-plan.md (the Linux work still open), docs/windows-token-leak.md, docs/kitboot.md.
