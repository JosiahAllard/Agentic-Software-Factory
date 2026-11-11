# Config design

Low-level design of `kitlib/config`. The high-level design is ARCHITECTURE.md ("Layers", facts; "Data ownership") and wins on a conflict.

## Contract

- **`.kit.json` is a repository's whole kit configuration**, optional. `config.load` reads it and checks it whole against `config.schema`; an unknown key or a wrong type is an error with the key named. With no file, the defaults of docs/kit-json.md "With no file" apply.
- **The schema is the documentation**: docs/kit-json.md is `schema.doc_lines()`, and a test keeps the two equal.
- **Rules a branch is judged by are read from the base's committed file**, never the working tree: gate config, push rules, owner paths.
- **`config.owner`** is the one rule for owner-only paths and the owner's identity. Every tool asks it.
- **`config.policy`** reads the kit's policy from config: heavy programs, roles, the kinds table, the repo hook module.
- **The machine config** is the machine's own settings. It lives in the PM repository's `.pm-kit/` folder, found through the PM-repo pointer (a machine registry value on Windows, a file on Linux). `.kit.json` and env never override it. The kit holds no machine path.
- **The PM-data record** says where the PM's data worktree is; `config` reads and checks it.
- **PM settings**: the machine config's `pm_start` section and the repositories it names are read here, by the session starts, the hub and the launcher alike.
- A test process is fenced from the real home: reading the machine config there raises `HomeFenced`.

## Packages and files

| Path | Holds |
|---|---|
| `load.py`, `schema.py`, `types.py` | Read and check `.kit.json`; every section, key, type, default and doc line |
| `owner.py`, `policy.py` | Owner paths and identity; heavy programs, roles, kinds, hook module |
| `user.py`, `pointer.py`, `copies.py` | The machine config, the PM-repo pointer, which kit copies would refuse a new key |
| `pmdata.py` | The PM-data record: file name, read, check |
| `pm.py` | The `pm_start` section, the known repositories |

## State

`.kit.json` (tracked, per repository); the machine config and the PM-data record (in the PM repository); the pointer (machine-wide). `config` writes only the machine config keys an owner tool sets.

## Errors

`ConfigError` names the file and the key. `HomeFenced` in a fenced test. A missing pointer or an unreadable machine config is reported as such; no tool falls back to a guessed path.

## Adding to it

A new key: one `Field` in `schema.py` with its type, default and doc line, then regenerate docs/kit-json.md. A machine fact goes in the machine config, never `.kit.json`. A key that would let a branch loosen a rule is read from the base, or does not exist.

## Tests

`tests/config`: the schema against docs/kit-json.md, every type, the pointer, the fence.

## Reference

docs/kit-json.md, docs/pm-data-branch.md, docs/machine-setup.md ("PM repo pointer").
