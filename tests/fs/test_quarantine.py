"""kitlib.fs.quarantine: a crash's zero-filled file told apart from a real one and set aside."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.fs import quarantine


class AllNulTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def file(self, data, name='f.json'):
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def test_only_a_non_empty_file_of_nul_bytes_is_all_nul(self):
        self.assertTrue(quarantine.all_nul(self.file(b'\0' * 300)))
        self.assertTrue(quarantine.all_nul(self.file(b'\0')))
        self.assertFalse(quarantine.all_nul(self.file(b'')))
        self.assertFalse(quarantine.all_nul(self.file(b'{"a": 1}')))
        self.assertFalse(quarantine.all_nul(self.file(b'{"a": 1' + b'\0' * 50)))

    def test_a_late_non_nul_byte_past_the_first_block_counts(self):
        data = b'\0' * (quarantine.BLOCK * 2 + 7) + b'x'
        self.assertFalse(quarantine.all_nul(self.file(data)))
        self.assertTrue(quarantine.all_nul(self.file(b'\0' * (quarantine.BLOCK * 2 + 7))))

    def test_a_missing_or_unreadable_path_is_not_all_nul(self):
        self.assertFalse(quarantine.all_nul(os.path.join(self.dir, 'gone.json')))
        self.assertFalse(quarantine.all_nul(self.dir))


class QuarantineTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def file(self, rel, data=b'\0\0'):
        path = os.path.join(self.root, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def test_moves_to_the_same_relative_path_under_corrupt(self):
        path = self.file('landings/L-0005.json')
        moved = quarantine.quarantine(path, self.root)
        self.assertEqual(moved, os.path.join(self.root, '_corrupt', 'landings', 'L-0005.json'))
        self.assertFalse(os.path.exists(path))
        with open(moved, 'rb') as f:
            self.assertEqual(f.read(), b'\0\0')

    def test_never_overwrites_an_earlier_copy(self):
        first = quarantine.quarantine(self.file('queue.json', b'\0'), self.root)
        second = quarantine.quarantine(self.file('queue.json', b'\0\0'), self.root)
        third = quarantine.quarantine(self.file('queue.json', b'\0\0\0'), self.root)
        self.assertEqual([os.path.basename(p) for p in (first, second, third)],
                         ['queue.json', 'queue.json.1', 'queue.json.2'])
        with open(first, 'rb') as f:
            self.assertEqual(f.read(), b'\0')
        with open(third, 'rb') as f:
            self.assertEqual(f.read(), b'\0\0\0')

    def test_a_failed_move_raises_and_keeps_the_file(self):
        path = self.file('active/r-1.json')
        with mock.patch('kitlib.fs.replace.place_new', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(OSError):
                quarantine.quarantine(path, self.root)
        self.assertTrue(os.path.exists(path))

    def test_listed_returns_store_relative_paths(self):
        self.assertEqual(quarantine.listed(self.root), [])
        quarantine.quarantine(self.file('landings/L-0001.json'), self.root)
        quarantine.quarantine(self.file('queue.json'), self.root)
        self.assertEqual(quarantine.listed(self.root), ['landings/L-0001.json', 'queue.json'])


if __name__ == '__main__':
    unittest.main()
