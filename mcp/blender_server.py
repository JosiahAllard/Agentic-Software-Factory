"""blender_server.py: the Blender MCP bridge as a stdio MCP server (docs/blender-mcp.md).

A thin entry point (ARCHITECTURE.md, Layers): it serves kitlib.blendermcp.server over this
process's stdin and stdout, booking every Blender on the machine's lock store. The plugin manifest
(.claude-plugin/plugin.json) starts it; it takes no arguments. It boots the kit before importing
kitlib (boot/kitboot.py: the source checkout, or the pinned release, verified), and exits on a kit
that fails that check. When stdin ends (the session went away) it closes every Blender it opened.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'boot'))
import kitboot                                                       # noqa: E402
kitboot.boot()                                                       # the kit, checked, before kitlib

from kitlib.blendermcp.server import run                            # noqa: E402


def main():
    rfile, wfile = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr             # a stray print must not corrupt the protocol stream
    run(rfile, wfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
