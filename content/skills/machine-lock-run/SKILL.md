---
name: machine-lock-run
description: Use before any heavy work on the shared machine - builds beyond the ordinary gate, mutation or stress runs, networked end-to-end runs, renders, local-AI generation, training - to wrap it in the machine lock or its background mode, read status, and handle busy and reservation refusals.
---

# Machine lock

Heavy work locked, light (ordinary gates, unit tiers) never. Agent run max agent cap of cores.

1. Look first: `python <kit>/tools/machine_lock.py status` (queue positions, expected starts).
2. Book only through a kit wrapper. Self-booking kit tool (`kitlib.lock.run.book`, or re-runs itself under `machine_lock.py run`): run directly, never under `machine_lock.py run`. Wrappers: `<kit>/tools/gate.py` (gates), `<kit>/tools/land.py` (landings), `<kit>/tools/daily_full.py` (daily full runs), `<kit>/tools/redfirst.py` (red-first runs), `<kit>/optional/comfy/comfy_gen.py` (local-AI generation; `up`/`down` per session), `<kit>/optional/comfy/render_glb.py` (renders). Repo runners: wrappers only if booking the kit lock; profile names them (`python <kit>/tools/doc_index.py -C <worktree> --find lock`). Other heavy: step 3.
3. `python <kit>/tools/machine_lock.py run --repo <repo> --slug <slug> --kind <kind> [--cores N] [--within THREADS] [--expect D] [--max-wait 5m | --no-wait] [--resource NAME] -- <command>`, kind, ports from brief; `--resource`: one-holder shared thing (`ports:e2e-a`). Opted-in repo may route to a load node (`running on load node NAME: <why>`); kept here by `--node main`, `--resource`, `--cores`, `--hold`, `--within`, uncommitted tracked changes, Windows commands. Node runs committed HEAD from checkout top: no untracked/ignored files; outputs deleted unless `--artifact GLOB`. Training (`--gpu-share training`, pausable): `training.md` beside this file.
4. Kind = size (`<kit>/kitlib/lock/kinds.json`): test, app-run, build medium; graph-build, daily-full, cpu-eval heavy; local-ai, render medium + gpu; measure heavy + gpu; quick-test 1 core. Touched tests only (filtered `gradle test --tests`, `dotnet test --filter`): `--kind quick-test --expect <estimate>`, one call; fits gaps. Repo kinds: `.kit.json` `kinds` section, bookable by `machine_lock.py run` from any checkout. `--cores N` up to agent cap. Known `--expect <duration>`: short run may jump ahead.
5. Threads: `MACHINE_LOCK_CPUS` (also `MACHINE_LOCK_CPUMASK`), never assumed. Container: pin (`docker run --cpuset-cpus "$MACHINE_LOCK_CPUS"`, or `docker update <container>` before `docker exec`); in-container `timeout -k 30 <limit>`; `--on-release "<command that kills it>"`.
6. `--hold <id>`: only owner/PM-given (exit 12 if unusable). `--detach` prints `<run id> pid <n>`; follow: `status <run id>`. `--within <threads>` (Linux cpuset list, e.g. 8-15, container's `--cpuset-cpus`): whole slots inside only; `--cores N` <= those.
7. Output to log; read at end.
8. Past `warn_min`: WARNING, no kill; check progress.
9. Stop own run: `python <kit>/tools/machine_lock.py stop <run id>`.

Refusals: exit 6 busy (`--no-wait`), 8 `--max-wait` out. 11: reservation leaves too few slots: wait, ask coordinator; `--expect` if shorter than guide. 2 nested: exceeds outer run's hold; run outside. 2 `--within`: malformed, no whole slot, fewer than `--cores`, or with `--hold`. 9 (LOST, node), 14 (started or reply lost, no exit code): may run; printed follow/stop lines, never rerun. 13: artifacts lost. 10 (lock not taken), 1 (`could not start the command`), 12: report. Bare `bash` = Git Bash, never WSL; missing: refused pre-booking, give full path. Gate's build daemons stopped only if that gate alone used them (machine config `machine_lock` `"daemons"`). Another session's exclusive window: pause gates, builds, heavy tests, landings until closed.

## Never

- Heavy work unlocked because busy; env variable as lock proof.
- Truncate locked run's live output (`head`, first-N filter).
- Touch others' runs, records, processes; `stop --force`, `hold`, `division`, `game`: report run id + `status` text to coordinator.
- Unlabelled measurement under load: label "shared machine".

Details: `<kit>/docs/lock-api.md`; read-only lock tools over MCP: `<kit>/docs/lock-mcp.md`.
