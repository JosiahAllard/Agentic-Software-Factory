"""blender_server.py: the Blender MCP bridge as Codex's stdio MCP server (mcp.json;
docs/blender-mcp.md). Takes no arguments. It boots the kit before importing kitlib
(boot/kitboot.py of the export or checkout it sits in, or PM_KIT_ROOT, verified) and exits on a
kit that fails that check. Codex's guard (hosts/codex/adapter/policy.py) refuses every call of
the blender_* tools for now (docs/blender-mcp.md "Known limits"), so a Codex session can list
them but not use them.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '..', '..', '..', 'boot'))  # the kit's own boot/kitboot.py
import kitboot                                                       # noqa: E402
kitboot.boot()                                                       # the kit, checked, before kitlib

from kitlib.blendermcp.server import run                            # noqa: E402


def main():
    rfile, wfile = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr             # a stray print must not corrupt the protocol stream
    run(rfile, wfile)
    return 0


if __name__ == '__main__':
    sys.exit(main())
