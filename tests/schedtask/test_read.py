"""kitlib.schedtask.read: reading scheduled tasks back, `schtasks /Query /XML ONE` (read_tasks, with
xml_input and parse) and `schtasks /Query /TN` (task_registered), and on Linux the pm-kit jobs.
Fake schtasks output and a fake subprocess.run; one read-only `schtasks /Query /XML ONE`
(RealRead) is the only read of this machine's task scheduler, and it skips when schtasks cannot
answer."""
import codecs
import subprocess
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from kitlib.proc import spawn
from kitlib.schedtask import host as schedhost
from kitlib.schedtask import read as R
from tests.platforms import windows_only


XML = """
<Tasks>


<!-- \\kit-daily-full -->

<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><URI>\\kit-daily-full</URI></RegistrationInfo>
  <Actions Context="Author">
    <Exec>
      <Command>C:\\Py\\python.exe</Command>
      <Arguments>"Q:\\kit\\tools\\daily_full.py" -C "Q:\\repo"</Arguments>
      <WorkingDirectory>Q:\\repo</WorkingDirectory>
    </Exec>
  </Actions>
</Task>


<!-- \\Folder\\no-uri -->

<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Actions Context="Author">
    <ComHandler><ClassId>{0}</ClassId></ComHandler>
    <Exec><Command>notepad.exe</Command></Exec>
  </Actions>
</Task>

</Tasks>
"""


DECLARED = '<?xml version="1.0" encoding="UTF-16"?>'   # what schtasks /Query /XML writes first
KIT_NON_ASCII = "Q:\\K\u00eft"


class FakeRun:
    def __init__(self, stdout=XML.encode("ascii"), code=0, stderr=b"", raises=None):
        self.stdout, self.code, self.stderr, self.raises = stdout, code, stderr, raises
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(args, self.code, self.stdout, self.stderr)


@mock.patch('kitlib.platform.NAME', 'windows')   # the Windows form, on any machine
class Read(unittest.TestCase):
    def test_parse_names_and_exec_actions(self):
        self.assertEqual(R.parse(XML), [
            {"path": r"\kit-daily-full", "actions": [
                {"exe": r"C:\Py\python.exe", "args": r'"Q:\kit\tools\daily_full.py" -C "Q:\repo"'}]},
            {"path": r"\Folder\no-uri", "actions": [{"exe": "notepad.exe", "args": ""}]}])

    def test_read_asks_schtasks_for_the_xml_with_a_timeout(self):
        run = FakeRun()
        self.assertEqual(len(R.read_tasks(run=run)), 2)
        (args, kw), = run.calls
        self.assertEqual(args, ["schtasks", "/Query", "/XML", "ONE"])
        self.assertEqual(kw["timeout"], R.TIMEOUT_S)

    def test_utf16_output_with_its_declaration_goes_to_the_parser_as_bytes(self):
        text = DECLARED + XML.replace("Q:\\kit", KIT_NON_ASCII)
        for raw in (codecs.BOM_UTF16_LE + text.encode("utf-16-le"), text.encode("utf-16-le"),
                    text.encode("utf-16"), codecs.BOM_UTF16_BE + text.encode("utf-16-be")):
            with self.subTest(raw=raw[:4]):
                self.assertIsInstance(R.xml_input(raw), bytes)
                tasks = R.read_tasks(run=FakeRun(stdout=raw))
                self.assertIn(KIT_NON_ASCII + "\\tools\\daily_full.py", tasks[0]["actions"][0]["args"])

    def test_single_byte_output_declaring_utf16_is_read_in_the_console_code_page(self):
        # schtasks /Query /XML writes encoding="UTF-16" and one byte per character to a pipe
        text = DECLARED + XML.replace("Q:\\kit", KIT_NON_ASCII)
        try:
            raw = text.encode("oem")
        except UnicodeEncodeError:
            self.skipTest("the OEM code page has no %r" % KIT_NON_ASCII)
        tasks = R.read_tasks(run=FakeRun(stdout=raw))
        self.assertEqual(tasks[0]["actions"][0]["args"],
                         '"%s\\tools\\daily_full.py" -C "Q:\\repo"' % KIT_NON_ASCII)

    def test_single_byte_output_declaring_a_single_byte_encoding_keeps_it(self):
        raw = (b'<?xml version="1.0" encoding="windows-1252"?>'
               + XML.replace("Q:\\kit", KIT_NON_ASCII).encode("cp1252"))
        self.assertIsInstance(R.xml_input(raw), bytes)
        self.assertIn(KIT_NON_ASCII, R.read_tasks(run=FakeRun(stdout=raw))[0]["actions"][0]["args"])

    def test_read_failures_raise(self):
        for run, err in ((FakeRun(code=1, stdout=b"ERROR: Access is denied."), RuntimeError),
                         (FakeRun(raises=subprocess.TimeoutExpired("schtasks", 4)), RuntimeError),
                         (FakeRun(raises=OSError("no schtasks")), OSError),
                         (FakeRun(stdout=b"not xml"), Exception)):
            with self.subTest(err=err, code=run.code, raises=run.raises):
                with self.assertRaises(err):
                    R.read_tasks(run=run)


