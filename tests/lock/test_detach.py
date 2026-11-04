"""kitlib.lock.detach, machine_lock.py run --detach's logic: WMI starts pythonw.exe running the
carrier tools/no_window.py, which starts the console python running machine_lock.py, so the run
has no console window; ShowWindow is not asked for. Unit tests of the argv, the WMI call and the
wrapper lookup on fakes. Through the tool, and a real detach: tests/lock/test_detach_cli.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from .helpers import REPO
from tests.platforms import windows_only

if os.name == "nt":
    from kitlib.lock import detach as dt
    from kitlib.proc import spawn

TOOL = os.path.join(REPO, "tools", "machine_lock.py")


@windows_only("the lock is Windows only")
class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.py = os.path.join(self.tmp, "python.exe")
        self.pyw = os.path.join(self.tmp, "pythonw.exe")
        for path in (self.py, self.pyw):
            open(path, "wb").close()

    def test_pythonw_runs_the_carrier_which_runs_the_console_python(self):
        argv = dt.command(self.py, r"C:\kit\tools\machine_lock.py", ["run", "--kind", "k", "--detach"],
                          "r-1", ["--", "cmd", "x y"])
        self.assertEqual(argv, [self.pyw, r"C:\kit\tools\no_window.py",
                                self.py, "-B", r"C:\kit\tools\machine_lock.py",
                                "run", "--kind", "k", "--detach", "--detached-id", "r-1",
                                "--", "cmd", "x y"])

    def test_from_pythonw_the_console_python_is_still_python_exe(self):
        argv = dt.command(self.pyw, r"C:\kit\tools\machine_lock.py", [], "r-1", ["--"])
        self.assertEqual(argv[:3], [self.pyw, r"C:\kit\tools\no_window.py", self.py])

    def test_the_carrier_is_the_kit_one_by_path(self):
        argv = dt.command(sys.executable, TOOL, [], "r-1", ["--"])
        self.assertEqual(os.path.normcase(argv[1]),
                         os.path.normcase(os.path.join(REPO, "tools", spawn.CARRIER)))
        self.assertEqual(spawn.CARRIER, "no_window.py")
        self.assertTrue(os.path.isfile(argv[1]), argv[1])
        self.assertIsNone(dt.missing(argv))

    def test_no_pythonw_beside_python_is_missing_not_a_console_start(self):
        os.remove(self.pyw)
        argv = dt.command(self.py, TOOL, [], "r-1", ["--"])
        self.assertEqual(dt.missing(argv), "pythonw.exe is not a file: %s" % self.pyw)

    def test_missing_names_the_first_file_that_is_not_there(self):
        tmp = os.path.join(REPO, "no-such-folder")
        argv = dt.command(os.path.join(tmp, "python.exe"), TOOL, [], "r-1", ["--"])
        self.assertIn("pythonw.exe is not a file", dt.missing(argv))
        argv = dt.command(self.py, TOOL, [], "r-1", ["--"])
        argv[1] = os.path.join(tmp, "no_window.py")
        self.assertIn("the carrier is not a file", dt.missing(argv))
        argv = dt.command(self.py, TOOL, [], "r-1", ["--"])
        argv[2] = os.path.join(tmp, "python.exe")
        self.assertIn("python.exe is not a file", dt.missing(argv))


class FakeRun:
    def __init__(self, stdout="0 4242\n", code=0, stderr="", raises=None):
        self.stdout, self.code, self.stderr, self.raises = stdout, code, stderr, raises
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(args, self.code, self.stdout, self.stderr)


@windows_only("the lock is Windows only")
class Create(unittest.TestCase):
    def test_wmi_gets_the_command_line_cwd_and_environment_and_no_show_window(self):
        run = FakeRun()
        argv = [r"C:\Py\pythonw.exe", r"C:\kit\tools\no_window.py", "a b"]
        self.assertEqual(dt.create(argv, r"C:\w", {"A": "1"}, run=run), (4242, None))
        (args, kw), = run.calls
        self.assertEqual(args[0], "powershell")
        self.assertIn("Win32_Process", args[-1])
        self.assertNotIn("ShowWindow", args[-1])
        data = json.loads(kw["input"])
        self.assertEqual(data, {"cmd": subprocess.list2cmdline(argv), "cwd": r"C:\w",
                                "env": ["A=1", "MACHINE_LOCK_DETACHED=1"]})

    def test_the_started_process_is_marked_and_keeps_its_launchers_origin(self):
        run = FakeRun()
        dt.create(["x"], "C:\\", {"MACHINE_LOCK_ORIGIN": "7:70;8:80"}, run=run)
        env = json.loads(run.calls[0][1]["input"])["env"]
        self.assertEqual(sorted(env), ["MACHINE_LOCK_DETACHED=1", "MACHINE_LOCK_ORIGIN=7:70;8:80"])

    def test_a_launcher_inside_a_detached_start_passes_no_origin(self):
        # Its ancestry stops at WMI's provider host: no origin, so tree_role reads an agent.
        run = FakeRun()
        given = {"MACHINE_LOCK_DETACHED": "1", "MACHINE_LOCK_ORIGIN": "7:70"}
        dt.create(["x"], "C:\\", given, run=run)
        env = json.loads(run.calls[0][1]["input"])["env"]
        self.assertEqual(env, ["MACHINE_LOCK_DETACHED=1"])
        self.assertEqual(given, {"MACHINE_LOCK_DETACHED": "1", "MACHINE_LOCK_ORIGIN": "7:70"})

    def test_the_names_are_the_ones_the_lock_reads(self):
        from kitlib.lock import run as lockrun, store
        self.assertEqual((store.DETACHED_ENV, lockrun.ORIGIN_ENV),
                         ("MACHINE_LOCK_DETACHED", "MACHINE_LOCK_ORIGIN"))
        self.assertEqual(store.tree_role([]), "agent")

    def test_failures_are_a_reason_and_no_pid(self):
        for run in (FakeRun(stdout="9 0"), FakeRun(code=1, stderr="boom"), FakeRun(stdout=""),
                    FakeRun(raises=OSError("no powershell")),
                    FakeRun(raises=subprocess.TimeoutExpired("powershell", 60))):
            with self.subTest(stdout=run.stdout, code=run.code, raises=run.raises):
                pid, why = dt.create(["x"], "C:\\", {}, run=run, sleep=lambda s: None)
                self.assertIsNone(pid)
                self.assertTrue(why)


class SeqRun(FakeRun):
    """Answers each WMI start with the next of `outs` (the last one repeats)."""
    def __init__(self, *outs):
        super().__init__()
        self.outs = list(outs)

    def __call__(self, args, **kw):
        self.stdout = self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]
        return super().__call__(args, **kw)


@windows_only("the lock is Windows only")
class CreateRetry(unittest.TestCase):
    """Win32_Process.Create's 8 (unknown failure) starts nothing (no pid): create asks again."""

    def create(self, run):
        slept = []
        return dt.create(["x"], "C:\\", {}, run=run, sleep=slept.append), slept

    def test_a_code_8_then_a_start_gives_the_pid(self):
        run = SeqRun("8 \n", "0 4242\n")
        (pid, why), slept = self.create(run)
        self.assertEqual((pid, why), (4242, None))
        self.assertEqual(len(run.calls), 2)
        self.assertEqual(slept, [dt.RETRY_DELAY_S])
        # the second ask is the same start: same command line, cwd and environment
        self.assertEqual(run.calls[0], run.calls[1])

    def test_code_8_every_time_is_tried_a_bounded_number_of_times_and_named(self):
        run = SeqRun("8 \n")
        (pid, why), slept = self.create(run)
        self.assertIsNone(pid)
        self.assertEqual(len(run.calls), dt.CREATE_TRIES)
        self.assertEqual(dt.CREATE_TRIES, 3)
        self.assertEqual(slept, [dt.RETRY_DELAY_S] * (dt.CREATE_TRIES - 1))
        self.assertTrue(why.startswith("got WMI code 8 (unknown failure) after 3 tries (exit 0)"),
                        why)

    def test_other_codes_are_final_and_named(self):
        for out, meaning in (("2 \n", "access denied"), ("3 \n", "insufficient privilege"),
                             ("9 \n", "path not found"), ("21 \n", "invalid parameter")):
            with self.subTest(out=out):
                run = SeqRun(out, "0 4242\n")
                (pid, why), slept = self.create(run)
                self.assertIsNone(pid)
                self.assertEqual((len(run.calls), slept), (1, []))
                self.assertIn("got WMI code %s (%s)" % (out.strip(), meaning), why)
                self.assertNotIn("tries", why)

    def test_a_code_8_with_a_pid_is_final(self):
        # A pid back means a process may have started: asking again could start a second one.
        for out in ("8 1234\n", "8 1234\r\n"):
            with self.subTest(out=out):
                run = SeqRun(out, "0 4242\n")
                (pid, why), slept = self.create(run)
                self.assertIsNone(pid)
                self.assertEqual((len(run.calls), slept), (1, []))
                self.assertIn("got WMI code 8 (unknown failure) with pid 1234 (exit 0)", why)

    def test_a_code_8_with_pid_0_is_asked_again(self):
        run = SeqRun("8 0\n", "0 4242\n")
        self.assertEqual(self.create(run)[0], (4242, None))
        self.assertEqual(len(run.calls), 2)

    def test_a_failed_powershell_after_code_8_tries_says_so(self):
        calls = []

        def run(args, **kw):
            calls.append(args)
            if len(calls) > 1:
                raise subprocess.TimeoutExpired("powershell", 60)
            return subprocess.CompletedProcess(args, 0, "8 \n", "")
        pid, why = dt.create(["x"], "C:\\", {}, run=run, sleep=lambda s: None)
        self.assertIsNone(pid)
        self.assertEqual(len(calls), 2)
        self.assertTrue(why.startswith("failed after 2 tries: "), why)

    def test_an_undocumented_code_is_named_as_such(self):
        (pid, why), _ = self.create(SeqRun("17 \n"))
        self.assertIsNone(pid)
        self.assertIn("got WMI code 17 (undocumented)", why)

    def test_output_that_is_no_code_is_quoted_and_not_retried(self):
        run = SeqRun("boom\n", "0 4242\n")
        (pid, why), slept = self.create(run)
        self.assertIsNone(pid)
        self.assertEqual((len(run.calls), slept), (1, []))
        self.assertIn("got 'boom' (exit 0)", why)

    def test_a_failed_powershell_is_not_retried(self):
        run = SeqRun("8 \n")
        run.code = 1
        (pid, why), slept = self.create(run)
        self.assertIsNone(pid)
        self.assertEqual((len(run.calls), slept), (1, []))


