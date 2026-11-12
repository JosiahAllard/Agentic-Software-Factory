---
name: mutation-testing
description: Use when you write or review a guard/regression test, or build or run a mutation-testing harness (mutate a production line, expect a test to fail). Covers why a guard stays green when it is decorative, stale or never ran, and the harness traps that score a mutant "caught" without testing it.
---

# Guard tests and mutation harnesses

1. Every guard shown failing: mutate guarded line, see fail, restore. Unfailable = decorative.
2. Mutate in throwaway worktree at pre-change commit; never in place, others' checkouts, shared kit. Heavy/repeated suites: machine lock (skill machine-lock-run).
3. Input silently substituting for the tested thing = test green when deleted: prevent substitution, or assert which ran.
4. Absence assertion ("X does NOT appear"): pair with positive case, same class, really producing X.
5. Assert named table cell, split by printed header; never whole row string (`'| 0 | -' not in row` passes when a neighbour changes).
6. Lock: assert mutation cannot LAND, not thread BLOCKED.
7. Green after mutation: decorative guard; stale anchor, never applied; no-op. Compare compiled bytecode (or equivalent).
8. Report "N of M mutations actually tested something", never "all caught".

Harness traps, silently mis-scored:
- Wrong checkout: assert mutated file is in the tested worktree.
- Stale `.pyc`: CPython keys `__pycache__` on mtime (whole seconds) + size; same-size rewrite within a second runs old code. Delete `__pycache__`, vary size, or `-B`.
- Filter with `|` through a shell = pipe (Windows command shell, /bin/sh on Linux): no result line, nothing tested. argv list, no `shell=True`.
- Missing result line fails loudly; never pass or catch.

## Never

- Count compile error, missing result, behaviour-neutral edit as caught.
- Leave mutation applied.

Details: the repo profile, "Build and test".
