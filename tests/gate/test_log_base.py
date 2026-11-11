"""Gate logs: `.kit.json` "landing".log_dir, else `<kit temp>/<repo>-gate`, where the kit temp is
kitlib.config.user.work_temp of the checkout (its own drive's temp folder, else the shared temp
folder, %LOCALAPPDATA%/Temp on Windows). Never whatever TEMP a process inherited, so an agent
whose TEMP was moved and the owner's shells read the same folder. The log base itself:
tests/records/test_place.py."""
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.config import user
from kitlib.gate import runner
from kitlib.records import place
from tests.home import machine_config


class _Cfg(object):
    def __init__(self, landing=None):
        self.landing = landing or {}

    def section(self, name):
        return self.landing if name == 'landing' else {}


class SharedTemp(unittest.TestCase):
    def test_windows_uses_localappdata_not_temp(self):
        env = {'LOCALAPPDATA': r'C:\lad', 'TEMP': r'D:\moved', 'TMP': r'D:\moved'}
        self.assertEqual(user.shared_temp(env, 'win32'), os.path.join(r'C:\lad', 'Temp'))

    def test_windows_without_localappdata_is_loud(self):
        for env in ({'TEMP': r'D:\moved'}, {'LOCALAPPDATA': '', 'TEMP': r'D:\moved'}):
            with self.assertRaises(RuntimeError) as cm:
                user.shared_temp(env, 'win32')
            self.assertIn('LOCALAPPDATA', str(cm.exception))

    def test_elsewhere_keeps_tempfile(self):
        with mock.patch('tempfile.gettempdir', return_value='/tmp/x'):
            self.assertEqual(user.shared_temp({}, 'darwin'), '/tmp/x')
            self.assertEqual(user.shared_temp({}, 'linux'), '/tmp')   # Linux: /tmp, whatever TMPDIR says


class NewLogDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='kit-logdir-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_kit_temp_that_cannot_be_made_falls_back_to_the_shared_temp(self):
        blocker = os.path.join(self.tmp, 'a-file')
        with open(blocker, 'w') as fh:
            fh.write('x')
        shared = os.path.join(self.tmp, 'shared')
        with mock.patch.object(user, 'work_temp', return_value=os.path.join(blocker, 'kit')), \
                mock.patch.object(user, 'shared_temp', return_value=shared), \
                contextlib.redirect_stderr(io.StringIO()):
            path = runner.new_log_dir(_Cfg(), self.tmp, 'demo', 'feat/x')
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.path.dirname(path),
                         os.path.join(shared, 'demo-gate', 'feat-x'))

    def test_a_usable_kit_temp_is_used(self):
        kit = os.path.join(self.tmp, 'kit')
        with mock.patch.object(user, 'work_temp', return_value=kit):
            path = runner.new_log_dir(_Cfg(), self.tmp, 'demo', 'feat/x')
        self.assertEqual(os.path.dirname(path),
                         os.path.join(kit, 'demo-gate', 'feat-x'))

    def test_the_fallback_says_so_once_on_stderr(self):
        blocker = os.path.join(self.tmp, 'a-file')
        with open(blocker, 'w') as fh:
            fh.write('x')
        shared, err = os.path.join(self.tmp, 'shared'), io.StringIO()
        with mock.patch.object(user, 'work_temp', return_value=os.path.join(blocker, 'kit')), \
                mock.patch.object(user, 'shared_temp', return_value=shared), \
                contextlib.redirect_stderr(err):
            path = runner.new_log_dir(_Cfg(), self.tmp, 'demo', 'feat/x')
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn(path, lines[0])
        self.assertIn('could not be made', lines[0])


def bad_machine_config(test):
    """A home whose machine config work_temp refuses (a relative temp_dir, in the fenced PM
    repository the pointer names), patched in."""
    home = tempfile.mkdtemp(prefix='kit-home-')
    test.addCleanup(shutil.rmtree, home, True)
    machine_config(home, {'temp_dir': 'relative'}, case=test)
    patch = mock.patch.object(user, 'home_dir', return_value=home)
    patch.start()
    test.addCleanup(patch.stop)


class BadMachineConfig(unittest.TestCase):
    """A machine config that cannot be read never stops gate logging: the shared temp's base,
    with a note (as make_temp does for the run folder)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='kit-logdir-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        bad_machine_config(self)
        self.shared = os.path.join(self.tmp, 'shared')
        patch = mock.patch.object(user, 'shared_temp', return_value=self.shared)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_log_base_is_the_fallback_base_with_a_note(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            base = place.log_base(_Cfg(), self.tmp, 'demo', runner.say_note)
        self.assertEqual(base, os.path.join(self.shared, 'demo-gate'))
        self.assertIn('machine config', err.getvalue())
        self.assertIn('temp_dir must be an absolute path', err.getvalue())

    def test_a_gate_run_still_gets_a_log_folder(self):
        with contextlib.redirect_stderr(io.StringIO()):
            path = runner.new_log_dir(_Cfg(), self.tmp, 'demo', 'feat/x')
        self.assertTrue(os.path.isdir(path))
        self.assertEqual(os.path.dirname(path), os.path.join(self.shared, 'demo-gate', 'feat-x'))


if __name__ == '__main__':
    unittest.main()
