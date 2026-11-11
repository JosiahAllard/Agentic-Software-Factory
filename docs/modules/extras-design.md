# Extras design

Low-level design of `kitlib/blendermcp` and the `optional/` packages. The high-level design is ARCHITECTURE.md ("Layers": apps, Optional) and wins on a conflict.

## Contract

- **`optional/` is installed on demand.** The core never imports it (`tests/host/test_layering.py`), and the kit works with none of it. An optional package may import `kitlib`; it follows the same rule as an entry point.
- **The Blender MCP bridge** (`blendermcp`) gives an agent a live headless Blender of its own. Each Blender is booked through the machine lock, started on its own port, driven through the pinned Blender Lab MCP add-on, watched, and closed. The tools that run Python or write files are refused to the no-edit roles.
- **The agent bridge** (`optional/bridge/`) is agent-to-agent messaging: a core, a service, an MCP front end, and one half per host under `optional/bridge/hosts/<name>/`. A host name appears there and in its peer list, nowhere else in the bridge.
- **`optional/loadnode/`** is the forced command a load node runs. **`optional/procwatch/`**, **`optional/agenttemp/`** and **`optional/usage/`** are small scheduled helpers. **`optional/comfy/`** drives a local image generator through the lock.
- Anything heavy an extra starts goes through the machine lock like any other run.

## Packages and files

| Path | Holds |
|---|---|
| `blendermcp/addon.py`, `instance.py`, `wire.py`, `codes.py`, `server.py` | Which Blender and add-on; the booked instances; the add-on's socket protocol; the snippets sent in; the MCP server |
| `optional/bridge/` | Core, service, MCP front end, host halves, `peers.json` |
| `optional/loadnode/`, `procwatch/`, `agenttemp/`, `usage/`, `comfy/` | One helper each |

## State

A Blender instance lives as long as its lock booking. The bridge keeps its own message store. Nothing here writes the kit's data.

## Errors

An extra that is not installed is reported as not installed; no core tool fails for its absence.

## Adding to it

A new optional package: a folder under `optional/` that imports `kitlib` and is imported by nothing in it. If the core must know of it, the core gets a seam (a config key, a registry entry), never an import.

## Tests

`tests/blendermcp`, and each optional package's own tests.

## Reference

docs/blender-mcp.md, optional/bridge/README.md, docs/load-nodes.md, docs/machine-setup.md ("Extras").
