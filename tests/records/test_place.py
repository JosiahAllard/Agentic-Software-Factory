"""kitlib.records.place: the per-repository folder key and the gate log bases (`.kit.json`
"landing".log_dir, else `<kit temp>/<repo>-gate`, where the kit temp is
kitlib.config.user.work_temp of the checkout: its own drive's temp folder, else the shared temp
folder, %LOCALAPPDATA%/Temp on Windows; never whatever TEMP a process inherited)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.config import user
from kitlib.records import place
from tests.platforms import windows_only

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVE = os.path.splitdrive(HERE)[0]


def other_letter(*avoid):
    used = {a[0].upper() for a in avoid if a}
    return next(c for c in 'QRSTUVWXYJKLMNOP' if c not in used) + ':'


class _Cfg(object):
    def __init__(self, landing=None):
        self.landing = landing or {}

    def section(self, name):
        return self.landing if name == 'landing' else {}


@windows_only('drive letters are Windows only')
@unittest.skipUnless(DRIVE, 'drive letters are Windows only')
class LogBase(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp(prefix='kit-home-')        # no machine config: no temp_dir
        self.addCleanup(shutil.rmtree, home, True)
        patch = mock.patch.object(user, 'home_dir', return_value=home)
        patch.start()
        self.addCleanup(patch.stop)
        self.root = os.path.join(DRIVE + os.sep, 'r', 'demo')
        self.notes = []

    def base(self, env, cfg=None):
        with mock.patch.dict(os.environ, env):
            return place.log_base(cfg or _Cfg(), self.root, 'demo', self.notes.append)

    def test_off_the_shared_temps_drive_the_logs_stay_on_the_checkouts_drive(self):
        lad = other_letter(DRIVE) + os.sep + 'lad'
        moved = other_letter(DRIVE, lad) + os.sep + 'moved'
        base = self.base({'LOCALAPPDATA': lad, 'TEMP': moved, 'TMP': moved})
        self.assertEqual(base, os.path.join(DRIVE + os.sep, user.TEMP_DIR_NAME, 'demo-gate'))

    def test_on_the_shared_temps_drive_the_base_ignores_a_moved_temp(self):
        moved = other_letter(DRIVE) + os.sep + 'moved'
        base = self.base({'LOCALAPPDATA': DRIVE + os.sep + 'lad', 'TEMP': moved, 'TMP': moved})
        self.assertEqual(base, os.path.join(DRIVE + os.sep, 'lad', 'Temp', 'demo-gate'))

    def test_the_fallback_base_is_in_the_shared_temp(self):
        lad = other_letter(DRIVE) + os.sep + 'lad'
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': lad}):
            self.assertEqual(place.fallback_log_base('demo'),
                             os.path.join(lad, 'Temp', 'demo-gate'))

    def test_configured_log_dir_still_wins(self):
        base = place.log_base(_Cfg({'log_dir': 'logs/gate'}), r'D:\r\demo', 'demo',
                              self.notes.append)
        self.assertEqual(base, os.path.join(r'D:\r\demo', 'logs/gate'))


class Folder(unittest.TestCase):
    """The key of a repository's folder under each kit folder: one per repository, every
    worktree of it sharing it."""

    def test_a_repo_key_is_the_folder_name_and_a_hash_of_the_common_dir(self):
        a = place.repo_key(r'C:\work\My.Repo\.git')
        self.assertRegex(a, r'^myrepo-[0-9a-f]{12}$')
        self.assertEqual(place.repo_key(r'c:\WORK\my.repo\.GIT'), a)   # Windows ignores case
        self.assertNotEqual(place.repo_key(r'C:\other\My.Repo\.git'), a)
        self.assertRegex(place.repo_key(r'C:\remotes\proj.git'), r'^projgit-[0-9a-f]{12}$')
        self.assertRegex(place.repo_key(r'C:\x\%%%\.git'), r'^repo-[0-9a-f]{12}$')
        self.assertLessEqual(len(place.repo_key('C:\\x\\%s\\.git' % ('n' * 200))), 40 + 13)


if __name__ == '__main__':
    unittest.main()
