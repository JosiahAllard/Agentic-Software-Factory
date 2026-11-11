"""kitlib.hubsite.hublog: the hub log file and the rotation the hub's log files share."""
import datetime
import os
import shutil
import tempfile
import unittest

from kitlib.hubsite import hublog

AT = datetime.datetime(2026, 11, 8, 4, 47, 5, 123456,
                       tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))


class Case(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='kit-hublog-')
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.path = os.path.join(self.folder, hublog.LOG_NAME)

    def read(self, path=None):
        with open(path or self.path, encoding='utf-8') as f:
            return f.read()


class AppendCase(Case):
    def test_the_file_moves_to_dot_1_once_it_reaches_the_cap_and_one_older_is_kept(self):
        for n in range(3):
            hublog.append(self.path, 'a%d' % n, 6)       # 'a0\na1\n' is 6 bytes: the cap
        self.assertEqual((self.read(), self.read(self.path + '.1')), ('a2\n', 'a0\na1\n'))
        for n in range(3, 6):
            hublog.append(self.path, 'a%d' % n, 6)
        # a3 fills the file to the cap; a4 moves it to .1, over the older one
        self.assertEqual((self.read(), self.read(self.path + '.1')), ('a4\na5\n', 'a2\na3\n'))
        self.assertEqual(sorted(os.listdir(self.folder)), [hublog.LOG_NAME, hublog.LOG_NAME + '.1'])

    def test_a_file_that_cannot_be_written_raises(self):
        with self.assertRaises(OSError):
            hublog.append(os.path.join(self.folder, 'missing', 'x.log'), 'line', 10)


class HubLogCase(Case):
    def test_a_line_is_utc_iso_then_the_level_then_the_text_on_one_line(self):
        log = hublog.HubLog(self.path, clock=lambda: AT)
        self.assertTrue(log.write('error', 'push to https://u:secret@host/r failed\nline two'))
        line = self.read()
        self.assertTrue(line.startswith('2026-11-08T09:47:05+00:00 ERROR push to '), line)
        self.assertNotIn('secret', line)
        self.assertEqual(line.count('\n'), 1)
        self.assertIn('failed\\nline two', line)

    def test_filed_writes_error_note_and_slow_and_hands_every_line_on(self):
        log = hublog.HubLog(self.path, clock=lambda: AT)
        seen = []
        for level in ('access', 'error', 'note', 'slow'):
            log.filed(level, level + ' text', out=lambda lv, t: seen.append((lv, t)))
        self.assertEqual([ln.split(' ', 1)[1] for ln in self.read().splitlines()],
                         ['ERROR error text', 'NOTE note text', 'SLOW slow text'])
        self.assertEqual([lv for lv, _ in seen], ['access', 'error', 'note', 'slow'])

    def test_a_log_that_cannot_be_written_loses_the_line_and_raises_nothing(self):
        log = hublog.HubLog(os.path.join(self.folder, 'missing', hublog.LOG_NAME))
        self.assertFalse(log.write('error', 'lost'))
        log.filed('error', 'lost too')

    def test_the_hub_log_rotates_at_its_cap(self):
        log = hublog.HubLog(self.path, clock=lambda: AT, cap=100)
        for n in range(10):
            log.write('note', 'line %d' % n)
        self.assertTrue(os.path.isfile(self.path + '.1'))
        self.assertLess(os.path.getsize(self.path), 100 + 50)
        self.assertTrue(self.read().endswith('NOTE line 9\n'))


if __name__ == '__main__':
    unittest.main()
