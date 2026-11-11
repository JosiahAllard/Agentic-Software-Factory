"""kitlib.git.lockplace: where a file's write lock goes (the common dir in a checkout, beside the
file outside one, NoPlace when git cannot tell)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.git import lockplace as L
from kitlib.git import repo as gitrepo
from tests.gitfixture import TempRepo


class OutsideGitTest(unittest.TestCase):
    def test_the_lock_sits_beside_the_file(self):
        folder = tempfile.mkdtemp(prefix='kit-lockplace-')
        self.addCleanup(shutil.rmtree, folder, True)
        path = os.path.join(folder, 'sub', 'FILE.md')
        self.assertEqual(L.lock_path(path, 'kit-x'), os.path.join(folder, 'sub', '.FILE.md.lock'))
        self.assertFalse(os.path.exists(os.path.dirname(path)))     # nothing is made


class InGitTest(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo()
        self.addCleanup(self.repo.cleanup)
        self.path = self.repo.path('missing', 'FILE.md')

    def test_the_lock_is_in_the_common_dir_named_by_prefix_and_the_same_once_the_folder_exists(self):
        lock = L.lock_path(self.path, 'kit-x')
        common = os.path.normcase(os.path.realpath(self.repo.common_dir()))
        self.assertEqual(os.path.normcase(os.path.dirname(lock)), common)
        self.assertTrue(os.path.basename(lock).startswith('kit-x-file.md-'), lock)
        self.assertNotEqual(L.lock_path(self.path, 'kit-y'), lock)  # two features, two locks
        os.makedirs(os.path.dirname(self.path))
        self.assertEqual(L.lock_path(self.path, 'kit-x'), lock)

    def test_git_that_cannot_say_is_no_place(self):
        bad = gitrepo.Place(gitrepo.UNKNOWN, None, 'git did not answer', None, None)
        with mock.patch.object(gitrepo, 'classify', return_value=bad):
            with self.assertRaises(L.NoPlace) as cm:
                L.lock_path(self.path, 'kit-x')
        self.assertEqual(cm.exception.detail, 'git did not answer')


if __name__ == '__main__':
    unittest.main()
