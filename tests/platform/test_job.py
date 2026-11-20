"""Tests for kitlib.proc.job: a job pins, measures and ends a tree; it fails closed;
masks past group 0 are set as group affinities (synthetic: one group here). Every
process here is the test's own."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from tests.platforms import windows_only

if os.name == "nt":
    import _winapi
    from kitlib.proc import job
    from kitlib.platform.windows import console as wincon, job as winjob, process, win32

# A parent that dies just after Job.start has made its child, before the resume: it prints the
# child's pid, waits for a line (the test holds the child's handle meanwhile), then exits 7.
# argv[1] "1" is Job(kill_on_close=True), "0" Job(kill_on_close=False).
DIES = "\n".join([
    "import os, subprocess, sys",
    "from kitlib.proc import job",
    "real = subprocess.Popen",
    "def made(*a, **kw):",
    "    p = real(*a, **kw)",
    "    sys.stdout.write('%d\\n' % p.pid)",
    "    sys.stdout.flush()",
    "    sys.stdin.readline()",
    "    os._exit(7)",
    "subprocess.Popen = made",
    "job.Job(kill_on_close=sys.argv[1] == '1').start(",
    "    [sys.executable, '-c', 'import time; time.sleep(60)'])"])

# A child that prints its raw environment block, one string per variable, as JSON.
ENV_BLOCK = "\n".join([
    "import ctypes, json, sys",
    "k = ctypes.WinDLL('kernel32')",
    "k.GetEnvironmentStringsW.restype = ctypes.c_void_p",
    "p, out = k.GetEnvironmentStringsW(), []",
    "while ctypes.wstring_at(p):",
    "    out.append(ctypes.wstring_at(p))",
    "    p += 2 * (len(out[-1]) + 1)",
    "sys.stdout.write(json.dumps(out))"])


def read_pid(proc, timeout=60):
    """The first line `proc` prints, as an int; AssertionError when none comes in `timeout`."""
    import threading
    got = []
    t = threading.Thread(target=lambda: got.append(proc.stdout.readline()), daemon=True)
    t.start()
    t.join(timeout)
    if not got or not got[0].strip():
        raise AssertionError("the parent printed no pid")
    return int(got[0])


def end_own(proc):
    """Ends the test's own child by its Popen (its handle) if it still runs, and reaps it."""
    if proc.poll() is None:
        proc.kill()
    proc.wait(30)
    for f in (proc.stdin, proc.stdout):
        f.close()


def end_by_handle(h):
    """Ends a process the test made, through the handle it holds, and closes the handle."""
    if win32.K32.WaitForSingleObject(h, 0) != win32.WAIT_OBJECT_0:
        win32.K32.TerminateProcess(h, 1)
    process.close(h)

