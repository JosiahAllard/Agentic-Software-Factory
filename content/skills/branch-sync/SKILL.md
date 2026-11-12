---
name: branch-sync
description: Use when the coordinator says the main branch moved and your branch must catch up. Covers merging the exact named commit, what never to do (rebase, local main, force-push), conflicts on your own branch, and re-gating.
---

# Bringing a branch up to date

Merge only the coordinator's hash, by step 3's command: no `git fetch`, `git status`, `git merge` first. Each command one plain call (no `cd`, launcher, nested shell): kit tool (`<kit>/tools/...`) with `-C <worktree>`.

1. Commit WIP by path (`python <kit>/tools/commit.py -C <worktree> -m "<subject>" --model "<model name>" <path> ...`): tracked tree clean.
2. Untracked generated files the base now tracks block merge: delete only those the profile's "Branches and worktrees" names.
3. Merge: `python <kit>/tools/sync_base.py -C <worktree> <hash> --model "<model name>"`. Fetches; refuses hash not on fetched remote main (never substitute or redo another way, commit.py refuses too: stop, tell coordinator); merges exactly it; checks regrowth; trailer on merge commit (fast-forward: none).
4. Regrowth (shrunk file back big, stale branch restoring deleted text): `git checkout <hash> -- <path>`, re-apply own change, commit.
5. Conflicts (merge in progress) in your files: file-edit tool on branch, one command per call, no scratch script rewriting tracked files.
6. Conflicts not yours: `git merge --abort`, tell coordinator.
7. Docs, backlog conflicts: never both sides blindly (union resurrects retired rows). `git diff <hash> -- <file>`, justify every added and deleted line; cannot: stop.
8. Finish: `python <kit>/tools/commit.py -C <worktree> -m "<subject>" --model "<model name>" <resolved path> ...` (repo's `"commit".merge_fold` passes merge's staged files; tool adds trailer).
9. After sync: verdict carried or renewed first (skill land-branch); gate on merged tree when asked: `python <kit>/tools/gate.py -C <worktree>`, flags per `<kit>/docs/gate-run.md` "The final gate" (landing refuses tip without right record). Name string proving merge built.
10. Report ready: merged hash, touched-test results; gate verdict line only if asked.

## Never

- Rebase, merge local `main` or `origin/...` by name, force-push, reset, rewrite history; push.
- `git commit --no-edit` (refused); non-tool commit identity.
- Resolve others' conflicts.
- Redo refused sync, guard/hook merge block another way (`git merge --no-commit` then commit.py, other tool, form): stop, report exact command, message to coordinator.

Details: `<kit>/docs/sync-base.md`.
