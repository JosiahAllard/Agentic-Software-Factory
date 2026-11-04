#!/usr/bin/env python3
"""Asset generation for art agents through the owner's local ComfyUI.

Free by construction: talks only to ComfyUI on this machine (127.0.0.1:8188),
never downloads models, installs nodes or calls a paid/API node. ComfyUI is started
headless, never through the Comfy Desktop app, and agents never ask the owner to open
Desktop: the install's own venv python runs ComfyUI/main.py directly, with no window,
in a job object of the process that started it, so it ends when that process ends.
The install is read (read-only) from Comfy Desktop's installations.json.

Two ways to use it:
  up / down    a session for an art run: `up` books a detached local-ai machine-lock run
               (resource comfy-port:<port>) whose keeper (the hidden `serve`) starts
               ComfyUI, waits up to --ready seconds (default 300) for it to answer and
               keeps it up; generating commands then book nothing of their own. It ends
               on `down`, at its --for limit, after ~10 idle minutes with an empty queue,
               or when the lock stops the run; a crashed server ends it too.
  one command  with no session, every generating command books its own local-ai run,
               starts ComfyUI if it is not up, and its server ends with the command.
A ComfyUI the owner started (port 8188 answering, not started by this tool) is used as
it is and never stopped. A port that is held but does not answer is waited for while
Comfy Desktop is still starting it (its comfy-procs file names the listener), and
otherwise refused, naming the process. If ComfyUI cannot be reached even after
trying to start it, or it stops answering mid-run, the command exits 2 with a
GENERATOR UNAVAILABLE error naming any files it already saved: the run stops there and
the agent reports the outage. It never writes prompts or placeholders in place of outputs.

Commands (all take --out DIR --name STEM; see `<command> --help`):
  check                         ComfyUI up? which approved models are present?
  up      [--for S] [--ready S] [--lock-wait S]   start a session (prints its run id)
  down                          end the session `up` started
  free                          unload models + free RAM/VRAM (run when your work is done)
  stop                          stop ComfyUI if this tool started it (a session: as `down`)
  image   --prompt  [--model zimage|krea2] [--width --height --count --seed]
  edit    --image A [--image2 B] --prompt       Qwen-Image-Edit 2511 (4-step)
  rembg   --image A                             BiRefNet background removal -> RGBA PNG
  upscale --image A                             Real-ESRGAN x4
  to3d    --image A [--engine trellis2|pixal3d|hunyuan3d] [--faces N]
  music   --tags "..." [--lyrics "[Instrumental]"] [--seconds 60] [--bpm 120]
  template --template NAME [--set NODE.INPUT=JSON ...] [--load NODE=FILE ...]
                                                run any installed, non-API template

Every command writes ComfyUI's own copies into a folder of its own,
output/agent/<YYYYmmdd-HHMMSS>_<command>_<name>/, and copies each result to
--out, which must be inside the repository the asset is for (the worktree's
asset folder), never a scratch folder. ComfyUI's copies are only a cache; the
owner may clear output/ at any time. A ComfyUI this tool starts puts output/ under
%AGENT_TEMP%\\comfy when AGENT_TEMP is set; a local ComfyUI keeps its folder.

Every saved file gets a sidecar <file>.json (tool, model, licence, inputs, seed,
settings, date, seconds taken) for the repository's ATTRIBUTIONS row.

GPU etiquette: generation shares the GPU with the game engine. Serialising
generations against each other and against the repos' engine, network and
render runs is the machine lock's job: a session, or else every generating command,
is a local-ai machine-lock run holding the resource comfy-port:<port> (a command waits
up to --lock-wait seconds, at most 540), and with no machine lock tool it refuses
(exit 2). When a command's run ends, a local ComfyUI has its models unloaded if
idle (the hidden `release` command, the run's --on-release). One
command is one slice: keep it under ~10 minutes. Keep images near 1 MP, and never
generate during a measurement window.

Knowledge base (read before; record sessions in the repo's docs/ops/assets.md): <kit>/knowledge/asset-generation/
"""
import argparse
import datetime
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
# The kit library, for the shared temp folder (kitlib.config.user.shared_temp: the machine
# config's `temp_dir`, else %LOCALAPPDATA%\Temp); importing it also installs its CreateProcess
# hook. Copied out of the kit, with no kitlib, the old %LOCALAPPDATA%\Temp paths are kept.
KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
if KIT_ROOT not in sys.path:
    sys.path.insert(0, KIT_ROOT)
try:
    from kitlib.config import user as kuser  # noqa: E402
except ImportError:
    kuser = None
sys.path.insert(0, TOOL_DIR)
import comfy_api as C  # noqa: E402
# winproc.py (Toolhelp + job-object helpers, standard library only) lives beside procwatch.py,
# a sibling tool folder next to this one (optional/procwatch/ in the kit); reused here
# instead of re-implementing pid/ctime verification and process termination.
sys.path.insert(0, os.path.join(TOOL_DIR, "..", "procwatch"))
import winproc as W  # noqa: E402


def _shared_temp():
    """kuser.shared_temp(), or None when the kit library is missing or LOCALAPPDATA is unset."""
    if kuser is None:
        return None
    try:
        return kuser.shared_temp()
    except (RuntimeError, OSError):
        return None


SHARED_TEMP = _shared_temp()

# Approved local models. Anything not here needs the owner's approval and a licence check.
MODELS = {
    "zimage": {"name": "Z-Image-Turbo (Tongyi-MAI)", "licence": "Apache-2.0",
               "files": ["z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"]},
    "krea2": {"name": "Krea 2 Turbo (fp8)", "licence": "Krea 2 Community License (commercial use only while "
              "company revenue < USD 1M/yr; acceptable-use policy applies)",
              "files": ["krea2_turbo_fp8_scaled.safetensors", "qwen3vl_4b_fp8_scaled.safetensors",
                        "qwen_image_vae.safetensors"]},
    "edit": {"name": "Qwen-Image-Edit 2511 (fp8) + Lightning 4-step LoRA", "licence": "Apache-2.0",
             "files": ["qwen_image_edit_2511_fp8mixed.safetensors", "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                       "qwen_image_vae.safetensors",
                       "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"]},
    "rembg": {"name": "BiRefNet", "licence": "MIT", "files": ["birefnet.safetensors"]},
    "upscale": {"name": "Real-ESRGAN x4plus", "licence": "BSD-3-Clause",
                "files": ["RealESRGAN_x4plus.safetensors"]},
    "trellis2": {"name": "TRELLIS.2 4B (Microsoft)", "licence": "MIT",
                 "files": ["trellis_2_int8_convrot.safetensors", "trellis_2_shape_vae_bf16.safetensors",
                           "trellis_2_texture_vae_bf16.safetensors", "dino_v3_L_naf_fp32.safetensors",
                           "birefnet.safetensors", "moge_2_vitl_normal_fp16.safetensors"]},
    "pixal3d": {"name": "Pixal3D (TencentARC)", "licence": "MIT (bundled components keep their own licences)",
                "files": ["pixal3d_int8_convrot.safetensors", "trellis_2_shape_vae_bf16.safetensors",
                          "trellis_2_texture_vae_bf16.safetensors", "dino_v3_L_naf_fp32.safetensors",
                          "birefnet.safetensors", "moge_2_vitl_normal_fp16.safetensors"]},
    "hunyuan3d": {"name": "Hunyuan3D 2.1 (Tencent)", "licence": "Tencent Hunyuan 3D 2.1 Community License "
                  "(not valid in EU/UK/South Korea; >1M MAU needs a Tencent licence; outputs may not train "
                  "other models)", "files": ["hunyuan_3d_v2.1.safetensors"]},
    "music": {"name": "ACE-Step 1.5 XL Turbo", "licence": "MIT (trained on licensed/royalty-free/synthetic data)",
              "files": ["acestep_v1.5_xl_turbo_bf16.safetensors", "qwen_0.6b_ace15.safetensors",
                        "qwen_4b_ace15.safetensors", "ace_1.5_vae.safetensors"]},
}
# MoGe-2 weights and the krea2_darkbrush LoRA are NOT approved (licence unchecked).
MAX_PIXELS = 2_100_000


