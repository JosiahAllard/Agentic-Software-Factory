# Blender MCP bridge

`mcp/blender_server.py` serves a stdio MCP server that gives an agent a live, headless Blender of its own. Each Blender is booked through the machine lock and driven through the Blender Lab MCP add-on, over a loopback socket. The logic is `kitlib/blendermcp/`:
- `wire.py` speaks the add-on's socket protocol.
- `addon.py` finds Blender and checks the add-on.
- `codes.py` holds the Python the tools send into Blender.
- `instance.py` opens, calls, watches and closes the Blenders.
- `server.py` holds the tools.

The names the manifests read (`SERVER_NAME`, `TOOL_NAMES`, the ports, the lock kind) are in `kitlib/blendermcp/__init__.py`, which imports nothing. The stdio JSON-RPC loop is `kitlib/mcpwire`, shared with the lock and hub servers.

- **Transport:** JSON-RPC 2.0, one message per line on stdin and stdout. It answers `initialize` (protocol 2025-06-18, 2025-03-26 or 2024-11-05), `ping`, `tools/list` and `tools/call`.
- **Plugin:** `.claude-plugin/plugin.json` names it `blender`, rendered from `hosts/claude/adapter/content.py` `MCP_SERVERS`. A Claude session shows its tools as `mcp__plugin_pm-kit_blender__<tool>`. Codex's plugin carries it as server `blender` (`hosts/codex/shell/mcp/blender_server.py`), but Codex refuses its calls ("Known limits").
- **What it is for:** looking at and checking a scene while you work on it: inspect it, try a change, render it and read the PNG. A deliverable 3D asset is still built by a headless Blender Python script that the repository keeps and reruns (skill specialty-3d-assets). Work done only through `blender_exec` is lost when the Blender ends, and nobody can review or rebuild it.

## Setup

