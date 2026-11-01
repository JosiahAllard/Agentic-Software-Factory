"""The owner's own process tree, for the tests that run the backlog tool (or stray restore) as the
owner: unmarked. An unmarked caller is the owner only when no host whose chats are never the owner
runs above it (kitlib.host.registry.unowned_caller reads ancestor_images), so on the real tree such
a test passes or fails with the host that runs the suite. These tests stub the tree instead:

  setUpModule / tearDownModule  a module that imports both runs its tests on the owner's tree
                                (registry.ancestor_images gives TREE, policy's per-process memo
                                cleared on both edges, so nothing leaks to the next module a pool
                                worker runs).
  argv(tool, *args)             the command line of `tool` run as a child on the owner's tree: this
                                file as the script, which stubs the tree and runs the tool as
                                __main__. A child walks its own tree, which no in-process stub reaches.

The tests of the rule itself (tests/backlog/test_codex_owner.py) pass their trees explicitly and
use none of this."""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TREE = ('python.exe', 'cmd.exe', 'explorer.exe')
_SAVED = []


def images():
    """The owner's shell's ancestors, as registry.ancestor_images gives them."""
    return list(TREE)


def _memo():
    from kitlib.backlog import policy
    return policy._REAL_TREE


def start():
    """Stub the tree until stop()."""
    from kitlib.host import registry
    memo = _memo()
    _SAVED.append((registry.ancestor_images, list(memo)))
    registry.ancestor_images = images
    memo.clear()


def stop():
    """Put back what start() replaced."""
    from kitlib.host import registry
    real, memo = _SAVED.pop()
    registry.ancestor_images = real
    _memo()[:] = memo


def setUpModule():
    start()


def tearDownModule():
    stop()


def argv(tool, *args):
    """[python, this file, tool, *args]: `tool` run as a child on the owner's tree."""
    return [sys.executable, os.path.abspath(__file__), tool] + list(args)


if __name__ == '__main__':
    sys.argv = sys.argv[1:]
    sys.path.insert(0, ROOT)
    from kitlib.host import registry as _registry
    _registry.ancestor_images = images
    runpy.run_path(sys.argv[0], run_name='__main__')
