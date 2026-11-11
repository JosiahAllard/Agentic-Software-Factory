# Git design

Low-level design of `kitlib/git`. The high-level design is ARCHITECTURE.md ("Layers", facts) and wins on a conflict.

## Contract

- **Every kit git child goes through `git.cmd.call()`**: a scrubbed environment, no prompt, no console window, a timeout. No other package starts `git`.
- **`git.repo` answers where a path is**: which repository, which work tree, whether it is the main checkout, the common dir. **`git.refs`** answers branch, commit and ancestry questions. Both only read.
- **`git.batch`** serves many reads from one git process.
- **`git.checkout`** is the write tools' main-checkout refusal: one rule, one message.
- **`git.defaults`** says what a repository with no `.kit.json` setting says about itself (main branch, name).
- **`git.stray`** lists stray changes in a main checkout.
- **`git.lockplace`** says where a file's write lock goes: in the git common dir when the file is in a checkout, beside the file otherwise.
- **`git/fake`** is an in-process git over the real on-disk repository format. The kit's unit tests run on it; it answers the forms the kit uses, byte for byte with real git, and nothing in production code calls it.
- `git` knows nothing of the backlog, worktree policy, config or hosts. It imports only the base layer.

## Packages and files

| Path | Holds |
|---|---|
| `cmd.py` | `call()` and its variants, the scrubbed environment |
| `repo.py`, `refs.py`, `batch.py`, `cache.py` | Detection, queries, the batched reader, the test-only memo |
| `checkout.py`, `stray.py`, `defaults.py`, `lockplace.py` | One rule each |
| `fake/` | The test double: object store, refs, index, work tree, diff, merge, history, remote, worktree commands |

First-use repo prep and the branch-overlap read are worktree features and live in `worktree`.

## State

None of its own. `git.cache` memoises object answers in test processes only.

## Errors

A git child that fails returns its code and text; callers decide. A read that cannot tell (an unreadable ref, no repository) says so; it never guesses a default.

## Adding to it

A new git read: a function in `refs.py` or `repo.py` over `cmd.call()`. A git form the kit starts using: teach `git/fake` the form, and mark a test `real_git` only when the fake cannot stand in. A feature that needs config or records belongs in a higher package.

## Tests

`tests/git`. The fake is checked against real git by the `real_git` classes and the daily run's second pass (docs/test-tools.md "Fake git").

## Reference

docs/worktree-paths.md, docs/preflight.md (the checkout guard), docs/test-tools.md.
