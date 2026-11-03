#!/usr/bin/env python3
"""Run a committed bpy script in Blender, headless, under the machine lock (kind render).

  python blender_run.py SCRIPT.py --out DIR [--blender PATH] [-- SCRIPT ARGS ...]

Blender gets `--background --factory-startup --python SCRIPT -- --out DIR SCRIPT ARGS`, runs with
DIR as its working folder and BLENDER_RUN_OUT=DIR in its environment; the script writes under DIR.
Only a run of the committed script counts as a build result, so SCRIPT must be tracked by git and
unchanged. DIR must be inside a git checkout (the worktree the asset is for), and after the run the
checkout's `git status` may show new or changed files under DIR only: anything else is reported
and the exit is 3. (Writes outside the checkout, Blender's own temp files among them, are not seen.)

Blender is found in this order: --blender, the KIT_BLENDER environment variable, `blender` on PATH,
then the newest install under Program Files\\Blender Foundation (Windows) or /snap/bin, /opt
(Linux). The run books a render-kind slot through the kit's tools/machine_lock.py (KIT_MACHINE_LOCK
overrides the path), on this machine (--node main), and refuses (exit 2) without it.

render_glb.py and motion_check.py reuse this module's lock command, Blender search and --out check.

Exit codes: 0 done, 2 refused, 3 wrote outside --out, else Blender's or the lock tool's code.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

IN_BLENDER = "bpy" in sys.modules
if not IN_BLENDER:
    # The kit library, when this file runs from the kit: importing it installs its CreateProcess
    # hook before anything here starts a process. Copied out of the kit, or inside Blender's own
    # Python (render_glb.py and motion_check.py import this module there), it is left out.
    KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    if KIT_ROOT not in sys.path:
        sys.path.insert(0, KIT_ROOT)
    try:
        import kitlib  # noqa: F401,E402
    except ImportError:
        pass

# the kit's machine lock entry point: <plugin root>/tools/machine_lock.py, or KIT_MACHINE_LOCK to override
MACHINE_LOCK_TOOL = os.environ.get("KIT_MACHINE_LOCK") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "machine_lock.py")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_WAIT = "540s"


class Refused(Exception):
    """Why the run may not start."""


# --- finding things --------------------------------------------------------------------------

def _version(path):
    """(major, minor, ...) from the first `Blender X.Y` folder in `path`, or () when there is none."""
    m = re.search(r"blender[ _-]?(\d+(?:\.\d+)*)", path.replace("\\", "/"), re.I)
    return tuple(int(p) for p in m.group(1).split(".")) if m else ()


def newest(paths):
    """The path whose folder names the highest Blender version (the last such path wins a tie),
    or None for none."""
    best = None
    for p in paths:
        if best is None or _version(p) >= _version(best):
            best = p
    return best


def install_candidates(env=None, platform=None):
    """Blender binaries in the usual install folders, in no particular order."""
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        bases = {env.get(k) for k in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)")} - {None, ""}
        found = []
        for base in sorted(bases):
            found += glob.glob(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
        return found
    return [p for p in ["/snap/bin/blender"] + glob.glob("/opt/blender*/blender") if os.path.isfile(p)]


def find_blender(given=None, env=None, platform=None):
    """The Blender binary to run: `given`, else KIT_BLENDER, else `blender` on PATH, else the newest
    install. Raises Refused when none is a file."""
    env = os.environ if env is None else env
    for p in (given, env.get("KIT_BLENDER")):
        if p:
            if os.path.isfile(p):
                return p
            raise Refused(f"no Blender at {p}")
    on_path = shutil.which("blender")
    if on_path:
        return on_path
    best = newest(install_candidates(env, platform))
    if best:
        return best
    raise Refused("no Blender found: pass --blender PATH or set KIT_BLENDER (it is not on PATH)")


def checkout_root(path):
    """The top folder of the git checkout holding `path` (the nearest folder above it with a .git
    entry), or None. `path` need not exist yet; links and junctions in it are resolved first, so
    the checkout is the one the path really lands in."""
    p = os.path.realpath(path)
    while True:
        if os.path.lexists(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def _inside(path, folder):
    path, folder = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(folder))
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def out_refusal(out):
    """Why `out` may not take output, or None: it must be inside a git checkout and not in .git,
    judged where it really lands (links and junctions resolved)."""
    out = os.path.realpath(out)
    root = checkout_root(out)
    if root is None:
        return (f"--out {out} is not inside a git checkout: output goes into the worktree the asset "
                "is for, never a scratch or temp folder")
    if _inside(out, os.path.join(root, ".git")):
        return f"--out {out} is inside {root}'s .git folder"
    return None


# --- the lock command ------------------------------------------------------------------------

def usable_python(python, binary):
    """The real interpreter path behind `python`, or None. `python` is asked for its sys.executable,
    so the Python Install Manager alias in WindowsApps resolves to the real install. None when it is
    not a file, the run fails, times out or prints nothing, or the answer is still under a
    WindowsApps folder (the Store stub) or is the Blender binary itself."""
    if not python or not os.path.isfile(python):
        return None
    try:
        done = subprocess.run([python, "-c", "import sys;print(sys.executable)"], capture_output=True,
                              text=True, timeout=10, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = done.stdout.strip().splitlines() if done.returncode == 0 else []
    real = lines[-1].strip() if lines else ""
    if not real or not os.path.isfile(real):
        return None
    path = os.path.normcase(os.path.abspath(real))
    if "windowsapps" in path.split(os.sep):
        return None
    if binary and path == os.path.normcase(os.path.abspath(binary)):
        return None
    return real


def script_args(argv):
    """The arguments after Blender's `--`."""
    return argv[argv.index("--") + 1:] if "--" in argv else []


