# Linux support plan

Status: planned 2026-10-25, revised 2026-10-26 to the owner's eleven answers (section 10), and
built through slice 14. Open: slice 15 (peer messaging and the bridge, after parity), slice 17 (CI
legs; there is no `.github`), the rest of slice 16 (docs), and the live proofs on a Linux machine
that sections 8 and 8.1 name. The code and its docstrings carry what was built; this file keeps
the decisions, the open items and the status of each slice.

It extends, and for Linux supersedes, the section "6. Platform seam (later refactor: Linux and macOS)" of docs/multi-host-plan.md.

Goal (the owner's): the whole kit runs on Linux out of the box, on any Linux distribution, at
parity with Windows: the PM, coordinators, the launcher, the hub, the guard, the machine lock,
the gate, landings, scheduled jobs, machine setup, plugin refresh, skills and docs. Parity means
without peer messaging and the bridge (after parity, question 7) and without macOS (out, question
1). The first goal is the plugin working on Linux; CI for Linux and Windows is a later phase
(question 8). Windows behaviour does not change: each slice kept the Windows code path as it was
and proved it with the existing Windows suite.

Evidence labels: "verified" is read in the code at the named symbol; "inferred" is reasoned from
code without running it on Linux; "unknown" needs a probe on a Linux box.

## 1. Where things stand

### 1.1 The front doors

- tools/kit.py refuses darwin, every other non-Windows non-Linux platform, and WSL
  (kitlib.machinesetup.linuxbox.front_door; question 11). Linux is let through (slice 14).
- kitlib/platform/windows/win32.py binds its DLLs lazily (kitlib.platform.dll.LazyDLL), so it and
  the modules built on it import on any platform, and a call off Windows raises PlatformUnsupported.
- The hook manifests: hookrun.INTERPRETERS is `{'windows': 'pythonw', 'linux': 'python3'}`
  (MCP_INTERPRETERS: `python`, `python3`); hosts.json lists windows and linux for both hosts with
  per-platform `process_names`; each host renders `hooks.linux.json` beside its `hooks.json`
  (slice 12). The guard declares Linux ready: kitlib.guard.READY_PLATFORMS.

### 1.2 What is still Windows-only

- The peer messaging and the bridge: hosts/claude/adapter/peer.py (Claude Code's `\\.\pipe\`
  address), optional/bridge/win/, kitlib/machinesetup/procwatch.py, and
  kitlib/platform/windows/console.py and winwatch.py. Slice 15; the console and window ones have
  no Linux need.
- The delegation runner, kitlib/delegations/place.py and run.py: drive paths, `.exe` and the
  registry (winreg). Windows only by design; it does not refuse on Linux, and its tests that need
  a junction or the real registry carry `windows_only`.
- The 21 files outside tests/ that still read os.name or sys.platform themselves
  (tests/platform/test_no_os_checks.py ALLOWED, 21 entries). The list only shrinks.
- The end-to-end landing tests skip on Linux ("the lock is Windows only"; in tests/landing/test_land*.py
  only a few unit tests run), so a landing has not run end to end there (slice 9, open proof c).
- Build-daemon sparing and the TCP connection table: kitlib/proc/tcp.py has only the Windows reader
  (GetExtendedTcpTable) and kitlib/proc/daemons.py is Windows only, so on Linux the end-of-run
  check (lock/run_end.py and run_held.py, `daemons.find`) only warns and kills the tree. The planned
  form reads /proc/net/tcp and tcp6, whose parsers after the table are portable. Open, in no slice.
- No CI workflow (slice 17). The ratchet (tools/ci_ratchet.py) is run by hand.

### 1.3 Shields

The pid shield (shield.py, a DACL against Claude Code's tree kill), PidHold (handles that stop pid
reuse), console.py (no console window flashes) and winwatch.py (user32 window watch) have no Linux
form, and none is needed. Verified on the reference VM (section 8.1, Claude Code 2.1.312, a local
stand-in API): a probe tree of the call's shell, a child in its group, a grandchild in a session of
its own and one reparented away, each beating.

- A Bash call that finishes, passes its timeout (moved to the background) or runs in the
  background ends with no kill at all, so the stale kill of a reused pid that the Windows shield
  answers never comes.
- Stopping a call (Escape, TaskStop, or SIGTERM or SIGINT to claude) kills its tree by pid: claude
  lists every process (`ps -A -o pid= -o ppid=`), sends SIGTERM to the shell's process group and
  each descendant (the one in a session of its own too), polls every 100 ms and lists again about
  1.35 s later. That it then sends SIGKILL to the group and each one still running is inferred
  (the probe's members ignore SIGTERM, yet all ended, and the call returned exit code 137).
- Each kill seen comes at most about 200 ms after a fresh listing, and pids are handed out in order
  up to pid_max (4194304 on the VM), so a reused pid is out of reach. A process already reparented
  away when claude lists (the kit's detach: a setsid double fork) is not reached; one still mid
  double fork is a descendant and is, while claude is not a child subreaper. That kill is aimed at
  the live tree, as a stop is meant to be.

A later Claude Code may change this; section 8.1 gives the probe to run again.

## 2. The platform layer

### 2.1 The seam

`kitlib/platform/` is the one place that knows the operating system. `__init__.py` picks the
backend once from sys.platform and exports NAME ('windows', 'linux' or 'unsupported'); on any
other platform (darwin included) there is no backend and every primitive raises
PlatformUnsupported naming sys.platform. `api.py` is the contract (names, signatures, docstrings;
no OS code); `dll.py` is the lazy DLL binding; `windows/` re-exports the Windows primitives
unchanged; `linux/` holds process, cpu, contain, locks, detach, schedule, paths and hardware (one
module each; there is no ipc.py: Unix sockets are slice 15).

Rules:

1. Outside kitlib/platform and tests, no module reads os.name or sys.platform. The test
   (tests/platform/test_no_os_checks.py) enforces it with an allowlist of the 21 files that still
   do (section 1.2); it fails on a file that starts asking and on a listed file that stopped.
2. The Windows backend is re-exports, so moving a caller onto the seam changes no Windows
   behaviour; the Windows suite proves each move.
3. No module imports a Windows DLL at import time. platform/windows/win32.py,
   platform/windows/pipe.py and gate/outfile.py bind lazily (first call), and the modules that had a
   module-level `from ctypes import wintypes` take it lazily or behind a guard, so an import on
   Linux works and a call fails with a clear "not on this platform" error. On Windows this moves one
   failure: a DLL or export that is missing fails at first call, naming the function, not at import
   (tests/platform/test_lazy_dll.py).
4. Guard path text goes through one function per shell (kitlib/guard/paths.py norm and the
   shell's resolve_path); hook commands come from one helper (hookrun.manifest_command).
5. A Linux primitive that cannot give the Windows guarantee says so in its name or raises; it
   never silently does less (section 2.3, question 4).

### 2.2 Windows mechanism to Linux mechanism

The mapping is built, except the TCP connection table (section 1.2); the module docstrings in kitlib/platform/linux/ carry it. The decisions that
are not obvious from the code:

- Identity is (pid, ctime). On Linux the seam's ctime is `(boot << 64) | starttime`, where `boot`
  is /proc/sys/kernel/random/boot_id read as one 128-bit integer and starttime is field 22 of
  /proc/<pid>/stat. starttime repeats across reboots, so the boot goes into the value, not beside
  it: a record or nest token written before a reboot names no live process after it, and every
  caller that compares (lock/run.py nest_check, lock/run_end.py end_sparing, proc/process.py alive,
  lock/store.py wrapper_state) refuses it with no change to its code. The nest token stays three
  fields (`<run id>:<pid>:<ctime>`, NEST_RE unchanged); Windows's ctime is still the FILETIME. The
  stamp (lock/run_end.py filetime_now) returns the same encoding. starttime counts clock ticks, so
  two processes can share one; a tie errs safe in both ordering uses. No fixed-width use of ctime
  exists in kitlib, hosts or tools, so a value wider than 64 bits reaches only Python ints, `%d` and
  JSON.
- Named mutexes and RunMutex become fcntl.flock on a lock file opened O_CLOEXEC; the kernel drops
  it when the holder dies, the same "abandoned counts as free" meaning. The treelock takes flock,
  not lockf, because a lockf lock belongs to the process: two descriptors in one process
  (writelock's threads) would never exclude each other, and closing any descriptor on the file
  would drop the lock. Linux locks are advisory, so readers are never blocked.
- The profile is `pwd.getpwuid(os.getuid()).pw_dir`, never $HOME alone (a session can change it).
  The machine pointer is /etc/pm-kit/pointer, else `<profile>/.config/pm-kit/pointer`, never
  $XDG_CONFIG_HOME.
- Scheduled tasks are systemd user `.timer` + `.service` units named `pm-kit-*`, with
  `loginctl enable-linger` only where user systemd exists; cron (`@reboot`) is first-class where
  there is none (question 5). The hub's start at logon is an XDG autostart `.desktop` entry, with a
  shell-profile line where there is no desktop session (cron has no logon form).
- Detach is `systemd-run --user --scope` with user systemd, else `setsid` (a double fork).
- Sessions are a tmux session by default, or a configured terminal command (question 6).
- Windows-only things with no Linux form: pythonw, CREATE_NO_WINDOW, the console hook, winwatch,
  the pid shield (section 1.3), icacls (chmod 0700 and 0600 instead), the `.lnk` (a `.desktop`
  file instead).

### 2.3 The Job contract on Linux

The lock's promises rest on job objects: a slot's processes stay on its CPUs and under its
memory, the whole tree dies with the slot, and no child gets out of it by the means ordinary tools
use. On Linux the nearest mechanism is a cgroup v2 leaf the kit owns. The threat model: a child
that calls setsid, double forks or resets its own affinity, as build tools and daemons do; not a
same-user process that sets out to leave its cgroup (a same-user process can write its own pid into
any cgroup.procs it can write, so this is not a security boundary, where a job object is stricter;
the plan accepts that and states it in the docs rather than defending against a deliberate move).

The owner's answer (question 4): heavy runs must work on Linux, with full cgroup v2 containment on
native Linux, and where nothing stronger exists (Docker included) they go ahead on weak
containment, shown as such; the owner accepts that risk. Settled at plan review: the root step is
offered at setup and declining it is allowed; a box that declines is S2 (or W2 off systemd),
shown as such, and still counts as "out of the box". A default Docker container probes to its
lower tier; the plan never claims strong containment in a container.

What the research (2026-10-26, from documentation) found, which decided the ladder: without root
and without cpuset delegation nothing gives full containment (systemd's default user manager
delegates only pids, memory and cpu); a cgroup namespace grants nothing; a PID namespace kills
every member when its init dies, needs no capabilities, and fails on Ubuntu 23.10 and later
(AppArmor's user-namespace rule, met for the interpreter alone) and under Docker's default seccomp
profile; plain Docker mounts cgroups read-only; a pidfd + subreaper watchdog misses processes
launched for the tree by servers outside it, gives no pinning and no memory cap (RLIMIT_AS breaks
V8, the JVM and Go, so it is dropped); and the one strong route without systemd is hand delegation
by root at boot, the kernel documentation's model.

The ladder, chosen by probing the machine at run time (kitlib/platform/linux/contain.py probe()),
not by distribution name, and never by trusting an exit code:

| Tier | What it is | Strength |
|---|---|---|
| S1 | a writable cgroup2 subtree with cpuset, memory and pids and `cgroup.kill` (kernel 5.14+): a `Delegate=yes` user scope, or the root-delegated /pm-kit directory | strong: CPU pinned, memory capped, guaranteed kill |
| S2 | the same without cpuset (systemd's default delegation) | memory and kill strong, CPU cooperative |
| W1 | a user and PID namespace whose pid 1 is the kit's reaper (contain.py NsJob) | partial: guaranteed kill, CPU cooperative, no memory cap |
| W2 | environment token + pidfd + process group + affinity, a sampled RSS watchdog | weak |

The tier's text (Tier.text) names what it lacks, in the lock's status, the heavy-run start line
and the run record. S2 is shown as partial, not weak. Without systemd, the boot hook
(kitlib.machinesetup.delegation) hands /pm-kit to the kit user and starts the kit's tmux server
(`tmux -L pm-kit`) in /pm-kit/anchor; a process outside that server gets the lower tier and is
told how to start from it (contain.py, "No systemd"). The S1 contract (create, start suspended,
narrow, kill, kill_on_close, stragglers, nesting) is the module docstring of contain.py. Short jobs
(`Job(short=True)`: git/cmd.py, records/inflight.py, machinesetup/extras.py run_tree) share one
carrier scope placed by busctl AttachProcessesToUnit, because a systemd user scope per call costs
about 84 ms on the VM against about 17 ms attached; the limit is that stopping the carrier, or
systemd-oomd choosing it, ends every short job of the process.

Not yet namespace-aware (each safe today; a later record). A pid is a number in one namespace, so
a lock run record names its wrapper's namespace (`pidns`) and a run nested in a W1 job names the
job's (`jobns`); contain.py's docstring (W1, NsJob) says how they are read. Queue entries and background runs
carry no `pidns` and no `jobns` (on Linux the run lock decides a queue entry; bg_ancestor fails
closed); giveback.py and the lock status (api.py: tree, GPU memory) read a record's pid
as this namespace's; the holders search does not look inside a W1 job.

### 2.4 Lock hardware detection on Linux

Built (kitlib/platform/linux/hardware.py, lock/layout.detect_linux; docstring carries the readers):
threads are the allowed set (sched_getaffinity intersected with the online CPUs, ids with gaps
kept, never range(threads)); a container sees the host's CPUs in /sys while its cgroup allows
fewer, so the allowed set also comes from the cgroup's cpuset.cpus.effective.

Decisions: one kit user per box (question 10), so there is no shared store. Settled at plan review,
how it is enforced: without the root step, setup checks per user and warns on a second user; with
it, a machine-wide marker (/etc/pm-kit/kit-user, linuxbox.MARKER) makes setup refuse a second user.
One kit per CPU set: a kit in a container is supported only as the only kit on that host, and
machine setup run inside a container warns and does not refuse. WSL double-books the same way, so
it stays refused until after native parity (question 11).

The proving test divides four synthetic /sys and /proc trees (tests/lock/linux_trees.py: a plain
desktop, a hybrid part, a box with gaps in its ids, a container whose cpuset allows fewer CPUs
than /sys lists) and checks that no two slots share an id. The CI phase adds its runner's own live
/sys as a fifth case; the VM's live run is tests/lock/test_layout_linux.py LiveSys.

## 3. Guard path semantics and the safety review

The guard is the kit's safety boundary, so its Linux work was High risk and landed alone after a
strong-tier review. Ten findings, each a test before the fix (tests/guard/test_linux_paths.py,
"Finding N" per class):

| # | Gap on Linux | Fixed in |
|---|---|---|
| 1 | an absolute `/path` resolved to None without a drive | guard/shell.py resolve_path; rules.py git_jail |
| 2 | `/` was never a root | rules.py is_root, root_search; jail.own_root |
| 3 | ledger glob lowered the pattern, not the path | rules.py ledger_glob |
| 4 | backslash kept literal in bash words | shell.py BASH_ESC and the lexer |
| 5 | MSYS mapping (`/c/x`) and the `/tmp` mapping | shell.py msys_to_win; jail.bash_tmp |
| 6 | normcase is a no-op on POSIX (a case-folding mount under-matches) | guard/paths.py norm, the mount table |
| 7 | home variables were USERPROFILE/HOMEDRIVE/HOMEPATH only | shell.py HOME_VAR_RE, rules.py `_ROOT_NAMES` |
| 8 | heavy-tool names missed Linux names (godot4, AppImage) | config/policy.py LINUX_HEAVY_DEFAULTS (tests/config/test_policy.py) |
| 9 | the machine-path scrub matched drive forms only | kitlib/machinepaths (`/home/<name>`) |
| 10 | symlinks | jail.real through the placer; a swap between check and use is a known limit, as with junctions |

The design: path text is parsed by the shell's grammar, then placed by a platform-aware placer.
On Linux a path is absolute when it starts with `/`; there are no drives, no MSYS mapping and no
`\` separators; `/` and `$HOME` are roots; comparison folds case only where the filesystem does
(the placer asks once per mount, read from /proc/self/mountinfo, and folds when in doubt, which
refuses more). The user pointer file is the user's to write, so an agent could write it too: the
guard holds it (docs/guard-jail-roles.md "Write jail", tests/guard/test_linux_pointer.py).

Already failing closed and to stay so: guard/context.py `_placed` (no drive, no ledger key),
`_default_lock_held`.

What kept Linux safe while slices 2 to 13 were built was the front door, not the guard: tools/kit.py
refused Linux, because a hook command that cannot start (the Claude manifest's `pythonw` on Linux)
is, for Claude Code, a non-blocking error (inferred from its hook contract), so the tool call goes
ahead unguarded. A plugin installed on Linux by hand around kit.py was therefore unguarded until
slice 12 gave the hook a Linux interpreter. The backstop that stays: hookrun blocks every guarded
tool call when the platform is not in READY_PLATFORMS (tests/hookrun/test_hookmain.py).

## 4. CI and test marking

The owner's answer (question 8): CI for Linux and Windows comes in a later phase, together; the
first goal is the plugin working on Linux. Built (slice 1): tests/platforms.py `windows_only` and
`posix_only` with a reasons table keyed by module.Class (used by 110 test files), and the ratchet
(kitlib/ci/ratchet.py, thin entry point tools/ci_ratchet.py, tests in tests/ci/, baseline
tests/linux-baseline.txt). The ratchet runs `python -m unittest discover -s tests -t .`, not
kit_tests.py or gate.py (both need job objects and the lock); a new failure fails the run, a fixed
one must be removed from the baseline in the same branch, and import failures count as failures.

Open, the later phase (slice 17): `.github/workflows/` legs on the owner's self-hosted Linux
runner (any Linux build, a VM included; real hardware and GPUs) and a Windows leg, Python at the
version the kit runs on, git configured with a throwaway identity. The Linux leg starts as the
ratchet, moves to `tools/kit_tests.py`, then to `gate.py --full` with a CI lock profile. The
runner's cgroup delegation is checked then (unknown: how the runner delegates cgroups to its
user). CI adds only strictness: nothing in the landing path reads it until the owner says so.

## 5. Per-platform shell text

Built (slice 13):

- Skills and roles are shell-neutral; content/skills/shell-and-worktree-traps has one section per
  platform (`## Windows`, `## Linux`), and no Windows-shell line remains in content/ outside it.
- The hub's to-do steps: `{run: <command>}` stays PowerShell, verbatim; `{run: {windows: <cmd>,
  linux: <cmd>}}` carries one command per OS and the hub shows the form for the machine the step
  is for (kitlib/hubvalidate/todosteps.py OSES, SHELLS). The "hand steps carry commands" ruling is
  kept.
- Codex's dialect default follows the platform (hosts/codex/adapter/events.py default_dialect).

Open (slice 16): sorting the docs that mention PowerShell: most are Windows-internal and say so.

## 6. Install and machine setup on a fresh Linux box

Target: any Linux distribution (question 5). Strong containment needs cgroup v2 and a kernel of
5.14 or later; anything else installs with a lower tier of the ladder (section 2.3). Ubuntu 24.04
is the reference. Needs git, python3 (the kit's version) and tmux (checked).

Built (slice 14): `installer/machine-setup.sh` checks arguments and runs tools/machine_setup.py,
as the .ps1 does; the Linux steps are in kitlib/machinesetup (linuxbox.py, delegation.py,
linuxjobs.py, extras.py). Step numbers below are the ones the code cites.

1. pointer: /etc/pm-kit/pointer (sudo once), or the user's one when the owner declines sudo.
2. kit check; 3. config folders: unchanged (portable).
4. links: symlinks.
5. managed settings: Claude Code's Linux managed-settings path (/etc/claude-code; not probed:
   unknown).
6. settings: the guard hook entry (boot/hook.py --settings-entry), interpreter python3.
7. fresh mode surveys: run in the contain backend. 8. owner email: unchanged.
9. extras: environment.d (`~/.config/environment.d/pm-kit.conf`), the `.desktop` file, chmod; the
   delegation step: probe the ladder and offer the root step, a `user@.service.d` drop-in setting
   `Delegate=cpuset cpu io memory pids` and a re-login, or, without systemd, a root boot hook that
   hands /pm-kit to the kit user. The owner may decline it. loginctl enable-linger only where
   user systemd exists. The second-user rule (question 10), the container warning and the WSL
   refusal are section 2.4.
10. timers: worktree sweep, agent-temp sweep, automatic tasks, coordinators after reboot, as
    systemd user units named `pm-kit-*.timer` or cron `@reboot` entries; the hub's start at logon
    is an XDG autostart entry (kitlib/platform/linux/schedule.py).
11. kit command: `~/.local/bin/kit` (machinesetup/kitcommand.py LINUX_FILE).

Each step reports what it could not do (no linger, no delegation, no sudo, no user systemd, no
desktop session) as a named problem; heavy runs go ahead on the tier the box allows, shown as
weak or partial where it is (question 4). Plugin refresh: pluginsource's detached refresh uses
the detach backend.

## 7. macOS

Out of scope for this plan. On darwin the seam picks no backend: kitlib.platform.NAME is
'unsupported', there is no darwin folder, and every primitive raises PlatformUnsupported;
tools/kit.py refuses darwin. A darwin backend would be a new folder beside linux/, built by its own
plan, later: macOS has no cgroups (the lock could only limit concurrency), no /proc, launchd instead
of systemd, and case-insensitive paths by default.

## 8. Slices

Each slice was one branch, landed in this order. Status is read from the code at the base:
done (the symbol that proves it), open, or dropped (none dropped). Done is 14 of 17 slices; 16 is
partly done; open are 15 and 17. The live proofs on a Linux machine are listed under each slice and
under 8.1.

CI is a later phase (question 8), so each Linux proof is a manual run on a Linux machine. The
safety net while CI waits (approved at plan review): `gate.py --full` on Windows for each slice;
before each backend slice lands, a manual run of `tools/ci_ratchet.py` at the branch tip on the
reference VM (Ubuntu 24.04, systemd, the delegation drop-in) and on one weak-mode box or container.
A VM on the owner's machine is outside the lock's view, so the VM run books the machine lock from
the host as heavy.

1. **Test markers and the ratchet.** Done: kitlib/ci/ratchet.py, tools/ci_ratchet.py, tests/ci/,
   tests/platforms.py, tests/linux-baseline.txt.
2. **Import-clean kitlib and the seam skeleton.** Done: kitlib/platform/ (api.py, dll.py,
   windows/, linux/), tests/platform/ (test_no_os_checks, test_imports, test_lazy_dll, test_seam),
   hosts.json `process_names` per platform, read by kitlib/host/registry.py.
3. **Locks.** Done: kitlib/platform/linux/locks.py (hold, RunLock, run_lock_held, lock_fd); the
   Linux branches of Store.lock, run_mutex, run_dead and TreeLock; tests/lock/test_store_linux.py,
   tests/fs/test_treelock.py. Store() with no root refuses on Linux by name until slice 9 gave it
   a real root.
4. **Process identity and kill.** Done: kitlib/platform/linux/process.py (the boot folded into
   ctime, section 2.2); tests/platform/test_linux_process.py, tests/lock/test_nest_linux.py.
5. **Containment and the pool.** Done: kitlib/platform/linux/contain.py (Job, NsJob, Carrier,
   probe) and cpu.py; tooldeps/pool.py and pytests/runner.py on the seam; tests/platform/
   test_linux_contain.py, test_linux_cpu.py. The pid shield is Windows only, so each Linux unit and
   worker carries the "could not shield" note.
6. **Lock hardware.** Done: kitlib/platform/linux/hardware.py, lock/layout.detect_linux; the four
   trees are in tests/lock/linux_trees.py, read through the real reader (fake_hardware is
   unchanged). Open proof: the VM's live /sys run (test_layout_linux.py LiveSys).
7. **Lock runs and detach.** Done: kitlib/platform/linux/detach.py, lock/run_held.py Slot give-back
   and the `leftovers` call sites in Slot.release and run_end.py end_sparing, landing/submit;
   tests/lock/test_slot_linux.py, test_detach_linux.py. Its live proofs ran on the VM (section 8.1).
   tests/lock/test_leftovers.py is Windows only; the Linux leftovers move is test_slot_linux Live's
   straggler test.
8. **Guard path semantics.** Done: section 3; READY_PLATFORMS includes linux
   (kitlib/guard/__init__.py), tests/guard/test_linux_paths.py.
9. **Paths and configuration.** Done: kitlib/platform/linux/paths.py (the profile from the
   password database, the pointer file, /tmp as the system temp whatever TMPDIR says, a /proc scan
   of this user's processes, the mountinfo reader); user.real_profile, lock/store.py real_root,
   pointer.registry, worktree/holders.py and classify.process_cwds read the seam; tests/config/
   test_linux_paths.py, tests/worktree/test_holders_linux.py. holders.blockers on Linux answers
   HELD, UNREAD or NOT_SEARCHED, each a refusal; the rules are in holders.py and docs/worktree-
   remove.md "Classify". Open:
   - (c) a landing end to end on a throwaway repository on the VM (the landing tests skip on
     Linux, section 1.2).
   - ssh-agent, gpg-agent and a desktop's gcr agent stay unread processes, so a removal while one
     runs still falls to the owner (`describe` names it).
   Known limits: a bind mount over a tree's file system leaves its removals UNREAD; in a WSL2
   distro every removal is NOT_SEARCHED; /proc is listed once, so a process that forks and exits
   during the scan can leave a child that inherits its cwd unseen; another user's processes cannot
   be seen; a user with no password database entry has no profile folder, so the machine lock's
   store cannot be opened.
10. **Scheduled jobs.** Done: kitlib/platform/linux/schedule.py (systemd user timers and cron,
    both first-class), hubsite/autostart.py, worktree/task.py, the sweeps,
    pmstart/consoletasks.py read-back. Open proof: a timer fires once on the VM, a cron entry on a
    box without user systemd.
11. **Sessions.** Done: kitlib/launcher/linuxwin.py (tmux, or a configured terminal command),
    launcher/stop.py, the proc process listing (proc/listing.py).
12. **Hook interpreter.** Done: hookrun.manifest_command and INTERPRETERS (python3 on Linux, run by
    /bin/sh; a tool event's line exits 2 when python3 fails or is missing, a start event's says
    the kit did not load), each host's `hooks.linux.json`, hosts.json platforms, the plugin export
    (kitlib.pluginsource.platform_manifests), and hookrun.mcp_interpreter for the MCP servers
    (`.claude-plugin/plugin.linux.json`, Codex's `mcp.linux.json`; tests/content/test_render.py
    ManifestTest). Open proof: the posix-only run of the lines (tests/hookrun/test_hook_linux.py) on a
    Linux machine.
13. **Shell text.** Done: section 5.
14. **Machine setup on Linux.** Done: installer/machine-setup.sh, kitlib/machinesetup
    (linuxbox.front_door, delegation.py, linuxjobs.py), tools/kit.py lets Linux through and keeps
    refusing darwin and WSL. Open proofs, on a Linux machine: machine setup on a fresh Ubuntu 24.04
    VM with the delegation drop-in ends with no problems and the lock status shows S1; machine
    setup in a default Docker container probes to its lower tier and the lock status shows it as
    such. No proof claims strong containment in a container.
15. **Peer messaging and the bridge.** Open (after Windows parity, question 7): peer.py on Claude
    Code's Linux socket (after a probe: its messaging address, likely a Unix socket, is unknown),
    the bridge's leader lock and transport on Unix sockets, procwatch. Risk High (networking).
16. **Docs.** Partly done: AGENTS.md ("Windows and Linux"), ARCHITECTURE.md's platform line, the
    multi-host plan's section 6 status line pointing here, docs/machine-setup.md's Linux notes.
    Open: the owner setup docs' Linux blocks and the PowerShell sort (section 5).
17. **CI legs (later phase, question 8).** Open: section 4. Proof: the Linux leg is green on the
    baseline and a planted failure fails it.

### 8.1 Running tests on the reference VM

The reference VM is the Hyper-V guest `kit-linux-ref` on the owner's machine: Ubuntu, 4 vCPUs,
user `kitvm`, reached by ssh with the key `~/.ssh/kit-linux-ref_ed25519` and its host key pinned
under the alias `kit-linux-ref`. Starting or stopping it is the PM's call; a worker that finds it
off, or ssh refusing the key, stops and reports. Its address changes at each boot, so read it from
its adapter (PowerShell; ssh's BatchMode makes a missing key fail at once rather than prompt):

```
$m = (Get-VMNetworkAdapter -VMName kit-linux-ref).MacAddress -replace '(..)(?!$)', '$1-'
$ip = (Get-NetNeighbor -LinkLayerAddress $m | ? AddressFamily -eq IPv4).IPAddress
$o = @('-o','HostKeyAlias=kit-linux-ref','-o','StrictHostKeyChecking=yes','-o','BatchMode=yes',
       '-i',"$env:USERPROFILE\.ssh\kit-linux-ref_ed25519")
$vm = $o + "kitvm@$ip"
```

1. Sync a commit, never the working tree, to a folder of its own (`<name>`: your slug):
   `git -C <worktree> archive --format=tar -o <scratch>\<name>.tar <rev>`;
   `scp @o <scratch>\<name>.tar "kitvm@${ip}:kit-runs/"`;
   `ssh @vm "cd ~/kit-runs && rm -rf <name> && mkdir <name> && tar -xf <name>.tar -C <name> && rm <name>.tar"`.
2. Run named tests under the host's machine lock. The VM is outside the lock's view; its 4 vCPUs
   are two of the host's cpu slots, so the run is `test`, and the resource keeps two VM
   runs from sharing the VM:
   `python <kit>/tools/machine_lock.py run --repo <repo> --slug <slug> --kind test --resource vm:kit-linux-ref -- ssh @vm "cd ~/kit-runs/<name> && python3 -m unittest -v <module>[.<Class>] ..." > <scratch>\<log> 2>&1`
   (in PowerShell, `@vm` splats; write the ssh options out in Git Bash). The containment tier the
   tests saw: `python3 -c "from kitlib.platform.linux import contain; print(contain.probe().text)"`
   in the same folder.
3. Collect: the host log is the run's whole output; read it when the run ends. Then
   `ssh @vm "rm -rf ~/kit-runs/<name>; systemctl --user list-units --all --no-legend 'pmkit-*'; pgrep -u kitvm -af python3"`
   removes the folder and shows any unit or process a test left behind (report one, never
   leave it running).

The tree-kill probe (section 1.3), to run again on a new Claude Code: its scripts are in
`~/ccprobe` on the VM (`probe.sh <scenario>`, `all.sh` for every one), and a Linux claude in
`~/ccinstall`. `mock.py` is a scripted stand-in for the Messages API on 127.0.0.1
(`ANTHROPIC_BASE_URL`, a fake key, a fresh HOME), so claude runs one Bash call with no account.
The call starts `tree.py`: P, the shell's child; A, a child of P in its group; B, a child of P in
a session of its own; C, double forked away. P, A and B ignore SIGTERM, SIGINT and SIGHUP, and
all four write beats and the signals they get. Scenarios: `finished`, `timeout` (moved to the
background), `bgexit`, `esc` (Escape in tmux), `fgterm` and `fgint` (the signal to claude),
`bgkill` and `timeoutkill` (TaskStop, found by ToolSearch). claude runs under
`strace -f -tt -e trace=kill,tkill,tgkill,pidfd_send_signal,pidfd_open,setsid,setpgid,execve -e signal=none`;
the report says which members kept beating after the call ended.

## 9. Risks and unknowns

- Unknown: Claude Code's Linux messaging address and session file fields (slice 15), and its
  managed settings path (slice 14, step 5).
- Unknown (CI phase): how the owner's self-hosted runner delegates cgroups to the runner user.
- Risk: the weak containment tiers look like they work. Mitigation: shown and recorded, not
  refused: the tier and what it lacks are named in the lock's status, the heavy-run start line and
  the run record (question 4).
- Risk: guard semantics drift between Windows and Linux. Mitigation: one placer with a platform
  argument, and the guard tests run both placers on every platform (they are pure).
- Risk: the seam move changes Windows behaviour by accident. Mitigation: the full Windows suite on
  every slice, and the Windows CI leg once the CI phase brings it.
- Risk: multi-user and container boxes break the one-store rule. Mitigation: one kit per CPU set
  and the second-user rule (section 2.4).

## 10. Questions for the owner

All eleven are answered (owner, 2026-10-26); the answers are folded into the sections above.

1. **macOS.** Out of this plan; macOS gets its own plan later (section 7).
2. **Linux CI runner.** A self-hosted box; support any Linux build, VM included; the primary target
   is full Linux support out of the box. Not GitHub-hosted. A Linux container for a quick local
   check is a tool, not a runner.
3. **Spending.** A Linux box or VM, with real hardware and GPU tests. No hosted-runner spend.
4. **No cgroup v2 delegation.** Heavy runs must work on Linux, with full cgroup v2 containment on
   native Linux. Where nothing stronger exists (Docker included), heavy runs go ahead with weak
   containment, shown as such; the owner accepts that risk (section 2.3).
5. **Supported Linux.** Any Linux distribution, with cron fallbacks where systemd is absent.
6. **Session windows.** Both supported; tmux by default, a configured terminal command if set.
7. **The bridge and peer messaging on Linux.** After Windows parity, not in the first build.
8. **A Windows CI leg.** CI for both comes in a later phase; the initial goal is the plugin working
   on Linux (section 4).
9. **Hook manifest form.** Per-platform manifests, rendered for each platform.
10. **Multi-user boxes.** One kit user per box; machine setup refuses a second.
11. **WSL.** Later, after native Linux parity; refused meanwhile (tools/kit.py; the kernel release
    names Microsoft). A kit in WSL beside the Windows kit would keep a second lock store for the
    same CPUs and memory.
