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

    def test_failure_removes_temp_and_keeps_original(self):
        writers = (lambda: atomic.write_text(self.path, 'new'),
                   lambda: atomic.write_bytes(self.path, b'new'),
                   lambda: atomic.write_text(self.path, 'new', fsync=True))
        failing = (mock.patch('kitlib.fs.replace.replace', side_effect=OSError('boom')),
                   mock.patch('kitlib.fs.replace.replace', side_effect=OSError('boom')),
                   mock.patch('os.fsync', side_effect=OSError('disk')))
        for write, fail in zip(writers, failing):
            with open(self.path, 'w') as f:
                f.write('old')
            with fail, self.assertRaises(OSError):
                write()
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

    def test_write_bytes_exact_and_replaces(self):
        data = b'a' + bytes([13, 10]) + b'b' + bytes([0])
        with open(self.path, 'wb') as f:
            f.write(b'old')
        atomic.write_bytes(self.path, data)
        with open(self.path, 'rb') as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(os.listdir(self.dir), ['a.json'])

    def test_fsync_flushes_temp_before_replace(self):
        order = []
        real_replace = os.replace

        def replace(src, dst):
            order.append('replace')
            real_replace(src, dst)
        with mock.patch('os.fsync', side_effect=lambda fd: order.append('fsync')):
            with mock.patch('kitlib.fs.replace.replace', side_effect=replace):
                atomic.write_text(self.path, 'x', fsync=True)
                atomic.write_bytes(self.path, b'y', fsync=True)
        self.assertEqual(order, ['fsync', 'replace', 'fsync', 'replace'])

    def test_no_fsync_by_default(self):
        with mock.patch('os.fsync') as fs:
            atomic.write_text(self.path, 'x')
            atomic.write_bytes(self.path, b'x')
        fs.assert_not_called()

    def test_cleanup_goes_through_fsreplace_remove(self):
        with mock.patch('kitlib.fs.replace.replace', side_effect=OSError('boom')):
            with mock.patch('kitlib.fs.replace.remove') as rm:
                with self.assertRaises(OSError):
                    atomic.write_text(self.path, 'x')
        rm.assert_called_once()
        self.assertEqual(os.path.dirname(rm.call_args[0][0]), self.dir)

    def test_prefix_names_the_temp_file(self):
        seen = []

        def replace(src, dst):
            seen.append(os.path.basename(src))
            os.replace(src, dst)
        with mock.patch('kitlib.fs.replace.replace', side_effect=replace):
            atomic.write_text(self.path, 'x', prefix='.ledger-')
        self.assertTrue(seen[0].startswith('.ledger-'))


if __name__ == '__main__':
    unittest.main()