@windows_only("the lock is Windows only")
class WrapperPid(unittest.TestCase):
    def lookup(self, snaps, ctimes, wait_s=1.0):
        snaps = list(snaps)
        clock = iter(range(1000))
        return dt.wrapper_pid(100, wait_s=wait_s, poll_s=0,
                              snapshot=lambda: snaps.pop(0) if len(snaps) > 1 else snaps[0],
                              ctime=ctimes.get, sleep=lambda s: None,
                              clock=lambda: next(clock) / 10.0)

    def test_the_carriers_python_child(self):
        snaps = [[(100, 4, "pythonw.exe")],
                 [(100, 4, "pythonw.exe"), (7, 100, "conhost.exe"), (200, 100, "python.exe")]]
        self.assertEqual(self.lookup(snaps, {100: 50, 200: 60, 7: 55}), 200)

    def test_a_reused_parent_pid_is_not_the_carriers_child(self):
        snaps = [[(100, 4, "pythonw.exe"), (300, 100, "python.exe")]]
        self.assertIsNone(self.lookup(snaps, {100: 50, 300: 40}))

    def test_none_when_the_carrier_is_gone(self):
        self.assertIsNone(self.lookup([[]], {}))

    def test_none_when_no_child_is_seen_in_time(self):
        self.assertIsNone(self.lookup([[(100, 4, "pythonw.exe")]], {100: 50}, wait_s=0.5))

    def test_a_failed_snapshot_is_none(self):
        def boom():
            raise OSError("no snapshot")
        self.assertIsNone(dt.wrapper_pid(100, snapshot=boom, ctime={100: 1}.get))


if __name__ == "__main__":
    unittest.main()
