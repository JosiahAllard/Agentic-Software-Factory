"""The headless ComfyUI start in comfy_gen.py: no Comfy Desktop, the install read from Comfy
Desktop's installations.json, the ready wait, the port cases, and the `up` / `serve` / `down`
session a run books through the machine lock. Fakes, except RealKill: one real process tree on
Windows (the keeper killed, its server must die with it).

usage: python -m unittest test_comfy_session -v
"""
import datetime
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, '..', '..', '..', 'optional', 'comfy')
sys.path.insert(0, CODE)
import comfy_gen as G  # noqa: E402

RID = "r-20261104T120000Z-abc123"


def ago(seconds):
    return G._iso(G._now() - datetime.timedelta(seconds=seconds))


class Tmp(unittest.TestCase):
    """A state folder and a Comfy Desktop config folder of the test's own."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.appdata = os.path.join(self.tmp, "Comfy Desktop")
        os.makedirs(self.appdata)
        for p in (mock.patch.multiple(G, STATE_DIR=self.tmp, APPDATA_COMFY=self.appdata,
                                      STATE_FILE=os.path.join(self.tmp, "state.json")),
                  mock.patch.object(G, "_agent_output_dir", return_value=None),
                  mock.patch.object(G.C, "HOST", "http://127.0.0.1:8188")):
            p.start()
            self.addCleanup(p.stop)

    def install(self, installs, last=None):
        """installations.json, last-session.json, and the venv python and main.py of each local
        install."""
        for inst in installs:
            if inst.get("installPath"):
                for rel in (("ComfyUI", ".venv", "Scripts", "python.exe"), ("ComfyUI", "main.py")):
                    path = os.path.join(inst["installPath"], *rel)
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    open(path, "w").close()
        with open(os.path.join(self.appdata, "installations.json"), "w") as f:
            json.dump(installs, f)
        if last is not None:
            with open(os.path.join(self.appdata, "last-session.json"), "w") as f:
                json.dump({"installationId": last}, f)

    def local(self, name, launch_args=""):
        return {"id": name, "installPath": os.path.join(self.tmp, name), "launchArgs": launch_args}

    def session(self, **kw):
        state = {"rid": RID, "keeper_pid": 11, "keeper_ctime": 1, "pid": 22, "ctime": 2,
                 "ready": True, "last_used": ago(0), "until": G._iso(G._now() + datetime.timedelta(hours=1))}
        state.update(kw)
        G.write_state(state)
        return state


def alive(pids):
    """A W.details fake: the pids in `pids` ({pid: ctime}) are alive, every other one is gone."""
    def details(pid):
        return {"ctime": pids.get(pid), "image": "C:\\x\\%d.exe" % pid}
    return details


class NoDesktop(Tmp):
    """Agents never start Comfy Desktop and never ask the owner to open it."""

    def test_no_desktop_start_is_left(self):
        for name in ("_desktop_launch_command", "desktop_exe", "DESKTOP_EXE", "DESKTOP_EXE_DEFAULT"):
            self.assertFalse(hasattr(G, name), name)
        with open(G.__file__, encoding="utf-8") as f:
            self.assertNotIn("Comfy Desktop.exe", f.read())

    def test_no_local_install_refuses_and_starts_nothing(self):
        self.install([{"id": "cloud"}], last="cloud")
        with mock.patch.object(G, "_start_server") as start:
            with self.assertRaises(RuntimeError) as e:
                G._launch(5)
        start.assert_not_called()
        self.assertIn("installations.json", str(e.exception))
        self.assertNotIn("open Comfy Desktop", str(e.exception))


class InstallChoice(Tmp):
    def test_last_session_naming_the_cloud_instance_still_finds_the_local_install(self):
        self.install([{"id": "cloud"}, self.local("inst-a", '--enable-manager --temp-directory "C:\\T"')],
                     last="cloud")
        cmd, cwd = G._direct_launch_command()
        self.assertIsNotNone(cmd, "no command for the local install")
        self.assertEqual(cmd[0], os.path.join(self.tmp, "inst-a", "ComfyUI", ".venv", "Scripts", "python.exe"))
        self.assertEqual(cwd, os.path.join(self.tmp, "inst-a"))
        self.assertIn("--enable-manager", cmd)
        self.assertEqual(cmd[cmd.index("--temp-directory") + 1], "C:\\T")
        self.assertEqual(cmd[-5:], ["--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch"])

    def test_a_local_install_last_session_names_wins(self):
        self.install([self.local("inst-a"), self.local("inst-b")], last="inst-b")
        cmd, _ = G._direct_launch_command()
        self.assertIn("inst-b", cmd[0])

    def test_no_last_session_takes_the_first_local_install(self):
        self.install([{"id": "cloud"}, self.local("inst-a")])
        cmd, _ = G._direct_launch_command()
        self.assertIsNotNone(cmd, "no command for the local install")
        self.assertIn("inst-a", cmd[0])

    def test_only_a_cloud_instance_gives_none(self):
        self.install([{"id": "cloud"}], last="cloud")
        self.assertEqual(G._direct_launch_command(), (None, None))

    def test_the_port_follows_comfy_url(self):
        self.install([self.local("inst-a")], last="inst-a")
        with mock.patch.object(G.C, "HOST", "http://127.0.0.1:8190"):
            cmd, _ = G._direct_launch_command()
        self.assertEqual(cmd[cmd.index("--port") + 1], "8190")


class FakeProc:
    def __init__(self, pid=500, codes=(None,)):
        self.pid, self.codes = pid, list(codes)

    def poll(self):
        return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]


class ReadyWait(Tmp):
    def wait(self, proc, listening=(), reachable=False, contains="out", ready_s=300, clock=None):
        job = mock.Mock()
        job.contains.return_value = contains
        log = os.path.join(self.tmp, "comfy.log")
        with open(log, "w") as f:
            f.write("Starting server\nTraceback (most recent call last):\nModuleNotFoundError: No module named 'foo'\n")
        with mock.patch.object(G, "_listening", return_value=set(listening)), \
                mock.patch.object(G, "_reachable", return_value=reachable), \
                mock.patch.object(G.W, "details", side_effect=alive({4242: 9})), \
                mock.patch("comfy_gen.time.sleep") as sleep, \
                mock.patch("comfy_gen.time.monotonic", side_effect=clock or (lambda: 0.0)):
            why = G._wait_ready(proc, job, ready_s, log)
        return why, sleep

    def test_a_cold_start_gets_at_least_five_minutes(self):
        self.assertGreaterEqual(G.READY_SECONDS, 300)
        self.assertGreaterEqual(inspect.signature(G.ensure_running).parameters["wait"].default, 300)

    def test_a_server_that_exits_early_ends_the_wait_with_its_log_tail(self):
        why, sleep = self.wait(FakeProc(codes=[1]))
        self.assertIn("code 1", why)
        self.assertIn("ModuleNotFoundError: No module named 'foo'", why)
        sleep.assert_not_called()

    def test_answering_with_its_own_listener_is_ready(self):
        why, _ = self.wait(FakeProc(pid=500), listening={500}, reachable=True)
        self.assertIsNone(why)

    def test_a_listener_in_its_job_is_its_own(self):
        why, _ = self.wait(FakeProc(pid=500), listening={501}, reachable=True, contains="in")
        self.assertIsNone(why)

    def test_a_foreign_listener_during_the_wait_refuses_naming_it(self):
        why, _ = self.wait(FakeProc(pid=500), listening={4242}, reachable=True)
        self.assertIn("4242", why)
        self.assertIn("4242.exe", why)

    def test_never_answering_times_out_with_the_log(self):
        ticks = iter(range(0, 10000, 100))
        why, _ = self.wait(FakeProc(), ready_s=300, clock=lambda: float(next(ticks)))
        self.assertIn("within 300s", why)
        self.assertIn("ModuleNotFoundError", why)


class PortCases(Tmp):
    """ensure_running before it starts anything: what is already on the port decides."""

    def ensure(self, reachable, listening=(), desktop=False, comes_up=True, details=None):
        with mock.patch.object(G, "_reachable", return_value=reachable), \
                mock.patch.object(G, "_listening", return_value=set(listening)), \
                mock.patch.object(G, "_desktop_child", return_value=desktop), \
                mock.patch.object(G, "_wait_for_stats", return_value=comes_up) as waited, \
                mock.patch.object(G.W, "details", side_effect=alive(details or {})), \
                mock.patch.object(G, "_launch") as launch, \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            try:
                return G.ensure_running(wait=300), launch, waited, None
            except RuntimeError as e:
                return None, launch, waited, e

    def test_an_answering_comfyui_not_ours_is_used_and_nothing_starts(self):
        owned, launch, _, err = self.ensure(True)
        self.assertIs(owned, False)
        self.assertIsNone(err)
        launch.assert_not_called()

    def test_a_listener_that_never_answers_and_is_not_desktop_refuses_naming_it(self):
        owned, launch, _, err = self.ensure(False, listening={777}, details={777: 5})
        self.assertIn("777", str(err))
        self.assertIn("777.exe", str(err))
        launch.assert_not_called()

    def test_comfy_desktop_still_starting_is_waited_for_and_used(self):
        owned, launch, waited, err = self.ensure(False, listening={555}, desktop=True)
        self.assertIsNone(err)
        self.assertIs(owned, False)
        waited.assert_called_once_with(300)
        launch.assert_not_called()

    def test_a_free_port_starts_it(self):
        owned, launch, _, err = self.ensure(False)
        self.assertIsNone(err)
        self.assertIs(owned, True)
        launch.assert_called_once_with(300)

    def test_desktop_child_reads_comfy_desktops_process_file(self):
        procs = os.path.join(self.appdata, "comfy-procs")
        os.makedirs(procs)
        with open(os.path.join(procs, "inst-a.json"), "w") as f:
            json.dump({"port": 8188, "childPid": 15056, "tree": [{"pid": 34952}]}, f)
        with open(os.path.join(procs, "inst-b.json"), "w") as f:
            json.dump({"port": 8188, "childPid": 2000, "tree": [], "stopRequestedAt": 1}, f)
        self.assertTrue(G._desktop_child(15056, 8188))
        self.assertTrue(G._desktop_child(34952, 8188))
        self.assertFalse(G._desktop_child(15056, 8190))
        self.assertFalse(G._desktop_child(2000, 8188))     # Desktop is stopping that one
        self.assertFalse(G._desktop_child(1, 8188))


class UpBooking(Tmp):
    def up(self, run_out="%s pid 4321\n" % RID, reachable=False, details=None, **kw):
        args = G.argparse.Namespace(ttl=kw.get("ttl", 1200), lock_wait=60, ready=300)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            tool = f.name
        self.addCleanup(os.remove, tool)
        done = subprocess.CompletedProcess([], 0, stdout=run_out, stderr="")
        out = io.StringIO()
        with mock.patch.object(G, "MACHINE_LOCK_TOOL", tool), \
                mock.patch.object(G, "_reachable", return_value=reachable), \
                mock.patch.object(G, "_direct_launch_command", return_value=(["py", "main.py"], "C:\\x")), \
                mock.patch.object(G.subprocess, "run", return_value=done) as run, \
                mock.patch.object(G.W, "details", side_effect=alive({4321: 7} if details is None else details)), \
                mock.patch("comfy_gen.time.sleep", side_effect=kw.get("sleep")), \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            rc = G.cmd_up(args)
        return rc, run, out.getvalue()

    def test_up_books_local_ai_detached_with_the_port_resource(self):
        self.session(keeper_pid=11, keeper_ctime=1)        # the keeper has written: ready
        with mock.patch.object(G, "_session_live", side_effect=[False, True, True, True]):
            rc, run, out = self.up()
        self.assertEqual(rc, 0, out)
        cmd = run.call_args[0][0]
        lock = cmd[:cmd.index("--")]
        self.assertIn("--detach", lock)
        self.assertEqual(lock[lock.index("--kind") + 1], "local-ai")
        self.assertEqual(lock[lock.index("--resource") + 1], "comfy-port:8188")
        keeper = cmd[cmd.index("--") + 1:]
        self.assertIn("serve", keeper)
        self.assertEqual(keeper[keeper.index("--for") + 1], "1200")
        self.assertIn("creationflags", run.call_args[1])
        self.assertIn(RID, out)

    def test_a_failed_start_is_reported_with_why(self):
        G.write_state({"rid": RID, "failed": "ComfyUI exited with code 1 before it answered"})
        rc, _, out = self.up()
        self.assertEqual(rc, 2)
        self.assertIn("code 1", out)

    def test_a_run_that_ends_before_the_keeper_is_ready_fails(self):
        rc, _, out = self.up(details={})          # the run's wrapper is gone, no state written
        self.assertEqual(rc, 2)
        self.assertIn(RID, out)

    def test_comfyui_already_answering_books_nothing(self):
        rc, run, out = self.up(reachable=True)
        self.assertEqual(rc, 0)
        run.assert_not_called()

    def test_a_live_session_is_reused(self):
        self.session()
        with mock.patch.object(G, "_session_live", return_value=True):
            rc, run, out = self.up()
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertIn(RID, out)


class SessionSkipsBooking(Tmp):
    """With a session up, a generating command is only an HTTP client of it: the session holds the
    local-ai slot, so the command books none of its own."""

    def main(self, live):
        argv = ["comfy_gen.py", "image", "--out", self.tmp, "--name", "cat", "--prompt", "x"]
        with mock.patch.object(G.sys, "argv", argv), \
                mock.patch.object(G, "_session_live", return_value=live), \
                mock.patch.object(G, "machine_lock_rerun") as rerun, \
                mock.patch.object(G, "run_handler", return_value=0) as handler:
            G.main()
        return rerun, handler

    def test_a_live_session_books_nothing(self):
        rerun, handler = self.main(True)
        rerun.assert_not_called()
        handler.assert_called_once()

    def test_no_session_books_the_slot(self):
        rerun, _ = self.main(False)
        rerun.assert_called_once()

    def test_the_per_command_booking_names_the_port_resource(self):
        args = G.argparse.Namespace(cmd="image", name="cat", lock_wait=60)
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            tool = f.name
        self.addCleanup(os.remove, tool)
        clean = {k: v for k, v in os.environ.items() if k not in ("MACHINE_LOCK_RUN", G.RERUN_ENV)}
        with mock.patch.dict(os.environ, clean, clear=True), \
                mock.patch.object(G, "MACHINE_LOCK_TOOL", tool), \
                mock.patch.object(G.subprocess, "call", return_value=0) as call, \
                mock.patch.object(G.sys, "exit"):
            G.machine_lock_rerun(args, ["image", "--name", "cat"])
        cmd = call.call_args[0][0]
        self.assertIn("--resource", cmd)
        self.assertEqual(cmd[cmd.index("--resource") + 1], "comfy-port:8188")

    def test_release_leaves_a_sessions_comfyui(self):
        state = self.session()
        with mock.patch.object(G, "_tool_started_and_alive", return_value=True), \
                mock.patch.object(G, "_stop_now") as stop, \
                mock.patch.dict(os.environ, {"MACHINE_LOCK_RUN": "r-x:1:2"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(G.cmd_release(G.argparse.Namespace(outer="")), 0)
        stop.assert_not_called()
        self.assertIn(state["rid"], out.getvalue())

    def test_stop_with_a_session_ends_it_through_down(self):
        self.session()
        with mock.patch.object(G, "cmd_down", return_value=0) as down, \
                mock.patch.object(G, "_stop_now") as stop:
            G.cmd_stop(G.argparse.Namespace())
        down.assert_called_once()
        stop.assert_not_called()

    def test_a_starting_session_is_waited_for_never_started_twice(self):
        self.session(ready=False)
        with mock.patch.object(G, "_reachable", return_value=False), \
                mock.patch.object(G, "_session_live", return_value=True), \
                mock.patch.object(G, "_wait_for_stats", return_value=True), \
                mock.patch.object(G, "_launch") as launch:
            self.assertTrue(G.ensure_running(wait=300))
        launch.assert_not_called()


class Keeper(Tmp):
    """serve's loop (_keep): it ends its server on a crash (non-zero), on `down`, at its --for end,
    and once idle with an empty queue."""

    def keep(self, proc, queue_busy=False, sleep=None, **state):
        self.session(keeper_pid=os.getpid(), **state)
        with mock.patch("comfy_gen.time.sleep", side_effect=sleep), \
                mock.patch.object(G, "_queue_busy", return_value=queue_busy), \
                mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            return G._keep(proc)

    def test_a_crash_ends_it_non_zero_and_records_why(self):
        self.assertNotEqual(self.keep(FakeProc(codes=[9])), 0)
        self.assertIn("9", G.read_state()["failed"])

    def test_down_ends_it(self):
        self.assertEqual(self.keep(FakeProc(), down=True), 0)

    def test_its_time_limit_ends_it(self):
        self.assertEqual(self.keep(FakeProc(), until=ago(1)), 0)

    def test_idle_with_an_empty_queue_ends_it(self):
        self.assertEqual(self.keep(FakeProc(), last_used=ago(G.IDLE_STOP_SECONDS + 5)), 0)

    def test_idle_but_busy_stays_up(self):
        n = {"loops": 0}

        def sleep(_):
            n["loops"] += 1
            if n["loops"] == 3:
                st = G.read_state()
                st["down"] = True
                G.write_state(st)

        self.assertEqual(self.keep(FakeProc(), queue_busy=True, sleep=sleep,
                                   last_used=ago(G.IDLE_STOP_SECONDS + 5)), 0)
        self.assertEqual(n["loops"], 3)

    def test_a_state_that_names_another_keeper_ends_it(self):
        self.session(keeper_pid=os.getpid())
        with mock.patch("comfy_gen.time.sleep", side_effect=lambda _: self.session(keeper_pid=1)), \
                mock.patch.object(G, "_queue_busy", return_value=False):
            self.assertEqual(G._keep(FakeProc()), 0)


class Serve(Tmp):
    def serve(self, why=None, keep=0, reachable=False):
        job, proc = mock.Mock(), FakeProc(pid=500)
        env = {"MACHINE_LOCK_RUN": RID + ":1:2"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(G, "_reachable", return_value=reachable), \
                mock.patch.object(G, "_listening", return_value=set()), \
                mock.patch.object(G, "_direct_launch_command", return_value=(["py"], "C:\\x")), \
                mock.patch.object(G, "_start_server", return_value=(job, proc)) as start, \
                mock.patch.object(G, "_wait_ready", return_value=why), \
                mock.patch.object(G, "_keep", return_value=keep) as kept, \
                mock.patch.object(G.W, "details", side_effect=alive({500: 3, os.getpid(): 4})), \
                mock.patch("sys.stdout", new_callable=io.StringIO), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            rc = G.cmd_serve(G.argparse.Namespace(ttl=600, ready=300))
        return rc, job, start, kept

    def test_ready_then_kept_and_the_job_ended_after(self):
        rc, job, _, kept = self.serve()
        self.assertEqual(rc, 0)
        kept.assert_called_once()
        job.terminate.assert_called()

    def test_a_failed_start_records_why_for_up_and_ends_the_job(self):
        rc, job, _, kept = self.serve(why="ComfyUI exited with code 1 before it answered")
        self.assertEqual(rc, 2)
        kept.assert_not_called()
        job.terminate.assert_called()
        state = G.read_state()
        self.assertEqual(state["rid"], RID)
        self.assertIn("code 1", state["failed"])

    def test_comfyui_already_answering_starts_nothing(self):
        rc, _, start, _ = self.serve(reachable=True)
        self.assertNotEqual(rc, 0)
        start.assert_not_called()
        self.assertIn("already", G.read_state()["failed"])

    def test_another_commands_comfyui_keeps_its_record(self):
        mine = {"pid": 500, "ctime": 3, "source": "direct", "last_used": ago(0)}
        G.write_state(mine)
        rc, _, start, _ = self.serve(reachable=True)
        self.assertNotEqual(rc, 0)
        start.assert_not_called()
        self.assertEqual(G.read_state(), mine)


class Down(Tmp):
    def down(self, details, rc=0):
        done = subprocess.CompletedProcess([], rc, stdout="run %s stopped" % RID, stderr="")
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            tool = f.name
        self.addCleanup(os.remove, tool)
        ticks = iter(range(0, 100000, 5))
        with mock.patch.object(G, "MACHINE_LOCK_TOOL", tool), \
                mock.patch.object(G.W, "details", side_effect=details), \
                mock.patch.object(G.subprocess, "run", return_value=done) as run, \
                mock.patch("comfy_gen.time.sleep"), \
                mock.patch("comfy_gen.time.monotonic", side_effect=lambda: float(next(ticks))), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = G.cmd_down(G.argparse.Namespace())
        return code, run, out.getvalue()

    def test_no_session_is_a_no_op(self):
        code, run, _ = self.down(alive({}))
        self.assertEqual(code, 0)
        run.assert_not_called()

    def test_the_keeper_ending_on_the_flag_needs_no_lock_stop(self):
        self.session()
        seen = {"n": 0}

        def details(pid):
            seen["n"] += 1
            return {"ctime": 1 if seen["n"] <= 1 else None, "image": None}

        code, run, _ = self.down(details)
        self.assertEqual(code, 0)
        run.assert_not_called()
        self.assertIsNone(G.read_state())

    def test_a_keeper_that_stays_is_stopped_through_the_lock(self):
        self.session()
        code, run, out = self.down(alive({11: 1}))
        self.assertEqual(code, 0)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[-2:], ["stop", RID])
        self.assertIn("creationflags", run.call_args[1])


class NoSlotNoStart(Tmp):
    """Only a command that holds its own local-ai slot (machine_lock_rerun returned in the re-run)
    may start ComfyUI: one that joined a session which has since ended refuses instead."""

    def launch(self, in_lock_run, why=None):
        self.install([self.local("inst-a")], last="inst-a")
        job, proc = mock.Mock(), FakeProc(pid=500)
        writes = []
        real_write = G.write_state
        with mock.patch.object(G, "_IN_LOCK_RUN", in_lock_run, create=True), \
                mock.patch.object(G, "_JOBS", []), \
                mock.patch.object(G, "_start_server", return_value=(job, proc)) as start, \
                mock.patch.object(G, "_wait_ready", return_value=why), \
                mock.patch.object(G, "write_state", side_effect=lambda s: (writes.append(dict(s)), real_write(s))), \
                mock.patch.object(G.W, "details", side_effect=alive({500: 3})), \
                mock.patch("sys.stderr", new_callable=io.StringIO):
            try:
                G._launch(5)
                err = None
            except RuntimeError as e:
                err = str(e)
        return err, start, job, writes

    def test_a_command_holding_no_slot_never_starts_comfyui(self):
        err, start, _, _ = self.launch(False)
        start.assert_not_called()
        self.assertIsNotNone(err, "_launch started ComfyUI with no slot booked")
        self.assertIn("slot", err)

    def test_a_command_in_its_own_lock_run_starts_it(self):
        err, start, _, _ = self.launch(True)
        self.assertIsNone(err)
        start.assert_called_once()

    def test_a_failed_launch_ends_its_server_and_clears_the_state(self):
        err, _, job, writes = self.launch(True, why="ComfyUI exited with code 1 before it answered")
        self.assertIn("code 1", err or "")
        self.assertTrue(any(w.get("pid") == 500 for w in writes), writes)
        job.terminate.assert_called_once()
        self.assertIsNone(G.read_state())

    def test_rerun_marks_the_command_as_holding_a_slot(self):
        args = G.argparse.Namespace(cmd="image", name="cat", lock_wait=60)
        with mock.patch.dict(os.environ, {G.RERUN_ENV: "1", "MACHINE_LOCK_RUN": "r-x:1:2"}), \
                mock.patch.object(G, "_IN_LOCK_RUN", False, create=True):
            G.machine_lock_rerun(args, ["image"])
            self.assertTrue(G._IN_LOCK_RUN)


class SessionEnds(Tmp):
    """A session asked to end, or failed, is not live; the down request survives any later write
    of the state; a wait on a session stops once it has failed."""

    def test_a_failed_or_down_session_is_not_live(self):
        with mock.patch.object(G.W, "details", side_effect=alive({11: 1})):
            self.assertTrue(G._session_live(self.session()))
            self.assertFalse(G._session_live(self.session(failed="ComfyUI exited with code 1")))
            state = self.session()
            G._request_down(RID)
            self.assertFalse(G._session_live(state))

    def test_a_state_write_after_down_keeps_the_request(self):
        state = self.session(keeper_pid=os.getpid())
        G._request_down(RID)
        state.pop("down", None)
        G.write_state(dict(state, last_used=ago(0)))     # a command's touch_last_used, say
        G.touch_last_used()
        with mock.patch("comfy_gen.time.sleep"), \
                mock.patch.object(G, "_queue_busy", return_value=True), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(G._keep(FakeProc()), 0)
        self.assertIn("down", out.getvalue())

    def test_a_session_failing_while_waited_on_ends_the_wait(self):
        self.session(ready=False)
        sleeps = []

        def sleep(_):
            sleeps.append(1)
            if len(sleeps) == 1:
                st = G.read_state()
                st["failed"] = "ComfyUI exited with code 7 before it answered"
                G.write_state(st)

        clock = iter(range(0, 100000, 2))
        with mock.patch.object(G, "_reachable", return_value=False), \
                mock.patch.object(G.W, "details", side_effect=alive({11: 1})), \
                mock.patch("comfy_gen.time.sleep", side_effect=sleep), \
                mock.patch("comfy_gen.time.time", side_effect=lambda: float(next(clock))), \
                mock.patch.object(G, "_launch") as launch:
            with self.assertRaises(RuntimeError) as e:
                G.ensure_running(wait=300)
        launch.assert_not_called()
        self.assertLessEqual(len(sleeps), 2, "it sat out the wait after the session failed")
        self.assertIn("code 7", str(e.exception))


class TruthfulAndBounded(Tmp):
    def test_up_names_another_commands_comfyui_as_such(self):
        G.write_state({"pid": 22, "ctime": 2, "source": "direct", "last_used": ago(0)})
        out = io.StringIO()
        with mock.patch.object(G, "_reachable", return_value=True), \
                mock.patch.object(G.W, "details", side_effect=alive({22: 2})), \
                mock.patch.object(G.subprocess, "run") as run, \
                mock.patch("sys.stdout", out), mock.patch("sys.stderr", out):
            rc = G.cmd_up(G.argparse.Namespace(ttl=600, lock_wait=60, ready=300))
        run.assert_not_called()
        self.assertNotIn("did not start it", out.getvalue())
        self.assertIn("another", out.getvalue())
        self.assertNotEqual(rc, 0)

    def test_a_lock_call_that_hangs_is_reported_not_raised(self):
        with mock.patch.object(G.subprocess, "run", side_effect=subprocess.TimeoutExpired(["x"], 120)):
            done = G._run_quiet(["x"])
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("120", done.stderr)

    def test_down_survives_a_hanging_lock_stop(self):
        self.session()
        ticks = iter(range(0, 100000, 5))
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            tool = f.name
        self.addCleanup(os.remove, tool)
        with mock.patch.object(G, "MACHINE_LOCK_TOOL", tool), \
                mock.patch.object(G.W, "details", side_effect=alive({11: 1})), \
                mock.patch.object(G.subprocess, "run", side_effect=subprocess.TimeoutExpired(["x"], 120)), \
                mock.patch("comfy_gen.time.sleep"), \
                mock.patch("comfy_gen.time.monotonic", side_effect=lambda: float(next(ticks))), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = G.cmd_down(G.argparse.Namespace())
        self.assertNotEqual(code, 0)
        self.assertIn(RID, out.getvalue())


@unittest.skipUnless(os.name == "nt", "a job object is Windows'")
class RealKill(unittest.TestCase):
    """The one real process test: a keeper that dies (killed, crashed) takes its ComfyUI with it.
    A sleeping python stands in for ComfyUI."""

    def test_killing_the_keeper_kills_its_server(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        script = os.path.join(tmp, "keeper.py")
        with open(script, "w") as f:
            f.write("import sys, time\nsys.path.insert(0, %r)\nimport comfy_gen as G\n"
                    "job, proc = G._start_server([sys.executable, '-c', 'import time; time.sleep(120)'],"
                    " None, %r)\nprint(proc.pid, flush=True)\ntime.sleep(120)\n"
                    % (os.path.abspath(CODE), os.path.join(tmp, "server.log")))
        keeper = subprocess.Popen([sys.executable, script], stdout=subprocess.PIPE, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(lambda: keeper.poll() is None and keeper.kill())
        line = keeper.stdout.readline().strip()
        self.assertRegex(line, r"^\d+$", "the keeper did not start a server")
        pid = int(line)
        ctime = G.W.details(pid)["ctime"]
        self.assertIsNotNone(ctime)
        self.addCleanup(G.W.terminate_verified, pid, ctime)
        keeper.kill()
        keeper.wait(30)
        keeper.stdout.close()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and G.W.details(pid)["ctime"] == ctime:
            time.sleep(0.2)
        self.assertNotEqual(G.W.details(pid)["ctime"], ctime, "the server outlived its keeper")


if __name__ == "__main__":
    unittest.main()
