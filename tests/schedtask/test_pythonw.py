"""The pythonw.exe a scheduled task runs (kitlib/schedtask/pythonw.py): the base install's, never a
WindowsApps alias. Nothing is registered here."""
import os
import sys
import unittest
from unittest import mock

from kitlib.schedtask import pythonw as P

SEP = chr(92)
ALIAS = SEP.join(['C:', 'Users', 'u', 'AppData', 'Local', 'Microsoft', 'WindowsApps', 'pythonw.exe'])
STORE = SEP.join(['C:', 'Program Files', 'WindowsApps', 'PythonSoftwareFoundation.Python.3.12_x', 'python.exe'])
BASE = SEP.join(['C:', 'Python', 'pythoncore-3.14-64'])


def never(*a, **k):
    raise AssertionError('PowerShell ran')


class Helper(unittest.TestCase):

    def test_a_windows_apps_folder_is_found_in_any_case_and_either_slash(self):
        self.assertTrue(P.under_windows_apps(ALIAS))
        self.assertTrue(P.under_windows_apps(STORE.lower()))
        self.assertTrue(P.under_windows_apps('/'.join(['C:', 'Users', 'u', 'AppData', 'Local', 'Microsoft',
                                                       'WINDOWSAPPS', 'pythonw.exe'])))
        self.assertFalse(P.under_windows_apps(os.path.join(BASE, 'pythonw.exe')))
        self.assertFalse(P.under_windows_apps(SEP.join(['C:', 'MyWindowsAppsCopy', 'pythonw.exe'])))

    def test_the_base_pythonw_is_in_the_base_prefix(self):
        self.assertEqual(P.base_pythonw(BASE), os.path.join(BASE, 'pythonw.exe'))
        self.assertEqual(P.base_pythonw(), os.path.join(sys.base_prefix, 'pythonw.exe'))

    def test_refusal_names_the_store_alias_before_a_missing_file(self):
        why = P.refusal(ALIAS, isfile=lambda p: True)
        self.assertIn('WindowsApps', why)
        self.assertIn(ALIAS, why)
        self.assertIn('no pythonw.exe at', P.refusal(os.path.join(BASE, 'pythonw.exe'), isfile=lambda p: False))
        self.assertIsNone(P.refusal(os.path.join(BASE, 'pythonw.exe'), isfile=lambda p: True))


class ShortcutPythonw(unittest.TestCase):
    def test_the_shortcut_target_is_the_base_install_pythonw_even_from_a_venv(self):
        with mock.patch.object(sys, 'executable', 'C:\\venv\\Scripts\\python.exe'), \
                mock.patch.object(P.os.path, 'isfile', return_value=True):
            self.assertEqual(P.windowless(), P.base_pythonw())

    def test_a_store_alias_base_install_falls_back_to_this_interpreter(self):
        alias = 'C:\\Store\\WindowsApps\\pythonw.exe'
        with mock.patch.object(P, 'base_pythonw', return_value=alias), \
                mock.patch.object(P.os.path, 'isfile', return_value=True):
            self.assertEqual(P.windowless(), sys.executable)


if __name__ == '__main__':
    unittest.main()
