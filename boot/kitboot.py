"""kitboot: the check every host entry runs before it imports kitlib (ARCHITECTURE.md "Packaging";
docs/multi-host-plan.md section 2.5; docs/kitboot.md). Standard library only, and nothing from the
kit is imported here: this file decides which kit may be imported. Lifted from the Codex plugin's
profile.py (discover, load_kit).

  boot(environ=None, here=None) -> str
      locate() then bind(): the kit root, first on sys.path. What an entry calls.
  locate(environ=None, here=None) -> Kit(root)
      which kit to run, checked, without touching sys.path:
      - PM_KIT_ROOT unset: the folder this file is in (the folder above boot/): a source checkout
        or an export of pushed main.
      - PM_KIT_ROOT set: a non-blank absolute path.
      - the root must hold kitlib/__init__.py and tools/machine_lock.py.
  bind(kit) -> str
      put kit.root first on sys.path and refuse a kitlib already loaded from anywhere else;
      returns the root.

`here` stands in for this file (tests). Every refusal is BootError; an entry fails closed on it
(the guard denies, a session hook says it could not load the kit, the lock server exits).
"""
import collections
import os
from pathlib import Path
import sys

ROOT_ENV = 'PM_KIT_ROOT'
REQUIRED = ('kitlib/__init__.py', 'tools/machine_lock.py')

Kit = collections.namedtuple('Kit', 'root')


class BootError(RuntimeError):
    """The kit cannot be run: a bad PM_KIT_ROOT, a folder that holds no kit, or a different kitlib
    already loaded."""


def _source(here):
    return Path(os.path.abspath(here or __file__)).parent.parent


def locate(environ=None, here=None):
    env = os.environ if environ is None else environ
    configured = env.get(ROOT_ENV)
    if configured is not None:
        if not configured.strip():
            raise BootError('%s must be a non-blank absolute path' % ROOT_ENV)
        root = Path(configured)
        if not root.is_absolute():
            raise BootError('%s must be absolute: %s' % (ROOT_ENV, configured))
        root = root.resolve()
    else:
        root = _source(here)
    for rel in REQUIRED:
        if not (root / rel).is_file():
            raise BootError('no kit at %s (no %s); set %s to the kit folder' % (root, rel, ROOT_ENV))
    return Kit(str(root))


def bind(kit):
    root = os.path.abspath(kit.root)
    want = os.path.normcase(os.path.realpath(os.path.join(root, 'kitlib', '__init__.py')))
    loaded = sys.modules.get('kitlib')
    if loaded is not None:
        origin = getattr(loaded, '__file__', None)
        if origin is None or os.path.normcase(os.path.realpath(origin)) != want:
            raise BootError('another kitlib is already loaded (%s), not %s' % (origin, root))
    if not sys.path or os.path.normcase(os.path.abspath(sys.path[0])) != os.path.normcase(root):
        sys.path.insert(0, root)
    return root


def boot(environ=None, here=None):
    return bind(locate(environ, here))
