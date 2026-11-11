# Test tools design

Low-level design of `kitlib/pytests`, `kitlib/tooldeps`, `kitlib/testlist`, `kitlib/ci` and `kitlib/redfirst`. The high-level design is ARCHITECTURE.md ("Testing") and wins on a conflict.

## Contract

- **Tests live in `tests/<pkg>/`** as unittest tests, import the library, and import a CLI only to test its arguments and exit codes.
- **A change runs the tests it can break.** `tooldeps` derives the import and mention graph; `kit_tests.py --select` runs the modules a branch's change reaches, plus the smoke set. `tests/selection.json` names what always runs and what widens a pick. The gate's `kit-suite` step runs this selection, on a branch and at a landing.
- **A full run is the daily run's.** `kit_tests.py --full` exists for it; a run by hand goes through `gate.py --full`. No gate or landing requires one, except that a change to a gate config file runs every step in full once, at its landing.
- **Two passes.** Tests run on the fake git; classes marked `real_git` run on real git. The daily run does both.
- **Each unit runs in its own process** (`pytests`) or a pooled interpreter (`tooldeps.pool`), inside a job, with a unit timeout. A test child starts fenced: no session binding, no real home.
- **A time budget in a test is a fence, not an assertion on speed** (`kitlib.clock`): its expiry is a visible, normal output, so machine load never decides a verdict.
- **`testlist`** lists every test name of a tree, so a move that loses a test shows.
- **`ci.ratchet`** holds the other OS's failing tests to a baseline that only shrinks.
- **Red first.** `redfirst.py` runs named new tests against the pre-change commit in a throwaway worktree and confirms they fail there. A fix lands with that proof; never an in-place mutant.
- `pytests` does not import `tooldeps`: the child's audit is handed in by `tools/pytests.py`.

## Packages and files

| Path | Holds |
|---|---|
| `pytests/runner.py`, `child.py` | The parent and the child of the parallel runner for a repository's tools tests |
| `tooldeps/deps.py`, `childenv.py`, `testcost.py` | Which tests a change can break; the child's environment; per-day cost |
| `tooldeps/selftree.py`, `selfrun.py`, `selflist.py`, `pool.py`, `pool_worker.py`, `kitrun.py`, `tiers.py` | The kit's own runner: selection, the worker pool, the two passes |
| `testlist/` | Test names from result files, and their diff |
| `ci/ratchet.py` | The ratchet |
| `redfirst/run.py`, `verdict.py` | The red-first run; its pure parts |

## State

`tests/selection.json` (tracked). Per-unit times and costs in the kit's temp folder. A red-first run's worktree is a scratch tree, removed at its end (`worktree.scratch`).

## Errors

`kit_tests: OK` is the one pass line; anything else is a failure. A unit that times out is a failure naming the unit. A red-first run whose tests pass on the old commit says so and exits non-zero.

## Adding to it

A test for package `x` goes in `tests/x/`. A test that must always run, or a path that must widen the pick, is a row in `tests/selection.json`. A new runner for red-first: `.kit.json` `redfirst.runners`.

## Tests

`tests/pytests`, `tests/tooldeps`, `tests/testlist`, `tests/ci`, `tests/redfirst`.

## Reference

docs/test-tools.md, docs/redfirst.md, docs/daily-full.md.
