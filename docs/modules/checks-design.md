# Checks design

Low-level design of the doc tools (`kitlib/docs`), the repo checks (`forbidden`, `scrub`, `machinepaths`, `pieces`, `trailerscheck`), the module map (`modulemap`) and the maintenance lint (`maintlint`). The high-level design is ARCHITECTURE.md ("Dependency rules", "Security") and wins on a conflict. Where today's tree differs: docs/redesign-work.md (W-012, W-029, W-038, W-039).

## Contract

- **Each check is one package with one question**, read-only, returning findings; its entry point prints and sets the exit code. A check never fixes what it finds.
- **`docs`**: the doc index (every tracked `.md` is listed, with keywords; lookups by topic and section), the doc budget (byte budgets, owner-only doc paths), and the code map, generated on demand and never committed.
- **`forbidden`** refuses a forbidden token anywhere in the tree without writing it down. **`scrub`** finds owner-specific text by its shape on any clone. **`machinepaths`** refuses a hard-coded machine path. Together they keep personal details and machine facts out of tracked files; `commit.py` and the gate run them.
- **`pieces`** maps a path to its piece. **`trailerscheck`** owns the trailers rule: which branch commits lack an agent's Co-Authored-By line. It warns; it never fails a gate or a landing.
- **`modulemap`** is the dependency rules made checkable. The map file (`docs/module-map.json`, owner-only) declares one module per package and exactly the allowed edges. `module_map.py --check` fails a crossing the map does not list; a function-level import counts. There are no ratchets: the map is the end state, and a new edge is a change to ARCHITECTURE.md first. The architecture page is generated from the map. Isolation runs prove a module's tests pass with only what its entry lets it reach.
- **A size line**: a `kitlib` file past 1,500 non-test lines fails a test, except the listed test doubles.
- **`maintlint`** is the upkeep lint: it flags on evidence that something is spent or wrong (a duplicate, a path gone, a dead pull, a landed worktree), never on age alone. It publishes one page to the hub and files at most the one record its gate-time check names.

## Packages and files

| Path | Holds |
|---|---|
| `kitlib/docs/` (`index.py`, `budget.py`, `codemap.py`, `callers.py`) | The doc tools |
| `forbidden/`, `scrub/`, `machinepaths/`, `pieces/` | One check each |
| `trailerscheck/` | The trailers rule and the gate step that warns |
| `modulemap/load.py`, `check.py`, `detectors/`, `isolate.py`, `page.py`, `scope.py`, `scopelog.py` | The map, the edge check, the crossing detectors per language, isolation runs, the page, the scope report |
| `maintlint/` | The run, one file per family of findings, the page, the publish, the lock slot |

## State

`docs/INDEX.md`, the module map and the generated page are tracked. `maintlint` keeps the last run's `result.json`; `modulemap.scopelog` keeps scope records where `records` places them.

## Errors

A check that cannot read what it checks reports that as a finding or a failure; it never passes by default.

## Adding to it

A new repo check: a package returning findings, an entry point, a gate step in `.kit.json`. A new language for the module map: a detector module. A new lint: a file in `maintlint` that flags on a fact.

## Tests

`tests/docs`, `tests/forbidden`, `tests/scrub`, `tests/machinepaths`, `tests/pieces`, `tests/trailerscheck`, `tests/modulemap`, `tests/maintlint`.

## Reference

docs/doc-tools.md, docs/repo-checks.md, docs/shipping.md ("The built-in scrub"), docs/module-map.md, docs/module-architecture.md (generated), docs/maint-lint.md.