- **Blender:** 5.1 or newer (the add-on's minimum). The bridge uses the executable named by `PM_KIT_BLENDER` if set, else `blender` on PATH. On Windows it then tries the newest `Blender Foundation\Blender <version>\blender.exe` under Program Files. A Blender older than 5.1 is refused.
- **The add-on:** Blender Lab MCP v1.0.3, installed as the extension `mcp` in that Blender version's `user_default` extensions folder:
  - Windows: `%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\mcp`.
  - Linux: `$XDG_CONFIG_HOME/blender/<version>/...`, or under `~/.config`.
  - `BLENDER_USER_EXTENSIONS` overrides both.
- **The add-on is GPL and is never copied into the kit.** The bridge starts the owner's own install, and it checks that install against the reviewed one before every open (`addon.check`).
  - The pin is the add-on folder's tree hash, `addon.TREE_SHA256` in `kitlib/blendermcp/addon.py`: SHA-256 over each file's relative path, a NUL, the file's own SHA-256 and a newline, in walk order, with `__pycache__` skipped. The SHA-256 of the zip it came from is recorded beside it in `addon.py`.
  - How the pin was made: the same hash over the zip's own entries (sorted, `__pycache__` skipped) gave the pinned hash, equal to the installed folder's tree hash. So the installed files are the reviewed zip's 9 files, byte for byte.
  - `__pycache__` is outside the pin, and Blender writes one there. Python never reads it: the bridge starts Blender with `--python-use-system-env` and an environment (`addon.launch_env`) with no PYTHON* variable of the caller's, plus `PYTHONPYCACHEPREFIX` set to a fresh empty folder for that Blender, `PYTHONDONTWRITEBYTECODE` and `PYTHONNOUSERSITE`. Python then looks for byte code only under that folder, so a `.pyc` planted beside the add-on's sources is never run. The folder is removed when the Blender ends.
  - A different version, or an edited copy, is refused with the `setup` error. An update is reviewed and pinned again in `addon.py`.
- **The command line:** `blender --background --python-use-system-env --online-mode --command blender_mcp --host 127.0.0.1 --port <port>` (`addon.launch_argv`). `--online-mode` is Blender's own option and must come before `--command`: the add-on's parser takes the rest of the line and refuses it. Blender runs in the worktree it was opened for.
- **Codex:** the env vars the Codex manifest passes through are `PM_KIT_ROOT` and `PM_KIT_BLENDER`.

## Tools

| tool | arguments | does |
|---|---|---|
| `blender_open` | `worktree` (absolute, an existing folder), `wait_s` (0-600, default 0) | Books and starts a Blender. Returns its info, including its `handle` (the lock run id), `port`, `pid` and `gpu_slots`. With `wait_s` 0, it is refused at once when no slot is free. |
| `blender_list` | none | This server's Blenders: `open` (handle, port, pid, run, calls, idle time) and the last 20 `ended`, each with why and how it ended. |
| `blender_inspect` | `handle` | The open scene: its objects (the first 500: type, transform, dimensions, materials, modifiers, mesh counts), the camera, the render engine and resolution, the materials and the collections. Limit 60 s. |
| `blender_render` | `handle`, and optionally `path` (an absolute `.png`), `width`, `height` (16-8192, default 1280x720), `engine` (`workbench` (the default), `eevee` or `cycles`), `camera`, `timeout_s` | Renders a PNG and returns its path, size, engine and whether it framed a camera. The scene's render settings are put back afterwards. With no camera named and none in the scene, a temporary camera frames every renderable object and is removed afterwards. Limit 300 s by default. |
| `blender_exec` | `handle`, `code`, `timeout_s` | Runs Python (bpy). Set `result` to a JSON-serialisable dict to get it back; printed output comes back as `stdout`. An exception comes back as an error with its traceback. Limit 120 s by default. |
| `blender_close` | `handle` | Stops the Blender and releases its booking. Unsaved work is lost: save with `blender_exec` first. |

- `timeout_s` is at most 3600. Past it the Blender is ended (`timeout`), because the add-on cannot stop code that is running. Open a new Blender afterwards.
- `blender_list` and `blender_inspect` carry `readOnlyHint`.

### Results

- A tool returns its payload as JSON text, and the same object as `structuredContent`.
- A failure is a tool result with `isError: true` and `{"ok": false, "error": {code, message, field}}`. The server keeps answering. The codes:
  - `invalid`: the arguments fail the schema; nothing is started.
  - `setup`: no Blender found, a Blender that is too old, or an add-on that is missing or not the pinned one.
  - `lock`: no slot, or the lock store could not be read.
  - `ports`: no free bridge port.
  - `port`: a listener that is not this Blender.
  - `start`: Blender exited or never listened. The message carries its log tail.
  - `stopped`: the lock stopped the run while Blender started.
  - `timeout`: the call ran past its limit.
  - `ended`: the Blender has ended, or is closing.
  - `busy`: `blender_close` while a call runs in that Blender; close it once the call returns.
  - `unknown`: no such handle.
  - `wire`: the socket failed.
  - `internal`: a bug, with a short trace.
- An error raised inside Blender is a result with `isError: true` that keeps the add-on's message and traceback.

## Lifetime

- **Open:**
  - The bridge picks the first port of 9877-9899 that meets three tests: no live lock entry holds `blender-port:<port>`, nothing listens on it, and an exclusive bind of it works. 9876, the add-on's default, which an owner's own Blender may hold, is never used.
  - It books a lock run and starts Blender in that run's job.
  - It then waits up to 120 s until the port listens and every listener on it is a process of that job.
  - A booking refused because another bridge took the port first tries the next port, up to three tries.
  - Anything that fails after the booking releases the booking and ends what it started.
- **Calls:**
  - Each Blender serves one call at a time, and each call has a deadline.
  - Before each call the port's listeners are read again. The add-on binds with SO_REUSEADDR, so on Windows another process could listen on the same port. A listener that is not this Blender's ends the Blender, so no call reaches the other process.
- **Ends:**
  - `blender_close`, or 20 minutes with no call (`kitlib.blendermcp.IDLE_S`), stops the add-on's server. Blender then exits 0, and the booking is released as `done`.
  - A Blender that does not exit within 15 s is ended with its job (`cancelled`).
  - Neither stops a Blender in a call: `blender_close` is refused (`busy`), and the idle close checks for a running call under the Blender's lock and leaves it be. Once a close begins, no new call starts, and a call is refused at once rather than waiting for the close.
  - A watcher thread looks every 5 s:
    - It ends a Blender whose run the lock was asked to stop (by the owner, or by the lock's stale check).
    - It releases one that exited by itself.
  - When stdin ends (the session went away) the server closes every Blender it opened. One still in a call is ended with its job, since nothing is left to take the reply.
  - If the server process dies instead, the run's job ends Blender (a Windows job object is kill-on-close), and the lock drops the booking of a dead wrapper.
- **Files:** each Blender's log is `<temp>/pm-kit-blender/<run id>.log`. It is removed after a clean exit and kept after any other end. A render with no `path` writes into the same folder.

## The lock

- **The booking:** each Blender is one lock run:
  - kind `render`;
  - `gpu_share` `training`, so it holds one gpu slot of the pool, plus the cpu slots of a `medium` run;
  - the resource `blender-port:<port>`.
  The repo and slug come from the worktree, and the note is "Blender MCP bridge on port <port>". `kinds.json` is unchanged.
- **Two at once:** the machine's 2 gpu slots let two Blenders run at once, and a third waits or is refused.
- **Whether two fit:** this was measured on a 12 GB-class GPU with two `render` bookings using `--gpu-share training`, which held gpu slots 0 and 1 at once.
  - The load was a Workbench render loop (1920x1080, AA 16, six 256x128 spheres, no shadows), 60 s each.
  - Alone: median 0.46 s per render (p90 0.72).
  - Side by side, over a 57 s overlap: 0.38 s and 0.34 s (p90 0.48 and 0.38).
  - GPU use: 35% median and 60% at most with both, against a 32% idle baseline.
  - VRAM rose from 3.0 GB to 3.3 GB.
  - These renders are bound by the run's pinned cores, not by the GPU, so two Workbench renders fit.
  - **EEVEE and Cycles were not measured.** A heavy EEVEE or Cycles scene may need the whole card, and two of them on one card may not fit.
- **The live test run** (Blender 5.2.2, 2026-11-03, a busy machine): each Blender booked `render` with `gpu_share` `training` and held 2 cpu slots and gpu slot 0, with resource `blender-port:9877`.
  - The first waited 225 s in the lock queue for its cpu slots. Blender then listened within about 4 s of its slot, and a 320x240 Workbench render of one cube took 1.9 s. After `blender_close` it exited 0, and its run ended `done`. Peak memory was 345 MB.
  - The second ran a call past its 3 s limit. Its run ended `cancelled` 7 s after it got its slot, counting Blender's start and the 3 s limit.

## Allow rules

Claude asks before each MCP tool call unless the tool is allowed. These are the lines for the owner's managed settings, under `permissions.allow`. The kit does not write them (the owner adds them):

```
"mcp__plugin_pm-kit_blender__blender_open",
"mcp__plugin_pm-kit_blender__blender_list",
"mcp__plugin_pm-kit_blender__blender_inspect",
"mcp__plugin_pm-kit_blender__blender_render",
"mcp__plugin_pm-kit_blender__blender_exec",
"mcp__plugin_pm-kit_blender__blender_close"
```

- **Launched sessions start without it.** Every session `launch.py` starts denies `mcp__plugin_pm-kit_blender` (docs/launcher.md "Session settings"), and a deny beats these allow lines. `launch.py <verb> ... --mcp blender` turns it on for that session and its agents.
- **`blender_exec` is a shell.** It runs arbitrary Python inside a Blender, in the agent's worktree, as the user who runs the session. The Python can read, write or delete any file that user can, and start any program.
- **The guard sees the call, not the Python.** No kit guard rule judges what the Python does: not the write jail, the git rules or the lock rules. For an edit role, allowing `blender_exec` gives that role a shell.
- **One role rule.** Rule `role-blender` (docs/guard-jail-roles.md "Role policy") refuses `blender_exec` and `blender_render` to every role that edits nothing (`read`, `test`, `lander`) wherever the repository turns the role policy on. `blender_render` is refused because its `path` can write a PNG anywhere.

## Testing

- `tests/blendermcp/` runs in the gate against a stub add-on (`tests/blendermcp/fakeblender.py`), on ports the test binds itself, and books through a test lock store.
- `tests/blendermcp/test_live.py` runs against the real Blender and the machine lock. It runs only with `PM_KIT_BLENDER_LIVE=1` and never in the gate. It drives `mcp/blender_server.py` over stdio:
  1. open a Blender, inspect it, build a cube, render a PNG, and close it;
  2. open a second Blender and run a call past its limit, which must end that Blender.

```
set PM_KIT_BLENDER_LIVE=1 && python -m unittest tests.blendermcp.test_live
```

## Known limits

- **Codex refuses the tools.**
  - `hosts/codex/adapter/policy.py` lets a marked Codex session call only the machine lock's read tools, so every `blender_*` call is refused there.
  - A Codex session can list the tools but not use them.
  - Opening them to Codex would loosen an existing guard, and that needs the owner's go.
- **Linux is checked on Windows only.**
  - The code runs on Linux, but no Linux run was made.
  - If the server dies there, the Blender is ended by the lock job's watchdog (`kitlib/platform/linux/contain.py`), not by a kill-on-close job object; that path is untested with Blender.
  - On Linux the listener table has no owners (`kitlib.proc.tcp` `listeners` reads `/proc/net/tcp` and `tcp6`, which name none), so any listener on the port counts as Blender's. Linux refuses a second listener on a port unless both set SO_REUSEPORT, which the add-on does not.
- **Only this add-on is pinned.** Blender also loads the user's other enabled add-ons, and the bridge checks none of them; on this machine they are the ones Blender ships (`cycles`, the io_* importers, `pose_library`, `bl_pkg`).
  - `--factory-startup --addons bl_ext.user_default.mcp` would load only this one: a probe found the command works that way.
  - It is not used: `--factory-startup` skips the user's `userpref.blend`, which holds the Cycles GPU device, so Cycles would render on the CPU. This follows from Blender's documented behaviour; it was not measured.
- **Renders only.** Blender runs headless (`--background`): there is no window and no viewport screenshot. Windows come later, and only if agents lack information without them.
- **No interrupt.** The add-on cannot stop code that is running, so a call past its limit ends the Blender and loses its scene.
- **One call at a time.** The server answers one request at a time, and the add-on serves one call at a time: a long render holds up every other tool of that session, `blender_list` included.