def slug(prefix, name):
    """`prefix-<name>`, the name cut to the lock's safe characters and 48 long."""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", os.path.splitext(os.path.basename(name))[0]).strip("-")[:48]
    return prefix + "-" + (stem or "run")


def blender_argv(binary, script, argv, factory=False):
    """Blender headless on `script` with `argv` after its `--`; a script error exits 1."""
    head = [binary, "--background"] + (["--factory-startup"] if factory else [])
    return head + ["--python-exit-code", "1", "--python", os.path.abspath(script), "--"] + list(argv)


def lock_cmd(python, tool, run_slug, command):
    """`command` under the machine lock tool: kind render, on this machine, waiting at most 9 min."""
    return [python, "-B", tool, "run", "--repo", "comfy", "--slug", run_slug, "--kind", "render",
            "--node", "main", "--max-wait", MAX_WAIT, "--"] + list(command)


def lock_python(binary):
    """A real python to run the lock tool, or Refused. Inside Blender sys.executable may be Blender
    itself, so the one on PATH is asked first there."""
    found = (shutil.which("python") or sys.executable) if IN_BLENDER else sys.executable
    python = usable_python(found, binary)
    if not python:
        raise Refused(f"no usable python to run the lock tool (got {found!r}: missing, a WindowsApps "
                      "Store stub, or Blender itself). Put a real python first on PATH")
    return python


def need_tool(tool):
    if not os.path.isfile(tool):
        raise Refused(f"no machine lock tool at {tool}, and a Blender run takes a render slot. "
                      "Install the kit's tools/machine_lock.py or set KIT_MACHINE_LOCK to it")


def ensure_locked(script, argv, run_slug, rerun_env, binary):
    """For a kit script that runs inside Blender (motion_check.py): return when this is its own
    locked re-run (`rerun_env` set by it, consumed here, and MACHINE_LOCK_RUN set by the lock
    tool); otherwise run `script` in Blender (`binary`) under the lock and exit with its code."""
    if os.environ.pop(rerun_env, None) and os.environ.get("MACHINE_LOCK_RUN"):
        return
    try:
        need_tool(MACHINE_LOCK_TOOL)
        python = lock_python(binary)
    except Refused as e:
        print(f"[machine-lock] refused: {e}. Exit 2.", file=sys.stderr)
        sys.exit(2)
    cmd = lock_cmd(python, MACHINE_LOCK_TOOL, run_slug, blender_argv(binary, script, argv))
    sys.exit(subprocess.call(cmd, env=dict(os.environ, **{rerun_env: "1"}), creationflags=0))


def play_action(objs, act):
    """Inside Blender: make `act` the one clip `objs` play. Their NLA tracks are muted (the glTF
    importer stashes every clip there) and, for a layered action (Blender 4.4 and later), the first
    slot suitable for each object is picked when Blender picked none."""
    for o in objs:
        ad = o.animation_data or o.animation_data_create()
        for track in ad.nla_tracks:
            track.mute = True
        ad.action = act
        if hasattr(ad, "action_slot") and ad.action_slot is None:
            slots = list(getattr(ad, "action_suitable_slots", None) or [])
            if slots:
                ad.action_slot = slots[0]


