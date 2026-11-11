"""Scheduled tasks (docs/modules/base-design.md): the one home of what every kit task shares.

- pythonw: the interpreter a Windows task runs (the base install's pythonw.exe, never the Store
  alias) and the refusal of one it must not run.
- line: the pieces of a PowerShell registration line (single-quoting, `$py`, the throw on a
  WindowsApps path).
- read: reading tasks back (`schtasks /Query`, or the pm-kit jobs on Linux): `read_tasks`,
  `task_registered`, and the parsing they use.
- host: the fake-able host object that reads, registers and writes tasks.

The feature that needs a task names it and gives its command; nothing here knows a repository, a
host or a kit feature. It imports only kitlib.platform and kitlib.proc.
"""
