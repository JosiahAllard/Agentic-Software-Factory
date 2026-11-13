# Linux support: what is still open

Linux support is built through slice 14 of the original plan: the platform seam
(docs/modules/base-design.md "Contract"), the Linux backends under `kitlib/platform/linux/`, the
guard's Linux path rules, machine setup (docs/machine-setup.md "Linux"), scheduled jobs, session
windows (docs/launcher.md "The window") and per-platform hook manifests. The code and its docstrings
carry what was built. This file keeps only the open slices and the live proofs that need a Linux
machine. Status read from the code at the base of the work that cut it down.

Goal (the owner's): the whole kit runs on Linux out of the box, on any distribution, at parity with
Windows; macOS is out. Peer messaging and the bridge come after parity. CI for Linux and Windows is a
later phase. Windows behaviour does not change: each slice keeps the Windows path as it was and is
proved with the existing Windows suite.

## Open slices

15. **Peer messaging and the bridge.** Peer messaging is built: `hosts/claude/adapter/peer.py`
    writes to Claude Code's Linux Unix socket through `kitlib/platform/linux/unixsock.py`, and
    `pm-relaunch` and `pm-crossover` run on Linux (docs/launcher.md "PM relaunch"). Its live proof
    with Claude Code is KIT-LINUX-PEER-LIVE-PROOF. Still open: the bridge's leader lock and
    transport on Unix sockets (`optional/bridge/win/` has no Linux form; KIT-LINUX-BRIDGE-UNIXSOCK)
    and `kitlib/machinesetup/procwatch.py` (KIT-LINUX-PROCWATCH). Risk High (networking).
16. **Docs, the rest.** The owner setup docs' Linux blocks, and sorting the docs that mention
    PowerShell: most are Windows-internal and should say so.
17. **CI legs.** There is no `.github`. `.github/workflows/` legs on the owner's self-hosted Linux
    runner (any Linux build, a VM included; real hardware and GPUs) and a Windows leg, Python at the
    version the kit runs on, git configured with a throwaway identity. The Linux leg starts as the
    ratchet (tools/ci_ratchet.py; docs/test-tools.md "Platform markers and the ratchet"), moves to
    `tools/kit_tests.py`, then to `gate.py --full` with a CI lock profile. CI adds only strictness:
    nothing in the landing path reads it until the owner says so. Proof: the Linux leg is green on
    the baseline and a planted failure fails it.

## Other open work

- The TCP connection table and build-daemon sparing: `kitlib/proc/tcp.py` reads the listeners on
  Linux from /proc/net/tcp and tcp6, but the connection table (`GetExtendedTcpTable`) and
  `kitlib/proc/daemons.py` are Windows only, so on Linux the end-of-run check (`daemons.find` in
  `kitlib/lock/run_end.py` and `run_held.py`) only warns and kills the tree. In no slice.
- The allowlist in `tests/platform/test_no_os_checks.py` of files that still read `os.name` or
  `sys.platform` themselves only shrinks.
- The end-to-end landing tests skip on Linux (some carry `windows_only`), so a landing has not run
  end to end there.
- The delegation runner (`kitlib/delegations/place.py`, `run.py`) is Windows only by design (drive
  paths, `.exe`, winreg); its tests that need a junction or the real registry carry `windows_only`.
- ssh-agent, gpg-agent and a desktop's gcr agent stay unread processes in the worktree holders
  search, so a removal while one runs still falls to the owner (docs/worktree-remove.md "Classify").
- Pid namespaces: queue entries and background runs carry no `pidns` and no `jobns`; `giveback.py`
  and the lock status read a record's pid as this namespace's; the holders search does not look
  inside a W1 job. Each is safe today; a later record.
- macOS: out of scope. On darwin the seam picks no backend (`kitlib.platform.NAME` is
  'unsupported') and `tools/kit.py` refuses it. WSL is refused until after native parity (a kit in
  WSL beside the Windows kit would keep a second lock store for the same CPUs and memory).

## Live proofs still open

Each is a manual run on a Linux machine (the reference VM below) until CI exists:

- A landing end to end on a throwaway repository.
- The VM's live /sys run (`tests/lock/test_layout_linux.py` `LiveSys`).
- A timer fires once on the VM, and a cron entry on a box without user systemd.
- The posix-only run of the hook lines (`tests/hookrun/test_hook_linux.py`).
- Machine setup on a fresh Ubuntu 24.04 VM with the delegation drop-in ends with no problems and the
  lock status shows S1; machine setup in a default Docker container probes to its lower tier and the
  lock status shows it as such. No proof claims strong containment in a container.

Before each backend slice lands, a manual run of `tools/ci_ratchet.py` at the branch tip on the
reference VM (Ubuntu 24.04, systemd, the delegation drop-in) and on one weak-mode box or container.
A VM on the owner's machine is outside the lock's view, so the VM run books the machine lock from the
host as heavy.

After each Claude Code update, re-run the tree-kill probe: the kit's detach and its lack of a pid shield on Linux rest on how Claude Code kills a call's tree (probe scripts on the reference VM, below).

Inferred from the Claude Code 2.1.323 bundle, not yet seen live (KIT-LINUX-PEER-LIVE-PROOF):
its Linux messaging address, a Unix socket under a `cc-socks` folder (`peer.SOCK_DIRS`,
`SOCK_NAME`), and its `procStart`, a `ps -o lstart` text. Unknown, to probe: its
managed settings path (`/etc/claude-code`, machine setup step 5), and how the owner's self-hosted
runner delegates cgroups to the runner user (slice 17).

## Running tests on the reference VM

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
