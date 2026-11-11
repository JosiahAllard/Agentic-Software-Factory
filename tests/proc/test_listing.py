"""kitlib.proc.listing: the process listing with its system calls faked. On Windows it asks
PowerShell (through kitlib.proc.spawn); on Linux it reads /proc (kitlib.platform.linux.process.rows).
kitlib.platform.NAME is patched to 'linux' for the Linux case, so it runs on Windows."""
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from kitlib.platform.linux import process as linux_process
from kitlib.proc import listing
from tests.platforms import windows_only

ROW = {'Name': 'claude', 'CommandLine': 'claude -n x', 'ExecutablePath': '/usr/bin/claude',
       'ProcessId': 7, 'ParentProcessId': 1, 'Created': '2026-10-27T10:00:00+00:00'}


def ran(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class ListProcessesTest(unittest.TestCase):

    def setUp(self):
        patch = mock.patch("kitlib.platform.NAME", "windows")
        patch.start()
        self.addCleanup(patch.stop)

    def call(self, result):
        with mock.patch.object(subprocess, "run", return_value=result) as run:
            out = listing.list_processes()
        args, kw = run.call_args
        self.assertEqual(args[0][:3], ["powershell", "-NoProfile", "-NonInteractive"])
        self.assertEqual(kw["timeout"], listing.TIMEOUT)
        return out

    def test_a_list_is_returned_as_is_and_one_process_becomes_a_list(self):
        self.assertEqual(self.call(ran(out='[{"ProcessId": 1}, {"ProcessId": 2}]')),
                         [{"ProcessId": 1}, {"ProcessId": 2}])
        self.assertEqual(self.call(ran(out='{"ProcessId": 1}')), [{"ProcessId": 1}])
        self.assertEqual(self.call(ran(out="")), [])

    def test_a_failed_powershell_raises_with_its_exit_code_and_stderr(self):
        with self.assertRaises(RuntimeError) as c:
            self.call(ran(rc=3, err="  access denied \n"))
        self.assertEqual(str(c.exception), "powershell exit 3: access denied")

    @windows_only("Windows only")
    def test_with_no_console_powershell_is_started_with_no_window(self):
        """Session start runs this where there may be no console: PowerShell must not open one."""
        from kitlib.proc import job
        from kitlib.platform.windows import win32
        for console, flags in ((False, win32.CREATE_NO_WINDOW), (True, 0)):
            with mock.patch.object(job, "has_console", return_value=console), \
                    mock.patch.object(subprocess, "run", return_value=ran(out="")) as run:
                self.assertEqual(listing.list_processes(), [])
            self.assertEqual(run.call_args[1].get("creationflags", 0), flags, console)


class Linux(unittest.TestCase):
    def setUp(self):
        patch = mock.patch('kitlib.platform.NAME', 'linux')
        patch.start()
        self.addCleanup(patch.stop)

    def test_list_processes_reads_proc_and_runs_no_powershell(self):
        with mock.patch.object(linux_process, 'rows', return_value=[ROW]) as rows, \
                mock.patch.object(subprocess, 'run') as run:
            self.assertEqual(listing.list_processes(), [ROW])
        rows.assert_called_once_with()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
