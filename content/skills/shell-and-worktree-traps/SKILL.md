---
name: shell-and-worktree-traps
description: Use before running shell commands on the Windows or Linux machine (Git Bash or PowerShell on Windows, bash on Linux), checking line endings, capturing test output, or removing worktrees and branches. Covers silent shell traps, one section per platform, that produce wrong or stray results, and the worktree and gate rules workers break by accident.
---

# Shell and worktree traps

Both platforms; then only your platform's section.
- Absolute paths; your assigned worktree.
- Script with paths, regexes, escapes: a file (file-write tool), run by absolute path. No such tool: small quoted-marker heredoc chunks (`<<'EOF'`, appended `>>`); one long heredoc fails "unexpected EOF looking for a matching quote"; the same form again fails again.
- Shell probes: cwd = scratch, never the main checkout (a stray there stops every landing).
- Line endings: committed blob, `git show <rev>:<path>`; `.gitattributes` normalises on commit.
- Test output: tee all to a file, read it; never grep live (a case-sensitive filter matched the pass banner, missed the failure). Gate or locked run: never through a truncating reader.
- Subprocesses: argument array, shell disabled.
- One foreground gate per worktree, assigned ports; no stray `&` (two fought over one build).
- Cleanup: `python <kit>/tools/worktree_remove.py -C <main checkout> <worktree>`, your worktrees only; else report to lander or coordinator.
- Usage: `<tool> --help` alone or into a file you Read; never piped to `head`, `tail`, `grep`.
- Wait on a background run per <kit>/docs/gate-records.md "Waiting on a gate: who arms what" (own run: `run_in_background` + exit notice); never foreground `sleep` (blocked) or a poll loop. Other conditions: `run_in_background` until-loop (`until <check>; do sleep 5; done`), or Monitor with `description` and `timeout_ms` (else fails).
- Never: `git worktree remove --force`; `git branch -D`; delete another session's branch or take over its run; kill by name or command line (other sessions' gates match): only your own process, by pid, start time unchanged; move an assigned worktree (configured location holds unless the owner assigns another).

Details: `<kit>/docs/worktree-remove.md`; `<kit>/docs/new-worktree.md`.

## Windows

1. Never leave a shell in a worktree being removed: `cd` holds a handle; `git worktree remove` fails "Permission denied".
2. Git Bash `grep -c $'\r'` matches every line: always-true CRLF test.
3. Bash heredoc mangles backslash paths (one worktree mangled, force-removed), `\\` to `\` in regexes, `\n`/`\0` to real newline/NUL in Python: use a file.
4. `[IO.File]::ReadAllText` / `WriteAllText`: relative path = PROCESS cwd (often main checkout), not `$PWD`; failed read `$null`, then an empty stray written. File-edit tool or absolute path.
5. `python - @'...'@`: here-string is an argument, not stdin; REPL loops, fills disk. Script file, or pipe it in.
6. ONE-object result: bare scalar, `.Count` = `$null`, `$x.Count -gt 0` passes silently. Wrap in `@()`.
7. Test output also: `dotnet test --logger trx`.

### Never (Windows)

- `[IO.File]` or `[System.IO]` with a relative path.
- Kill by name: `Stop-Process -Name`, `taskkill /IM`, `CommandLine -match ... | Stop-Process`.

## Linux

1. Shell in a worktree being removed: `git worktree remove` fails or strands it in a deleted directory.
2. `grep -c $'\r'` counts real CRs; a Windows-edited checkout may differ from the blob.
3. Unquoted heredoc (`<<EOF`) expands `$var`, backticks, `\` before Python or sed: quote the marker (`<<'EOF'`) or use a file.
4. Quote paths and variables: `"$x"`, never bare `$x` (spaces, globs split or expand). `rm -rf $dir/`, `dir` unset = `rm -rf /`; `--preserve-root` catches only that form, `$dir/*` nothing.
5. Pipeline status = last command's: `cmd | tee log` green when `cmd` failed. `set -o pipefail`, or file + `$?` of the command. Truncating reader (`head`): writer dies of SIGPIPE.
6. cwd set by a sourced file, `cd` or subshell does not carry between tool calls: absolute paths or `git -C`.
7. No `nohup` or bare `&` (survival depends on the host's shell tool): `run_in_background`.
8. Executable bit, symlinks: as git recorded; no `chmod -R` on a tree; no `cp -r`/`rm -rf` through a symlink out of the worktree.

### Never (Linux)

- Kill by name: `pkill -f`, `killall`, `kill $(pgrep ...)`.
- `sudo`, `chmod -R`, `rm -rf` on a variable-built path.
