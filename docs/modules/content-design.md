# Content design

Low-level design of `kitlib/content`, `kitlib/githooks` and the `content/` folder. The high-level design is ARCHITECTURE.md ("Content") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-008).

## Contract

- **Every role and skill is written once**, in `content/roles/<role>.md` and `content/skills/<name>/SKILL.md`. Roles are referred to by bare name; each host's instructions say how a name maps to its dispatch name.
- **`render_content.py --write` renders** the roles into each host's agents and manifests, with the kit's one version from `kit.json`. Skills are not copied: every host loads `content/skills/` itself, so skill text is the same everywhere.
- **Rendered files are committed and never edited by hand.** Each one that can hold a comment opens with a generated-file line naming its source. `commit.py` and the pre-commit hook render when a source is staged; the guard refuses a hand edit; a test fails on drift.
- **The role list lives only in `content/roles/`.** `content.roster()` is its one reader: docs/ROSTER.md's tables, the launcher's agent list and the backlog policy's role groups all come from it. A role is added or removed with one file and a render.
- **A role file names no model.** A role has a tier; each host maps tier to model, and gives a role its own model only in `hosts.json` `role_models`.
- **A role's mode** (`mode:` in its file) is what the guard's role policy reads; `.kit.json` `roles.<role>.mode` overrides it.
- **`content/default-profile.md`** is the agent profile a role reads in a repository with none. It is never rendered.
- **Dispatch rules live in the skills**, not in code: which role takes which work, when a small known-cause fix may go to a lighter worker on a High-risk path (its review stays strong), who reviews.

## Packages and files

| Path | Holds |
|---|---|
| `kitlib/content/__init__.py` | Load roles and skills, `roster()`, `names_of()`, render through each host's `ContentRenderer`, drift check |
| `kitlib/content/roster.py` | docs/ROSTER.md's tables from the roles and each host's model maps |
| `kitlib/content/oncommit.py` | Is this a kit checkout, which staged paths need a render |
| `kitlib/githooks/` | The pre-commit dispatch: the render step, then the opt-in ledger check |
| `content/roles/`, `content/skills/`, `content/default-profile.md`, `content/README.md` | The sources |

## State

The sources and the rendered files, all tracked. No cache.

## Errors

A role file that fails its schema, a role one host cannot render, or a rendered file that differs from its source fails the render and the drift test with the file named.

## Adding to it

A role: one file in `content/roles/`, then render. A skill: one folder in `content/skills/`. A rule for agents goes in the skill that owns the task, once; a role file points at it.

## Tests

`tests/content` (drift, role names found in prose and code, the roster), each host's renderer tests.

## Reference

docs/content.md, content/README.md, docs/ROSTER.md, docs/specialty-skills.md, docs/shipping.md.
