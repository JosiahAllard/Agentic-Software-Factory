# Host design

Low-level design of `kitlib/host`, `kitlib/attribution`, the host folders `hosts/<name>/` and `boot/`. The high-level design is ARCHITECTURE.md ("Hosts", "Host contract") and wins on a conflict.

## Contract

- **`kitlib` names no host.** A host name appears only in `hosts.json`, the host's own folder, the bridge's host halves and peer list, and tests.
- **`hosts.json` is the registry**, read as data by `host.registry`: adapter module, shell folder, platforms, capabilities, hook data (`guarded_tools`, `fast_path`), the tier-to-model map, `role_models`, and the `profile` section.
- **The host contract** (`kitlib/host/__init__.py`): `Action` (what a tool call does, host-neutral), `Decision` (allow through, ask, deny, with the reason), `HostAdapter`, `HostProfile`, `SessionHost`, `CommandHost`. Protocols and data only.
- **A `HostAdapter`** parses its hook payloads into `Action`, renders a `Decision` back, and supplies its `SessionHost`, `ContentRenderer` and, where the host has them, a `CommandHost`, a launcher, a plugin installer and a push delivery.
- **A missing capability is declared, never guessed.** A host with no `ask` gets deny with the reason; a host whose hooks carry no subagent identity gets the role-only policy.
- **Agent names and the session mark** live here: the kit plugin's name and prefix, a role's bare and qualified name, and what kind of session a process runs in.
- **`attribution`** holds how a commit names its maker: the agent trailer, the model name and its env var, the co-author line. `commit`, `syncbase` and `ledger` read it here.
- **One hook entry**: `boot/hook.py <host> <event> [<part>]`. `boot/kitboot.py` runs before any `kitlib` import and is standard-library only.
- **Adding a host changes no other folder.**

## Packages and files

| Path | Holds |
|---|---|
| `kitlib/host/__init__.py` | The contract types |
| `kitlib/host/registry.py` | `hosts.json` read and checked; the plugin name |
| `kitlib/host/content.py` | The content contract: the neutral role and skill model, `ContentRenderer`, `MODES` |
| `kitlib/host/agentname.py`, `sessionmark.py` | Agent names; the session mark |
| `kitlib/attribution/` | Trailers, model name, co-author line |
| `hosts/<name>/adapter/` | The host's Python: adapter, hook parse and render, content renderer, session and command host |
| `hosts/<name>/shell/` | Rendered agents and manifests, hook registration; never edited by hand |
| `boot/hook.py`, `boot/kitboot.py` | The hook entry; the boot check |

The hook runner and the kit header are `kitlib/hookrun` (docs/modules/guard-design.md): they need the guard, so they sit in the guard's layer (ARCHITECTURE.md "Layers").

## State

`hosts.json`, tracked. Nothing else.

## Errors

`RegistryError` for a `hosts.json` that cannot be read or names an unknown host. A hook payload the adapter cannot parse is the guard's to fail closed on.

## Adding to it

A new host: docs/new-host.md (the `hosts.json` entry, the adapter, the shell, recorded fixtures, the conformance subclass). A new field in `HostProfile` only when some code consumes it.

## Tests

`tests/host`: `conformance.py` defines a supported host; each host subclasses it with recorded payloads. `test_leaks.py` fails a host name in `kitlib`; `test_layering.py` fails an import of a host folder or `optional/`.

## Reference

docs/new-host.md, docs/ROSTER.md, docs/kitboot.md, docs/guard-fallback.md ("Agent names", "Session mark"), hosts/codex/README.md.
