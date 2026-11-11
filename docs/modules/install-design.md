# Install design

Low-level design of `kitlib/pluginsource`, `kitlib/allowrules`, `kitlib/machinesetup`, `kitlib/stackskill`, `kitlib/kitcmd`, `kitlib/pmdata`, and the `installer/` and `templates/` folders. The high-level design is ARCHITECTURE.md ("Packaging") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-005, W-019, W-020, W-036, W-041, W-042, W-043).

## Contract

- **The machine runs the kit from an export of pushed main**, never from a checkout and never from a copy in a repository. `pluginsource` owns the export: its live root, one folder per exported commit, the marker that says what a folder is, pruning of old folders, and the lock a refresh holds.
- **A refresh** exports the new pushed main, re-points every account's plugin at it, and leaves running sessions on the folder they started from until they restart; the hub runs it as a timed job, and it can be run by hand.
- **Gate and landing tools pin themselves** to an export of pushed main (docs/modules/gate-design.md); everything that prints a kit command names the live export.
- **Machine setup runs once per machine and writes nothing into any repository.** It surveys, prints a plan, and applies it: the PM-repo pointer, each account's settings and plugin, managed settings, the agents' temp folder and its sweep, the scheduled tasks, the `kit` command on the PATH. Every step is idempotent and reports what it changed.
- **A repository needs nothing installed.** One that wants a committed setup asks for it (`machine_setup.py --repo`): a `.gitignore`, a starter doc index, a gate config for its stack, and the stack's skills (`stackskill`) with the kit's location written in.
- **Scheduled tasks are registered through `schedtask`**; machine setup only decides which exist.
- **`allowrules`** prints the permission rules that let an agent submit a landing unprompted, and finds rules that still name an old export.
- **`kit <tool>`** runs a tool from the export the machine uses now.
- **`pmdata`** makes or re-links the PM-data branch and its worktree; the record that says where it is belongs to `config`.
- **Setup tools are the owner's**: the guard refuses an agent the setup scripts and the managed settings file.
- **There is no pinned release.** The export of pushed main is the one way the kit ships; `boot/kitboot.py` checks the kit it boots and nothing more.

## Packages and files

| Path | Holds |
|---|---|
| `pluginsource/layout.py`, `export.py`, `state.py`, `refresh.py`, `prune.py` | Where the export is and its marker; writing one; each account's state; the refresh; pruning |
| `allowrules/` | The rule text and the scan |
| `machinesetup/__init__.py`, `common.py` | The survey, the plan, the apply |
| `machinesetup/settings.py`, `plugin.py`, `managed.py`, `pointer.py`, `owner.py`, `machineconfig.py` | Per-account settings; the plugin per account; managed settings; the pointer; the owner's email; machine config keys |
| `machinesetup/extras.py`, `agenttemp.py`, `worktreesweep.py`, `procwatch.py`, `kitcommand.py`, `links.py`, `inventory.py`, `memory.py` | The extras, one each; the account inventory; a repository's memory folder |
| `machinesetup/repo.py`, `linuxbox.py`, `linuxjobs.py`, `delegation.py` | The committed repo setup; the Linux box checks, jobs and containment delegation |
| `stackskill/`, `kitcmd/`, `pmdata/init.py` | One job each |
| `installer/` | `machine-setup.ps1`, `machine-setup.sh`, `new-repo.ps1`: front ends that check arguments and run the tool |
| `templates/common/`, `templates/stacks/<stack>/` | What the committed setup copies, or what a role (`content/roles/`) or `docs/install.md`'s hand steps draft or copy from |

## State

The export folders under the live root; each account folder's settings and plugin state; the machine config keys setup writes; the registered tasks. Setup reads before it writes and never touches a repository's tracked files outside `--repo`.

## Errors

`RefreshError` and `StateError` for an export or account that cannot be read or moved; a busy refresh lock is `Busy`. A setup step that fails reports the step and leaves the rest of the plan unapplied.

## Adding to it

A new machine step: a survey function returning plan items, in `machinesetup`. A new stack: a folder under `templates/stacks/` with its `stack.json`. A file under `templates/` belongs there only if a setup step copies it, or a role (`content/roles/*.md`) or `docs/install.md`'s hand steps draft or copy from it; `tests/templates/test_all_copied.py` checks it.

## Tests

`tests/pluginsource`, `tests/machinesetup`, `tests/allowrules`, `tests/stackskill`, `tests/kitcmd`, `tests/pmdata`.

## Reference

docs/plugin-source.md, docs/machine-setup.md, docs/install.md, docs/allow-rules.md, docs/pm-data-branch.md, docs/kitboot.md, docs/shipping.md.