# ---------------------------------------------------------------- ComfyUI lifecycle
# This tool starts ComfyUI itself, headless: the install's venv python runs ComfyUI/main.py with
# the arguments Comfy Desktop would give it (_direct_launch_command), never the Desktop app,
# whose own start (venv re-sync, pygit2 setup) took ~4 minutes here and outlived the old 120 s
# wait. The server runs in a kill-on-close job object of the process that started it
# (_start_server): a one-shot command's server ends with the command, a session's with its
# keeper (`serve`), however either ends, crash and kill included. It shuts down only what IT
# started - a ComfyUI the owner started is never touched. One small state file under
# <shared temp>\comfy_gen\ records the server's pid and creation time (so a reused pid is never
# mistaken for ours - see winproc.terminate_verified), when it was last used, and for a session
# its lock run id and keeper. The shared temp (SHARED_TEMP), else LOCALAPPDATA\Temp, never TEMP:
# `stop` from the owner's shell must find the state an agent wrote, even when agents run with
# TEMP moved
STATE_DIR = (os.path.join(SHARED_TEMP, "comfy_gen") if SHARED_TEMP else
             os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".", "Temp",
                          "comfy_gen"))
STATE_FILE = os.path.join(STATE_DIR, "state.json")
IDLE_STOP_SECONDS = 600
READY_SECONDS = 300          # a cold start: venv check, custom nodes, the model scan
SESSION_SECONDS = 3600       # `up --for` default: the most a session lives
POLL_SECONDS = 5             # the keeper's loop
DOWN_WAIT_SECONDS = 30       # `down`: how long the keeper gets to end on its request
LOCK_CALL_SECONDS = 120      # a machine_lock.py call (_run_quiet) that takes longer is reported
LOG_TAIL = 1500              # bytes of a server log quoted when it fails
APPDATA_COMFY = os.path.join(os.environ.get("APPDATA", ""), "Comfy Desktop")
NO_INSTALL = ("cannot start ComfyUI: no local install in Comfy Desktop's installations.json "
              "(an entry with an installPath holding ComfyUI\\.venv\\Scripts\\python.exe and "
              "ComfyUI\\main.py) under {}. Report it: agents never start the Desktop app, nor ask the owner to.")


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (dt.microsecond // 1000)


def _parse_iso(s):
    if not s:
        return None
    s = s[:-1] + "+00:00" if s.endswith("Z") else s
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def read_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return None


def write_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)


def clear_state():
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _pid_alive_at(pid, ctime):
    """True when pid is still the exact process created at ctime (never a reused pid)."""
    d = W.details(pid)
    return d["ctime"] is not None and d["ctime"] == ctime


def _tool_started_and_alive(state):
    return bool(state and state.get("pid") is not None
                and _pid_alive_at(state["pid"], state.get("ctime")))


def touch_last_used(state=None):
    """Refresh the idle clock. Safe to call whether or not ComfyUI was started by this tool."""
    try:
        state = state or read_state()
        if state:
            state["last_used"] = _iso(_now())
            write_state(state)
    except OSError:
        pass


def _reachable(timeout=3):
    try:
        C.get_json("/system_stats", timeout=timeout)
        return True
    except Exception:
        return False


def _wait_for_stats(wait, session=None):
    """Wait up to `wait` seconds for ComfyUI to answer. With `session` (a run id), stop early once
    the state says that session failed, was asked to end, or is gone: nothing will answer then."""
    deadline = time.time() + wait
    while time.time() < deadline:
        if _reachable():
            return True
        if session:
            state = read_state()
            if (not state or state.get("rid") != session or state.get("failed")
                    or _down_requested(state)):
                return False
        time.sleep(2)
    return False


def _agent_output_dir():
    """<AGENT_TEMP>\\comfy when AGENT_TEMP is set (process env, else the user's registry
    environment), else None. A ComfyUI this tool starts writes there, so agent generations land in
    the agents' temp folder rather than the ComfyUI install . A local ComfyUI
    keeps its folder."""
    root = os.environ.get("AGENT_TEMP")
    if not root:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                root = winreg.QueryValueEx(key, "AGENT_TEMP")[0]
        except (ImportError, OSError):
            root = None
    if not root:
        return None
    path = os.path.join(root, "comfy")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


def _host_port():
    """(host, port) this tool talks to: C.HOST, which COMFY_URL sets."""
    parsed = urllib.parse.urlparse(C.HOST)
    return parsed.hostname or "127.0.0.1", parsed.port or 8188


def _pick_install(installs, last_id):
    """The install to start: the one last-session.json names when it is a local install (it has
    an installPath; a Comfy Cloud entry has none), else the first local install listed. None when
    there is none."""
    local = [i for i in installs if isinstance(i, dict) and i.get("installPath")]
    return next((i for i in local if i.get("id") == last_id), local[0] if local else None)


def _direct_launch_command():
    """The command Comfy Desktop would use for its install: the venv python running
    ComfyUI/main.py from the install path, with the same extra-model-paths config and
    input/output directories and the install's own launchArgs, headless on C.HOST's port.
    Read-only: nothing here is written to the Comfy Desktop config. Returns (argv, cwd), or
    (None, None) when there is no local install or a piece of it is missing."""
    try:
        try:
            with open(os.path.join(APPDATA_COMFY, "last-session.json"), encoding="utf-8") as f:
                last_id = json.load(f).get("installationId")
        except (OSError, ValueError, AttributeError):
            last_id = None
        with open(os.path.join(APPDATA_COMFY, "installations.json"), encoding="utf-8") as f:
            inst = _pick_install(json.load(f), last_id)
        if inst is None:
            return None, None
        inst_id, install_path = inst.get("id"), inst["installPath"]
        python_exe = os.path.join(install_path, "ComfyUI", ".venv", "Scripts", "python.exe")
        main_py = os.path.join("ComfyUI", "main.py")
        if not os.path.isfile(python_exe) or not os.path.isfile(os.path.join(install_path, main_py)):
            return None, None
        args = ["-s", main_py]
        yaml_cfg = os.path.join(APPDATA_COMFY, "instance-model-paths", f"{inst_id}.yaml")
        if os.path.isfile(yaml_cfg):
            args += ["--extra-model-paths-config", yaml_cfg]
        try:
            with open(os.path.join(APPDATA_COMFY, "settings.json"), encoding="utf-8") as f:
                settings = json.load(f)
        except (FileNotFoundError, ValueError):
            settings = {}
        if settings.get("inputDir"):
            args += ["--input-directory", settings["inputDir"]]
        out_dir = _agent_output_dir() or settings.get("outputDir")
        if out_dir:
            args += ["--output-directory", out_dir]
        # posix=False keeps Windows backslashes but also keeps the quotes round a token; Comfy
        # Desktop writes `--temp-directory "C:\ComfyUI"`, so strip one pair.
        args += [a[1:-1] if len(a) >= 2 and a[0] == a[-1] == '"' else a
                 for a in shlex.split(inst.get("launchArgs") or "", posix=False)]
        # Headless and on the same host/port this tool talks to (C.HOST), forced last so an
        # earlier occurrence in launchArgs never wins (argparse keeps the last of a repeated flag).
        host, port = _host_port()
        args += ["--listen", host, "--port", str(port), "--disable-auto-launch"]
        return [python_exe] + args, install_path
    except (FileNotFoundError, KeyError, ValueError, TypeError, OSError):
        return None, None


def _new_log():
    return os.path.join(STATE_DIR, f"comfy-{datetime.datetime.now():%Y%m%d-%H%M%S}.log")


