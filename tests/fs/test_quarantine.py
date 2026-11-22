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


class ReadJsonTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, 'state.json')

    def write(self, data):
        with open(self.path, 'wb') as f:
            f.write(data)

    def test_an_object_is_read_with_or_without_a_bom(self):
        for data in (b'{"a": 1}', b'\xef\xbb\xbf{"a": 1}'):
            self.write(data)
            self.assertEqual(quarantine.read_json(self.path, self.root), ({'a': 1}, None))

    def test_no_file_is_absent_and_moves_nothing(self):
        self.assertEqual(quarantine.read_json(self.path, self.root), (None, None))
        self.assertEqual(quarantine.listed(self.root), [])

    def test_a_file_that_is_not_a_json_object_is_set_aside_and_read_as_absent(self):
        for data in (b'\0' * 1585, b'{not json', b'[]', b'\xff\xfe{}'):
            self.write(data)
            doc, moved = quarantine.read_json(self.path, self.root)
            self.assertIsNone(doc)
            self.assertFalse(os.path.exists(self.path))
            with open(moved, 'rb') as f:
                self.assertEqual(f.read(), data)
        self.assertEqual(len(quarantine.listed(self.root)), 4)

    def test_retry_reads_a_non_nul_file_once_more_and_a_nul_file_never(self):
        sleeps = []

        def finish(s):                  # the write in flight lands during the wait
            sleeps.append(s)
            self.write(b'{"a": 2}')
        self.write(b'{"a": ')
        with mock.patch('time.sleep', side_effect=finish):
            self.assertEqual(quarantine.read_json(self.path, self.root, retry_s=0.5), ({'a': 2}, None))
            self.write(b'\0\0')
            self.assertIsNone(quarantine.read_json(self.path, self.root, retry_s=0.5)[0])
            self.write(b'{"a": ')
            self.assertIsNone(quarantine.read_json(self.path, self.root)[0])
        self.assertEqual(sleeps, [0.5])

    def test_a_refused_move_raises_set_aside_failed_and_keeps_the_file(self):
        self.write(b'\0\0')
        with mock.patch('kitlib.fs.replace.place_new', side_effect=PermissionError(13, 'denied')):
            with self.assertRaises(quarantine.SetAsideFailed) as cm:
                quarantine.read_json(self.path, self.root)
        self.assertIsInstance(cm.exception, OSError)
        self.assertTrue(cm.exception.why)
        self.assertTrue(os.path.exists(self.path))


if __name__ == '__main__':
    unittest.main()
