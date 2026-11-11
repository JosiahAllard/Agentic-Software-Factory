# kitboot: the check before kitlib

Every host entry runs `boot/kitboot.py` before it imports kitlib (ARCHITECTURE.md "Packaging"). It is standard library only and imports nothing from the
kit: it decides which kit may be imported.

## Which kit runs

| Environment | Kit |
|---|---|
| `PM_KIT_ROOT` unset | the folder `boot/` is in: a source checkout or an export of pushed main |
| `PM_KIT_ROOT` set | that folder; it must be a non-blank absolute path |

The kit must hold `kitlib/__init__.py` and `tools/machine_lock.py`. There is no pinned release
(ARCHITECTURE.md "Packaging"). A kitlib already loaded from any other folder is refused.

## API

- `boot(environ=None, here=None) -> str`: `locate`, then `bind`. What an entry calls.
- `locate(environ=None, here=None) -> Kit(root)`: the checks above, `sys.path` untouched.
- `bind(kit) -> str`: the root first on `sys.path`; refuses a different kitlib already loaded.

Every refusal is `BootError`. The entries fail closed on it: the guard exits 2 (a block, the
reason on stderr), a session-start hook says in plain text that the kit did not load, and the lock
MCP server and the hub MCP server exit.

## Who calls it

- `boot/hook.py`, every host's one hook entry, from the folder beside it. It decides an unguarded
  tool (TodoWrite, WebFetch...: one its host's `hosts.json` `hook.guarded_tools` leaves out) before it boots,
  so those calls never pay for the boot. Every host's plugin root is laid out as the kit
  (Codex's is the whole export), so its `boot/` is the kit's own; no host ships a copy of it.
  Codex reaches it through the hooks plugin `pm-kit-hooks`, whose stub runs the installed
  export's `boot/hook.py` (docs/plugin-source.md "Codex hooks plugin").
- `mcp/lock_server.py`, `mcp/hub_server.py` and `mcp/blender_server.py`.
- The Codex shell's entry scripts (`hosts/codex/shell/scripts/`, for example `kit.py` and `launch.py`) and its MCP servers, through the kit's own `boot/kitboot.py` four folders above each.

Each puts `boot/` on `sys.path` relative to its own file, then calls `kitboot.boot()`. Tests:
`tests/boot`.
