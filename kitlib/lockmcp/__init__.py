"""The lock MCP server's logic (ARCHITECTURE.md, Scheduler UI): MCP tools over kitlib.lock.api.
docs/lock-mcp.md is the contract; mcp/lock_server.py is the stdio entry point.

PLUGIN_NAME, SERVER_NAME and OWNER_ONLY (the retired owner writes) live in kitlib.lock.names, where
the guard reads them; they are re-exported here for the server and its callers. The host registry
loads only when a prefix is asked for."""
from kitlib.lock.names import OWNER_ONLY, PLUGIN_NAME, SERVER_NAME


def tool_prefix(host=None, plugin=PLUGIN_NAME, server=SERVER_NAME, path=None):
    """What a plugin MCP server's tool names start with on `host` (a hosts.json name; None: the
    default host): its profile mcp_tool_format up to {tool}, filled in with `plugin` and
    `server`. On Claude, mcp__plugin_<plugin>_<server>__: INFERRED from Claude Code's plugin
    naming; no real PreToolUse payload has been seen yet."""
    from kitlib.host import registry
    name = registry.default(path) if host is None else host
    fmt = registry.host(name, path).profile.mcp_tool_format
    return fmt.split('{tool}', 1)[0].format(plugin=plugin, server=server)

