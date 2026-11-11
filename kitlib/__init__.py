"""kitlib: all kit logic, no command lines.

Importing it installs kitlib.proc.console's CreateProcess hook (Windows): from then on no start
in this process opens a console window, a bare subprocess or multiprocessing start included. The
import stays cheap: no ctypes until the first start that needs the hook."""
from kitlib import platform as _platform

if _platform.NAME == 'windows':
    from .proc import console as _console
    _console.install()
