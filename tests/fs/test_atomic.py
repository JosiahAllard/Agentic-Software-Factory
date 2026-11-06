"""kitlib.fs.atomic: a whole-file write through a unique temp file, cleaned up on error."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.fs import atomic


class WriteTextTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 'a.json')

    def test_writes_lf_utf8_and_replaces(self):
        with open(self.path, 'w') as f:
            f.write('old')
        atomic.write_text(self.path, 'xé\ny\n')
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), 'xé\ny\n'.encode('utf-8'))
        self.assertEqual(os.listdir(self.dir), ['a.json'])

    def test_failed_replace_removes_temp_and_keeps_original(self):
        with open(self.path, 'w') as f:
            f.write('old')
        with mock.patch('kitlib.fs.replace.replace', side_effect=OSError('boom')):
            with self.assertRaises(OSError):
                atomic.write_text(self.path, 'new')
        self.assertEqual(os.listdir(self.dir), ['a.json'])
        with open(self.path) as f:
            self.assertEqual(f.read(), 'old')

    def test_each_write_uses_its_own_temp_name(self):
        seen = []

        def spy(src, dst):
            seen.append(src)
            os.replace(src, dst)
        with mock.patch('kitlib.fs.replace.replace', side_effect=spy):
            atomic.write_text(self.path, '1')
            atomic.write_text(self.path, '2')
        self.assertEqual(len(set(seen)), 2)
        self.assertTrue(all(os.path.dirname(s) == self.dir for s in seen))

    def test_makedirs(self):
        deep = os.path.join(self.dir, 'n', 'f.txt')
        with self.assertRaises(OSError):
            atomic.write_text(deep, 'x')
        atomic.write_text(deep, 'x', makedirs=True)
        with open(deep) as f:
            self.assertEqual(f.read(), 'x')


if __name__ == '__main__':
    unittest.main()
