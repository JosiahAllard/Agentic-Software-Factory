# Backlog operations: rare cases

Read from `SKILL.md` when the case comes up.

## block --until

A hold a program can check: one `--on <member>` with `--until "<condition>"`: `todo:T-n done`, `question:Q-n answered`, `approval:A-n decided`, `record:<repo>:<ID> closed`, `path:<path> exists|absent`, `plugin:<folder> absent`. The hub clears it when met. On `owner`: only the first three, and a coordinator may set one only when its block adds `owner` or the hold already ends on such lines (`<kit>/docs/backlog.md` "block --until").

## Store kinds

`main` writes only the files in your worktree (nothing staged; it refuses the main checkout); the PM data worktree commits by itself; `ref` and `branch` land on the store at once and never touch the checkout.
