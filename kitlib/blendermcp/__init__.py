"""The Blender MCP bridge (docs/blender-mcp.md): MCP tools that give an agent a live, headless
Blender of its own, booked through the machine lock, driven through the Blender Lab MCP add-on.

wire.py speaks the add-on's socket protocol; addon.py finds Blender and checks the add-on is the
reviewed one; codes.py holds the Python the tools send into Blender; instance.py opens, calls,
watches and closes the Blenders; server.py is the MCP server; mcp/blender_server.py (Claude) and
hosts/codex/shell/mcp/blender_server.py (Codex) serve it over stdio.

The names here import nothing, so a manifest or the guard can read them without the server."""

# The server's key under the plugin's mcpServers, and the name it reports.
SERVER_NAME = 'blender'

# The ports the bridge gives its Blenders: 9877-9899. 9876 is the add-on's default, which an
# owner's own Blender may hold, and is never used.
PORTS = tuple(range(9877, 9900))
# The machine lock resource a Blender's port is held as, for the Blender's whole life.
RESOURCE_PREFIX = 'blender-port:'
# The lock kind a Blender is booked as, with gpu_share "training": one gpu slot of the pool, so
# two Blenders can run at once on the two slots (docs/blender-mcp.md "The lock").
KIND = 'render'
GPU_SHARE = 'training'
# A Blender left with no call for this long is closed.
IDLE_S = 20 * 60

TOOL_NAMES = ('blender_open', 'blender_list', 'blender_inspect', 'blender_render', 'blender_exec',
              'blender_close')
