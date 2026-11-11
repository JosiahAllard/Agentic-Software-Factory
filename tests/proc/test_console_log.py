"""Tests for where kitlib.proc.console keeps its incident log: the folder a registered provider
gives (kitlib.config.user registers the kit's), else the OS temp folder."""
import os
import tempfile
import unittest
from unittest import mock

from kitlib.config import user
from kitlib.proc import console


class LogFolderTest(unittest.TestCase):

    def setUp(self):
        self.registered = console._log_dir      # what importing kitlib.config.user left
        self.addCleanup(console.set_log_dir, self.registered)
        patch = mock.patch.object(console, "LOG_PATH", None)
        patch.start()
        self.addCleanup(patch.stop)

    def test_no_provider_uses_os_temp(self):
        console.set_log_dir(None)
        self.assertEqual(console.log_path(), os.path.join(tempfile.gettempdir(), console.LOG_NAME))

    def test_config_registers_provider(self):
        self.assertIs(self.registered, user._console_log_dir)
        with mock.patch.object(user, "work_temp", return_value=os.path.join("W:", "t")):
            self.assertEqual(console.log_path(), os.path.join("W:", "t", console.LOG_NAME))
        with mock.patch.object(user, "work_temp", side_effect=user.HomeFenced("fenced")):
            self.assertIsNone(console.log_path())      # a fenced test process: no log