TREE = ("import subprocess,sys,time;"
        "g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']);"
        "open(sys.argv[1],'w').write(str(g.pid)); time.sleep(60)")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Inside an outer job pinned to one thread: an inner job pinned to another thread (outside the
# outer one) and one pinned to the outer thread; each starts a child and says what it read back.
NESTED = "\n".join([
    "import json, sys",
    "from kitlib.proc import job",
    "r = {}",
    "for k, m in (('outside', int(sys.argv[1])), ('inside', int(sys.argv[2]))):",
    "    j = job.Job(m)",
    "    j.start([sys.executable, '-c', 'pass']).wait(30)",
    "    r[k] = [j.pin, j.effective]",
    "    if k == 'outside':",
    "        r['note'] = j.note",
    "    j.end(kill=True)",
    "open(sys.argv[3], 'w').write(json.dumps(r))"])


def wait_file(path, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path):
            with open(path) as f:
                s = f.read().strip()
            if s:
                return int(s)
        time.sleep(0.05)
    raise AssertionError("%s never written" % path)


def affinity(h):
    pm, sm = ctypes.c_size_t(), ctypes.c_size_t()
    if not win32.K32.GetProcessAffinityMask(h, ctypes.byref(pm), ctypes.byref(sm)):
        raise ctypes.WinError(ctypes.get_last_error())
    return pm.value, sm.value


def own_mask():
    """This process's affinity: under the machine lock (the gate runs the suite as a run) it is
    the outer run's threads, and a child of a job nested in that run's job runs on no others."""
    return affinity(win32.K32.GetCurrentProcess())[0]


def first_own_thread():
    m = own_mask()
    return (m & -m).bit_length() - 1


@windows_only("Windows only")
class Plans(unittest.TestCase):
    def test_group_zero_is_a_basic_pin(self):
        self.assertEqual(winjob.affinity_plan(0xF0), ("basic", 0xF0))
        self.assertIsNone(winjob.affinity_plan(None))
        self.assertIsNone(winjob.affinity_plan(0))

    def test_a_mask_past_64_is_set_per_group(self):
        self.assertEqual(winjob.affinity_plan(0b11 | (0b1 << 64)), ("groups", [(0, 0b11), (1, 1)]))
        self.assertEqual(winjob.affinity_plan(0b1 << 70), ("groups", [(1, 1 << 6)]))

    def test_group_affinities_lay_out_as_windows_reads_them(self):
        raw = bytes(winjob.group_affinities([(0, 0b11), (1, 1 << 63)]))
        self.assertEqual(len(raw), 32)                       # GROUP_AFFINITY is 16 bytes
        self.assertEqual(int.from_bytes(raw[0:8], "little"), 0b11)
        self.assertEqual(int.from_bytes(raw[8:10], "little"), 0)
        self.assertEqual(int.from_bytes(raw[16:24], "little"), 1 << 63)
        self.assertEqual(int.from_bytes(raw[24:26], "little"), 1)


@windows_only("Windows only")
class LimitRefused(unittest.TestCase):
    """SetInformationJobObject refused (faked): a Job never comes back without kill-on-close."""

    def test_an_unpinned_job_whose_limit_is_refused_raises(self):
        with mock.patch.object(winjob, "_set_ext", return_value=False):
            with self.assertRaisesRegex(job.JobError, "SetInformationJobObject"):
                job.Job()

    def test_a_job_that_asks_for_no_kill_on_close_accepts_a_refused_limit(self):
        # kitlib.git.cmd makes Job(kill_on_close=False): a refusal leaves it as asked.
        with mock.patch.object(winjob, "_set_ext", return_value=False):
            j = job.Job(kill_on_close=False)
        try:
            self.assertFalse(j.kill_on_close)
            self.assertIsNone(j.pin)
        finally:
            j.close()

    def test_a_no_kill_job_that_cannot_hold_kill_on_close_for_the_birth_starts_nothing(self):
        with mock.patch.object(winjob, "_set_ext", return_value=False):
            j = job.Job(kill_on_close=False)
            try:
                with mock.patch.object(subprocess, "Popen") as popen:
                    with self.assertRaisesRegex(job.JobError, "birth"):
                        j.start([sys.executable, "-c", "pass"])
                popen.assert_not_called()
            finally:
                j.close()

    def test_a_kill_on_close_not_cleared_after_the_resume_ends_the_child(self):
        # Left set, the job's close would end a git write: the child, just resumed, is ended.
        started, real = [], subprocess.Popen

        def spy(*a, **kw):
            started.append(real(*a, **kw))
            return started[-1]
        j = job.Job(kill_on_close=False)
        try:
            with mock.patch.object(winjob, "_set_ext", side_effect=lambda h, flags, affinity=0:
                                   bool(flags & win32.JOB_KILL_ON_CLOSE)), \
                    mock.patch.object(subprocess, "Popen", spy):
                with self.assertRaisesRegex(job.JobError, "cleared"):
                    j.start([sys.executable, "-c", "import time; time.sleep(30)"])
            self.assertIsNotNone(started[0].poll())
        finally:
            j.close()

    def test_a_pinned_job_whose_limit_is_refused_twice_raises(self):
        with mock.patch.object(winjob, "_set_ext", return_value=False):
            with self.assertRaisesRegex(job.JobError, "SetInformationJobObject"):
                job.Job(1 << first_own_thread())

    def test_a_refused_pin_alone_drops_the_pin_and_keeps_kill_on_close(self):
        calls = []

        def refuse_pin(h, flags, affinity=0):
            calls.append(flags)
            return not flags & win32.JOB_AFFINITY

        with mock.patch.object(winjob, "_set_ext", side_effect=refuse_pin):
            j = job.Job(1 << first_own_thread())
        try:
            self.assertIsNone(j.pin)
            self.assertIn("could not be set", j.note)
            self.assertTrue(calls[-1] & win32.JOB_KILL_ON_CLOSE)
        finally:
            j.close()


@windows_only("Windows only")
class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kitlib-job-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_pinned_child_runs_on_its_threads_and_is_measured(self):
        first = first_own_thread()
        j = job.Job(1 << first)
        try:
            self.assertIsNone(j.note)
            self.assertEqual(j.mask, 1 << first)
            p = j.start([sys.executable, "-c", "import time; time.sleep(5)"])
            self.addCleanup(p.wait, 30)     # reap it: an unread Popen warns "still running"
            h = process.open_process(p.pid)
            try:
                self.assertEqual(affinity(h)[0], 1 << first)
            finally:
                process.close(h)
            cpu_s, commit, ws = j.usage()
            self.assertGreaterEqual(cpu_s, 0)
            self.assertGreater(commit, 0)
            self.assertGreater(ws, 0)
        finally:
            j.end(kill=True)

    def test_end_kill_ends_the_whole_tree(self):
        pidfile = os.path.join(self.tmp, "pid")
        j = job.Job()
        p = j.start([sys.executable, "-c", TREE, pidfile])
        g = wait_file(pidfile)
        h = process.open_process(g, process.SYNCHRONIZE)
        try:
            j.end(kill=True)
            self.assertEqual(win32.K32.WaitForSingleObject(h, 20000), win32.WAIT_OBJECT_0)
        finally:
            process.close(h)
        p.wait(30)

    def test_end_without_kill_leaves_a_daemon_running_unpinned(self):
        pidfile = os.path.join(self.tmp, "pid")
        first = first_own_thread()
        j = job.Job(1 << first)
        p = j.start([sys.executable, "-c", TREE, pidfile])
        g = wait_file(pidfile)
        h = process.open_process(g, process.SYNCHRONIZE | process.PROCESS_TERMINATE
                                 | process.PROCESS_QUERY_LIMITED_INFORMATION)
        try:
            j.end(kill=False)
            self.assertEqual(win32.K32.WaitForSingleObject(h, 0), win32.WAIT_TIMEOUT)
            self.assertEqual(affinity(h)[0], own_mask())         # every thread it may use back
        finally:
            win32.K32.TerminateProcess(h, 1)
            process.close(h)
            p.kill()
            p.wait(30)

    def test_contains_tells_a_process_in_the_job_from_one_outside(self):
        j = job.Job()
        try:
            p = j.start([sys.executable, "-c", "import time; time.sleep(30)"])
            self.addCleanup(p.wait, 30)
            self.assertEqual(j.contains(p.pid), "in")
            self.assertEqual(j.contains(os.getpid()), "out")
            self.assertEqual(j.contains(0), "unknown")
        finally:
            j.end(kill=True)
        self.assertEqual(j.contains(p.pid), "unknown")           # the job is closed

    def test_contains_counts_a_process_in_a_nested_job_as_in(self):
        outer = job.Job()
        try:
            p = outer.start([sys.executable, "-c", "import time; time.sleep(30)"])
            self.addCleanup(p.wait, 30)
            inner = job.Job()
            try:
                inner.assign(p)                 # p is in outer already: inner nests in outer
                self.assertEqual(inner.contains(p.pid), "in")
                self.assertEqual(outer.contains(p.pid), "in", "a nested job's process is out")
            finally:
                inner.end(kill=True)
        finally:
            outer.end(kill=True)

    def test_a_closed_job_starts_nothing(self):
        j = job.Job()
        j.close()
        with mock.patch.object(subprocess, "Popen") as popen:
            with self.assertRaisesRegex(job.JobError, "closed"):
                j.start([sys.executable, "-c", "pass"])
        popen.assert_not_called()

    def test_a_child_not_born_in_the_job_is_ended_not_run(self):
        # Nothing runs outside the job. The old machine_lock Job ran it anyway,
        # unpinned ("not in a job"): red there. Here the CreateProcess wrapper ignores the job
        # list, as an older kit copy's would.
        marker = os.path.join(self.tmp, "ran")
        j = job.Job()
        try:
            with mock.patch.object(_winapi, "CreateProcess", wincon._original):
                with self.assertRaisesRegex(job.JobError, "not born in its job"):
                    j.start([sys.executable, "-c", "open(%r, 'w').close()" % marker])
        finally:
            j.close()
        time.sleep(1)
        self.assertFalse(os.path.exists(marker))

    def test_a_parent_that_dies_before_the_resume_leaves_no_child(self):
        # The parent dies just after the child is made, before it is resumed (and, before the
        # job-list birth, before it was put in the job): the child must end with it. Red before:
        # the child stayed suspended, in no job of the parent's, for good.
        for kill in ("1", "0"):
            with self.subTest(kill_on_close=kill):
                parent = subprocess.Popen([sys.executable, "-c", DIES, kill], cwd=ROOT,
                                          env=dict(os.environ, PYTHONPATH=ROOT),
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                self.addCleanup(end_own, parent)
                h = process.open_process(read_pid(parent), process.SYNCHRONIZE
                                         | process.PROCESS_TERMINATE)
                self.addCleanup(end_by_handle, h)
                parent.stdin.write(b"go\n")
                parent.stdin.flush()
                self.assertEqual(parent.wait(60), 7)
                self.assertEqual(win32.K32.WaitForSingleObject(h, 10000), win32.WAIT_OBJECT_0,
                                 "the child outlived its parent")

    def test_a_no_kill_job_s_command_outlives_the_job(self):
        # kill-on-close is held only from the birth to the resume: kitlib.git.cmd's writes run
        # on after their job's handle closes (as at the parent's death).
        j = job.Job(kill_on_close=False)
        p = j.start([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            j.close()
            with self.assertRaises(subprocess.TimeoutExpired):
                p.wait(1)
        finally:
            p.kill()
            p.wait(30)

    def test_before_resume_runs_while_suspended(self):
        seen = []
        j = job.Job()
        try:
            p = j.start([sys.executable, "-c", "pass"],
                        before_resume=lambda q: seen.append(q.poll()))
            self.assertEqual(p.wait(60), 0)
            self.assertEqual(seen, [None])
        finally:
            j.end(kill=True)

    def test_pids_lists_the_tree(self):
        pidfile = os.path.join(self.tmp, "pid")
        j = job.Job()
        try:
            p = j.start([sys.executable, "-c", TREE, pidfile])
            self.addCleanup(p.wait, 30)     # reap it: an unread Popen warns "still running"
            g = wait_file(pidfile)
            self.assertTrue({p.pid, g} <= set(j.pids()))
        finally:
            j.end(kill=True)


    def test_the_pin_reads_back_as_pinned_when_no_outer_job_overrides_it(self):
        first = first_own_thread()
        j = job.Job(1 << first)
        try:
            j.start([sys.executable, "-c", "pass"]).wait(30)
            self.assertEqual((j.pin, j.effective), ("pinned", 1 << first))
        finally:
            j.end(kill=True)

    def test_a_mask_outside_an_outer_job_reads_back_overridden(self):
        own = own_mask()
        if bin(own).count("1") < 2:
            self.skipTest("needs two threads this process may use")
        a = (own & -own).bit_length() - 1
        rest = own & ~(1 << a)
        b = (rest & -rest).bit_length() - 1
        out = os.path.join(self.tmp, "pin.json")
        env = dict(os.environ, PYTHONPATH=ROOT)
        outer = job.Job(1 << a)
        try:
            p = outer.start([sys.executable, "-c", NESTED, str(1 << b), str(1 << a), out], env=env)
            self.assertEqual(p.wait(60), 0)
        finally:
            outer.end(kill=True)
        with open(out) as f:
            got = json.load(f)
        self.assertEqual(got["outside"], ["overridden", 1 << a])
        self.assertIn("outer job", got["note"])
        self.assertEqual(got["inside"], ["pinned", 1 << a])

    def test_init_closes_the_job_when_anything_after_creating_it_raises(self):
        closed, real = [], job.Job.close

        def spy(self):
            closed.append(self.h)
            real(self)

        with mock.patch.object(job.Job, "close", spy):
            with mock.patch.object(winjob.cpu, "all_mask", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    job.Job(1)
        self.assertEqual(len(closed), 1)
        self.assertTrue(closed[0])

    def test_a_short_job_is_the_same_job_object(self):
        """short is for Linux's carrier scope; here it changes nothing."""
        j = job.Job(short=True)
        p = j.start([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertTrue(j.kill_on_close)
            self.assertTrue(j.contains(p.pid))
        finally:
            j.end(kill=True)
            p.wait(30)


@windows_only("Windows only")
class EnvBlock(unittest.TestCase):
    """A job birth (win32.create_process) gives the child the environment block _winapi's
    CreateProcess gives it on this interpreter, and refuses what _winapi refuses."""

    def started(self, env, born):
        """The child's block, or the exception's (type, winerror)."""
        argv = [sys.executable, "-c", ENV_BLOCK]
        try:
            if born:
                j = job.Job()
                try:
                    p = j.start(argv, env=env, stdout=subprocess.PIPE)
                    out = p.communicate(timeout=60)[0]
                finally:
                    j.end(kill=True)
            else:
                out = subprocess.run(argv, env=env, stdout=subprocess.PIPE, timeout=60).stdout
        except (OSError, ValueError) as e:
            return type(e).__name__, getattr(e, "winerror", None)
        return json.loads(out)

    def test_each_block_is_the_one_winapi_makes(self):
        root = {"SYSTEMROOT": os.environ["SYSTEMROOT"]}
        for env in ({"PATH": "a", "Path": "b"}, {"Path": "b", "PATH": "a"},
                    {"b": "1", "A": "2", "c": "3", "_x": "4", "a1": "5"},
                    {"=C:": "C:\\x", "=D:": "D:\\", "Z": "z"}, {"": "1"}, {"=": "1"},
                    {"A=B": "1"}, {"A": "1\0"}, {}):
            with self.subTest(env=env):
                env = dict(env, **root) if env else env
                self.assertEqual(self.started(env, True), self.started(env, False))


@windows_only("Windows only")
class Processes(unittest.TestCase):
    """Job.processes counts every process ever started in the job: the pool's per-unit count of
    process starts (kitlib/tooldeps/pool.py) reads it before and after a unit."""

    def setUp(self):
        # A child of a parent with no console gets one of its own (Job.start: CREATE_NO_WINDOW),
        # and that console's conhost is started in the job too.
        from kitlib.proc import console
        self.each = 1 if console.has_console() else 2

    def test_n_children_raise_the_count_by_n(self):
        j = job.Job()
        try:
            self.assertEqual(j.processes(), 0)
            for _ in range(3):
                self.assertEqual(j.start([sys.executable, "-c", "pass"]).wait(60), 0)
            self.assertEqual(j.processes(), 3 * self.each)
        finally:
            j.end(kill=True)
        self.assertIsNone(j.processes())                        # the job is closed

    def test_processes_in_a_job_nested_in_it_count_too(self):
        # The child starts two processes in a job of its own, as kitlib.git.cmd starts each git.
        env = dict(os.environ, PYTHONPATH=ROOT)
        code = ("import sys\nfrom kitlib.proc import job\nj = job.Job()\n"
                "for _ in range(2):\n    j.start([sys.executable, '-c', 'pass']).wait(60)\n"
                "j.end(kill=True)\n")
        outer = job.Job()
        try:
            self.assertEqual(outer.start([sys.executable, "-c", code], env=env).wait(60), 0)
            self.assertEqual(outer.processes(), self.each + 2)
        finally:
            outer.end(kill=True)

    def test_a_child_with_a_console_of_its_own_counts_its_conhost(self):
        j = job.Job()
        try:
            p = j.start([sys.executable, "-c", "pass"], creationflags=win32.CREATE_NO_WINDOW)
            self.assertEqual(p.wait(60), 0)
            self.assertEqual(j.processes(), 2)
        finally:
            j.end(kill=True)


if __name__ == "__main__":
    unittest.main()