@windows_only("schtasks is Windows'")
class RealRead(unittest.TestCase):
    def test_this_machines_task_list_parses(self):
        """One read-only `schtasks /Query /XML ONE`; skips when schtasks cannot answer in time."""
        seen = []

        def run(*a, **kw):
            seen.append(spawn.run(*a, **kw))
            return seen[-1]
        try:
            tasks = R.read_tasks(run=run, timeout=30)
        except (OSError, RuntimeError) as e:
            self.skipTest("schtasks could not answer: %s" % e)
        except ET.ParseError as e:
            # no task at all, or an informational message: nothing of the XML to check
            raw = seen[-1].stdout or b""
            if b"<Task" in raw or b"<\0T\0a\0s\0k\0" in raw:
                raise
            self.skipTest("schtasks gave no task XML: %s" % e)
        self.assertIsInstance(tasks, list)
        for t in tasks:
            self.assertTrue(t["path"])
            for a in t["actions"]:
                self.assertEqual(set(a), {"exe", "args"})


class Linux(unittest.TestCase):
    """On Linux read_tasks is the pm-kit jobs read back,
    never schtasks."""

    def setUp(self):
        patch = mock.patch("kitlib.platform.NAME", "linux")
        patch.start()
        self.addCleanup(patch.stop)
        self.mock = mock

    def test_read_tasks_is_the_jobs_read_back(self):
        from kitlib.platform.linux import schedule
        jobs = [{"path": "\\pm-kit-x", "name": "x", "actions": []}]
        with self.mock.patch.object(schedule, "read_back", return_value=jobs) as got:
            self.assertEqual(R.read_tasks(run="RUN"), jobs)
        got.assert_called_once_with(run="RUN")



@mock.patch('kitlib.platform.NAME', 'windows')
class TaskRegistered(unittest.TestCase):
    """read.task_registered against a fake subprocess.run: schtasks is never run."""

    def query(self, rc=0, out='', err='', raises=None):
        seen = {}

        def run(cmd, **kw):
            seen.update(kw, cmd=cmd)
            if raises:
                raise raises
            return subprocess.CompletedProcess(cmd, rc, out, err)
        return seen, run

    def test_exit_0_is_registered_and_a_timeout_is_passed(self):
        seen, run = self.query(0)
        self.assertTrue(R.task_registered('agent-temp-sweep', run=run))
        self.assertEqual(seen['cmd'], ['schtasks', '/Query', '/TN', 'agent-temp-sweep'])
        self.assertEqual(seen['timeout'], R.TASK_TIMEOUT)

    @windows_only('the not-found text is Windows\' own')
    def test_not_found_is_not_registered(self):
        import ctypes
        _, run = self.query(1, err='ERROR: %s\n' % ctypes.FormatError(2))
        self.assertFalse(R.task_registered('agent-temp-sweep', run=run))

    def test_access_denied_is_a_named_failure_not_unregistered(self):
        _, run = self.query(1, err='ERROR: Access is denied.\n')
        with self.assertRaises(RuntimeError) as c:
            R.task_registered('agent-temp-sweep', run=run)
        self.assertIn('Access is denied', str(c.exception))

    def test_a_timeout_is_a_named_failure(self):
        _, run = self.query(raises=subprocess.TimeoutExpired('schtasks', 30))
        with self.assertRaises(RuntimeError) as c:
            R.task_registered('agent-temp-sweep', timeout=30, run=run)
        self.assertIn('timed out after 30 s', str(c.exception))

    def test_the_hosts_task_exists_uses_it(self):
        with mock.patch.object(R, 'task_registered', lambda name: ('asked', name)):
            self.assertEqual(schedhost.Host().task_exists('agent-temp-sweep'), ('asked', 'agent-temp-sweep'))



if __name__ == "__main__":
    unittest.main()
