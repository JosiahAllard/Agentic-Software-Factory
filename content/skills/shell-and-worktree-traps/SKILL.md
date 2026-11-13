---
name: shell-and-worktree-traps
description: Use before running shell commands on the Windows or Linux machine (Git Bash or PowerShell on Windows, bash on Linux), checking line endings, capturing test output, or removing worktrees and branches. Covers silent shell traps, one section per platform, that produce wrong or stray results, and the worktree and gate rules workers break by accident.
---

# Shell and worktree traps

Both platforms; then only your platform's section.
- Absolute paths; assigned worktree.
- Script with paths, regexes, escapes: file (file-write tool), run by absolute path. No tool: small quoted-marker heredoc chunks (`<<'EOF'`, appended `>>`); one long heredoc fails, again on retry.
- Shell probes: cwd scratch, never main checkout (stray stops every landing).
- Line endings: committed blob, `git show <rev>:<path>`; `.gitattributes` normalises on commit.
- Test output: tee to file, read it; never grep live (filters miss failures). Gate, locked run: no truncating reader.
- Subprocesses: argument array, no shell.
- One foreground gate per worktree, assigned ports; no stray `&`.
- Cleanup: `python <kit>/tools/worktree_remove.py -C <main checkout> <worktree>`, own worktrees only; else report to lander/coordinator.
- Usage: `<tool> --help` alone or to file you Read; never piped to `head`, `tail`, `grep`.
- Background run wait: <kit>/docs/gate-records.md "Waiting on a gate: who arms what" (own: `run_in_background` + exit notice); never foreground `sleep` (blocked) or poll loop. Other conditions: `run_in_background` until-loop (`until <check>; do sleep 5; done`), or Monitor with `description`, `timeout_ms` (else fails).
- Never: `git worktree remove --force`; `git branch -D`; delete another session's branch, take its run; kill by name or command line (others' gates match): own process only, by pid, start time unchanged; move assigned worktree (configured location unless owner reassigns).

Details: `<kit>/docs/worktree-remove.md`; `<kit>/docs/new-worktree.md`.

## Windows

1. No shell in worktree being removed: `git worktree remove` fails "Permission denied".
2. Git Bash `grep -c $'\r'` matches every line: always-true CRLF test.
3. Bash heredoc mangles backslash paths, `\\` to `\` in regexes, `\n`/`\0` to real newline/NUL in Python: use file.
4. `[IO.File]::ReadAllText` / `WriteAllText`: relative path = PROCESS cwd, not `$PWD`; failed read `$null`, then empty stray written. File-edit tool or absolute path.
5. `python - @'...'@`: here-string is an argument, not stdin. Script file, or pipe it.
6. ONE-object result: bare scalar, `.Count` = `$null`, `$x.Count -gt 0` passes silently. Wrap in `@()`.
7. Test output also: `dotnet test --logger trx`.
8. Git Bash rewrites a one-slash Windows flag into a path (`taskkill /PID 123` becomes `taskkill <Git install folder>/PID 123`): double it, `taskkill //PID <pid>`.

### Never (Windows)

- `[IO.File]` or `[System.IO]` with relative path.
- Kill by name: `Stop-Process -Name`, `taskkill /IM`, `CommandLine -match ... | Stop-Process`.

## Linux

1. Shell in worktree being removed: `git worktree remove` fails or strands it in a deleted directory.
2. `grep -c $'\r'` counts real CRs; Windows-edited checkout may differ from blob.
3. Unquoted heredoc (`<<EOF`) expands `$var`, backticks, `\` before Python/sed: quote marker (`<<'EOF'`) or file.
4. Quote: `"$x"`, never bare `$x` (spaces, globs). `rm -rf $dir/`, `dir` unset = `rm -rf /`; `--preserve-root` catches only that, `$dir/*` nothing.
5. Pipeline status = last command's: `cmd | tee log` green when `cmd` failed. `set -o pipefail`, or file + `$?`. Truncating reader (`head`): writer dies of SIGPIPE.
6. cwd from sourced file, `cd`, subshell does not carry between tool calls: absolute paths, `git -C`.
7. No `nohup`, bare `&`: `run_in_background`.
8. Executable bit, symlinks as git recorded; no `cp -r`/`rm -rf` through symlink out of worktree.

### Never (Linux)

- Kill by name: `pkill -f`, `killall`, `kill $(pgrep ...)`.
- `sudo`; `chmod -R` on a tree.
- `rm -rf` on variable-built path.
