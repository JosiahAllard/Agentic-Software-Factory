"""The lock MCP's names, kept with the lock they name (ARCHITECTURE.md "Layers": the guard reads
them without importing the MCP server's package, kitlib.lockmcp, which re-exports them). The kit
plugin's name is the host registry's (kitlib.host.registry.PLUGIN_NAME). Nothing here imports
anything."""

# The lock MCP server's key under the plugin's mcpServers, and the name it reports.
SERVER_NAME = 'machine-lock'

# The lock MCP server's retired owner writes (the owner's decision: the server ships read tools only).
# Pinned so the server never serves one again (server.py asserts it) and so the guard still denies
# them by name: a server process started from an older release keeps serving its old tools.
OWNER_ONLY = ('lock_hold_create', 'lock_hold_edit', 'lock_hold_end', 'lock_set_game_mode',
              'lock_set_games', 'lock_set_division', 'lock_end_run')