def _tail(path):
    """The last LOG_TAIL bytes of a server log, '' when it cannot be read."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - LOG_TAIL))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _listening(port):
    """The pids that listen on TCP `port` now (kitlib.proc.tcp.listeners; a Linux row's pid is
    None). Empty when the table cannot be read or the kit library is missing."""
    try:
        from kitlib.proc import tcp
        return {pid for end, pid in tcp.listeners() if end[2] == port}
    except (ImportError, OSError):
        return set()


def _describe(pid):
    if pid is None:
        return "a process whose owner cannot be read"
    return f"pid {pid} ({W.details(pid).get('image') or 'image unreadable'})"


def _desktop_child(pid, port):
    """Is `pid` the ComfyUI Comfy Desktop runs on `port`: does a comfy-procs/<id>.json of Desktop's
    name it (its childPid, or a pid in its tree) with no stop asked?"""
    folder = os.path.join(APPDATA_COMFY, "comfy-procs")
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("port") != port or rec.get("stopRequestedAt"):
                continue
            pids = {rec.get("childPid")} | {t.get("pid") for t in rec.get("tree") or []}
        except (OSError, ValueError, AttributeError, TypeError):
            continue
        if pid is not None and pid in pids:
            return True
    return False


def _port_held(wait):
    """The port check before a start, with nothing answering at C.HOST. None when nothing listens
    on its port. Comfy Desktop's ComfyUI still starting there is waited for, up to `wait`
    seconds: True once it answers (the owner's: use it as it is). Anything else listening there
    raises RuntimeError naming the process; nothing is started beside it."""
    _host, port = _host_port()
    pids = _listening(port)
    if not pids:
        return None
    if any(_desktop_child(p, port) for p in pids):
        print(f"[comfy_gen] Comfy Desktop is starting ComfyUI on port {port}; waiting up to {wait}s "
              "for it", file=sys.stderr)
        if _wait_for_stats(wait):
            return True
        raise RuntimeError(f"Comfy Desktop's ComfyUI on port {port} did not answer within {wait}s; "
                           "this tool starts none beside it")
    raise RuntimeError(f"cannot start ComfyUI: port {port} is held by "
                       + ", ".join(_describe(p) for p in sorted(pids, key=str))
                       + ", which does not answer as ComfyUI; nothing was started")


def _start_server(cmd, cwd, log_path):
    """Start ComfyUI (`cmd`, from _direct_launch_command) in a kill-on-close job object of this
    process (kitlib.proc.job.Job): when this process ends, however it ends, the job's handle closes
    and ComfyUI ends with every process it started. No breakaway, no window (Job.start adds
    CREATE_NO_WINDOW for a parent with no console). Returns (job, proc); the caller keeps the job
    open while the server should live. RuntimeError off Windows or with no kit library; OSError
    when the command cannot start."""
    if os.name != "nt":
        raise RuntimeError("cannot start ComfyUI: this tool starts it on Windows only")
    try:
        from kitlib.proc.job import Job
    except ImportError:
        raise RuntimeError(f"cannot start ComfyUI: no kit library (kitlib.proc.job) at {KIT_ROOT}")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    job = Job(kill_on_close=True)
    try:
        with open(log_path, "ab") as logf:
            proc = job.start(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=logf,
                             stderr=subprocess.STDOUT)
    except BaseException:
        job.close()
        raise
    return job, proc


def _ours(job, proc, pid):
    """Is the listener `pid` the ComfyUI just started (its process, or one its job holds)? None (a
    Linux listener, its owner unread) counts as ours."""
    if pid is None or pid == proc.pid:
        return True
    try:
        return job.contains(pid) == "in"
    except Exception:
        return False


def _wait_ready(proc, job, ready_s, log_path):
    """Wait up to `ready_s` seconds for the ComfyUI just started to answer at C.HOST. None once it
    answers on a listener of its own; else why not, with its log's tail where that helps: it
    exited first (no use waiting on), another process took the port, or the time ran out."""
    _host, port = _host_port()
    deadline = time.monotonic() + ready_s
    while True:
        code = proc.poll()
        if code is not None:
            return (f"ComfyUI exited with code {code} before it answered; its log {log_path} "
                    f"ends:\n{_tail(log_path)}")
        foreign = [p for p in _listening(port) if not _ours(job, proc, p)]
        if foreign:
            return (f"port {port} was taken by {_describe(foreign[0])}, not the ComfyUI this tool "
                    "started; that one is ended and the other process left alone")
        if _reachable():
            return None
        if time.monotonic() >= deadline:
            return (f"ComfyUI did not answer at {C.HOST} within {ready_s}s of starting it; its log "
                    f"{log_path} ends:\n{_tail(log_path)}")
        time.sleep(2)


_JOBS = []   # the job of a ComfyUI this command started, open while this process lives
_IN_LOCK_RUN = False   # set by machine_lock_rerun in the re-run: this command holds its own slot


def _launch(wait):
    """Start ComfyUI for this one command and wait for it (_wait_ready). The server lives in this
    process's job (_start_server), so it ends with the command; the run's release then finds it
    gone. Only a command in its own local-ai lock run (_IN_LOCK_RUN) may start one: a command that
    joined a session (and so booked nothing) is refused once that session has ended. Raises
    RuntimeError with why when it cannot start or does not answer."""
    cmd, cwd = _direct_launch_command()
    if cmd is None:
        raise RuntimeError(NO_INSTALL.format(APPDATA_COMFY))
    if not _IN_LOCK_RUN:
        raise RuntimeError(f"ComfyUI is not running at {C.HOST} and this command holds no local-ai "
                           "slot (it joined a session that has since ended, so it booked none); "
                           "nothing was started. Run the command again: it books its own slot. "
                           "Or `comfy_gen.py up` first for a new session.")
    print(f"[comfy_gen] ComfyUI not reachable at {C.HOST}; starting it headless", file=sys.stderr)
    log_path = _new_log()
    try:
        job, proc = _start_server(cmd, cwd, log_path)
    except OSError as e:
        raise RuntimeError(f"cannot start ComfyUI ({cmd[0]}): {e}")
    _JOBS.append(job)
    write_state({"pid": proc.pid, "ctime": W.details(proc.pid)["ctime"],
                 "cmd": subprocess.list2cmdline(cmd), "source": "direct", "log": log_path,
                 "started": _iso(_now()), "last_used": _iso(_now())})
    print(f"[comfy_gen] ComfyUI starting, pid {proc.pid}; log: {log_path}", file=sys.stderr)
    why = _wait_ready(proc, job, wait, log_path)
    if why:
        job.terminate()
        clear_state()
        raise RuntimeError(why)


def _down_path(rid):
    """The file `down` makes to end session `rid`. A file of its own, never a field of the state:
    every writer of the state rewrites it whole, so a flag there could be lost."""
    return os.path.join(STATE_DIR, f"down-{rid}")


def _request_down(rid):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(_down_path(rid), "w", encoding="utf-8") as f:
        f.write(_iso(_now()))


def _down_requested(state):
    return bool(state and (state.get("down")
                           or (state.get("rid") and os.path.exists(_down_path(state["rid"])))))


def _clear_down(rid):
    try:
        os.remove(_down_path(rid))
    except OSError:
        pass


def _session_live(state):
    """Is `state` a session's (`up`) whose keeper is still the process it recorded, not failed and
    not asked to end?"""
    return bool(state and state.get("rid") and state.get("keeper_pid") is not None
                and not state.get("failed") and not _down_requested(state)
                and _pid_alive_at(state["keeper_pid"], state.get("keeper_ctime")))


def ensure_running(wait=READY_SECONDS):
    """Make sure ComfyUI answers at C.HOST, starting it if needed. Returns True when THIS tool
    owns the running ComfyUI (a session's, or one this command started) - the only kind `stop`
    and `release` will ever touch. Returns False when it was already reachable and is not ours:
    the owner started it, and it is left running untouched. Raises RuntimeError when it cannot
    be had (why, and the server log's tail when it started one)."""
    state = read_state()
    if _reachable():
        if _tool_started_and_alive(state or {}):
            touch_last_used()    # re-read: the keeper may have written since
            return True
        if state and not _session_live(state):
            clear_state()   # stale record: a previous run of ours, now gone or pid reused
        return False
    if _session_live(state) or _tool_started_and_alive(state or {}):
        # a session's ComfyUI `up` is starting, or ours from an earlier start still booting:
        # wait for that one, never start a second
        if not _wait_for_stats(wait, session=state.get("rid")):
            now = read_state() or {}
            if state.get("rid") and now.get("rid") == state["rid"] and now.get("failed"):
                raise RuntimeError(f"ComfyUI session {now['rid']} failed: {now['failed']}")
            if state.get("rid") and (now.get("rid") != state["rid"] or _down_requested(now)):
                raise RuntimeError(f"ComfyUI session {state['rid']} ended before it answered; run "
                                   "the command again (or `comfy_gen.py up` first)")
            raise RuntimeError(f"ComfyUI did not answer at {C.HOST} within {wait}s"
                               + (f" (log: {state['log']})" if state.get("log") else ""))
        touch_last_used()
        return True
    clear_state()
    if _port_held(wait):
        return False
    _launch(wait)
    touch_last_used()
    return True


def _stop_now(manual):
    state = read_state()
    if not state:
        if manual:
            print("[comfy_gen] nothing recorded as started by this tool; nothing to stop")
        return 0
    pid, ctime = state.get("pid"), state.get("ctime")
    d = W.details(pid)
    if d["ctime"] is None:
        print(f"[comfy_gen] pid {pid} is already gone")
    elif d["ctime"] != ctime:
        print(f"[comfy_gen] pid {pid} was reused by another process since we started ComfyUI; "
              "not touching it")
    else:
        victims = [(pid, ctime)]
        for p, ppid, _name in W.snapshot():
            if ppid == pid:
                dd = W.details(p)
                # a child started after ours; an older process only names our pid as a stale ppid
                if dd["ctime"] is not None and dd["ctime"] >= ctime:
                    victims.append((p, dd["ctime"]))
        for p, ct in victims:
            print(f"[comfy_gen] stop pid {p}: {W.terminate_verified(p, ct)}")
    clear_state()
    return 0


def cmd_stop(args):
    state = read_state()
    if state and state.get("rid"):
        return cmd_down(args)
    return _stop_now(manual=True)


# ---------------------------------------------------------------- the session (up / serve / down)
def _queue_busy():
    """Is a job running or queued on ComfyUI? An unreachable ComfyUI is not busy."""
    try:
        q = C.get_json("/queue", timeout=5)
        return bool(q.get("queue_running") or q.get("queue_pending"))
    except Exception:
        return False


def _keep(proc):
    """The keeper's loop, every POLL_SECONDS. It returns, for its caller to end the server, when
    the server has exited (a crash: why is recorded for `up` and `check`; exit 3), `down` has asked
    (_down_requested), the session's --for time is up, it has been idle IDLE_STOP_SECONDS with an
    empty queue, or the state no longer names this keeper."""
    me = os.getpid()
    while True:
        time.sleep(POLL_SECONDS)
        state = read_state()
        code = proc.poll()
        if code is not None:
            log = (state or {}).get("log") or "?"
            why = f"ComfyUI exited with code {code} during the session; its log {log} ends:\n{_tail(log)}"
            if state and state.get("keeper_pid") == me:
                state.update(failed=why, ready=False)
                write_state(state)
            print(why, file=sys.stderr)
            return 3
        if not state or state.get("keeper_pid") != me:
            print("[comfy_gen] serve: the state no longer names this session; ending it")
            return 0
        if _down_requested(state):
            print("[comfy_gen] serve: `down` asked; ending the session")
            return 0
        until = _parse_iso(state.get("until"))
        if until is not None and _now() >= until:
            print("[comfy_gen] serve: the session's --for time is up; ending it")
            return 0
        last = _parse_iso(state.get("last_used")) or _now()
        if (_now() - last).total_seconds() >= IDLE_STOP_SECONDS and not _queue_busy():
            print(f"[comfy_gen] serve: idle {IDLE_STOP_SECONDS // 60} minutes; ending the session")
            return 0


def cmd_serve(args):
    """The session's keeper (internal: the command of `up`'s detached local-ai lock run). It starts
    ComfyUI in a job of its own, waits for it, records it in the state (its run id, this keeper,
    the server, ready or why it failed), keeps it (_keep) and ends it, whatever ends this: the
    job closes with this process too, so a killed keeper takes ComfyUI along."""
    rid = os.environ.get("MACHINE_LOCK_RUN", "").split(":")[0]
    me, now = os.getpid(), _now()
    prior = read_state()
    state = {"rid": rid, "keeper_pid": me, "keeper_ctime": W.details(me)["ctime"],
             "started": _iso(now), "last_used": _iso(now), "ready": False,
             "until": _iso(now + datetime.timedelta(seconds=args.ttl))}

    def fail(why, code):
        state["failed"] = why
        write_state(state)
        print(why, file=sys.stderr)
        return code

    if _reachable():
        if _tool_started_and_alive(prior or {}):
            # this tool's ComfyUI (another session's, or a command's own): its record stays, so
            # whoever owns it still ends it
            whose = (f"session {prior['rid']}'s" if prior.get("rid") else
                     "another comfy_gen command's own (it ends with that command)")
            print(f"ComfyUI already answers at {C.HOST}: it is {whose}; no session started. Run "
                  "`up` again once it has gone.", file=sys.stderr)
            return 3
        return fail(f"ComfyUI already answers at {C.HOST}, started outside this tool: use it as "
                    "it is; no session started", 3)
    try:
        if _port_held(args.ready):
            return fail(f"Comfy Desktop's ComfyUI now answers at {C.HOST}: use it as it is; no "
                        "session started", 3)
    except RuntimeError as e:
        return fail(str(e), 2)
    cmd, cwd = _direct_launch_command()
    if cmd is None:
        return fail(NO_INSTALL.format(APPDATA_COMFY), 2)
    log_path = _new_log()
    try:
        job, proc = _start_server(cmd, cwd, log_path)
    except (OSError, RuntimeError) as e:
        return fail(f"cannot start ComfyUI ({cmd[0]}): {e}", 2)
    try:
        state.update(pid=proc.pid, ctime=W.details(proc.pid)["ctime"], log=log_path,
                     cmd=subprocess.list2cmdline(cmd), source="session")
        write_state(state)
        print(f"[comfy_gen] serve: session {rid}: ComfyUI pid {proc.pid} starting; log {log_path}",
              flush=True)
        why = _wait_ready(proc, job, args.ready, log_path)
        if why:
            return fail(why, 2)
        state.update(ready=True, last_used=_iso(_now()))
        write_state(state)
        print(f"[comfy_gen] serve: ComfyUI up at {C.HOST}", flush=True)
        return _keep(proc)
    finally:
        job.terminate()
        job.close()
        final = read_state()
        if final and final.get("keeper_pid") == me and not final.get("failed"):
            clear_state()
        _clear_down(rid)


def _lock_cmd(*args):
    return [sys.executable, "-B", MACHINE_LOCK_TOOL] + list(args)


def _run_quiet(cmd):
    """A machine_lock.py call, its output captured, so it needs no console of ours: no window. One
    that does not answer within LOCK_CALL_SECONDS comes back as exit 124 with why in stderr."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=LOCK_CALL_SECONDS,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, stdout="",
                                           stderr=f"machine_lock.py did not answer within "
                                                  f"{e.timeout:g}s")


def _await_session(rid, pid, ctime, wait_s):
    """Wait for session `rid` to be ready, as its keeper writes it in the state. `pid` (created at
    `ctime`) is its lock run's wrapper, or its keeper: alive while the run is. 0 once ready; 2 when
    the keeper recorded why it failed, the run ended first, or `wait_s` ran out (the run is then
    stopped)."""
    deadline = time.monotonic() + wait_s
    while True:
        state = read_state()
        if state and state.get("rid") == rid:
            if state.get("failed"):
                print(f"ComfyUI session {rid} did not start: {state['failed']}")
                return 2
            if state.get("ready"):
                print(f"ComfyUI up at {C.HOST}: session {rid} (pid {state.get('pid')}, log "
                      f"{state.get('log')}). Generating commands use it and book nothing of their "
                      f"own. `comfy_gen.py down` ends it; so do {IDLE_STOP_SECONDS // 60} idle "
                      "minutes and its --for time.")
                return 0
        if ctime is None or not _pid_alive_at(pid, ctime):
            print(f"ComfyUI session {rid}: its lock run ended before ComfyUI was ready (see "
                  f"machine_lock.py status {rid})")
            return 2
        if time.monotonic() >= deadline:
            done = _run_quiet(_lock_cmd("stop", rid))
            print(f"ComfyUI session {rid} was not ready within {wait_s}s; its lock run is stopped "
                  f"(exit {done.returncode})")
            return 2
        time.sleep(2)


def cmd_up(args):
    """Start a ComfyUI session for an art run (the module docstring): book a detached local-ai
    lock run holding comfy-port:<port>, whose command is the keeper (`serve`), and wait until
    ComfyUI answers or fails. Prints the run id. Nothing is booked when ComfyUI already answers
    or a session is already up."""
    state = read_state()
    if _session_live(state):
        if state.get("ready"):
            print(f"ComfyUI session {state['rid']} is already up at {C.HOST}; `comfy_gen.py down` "
                  "ends it")
            return 0
        print(f"[comfy_gen] up: session {state['rid']} is starting; waiting for it", file=sys.stderr)
        return _await_session(state["rid"], state["keeper_pid"], state.get("keeper_ctime"),
                              args.ready + 60)
    if _reachable():
        if _tool_started_and_alive(state or {}):
            if state.get("rid"):
                print(f"ComfyUI session {state['rid']} is ending (`down`, or it failed); run `up` "
                      "again once it has gone. Nothing booked.")
            else:
                print(f"ComfyUI already answers at {C.HOST}: another comfy_gen command started it "
                      "for itself and it ends with that command. Nothing booked; run `up` again "
                      "once it has gone.")
            return 2
        print(f"ComfyUI already answers at {C.HOST}, started outside this tool (by you or Comfy "
              "Desktop): use it as it is; nothing booked")
        return 0
    if not os.path.isfile(MACHINE_LOCK_TOOL):
        print(f"[machine-lock] refused: no machine lock tool at {MACHINE_LOCK_TOOL}, and a "
              "session takes a local-ai slot. Exit 2.", file=sys.stderr)
        return 2
    if _direct_launch_command()[0] is None:
        print(NO_INSTALL.format(APPDATA_COMFY), file=sys.stderr)
        return 2
    _host, port = _host_port()
    wait = max(1, min(int(args.lock_wait), 540))
    keeper = [sys.executable, "-B", os.path.abspath(__file__), "serve", "--for", str(args.ttl),
              "--ready", str(args.ready)]
    done = _run_quiet(_lock_cmd("run", "--detach", "--repo", "comfy", "--slug", "session",
                                "--kind", "local-ai", "--resource", f"comfy-port:{port}",
                                "--max-wait", f"{wait}s", "--expect", f"{args.ttl}s", "--") + keeper)
    m = re.match(r"\s*(r-\S+) pid (\d+)", done.stdout or "")
    if done.returncode != 0 or not m:
        print(f"[comfy_gen] up: the machine lock did not start the session (exit {done.returncode}): "
              + ((done.stdout or "") + (done.stderr or "")).strip(), file=sys.stderr)
        return 2
    rid, pid = m.group(1), int(m.group(2))
    print(f"[comfy_gen] up: lock run {rid} (local-ai, comfy-port:{port}); waiting for ComfyUI",
          file=sys.stderr)
    return _await_session(rid, pid, W.details(pid)["ctime"], wait + args.ready + 60)


def cmd_down(args):
    """End the session `up` started: make its down request (_request_down), on which its keeper
    ends ComfyUI and its lock run within POLL_SECONDS; a keeper still there after DOWN_WAIT_SECONDS
    has its lock run stopped (machine_lock.py stop: the run's job ends, keeper and ComfyUI with
    it). The request is a file of its own, so no later write of the state can drop it."""
    state = read_state()
    if not state or not state.get("rid"):
        print("[comfy_gen] down: no ComfyUI session; nothing to end")
        return 0
    rid = state["rid"]
    if state.get("keeper_pid") is not None and _pid_alive_at(state["keeper_pid"],
                                                             state.get("keeper_ctime")):
        _request_down(rid)
        deadline = time.monotonic() + DOWN_WAIT_SECONDS
        while _pid_alive_at(state["keeper_pid"], state.get("keeper_ctime")):
            if time.monotonic() >= deadline:
                done = _run_quiet(_lock_cmd("stop", rid))
                print(f"[comfy_gen] down: the keeper did not end within {DOWN_WAIT_SECONDS}s; "
                      f"machine_lock.py stop {rid}: exit {done.returncode} "
                      + ((done.stdout or "") + (done.stderr or "")).strip())
                if done.returncode != 0:
                    return 1
                break
            time.sleep(1)
    final = read_state()
    if final and final.get("rid") == rid:
        clear_state()
    _clear_down(rid)
    print(f"[comfy_gen] down: session {rid} ended")
    return 0


def engine_running():
    try:
        # Its output is captured, so it needs no console of ours: no window from a parent without one.
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except Exception:
        return []
    return sorted({line.split(",")[0].strip('"') for line in out.splitlines() if "godot" in line.lower()})


def installed_files():
    names = set()
    for folder in C.get_json("/models"):
        try:
            names.update(os.path.basename(f.replace("\\", "/")) for f in C.get_json(f"/models/{folder}"))
        except Exception:
            pass
    return names


EXIT_GENERATOR_DOWN = 2
SAVED = []  # every file this command saved so far, named when the generator is lost mid-run


class GeneratorDown(Exception):
    """ComfyUI cannot be reached: not started, or lost mid-run."""


# what a lost connection to ComfyUI raises: a URLError whose reason is an OSError (refused, reset,
# timed out), or a dropped or stalled socket while a response is read. An HTTPError is a URLError
# too, but it is a live ComfyUI answering: never an outage (is_outage).
CONNECTION_ERRORS = (urllib.error.URLError, ConnectionError, TimeoutError)


def is_outage(e):
    if isinstance(e, urllib.error.HTTPError):
        return False
    if isinstance(e, urllib.error.URLError):
        return isinstance(e.reason, OSError)
    return isinstance(e, (ConnectionError, TimeoutError))


def outage_lines(why, saved):
    lines = [f"GENERATOR UNAVAILABLE: ComfyUI at {C.HOST}: {why}",
             "Stop this run and report the outage. Do not write prompts or placeholders in place "
             "of the outputs; rerun the command once the generator is back."]
    if saved:
        lines.append("saved before it was lost: " + ", ".join(saved))
    return lines


def _ensure_running_or_down():
    try:
        ensure_running()
    except RuntimeError as e:
        raise GeneratorDown(str(e))


def preflight(args, model_keys):
    _ensure_running_or_down()
    have = installed_files()
    missing = [f for k in model_keys for f in MODELS[k]["files"] if f not in have]
    if missing:
        print("missing model files (still downloading, or not installed): " + ", ".join(missing), file=sys.stderr)
        sys.exit(3)



RUN_FOLDER = None  # set once per command in run_handler


def run_job(api, timeout=900):
    """Submit with every save node writing into this command's own folder under ComfyUI's output/agent/."""
    for node in api.values():
        inputs = node.get("inputs", {})
        if isinstance(inputs.get("filename_prefix"), str):
            leaf = inputs["filename_prefix"].replace("\\", "/").rstrip("/").split("/")[-1] or "out"
            inputs["filename_prefix"] = f"{RUN_FOLDER}/{leaf}"
    return C.run(api, timeout=timeout)


def save_outputs(args, entry, meta, started, only_nodes=None):
    os.makedirs(args.out, exist_ok=True)
    saved = []
    items = [o for o in C.outputs(entry) if only_nodes is None or o[0] in only_nodes]
    for n, (nid, kind, item) in enumerate(items):
        ext = os.path.splitext(item["filename"])[1] or ".bin"
        stem = args.name if len(items) == 1 else f"{args.name}_{n + 1:02d}"
        path = os.path.join(args.out, stem + ext)
        with open(path, "wb") as f:
            f.write(C.view(item, timeout=600))
        side = dict(meta, file=os.path.basename(path), output_node=nid, output_kind=kind,
                    tool="ComfyUI (local)", generated=datetime.datetime.now().isoformat(timespec="seconds"),
                    seconds=round(time.time() - started, 1))
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump(side, f, indent=2)
        saved.append(path)
        SAVED.append(path)
        print(f"saved {path}")
    if not saved:
        print("the run finished but produced no saved files", file=sys.stderr)
        sys.exit(5)
    return saved


def meta_for(key, **extra):
    m = MODELS[key]
    return {"model": m["name"], "licence": m["licence"], **extra}


def seed_of(args):
    return args.seed if args.seed is not None else random.randrange(2**48)


def set_input(api, node, name, value):
    if node not in api:
        raise SystemExit(f"node {node} not in this workflow")
    api[node]["inputs"][name] = value


def keep_outputs(api, keep):
    """Drop output nodes we don't want saved (previews stay: they write temp files only)."""
    for nid in [k for k, v in api.items() if v["class_type"].startswith("Save") and k not in keep]:
        del api[nid]


def prepare_object_image(args):
    """BiRefNet cut-out -> tight crop -> centred on a white square, saved next to the output."""
    from PIL import Image
    api = C.to_api(C.load_template("utility_birefnet_remove_background"))
    set_input(api, "17", "image", C.upload_image(args.image))
    api["90"] = {"class_type": "SaveImage", "inputs": {"images": ["19:16", 0], "filename_prefix": "agent/prep"}}
    entry = run_job(api, timeout=300)
    item = [o for o in C.outputs(entry) if o[0] == "90"][0][2]
    os.makedirs(args.out, exist_ok=True)
    cut = os.path.join(args.out, f"{args.name}_input_cutout.png")
    with open(cut, "wb") as f:
        f.write(C.view(item))
    im = Image.open(cut).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 24 else 0).getbbox() or (0, 0, *im.size)
    obj = im.crop(box)
    side = int(max(obj.size) * 1.15)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(obj, ((side - obj.size[0]) // 2, (side - obj.size[1]) // 2))
    prepared = os.path.join(args.out, f"{args.name}_input_prepared.png")
    canvas.convert("RGB").resize((1024, 1024)).save(prepared)
    return prepared


# ---------------------------------------------------------------- commands
def cmd_free(args):
    """Unload ComfyUI's models and free its RAM/VRAM, unless work is queued."""
    import urllib.request
    try:
        q = C.get_json("/queue", timeout=5)
        before = C.get_json("/system_stats", timeout=5)["devices"][0].get("vram_free", 0)
    except Exception as e:
        print(f"ComfyUI not reachable at {C.HOST}: {e}")
        return 2
    busy = len(q.get("queue_running", [])) + len(q.get("queue_pending", []))
    if busy:
        print(f"ComfyUI has {busy} job(s) running or queued; not freeing. Run `free` again when it is idle.")
        return 3
    req = urllib.request.Request(C.HOST + "/free", data=b'{"unload_models": true, "free_memory": true}',
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()
    time.sleep(3)
    after = C.get_json("/system_stats", timeout=5)["devices"][0].get("vram_free", 0)
    print(f"freed: VRAM free {before // 2**20} -> {after // 2**20} MiB")
    return 0


def cmd_check(args):
    try:
        stats = C.get_json("/system_stats", timeout=3)
    except Exception as e:
        # check never starts it (it holds no lock); it only says whether a job could
        if _direct_launch_command()[0] is None:
            print(f"ComfyUI not reachable at {C.HOST} ({e}) and no local Comfy install to start it "
                  "from (" + NO_INSTALL.format(APPDATA_COMFY) + ")")
            return 2
        busy = engine_running()
        print("ComfyUI is off; `comfy_gen.py up` starts it headless for an art run (`down` ends "
              "it), and a lone generating command starts it for itself. Models are checked then.")
        print("  engine: " + (("RUNNING " + ", ".join(busy) + " -> wait or take the engine lock") if busy else "idle"))
        return 0
    have = installed_files()
    dev = stats.get("devices", [{}])[0]
    print(f"ComfyUI {stats['system'].get('comfyui_version')} at {C.HOST}; "
          f"GPU {dev.get('name', '?')} free {dev.get('vram_free', 0) // 2**20} MiB")
    code = 0
    for key, m in MODELS.items():
        missing = [f for f in m["files"] if f not in have]
        print(f"  {'OK     ' if not missing else 'MISSING'} {key:9} {m['name']}"
              + (f"  (missing: {', '.join(missing)})" if missing else ""))
        if missing:
            code = 3
    busy = engine_running()
    print("  engine: " + (("RUNNING " + ", ".join(busy) + " -> wait or take the engine lock") if busy else "idle"))
    return code


def cmd_image(args):
    if args.width % 16 or args.height % 16:
        raise SystemExit("width and height must be multiples of 16")
    if args.width * args.height > MAX_PIXELS:
        raise SystemExit(f"{args.width}x{args.height} is too large; stay near 1 MP and upscale later")
    preflight(args, [args.model])
    first = seed_of(args)
    for i in range(args.count):
        seed = first + i
        started = time.time()
        if args.model == "zimage":
            api = C.to_api(C.load_template("image_z_image_turbo"))
            set_input(api, "57:27", "text", args.prompt)
            set_input(api, "57:13", "width", args.width)
            set_input(api, "57:13", "height", args.height)
            set_input(api, "57:3", "seed", seed)
            set_input(api, "57:3", "steps", args.steps or 8)
            save = "9"
        else:
            api = C.to_api(C.load_template("image_krea2_turbo_t2i"))
            set_input(api, "30:19", "value", args.prompt)   # raw prompt
            set_input(api, "30:24", "value", False)         # no LLM prompt rewrite
            set_input(api, "30:23", "value", False)         # no darkbrush LoRA (licence unchecked)
            set_input(api, "30:5", "width", args.width)
            set_input(api, "30:5", "height", args.height)
            set_input(api, "30:3", "seed", seed)
            if args.steps:
                set_input(api, "30:3", "steps", args.steps)
            save = "29"
        keep_outputs(api, {save})
        entry = run_job(api, timeout=args.timeout)
        stem = args.name
        args.name = stem if args.count == 1 else f"{stem}_{i + 1:02d}"
        save_outputs(args, entry, meta_for(args.model, prompt=args.prompt, seed=seed,
                                           width=args.width, height=args.height), started, {save})
        args.name = stem
    return 0


def cmd_edit(args):
    preflight(args, ["edit"])
    started = time.time()
    api = C.to_api(C.load_template("image_qwen_image_edit_2511"))
    set_input(api, "41", "image", C.upload_image(args.image))
    if args.image2:
        set_input(api, "83", "image", C.upload_image(args.image2))
    else:
        del api["83"]
        for nid in ("170:149", "170:151"):
            api[nid]["inputs"].pop("image2", None)
    set_input(api, "170:151", "prompt", args.prompt)
    set_input(api, "170:168", "value", True)   # Lightning LoRA: 4 steps, cfg 1
    seed = seed_of(args)
    set_input(api, "170:169", "seed", seed)
    keep_outputs(api, {"9"})
    entry = run_job(api, timeout=args.timeout)
    return 0 if save_outputs(args, entry, meta_for("edit", prompt=args.prompt, seed=seed,
                                                   source=os.path.basename(args.image),
                                                   source2=os.path.basename(args.image2 or "")),
                             started, {"9"}) else 1


def cmd_rembg(args):
    preflight(args, ["rembg"])
    started = time.time()
    api = C.to_api(C.load_template("utility_birefnet_remove_background"))
    set_input(api, "17", "image", C.upload_image(args.image))
    api["90"] = {"class_type": "SaveImage", "inputs": {"images": ["19:16", 0], "filename_prefix": "agent/rembg"}}
    entry = run_job(api, timeout=args.timeout)
    return 0 if save_outputs(args, entry, meta_for("rembg", source=os.path.basename(args.image)),
                             started, {"90"}) else 1


def cmd_upscale(args):
    preflight(args, ["upscale"])
    started = time.time()
    api = {
        "1": {"class_type": "LoadImage", "inputs": {"image": C.upload_image(args.image)}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "RealESRGAN_x4plus.safetensors"}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "agent/upscale"}},
    }
    entry = run_job(api, timeout=args.timeout)
    return 0 if save_outputs(args, entry, meta_for("upscale", source=os.path.basename(args.image), scale=4),
                             started, {"4"}) else 1


def cmd_to3d(args):
    preflight(args, [args.engine])
    started = time.time()
    seed = seed_of(args)
    if args.engine == "hunyuan3d":
        # Hunyuan3D reconstructs whatever is in frame, background included (a flat plate
        # with the object in relief). Cut the object out, crop it and centre it on white first.
        prepared = prepare_object_image(args)
        api = C.to_api(C.load_template("3d_hunyuan3d-v2.1"))
        set_input(api, "2", "image", C.upload_image(prepared))
        set_input(api, "7", "seed", seed)
        set_input(api, "10", "filename_prefix", "agent/3d")
        keep_outputs(api, {"10"})
        outs = {"10"}
    else:
        api = C.to_api(C.load_template("3d_pixal3d_trellis2_image_to_model"))
        set_input(api, "122", "image", C.upload_image(args.image))
        set_input(api, "316", "value", args.engine == "trellis2")  # switch: TRELLIS.2 vs Pixal3D
        for k in C.find(api, "KSampler"):
            set_input(api, k, "seed", seed + int(k))
        if args.faces:
            set_input(api, "186", "target_face_count", args.faces)
        set_input(api, "288", "value", args.texture)  # baked texture size (template default 4096)
        set_input(api, "322", "filename_prefix", "agent/3d")
        keep_outputs(api, {"322"})
        outs = {"322"}
    entry = run_job(api, timeout=args.timeout)
    return 0 if save_outputs(args, entry, meta_for(args.engine, source=os.path.basename(args.image), seed=seed,
                                                   faces=args.faces, texture=args.texture), started, outs) else 1


def cmd_music(args):
    preflight(args, ["music"])
    started = time.time()
    seed = seed_of(args)
    api = C.to_api(C.load_template("audio_ace_step1_5_xl_turbo"))
    enc = C.find(api, "TextEncodeAceStepAudio1.5")[0]
    set_input(api, enc, "tags", args.tags)
    set_input(api, enc, "lyrics", args.lyrics)
    set_input(api, enc, "bpm", args.bpm)
    set_input(api, enc, "duration", float(args.seconds))
    set_input(api, enc, "seed", seed)
    set_input(api, C.find(api, "EmptyAceStep1.5LatentAudio")[0], "seconds", float(args.seconds))
    set_input(api, "3", "seed", seed)
    save = C.find(api, "SaveAudioAdvanced")[0]
    api[save] = {"class_type": "SaveAudio", "inputs": {"audio": api[save]["inputs"]["audio"],
                                                       "filename_prefix": "agent/music"}}
    entry = run_job(api, timeout=args.timeout)
    return 0 if save_outputs(args, entry, meta_for("music", tags=args.tags, lyrics=args.lyrics, bpm=args.bpm,
                                                   seconds=args.seconds, seed=seed), started, {save}) else 1


def cmd_template(args):
    if args.template.startswith("api_"):
        raise SystemExit("API templates are paid cloud nodes and are not approved")
    busy_ok = preflight_generic(args)
    api_check = C.to_api(C.load_template(args.template))
    paid = [v["class_type"] for v in api_check.values()
            if C.object_info().get(v["class_type"], {}).get("api_node")]
    if paid:
        raise SystemExit("this template uses paid API nodes (" + ", ".join(sorted(set(paid))) + "); not approved")
    started = time.time()
    api = C.to_api(C.load_template(args.template))
    for spec in args.load or []:
        node, path = spec.split("=", 1)
        set_input(api, node, "image", C.upload_image(path))
    for spec in args.set or []:
        key, val = spec.split("=", 1)
        node, name = key.split(".", 1)
        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            pass
        set_input(api, node, name, val)
    if args.print_api:
        print(json.dumps(api, indent=1))
        return 0
    entry = run_job(api, timeout=args.timeout)
    save_outputs(args, entry, {"model": f"template {args.template}", "licence": "CHECK: see knowledge base",
                               "settings": args.set, "loaded": args.load}, started)
    return 0 if busy_ok else 0


def preflight_generic(args):
    _ensure_running_or_down()
    return True


def main():
    argv = sys.argv[1:]
    if argv and argv[0].startswith("--") and argv[0] not in ("-h", "--help"):
        argv = (["check"] if argv[0] == "--check" else ["image"]) + [a for a in argv if a != "--check"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, needs_out=True):
        if needs_out:
            p.add_argument("--out", required=True, help="directory to save into")
            p.add_argument("--name", required=True, help="file name stem")
        p.add_argument("--seed", type=int)
        p.add_argument("--timeout", type=int, default=900)
        p.add_argument("--lock-wait", type=int, default=900,
                       help="seconds to wait for the machine lock's GPU slot (capped at 540)")
        return p

    sub.add_parser("check")
    sub.add_parser("free")
    sub.add_parser("stop", help="stop ComfyUI if this tool started it (no-op otherwise); a "
                   "session's: as `down`")
    p = sub.add_parser("up", help="start a ComfyUI session for an art run (headless, booked in the "
                       "machine lock); `down` ends it")
    p.add_argument("--for", dest="ttl", type=int, default=SESSION_SECONDS,
                   help="seconds the session may live at most (default %(default)s)")
    p.add_argument("--lock-wait", type=int, default=540,
                   help="seconds to wait for the local-ai slot (capped at 540)")
    p.add_argument("--ready", type=int, default=READY_SECONDS,
                   help="seconds ComfyUI gets to answer once started (default %(default)s)")
    sub.add_parser("down", help="end the session `up` started")
    p = sub.add_parser("serve")     # internal: the session's keeper, the command of up's lock run
    p.add_argument("--for", dest="ttl", type=int, default=SESSION_SECONDS)
    p.add_argument("--ready", type=int, default=READY_SECONDS)
    p = sub.add_parser("release")   # internal: the local-ai run's --on-release (cmd_release)
    p.add_argument("--outer", default="")
    p = common(sub.add_parser("image"))
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", choices=["zimage", "krea2"], default="zimage")
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--count", type=int, default=1, choices=range(1, 9))
    p.add_argument("--steps", type=int)
    p = common(sub.add_parser("edit"))
    p.add_argument("--image", required=True)
    p.add_argument("--image2")
    p.add_argument("--prompt", required=True)
    p = common(sub.add_parser("rembg"))
    p.add_argument("--image", required=True)
    p = common(sub.add_parser("upscale"))
    p.add_argument("--image", required=True)
    p = common(sub.add_parser("to3d"))
    p.add_argument("--image", required=True)
    p.add_argument("--engine", choices=["trellis2", "pixal3d", "hunyuan3d"], default="trellis2")
    p.add_argument("--faces", type=int, default=50000,
                   help="target face count before UV unwrap (template default 700000; 50000 is plenty to clean up)")
    p.add_argument("--texture", type=int, default=2048, choices=[512, 1024, 2048, 4096],
                   help="baked texture size for trellis2/pixal3d (4096 makes ~20 MB GLBs)")
    p = common(sub.add_parser("music"))
    p.add_argument("--tags", required=True, help="style, instruments, mood, BPM words")
    p.add_argument("--lyrics", default="[Instrumental]")
    p.add_argument("--seconds", type=float, default=60)
    p.add_argument("--bpm", type=int, default=120)
    p = common(sub.add_parser("template"))
    p.add_argument("--template", required=True)
    p.add_argument("--set", action="append", help="NODE.INPUT=JSON-or-text")
    p.add_argument("--load", action="append", help="NODE=local image path for a LoadImage node")
    p.add_argument("--print-api", action="store_true", help="print the converted prompt and stop")

    args = ap.parse_args(argv)
    # a command run while a session (`up`) is live uses its ComfyUI and books nothing: the session's
    # lock run already holds the local-ai slot and the port
    if (args.cmd not in UNLOCKED and not getattr(args, "print_api", False)
            and not _session_live(read_state())):
        machine_lock_rerun(args, argv)
    return run_handler(args)


# the kit's machine lock entry point: <plugin root>/tools/machine_lock.py, or KIT_MACHINE_LOCK to override
MACHINE_LOCK_TOOL = os.environ.get("KIT_MACHINE_LOCK") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "machine_lock.py")


RERUN_ENV = "COMFY_GEN_LOCKED"     # set only for the command this function re-runs
UNLOCKED = ("check", "free", "stop", "release", "up", "down", "serve")   # never re-run in a slot
# (`up` books its own detached run, whose command is `serve`)


def release_cmdline():
    """The --on-release command: `release`, told which run (if any) encloses this one, so a run
    nested in it (holding no slot of its own) leaves ComfyUI to that run."""
    return subprocess.list2cmdline([sys.executable, "-B", os.path.abspath(__file__), "release",
                                    "--outer=" + os.environ.get("MACHINE_LOCK_RUN", "")])


def machine_lock_rerun(args, argv):
    """Re-run this command under the machine-wide lock (kind local-ai), then exit with its code.
    Returns without re-running only in that re-run itself (RERUN_ENV, consumed here so nothing it
    starts inherits it). Inside another locked run (MACHINE_LOCK_RUN) it still goes through the
    tool: that runs it directly when the enclosing run holds gpu and takes gpu when it holds only
    cpu. The run also holds comfy-port:<port>: local-ai runs share the GPU, the port they cannot.
    With no tool it refuses
    (exit 2) rather than run on a GPU slot nobody holds. The run's --on-release is `release`: the
    ComfyUI it used is freed when the slot ends (one this command started has already ended with
    it: _start_server)."""
    global _IN_LOCK_RUN
    if os.environ.pop(RERUN_ENV, None) and os.environ.get("MACHINE_LOCK_RUN"):
        _IN_LOCK_RUN = True     # this command holds its own slot: it may start ComfyUI (_launch)
        return
    if not os.path.isfile(MACHINE_LOCK_TOOL):
        print(f"[machine-lock] refused: no machine lock tool at {MACHINE_LOCK_TOOL}, and generation "
              "takes a local-ai slot. Install the kit's tools/machine_lock.py or set "
              "KIT_MACHINE_LOCK to it. Exit 2.", file=sys.stderr)
        sys.exit(2)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", getattr(args, "name", None) or args.cmd).strip("-")[:48] or args.cmd
    wait = max(1, min(int(getattr(args, "lock_wait", 540)), 540))
    cmd = [sys.executable, "-B", MACHINE_LOCK_TOOL, "run", "--repo", "comfy", "--slug", stem,
           "--kind", "local-ai", "--resource", f"comfy-port:{_host_port()[1]}",
           "--max-wait", f"{wait}s", "--on-release", release_cmdline(), "--",
           sys.executable, os.path.abspath(__file__)] + list(argv)
    sys.exit(subprocess.call(cmd, env=dict(os.environ, **{RERUN_ENV: "1"})))


def cmd_release(args):
    """The local-ai run's --on-release: its slot is ending, so free ComfyUI now. A ComfyUI this tool
    started is stopped (with its children, ctime-checked); a local ComfyUI is never stopped, only
    its models unloaded, and only when its queue is idle (as `free`). Skipped when the run was
    nested in an enclosing run (MACHINE_LOCK_RUN still the enclosing one's): that run still holds
    the slot, and when a session (`up`) owns ComfyUI: its keeper ends it. Always exits 0; what it
    did is printed."""
    state = read_state()
    if state and state.get("rid"):
        print(f"[comfy_gen] release: ComfyUI belongs to session {state['rid']}; left as is")
        return 0
    if os.environ.get("MACHINE_LOCK_RUN", "") == (args.outer or ""):
        print("[comfy_gen] release: nested in an enclosing run that still holds the GPU; "
              "ComfyUI left as is")
        return 0
    if _tool_started_and_alive(state):
        print("[comfy_gen] release: the local-ai slot ended; stopping the ComfyUI this tool started")
        _stop_now(manual=False)
    elif _reachable():
        print("[comfy_gen] release: the local-ai slot ended; freeing the models of the running "
              "ComfyUI (not started by this tool, so it stays up)")
        try:
            cmd_free(args)
        except Exception as e:      # an on-release step reports, it never fails the run
            print(f"[comfy_gen] release: free failed: {e}")
    else:
        print("[comfy_gen] release: no ComfyUI running; nothing to free")
    return 0


def run_handler(args):
    global RUN_FOLDER
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", getattr(args, "name", None) or args.cmd).strip("-")[:48] or args.cmd
    RUN_FOLDER = f"agent/{datetime.datetime.now():%Y%m%d-%H%M%S}_{args.cmd}_{stem}"
    handler = {"check": cmd_check, "free": cmd_free, "stop": cmd_stop, "release": cmd_release,
               "up": cmd_up, "down": cmd_down, "serve": cmd_serve,
               "image": cmd_image, "edit": cmd_edit,
               "rembg": cmd_rembg, "upscale": cmd_upscale, "to3d": cmd_to3d, "music": cmd_music,
               "template": cmd_template}[args.cmd]
    del SAVED[:]
    try:
        code = handler(args) or 0
    except GeneratorDown as e:
        print("\n".join(outage_lines(e, SAVED)), file=sys.stderr)
        code = EXIT_GENERATOR_DOWN
    except urllib.error.HTTPError as e:     # a URLError, but ComfyUI answered: the job failed
        print(f"ComfyUI at {C.HOST} answered HTTP {e.code}: {e.reason}", file=sys.stderr)
        code = 1
    except CONNECTION_ERRORS as e:
        if is_outage(e):
            print("\n".join(outage_lines(f"lost mid-run ({e})", SAVED)), file=sys.stderr)
            code = EXIT_GENERATOR_DOWN
        else:
            print(f"ComfyUI at {C.HOST}: {e}", file=sys.stderr)
            code = 1
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        code = 1
    finally:
        # Refresh the idle clock the moment the job ends (a no-op if ComfyUI is not ours): a
        # session's keeper counts its idle minutes from here. A ComfyUI this command started ends
        # with it (its job), so its record goes too.
        if _JOBS:
            for job in _JOBS:
                job.terminate()
            clear_state()
        elif args.cmd not in ("serve", "down", "up"):
            touch_last_used()
    sys.exit(code)


if __name__ == "__main__":
    main()