# --- the committed-script and stray-write checks ----------------------------------------------

def _git(root, *args):
    done = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True,
                          creationflags=NO_WINDOW)
    return done.returncode, done.stdout


def script_refusal(script, git=_git):
    """Why `script` is not a committed, unchanged file of a git checkout, or None."""
    if not os.path.isfile(script):
        return f"no script at {script}"
    root = checkout_root(script)
    if root is None:
        return f"{script} is not in a git checkout; only a committed script counts"
    rel = os.path.relpath(os.path.abspath(script), root).replace(os.sep, "/")
    code, _ = git(root, "ls-files", "--error-unmatch", "--", rel)
    if code != 0:
        return f"{rel} is not tracked in {root}: commit the script first"
    code, out = git(root, "status", "--porcelain", "--", rel)
    if code != 0 or out.strip():
        return f"{rel} has uncommitted changes: commit the script first"
    return None


def status_paths(porcelain):
    """{path: status line} from `git status --porcelain` output (a rename counts as its new path)."""
    paths = {}
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].split(" -> ")[-1].strip()
        if len(path) >= 2 and path[0] == path[-1] == '"':
            path = path[1:-1]
        paths[path] = line
    return paths


def stray_writes(before, after, out_rel):
    """Paths new or changed in `after` (porcelain text) against `before` that are not under
    `out_rel` (relative to the checkout top, '/'-separated; '' or '.' is the whole checkout)."""
    old, new = status_paths(before), status_paths(after)
    out_rel = out_rel.strip("/")
    stray = []
    for path, line in sorted(new.items()):
        if old.get(path) == line:
            continue
        p = path.rstrip("/")
        if out_rel in ("", ".") or p == out_rel or p.startswith(out_rel + "/"):
            continue
        # an untracked folder above --out, listed whole: only --out is under it
        if path.endswith("/") and (out_rel + "/").startswith(p + "/"):
            continue
        stray.append(path)
    return stray


def _status(root, git=_git):
    """`git status --porcelain` of the checkout; Refused when git fails (never an empty answer,
    which would read as "nothing changed")."""
    code, out = git(root, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        raise Refused(f"git status failed in {root} (exit {code})")
    return out


def parse_args(argv):
    if "--" in argv:
        i = argv.index("--")
        own, rest = argv[:i], argv[i + 1:]
    else:
        own, rest = argv, []
    ap = argparse.ArgumentParser(prog="blender_run.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("script", help="the committed bpy script")
    ap.add_argument("--out", required=True, help="the folder the script writes into, in the worktree")
    ap.add_argument("--blender", help="the Blender binary (default: found as the docstring says)")
    opts = ap.parse_args(own)
    opts.script_args = rest
    return opts


def main(argv=None, git=_git):
    try:
        opts = parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as e:
        return 2 if e.code else 0
    out = os.path.realpath(opts.out)    # where it really lands: links and junctions resolved
    try:
        for why in (script_refusal(opts.script, git), out_refusal(out)):
            if why:
                raise Refused(why)
        root = checkout_root(out)
        if os.path.normcase(out) == os.path.normcase(root):
            raise Refused(f"--out {out} is the checkout's top folder; name a folder inside it, "
                          "or no write could be told apart from the script's output")
        binary = find_blender(opts.blender)
        need_tool(MACHINE_LOCK_TOOL)
        python = lock_python(binary)
        before = _status(root, git)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    os.makedirs(out, exist_ok=True)
    command = blender_argv(binary, opts.script, ["--out", out] + opts.script_args, factory=True)
    cmd = lock_cmd(python, MACHINE_LOCK_TOOL, slug("blender", opts.script), command)
    code = subprocess.call(cmd, cwd=out, env=dict(os.environ, BLENDER_RUN_OUT=out), creationflags=0)
    try:
        after = _status(root, git)
    except Refused as e:
        print(f"REFUSED: {e}; the run's writes could not be checked", file=sys.stderr)
        return 3
    stray = stray_writes(before, after, os.path.relpath(out, root).replace(os.sep, "/"))
    for path in stray:
        print(f"OUTSIDE --out: {path}", file=sys.stderr)
    if stray:
        print(f"REFUSED: the script wrote {len(stray)} path(s) outside {out}; remove them", file=sys.stderr)
        return 3
    if code == 0:
        print(f"OUT {out}")
    return code


if __name__ == "__main__":
    sys.exit(main())
