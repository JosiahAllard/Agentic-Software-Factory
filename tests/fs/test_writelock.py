"""kitlib.fs.writelock: one writer of a file at a time, on the lock file its caller names; nesting
and errors (where a file's lock goes: tests/git/test_lockplace.py)."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from kitlib.fs import writelock as W

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# A child that holds the lock file argv[2] until its stdin closes.
HOLDER = ('import sys; sys.path.insert(0, sys.argv[1]); from kitlib.fs import writelock as W\n'
          'with W.locked(sys.argv[2], 5):\n'
          '    print("held", flush=True); sys.stdin.read()\n')


class WriteLockTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='kit-fs-')
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 'sub', '.FILE.md.lock')

    def test_the_named_lock_file_and_its_folder_are_made_and_kept(self):
        self.assertFalse(W.held(self.path))             # no lock file yet
        with W.locked(self.path, 1) as got:
            self.assertEqual(got, self.path)
            self.assertTrue(os.path.isfile(self.path))
            self.assertTrue(W.held(self.path))
        self.assertFalse(W.held(self.path))
        self.assertTrue(os.path.isfile(self.path))      # never removed

    def test_nests_in_its_thread_and_excludes_another_thread(self):
        seen = []

        def other():
            try:
                with W.locked(self.path, 0.2, pause=0.02):
                    seen.append('took it')
            except W.Busy as e:
                seen.append(e)
        with W.locked(self.path, 1):
            with W.locked(self.path.upper(), 1):
                self.assertTrue(W.held(self.path))
            self.assertTrue(W.held(self.path))
            t = threading.Thread(target=other)
            t.start()
            t.join(30)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0], W.Busy)
        self.assertEqual(seen[0].wait, 0.2)
        self.assertIn('held by pid %d' % os.getpid(), seen[0].holder)

    def test_another_process_holding_it_is_busy_after_the_wait(self):
        proc = subprocess.Popen([sys.executable, '-c', HOLDER, ROOT, self.path],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        self.addCleanup(proc.communicate, timeout=30)
        self.addCleanup(proc.stdin.close)
        self.assertEqual(proc.stdout.readline().strip(), 'held')
        start = time.monotonic()
        with self.assertRaises(W.Busy) as cm:
            with W.locked(self.path, 0.3):
                self.fail('the block ran under a held lock')
        self.assertGreaterEqual(time.monotonic() - start, 0.3)
        self.assertIn('held by pid %d' % proc.pid, str(cm.exception))
        self.assertEqual(cm.exception.lock, self.path)

    def test_a_lock_that_cannot_be_made_or_taken_says_which(self):
        with mock.patch.object(W.os, 'makedirs', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(W.CannotMake):
                with W.locked(self.path, 1):
                    pass
        with mock.patch.object(W.TreeLock, 'acquire', side_effect=W.TreeLockError('broken')):
            with self.assertRaises(W.CannotTake) as cm:
                with W.locked(self.path, 1):
                    pass
        self.assertEqual(str(cm.exception.detail), 'broken')
        self.assertFalse(W.held(self.path))


if __name__ == '__main__':
    unittest.main()
