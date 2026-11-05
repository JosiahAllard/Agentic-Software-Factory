"""One PM start checklist run reads each shared source once (docs/pm-start.md): the task scheduler, the
machine config's launcher.repos and plugin Settings, the PM repository's toplevel and the pm_watch
config. Each reader is replaced by a counting fake; the checks that use it still see its answer."""
import datetime
import unittest
from unittest import mock

from kitlib import pmstart, pmwatch
from kitlib.pmstart import consoletasks
from tests.home import temp_home
from tests.pmstart import HANDOFF_FAKE, keep_off_launcher_repos

NOW = datetime.datetime(2026, 10, 13, 9, 0)
GONE = "Q:/no-such-repo-folder"


def setUpModule():
    keep_off_launcher_repos()


class ReadsOnceTest(unittest.TestCase):

    def run_checklist(self, **over):
        temp_home(self)
        calls = {k: 0 for k in ("repos", "settings", "tasks", "top", "watch")}

        def counted(key, value):
            def read(*a, **k):
                calls[key] += 1
                return value
            return read
        fakes = dict(pm_data=lambda: None, handoff=HANDOFF_FAKE, reminders={"read": lambda p: "[]"},
                     liveness={"read": lambda p: "{}"},
                     memory={"check": lambda path, label=None: None, "folder": lambda top: None},
                     repo_prep={"prep": lambda path: []},
                     watcher={"now": 0}, foreground_lock={"read": lambda: 0})
        fakes.update(over)
        with mock.patch.object(pmstart, "launcher_repos", counted("repos", {"a": GONE})), \
                mock.patch.object(pmstart, "plugin_settings", counted("settings", None)), \
                mock.patch.object(pmstart, "pm_memory_repo", counted("top", "X:/pm")), \
                mock.patch.object(consoletasks, "read_tasks", counted("tasks", [])), \
                mock.patch.object(pmwatch, "load_config", counted("watch", {"state_file": GONE + "/s.json"})):
            txt = pmstart.checklist({}, NOW, procs=[], **fakes)
        return txt, calls

    def test_each_source_is_read_once(self):
        _txt, calls = self.run_checklist()
        self.assertEqual(calls["tasks"], 1)
        self.assertEqual(calls["repos"], 1)
        self.assertEqual(calls["settings"], 1)
        self.assertEqual(calls["top"], 1)
        self.assertEqual(calls["watch"], 1)

    def test_a_failed_read_is_each_checks_own_line(self):
        temp_home(self)
        boom = mock.Mock(side_effect=RuntimeError("no schtasks"))
        with mock.patch.object(consoletasks, "read_tasks", boom):
            txt = pmstart.checklist({}, NOW, procs=[], pm_data=lambda: None, handoff=HANDOFF_FAKE,
                                    reminders={"read": lambda p: "[]"}, liveness={"read": lambda p: "{}"},
                                    repos={"a": GONE}, repo_prep={"prep": lambda p: []},
                                    watcher={"beat": lambda: None, "now": 0}, foreground_lock={"read": lambda: 0})
        self.assertEqual(boom.call_count, 1)
        self.assertIn("- could not check the worktree sweep tasks: RuntimeError: no schtasks", txt)
        if consoletasks.platform.NAME != "linux":
            self.assertIn("- could not check console_tasks: RuntimeError: no schtasks", txt)


if __name__ == "__main__":
    unittest.main()
