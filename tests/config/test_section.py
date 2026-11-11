"""kitlib.config.pm.section: the one reader of a machine config key, and its one failure meaning
(docs/modules/config-design.md "Errors"). The three readers built on it give the same answers."""
import json
import os
import tempfile
import unittest

from kitlib import coordinatorstart, pmwatch
from kitlib.config import ConfigError
from kitlib.config import pm as pmconfig


class SectionTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = os.path.join(d.name, 'config.json')

    def put(self, text, mode='w'):
        with open(self.path, mode, **({} if 'b' in mode else {'encoding': 'utf-8'})) as f:
            f.write(text)

    def test_the_value_of_the_key_is_its_dict(self):
        self.put(json.dumps({'k': {'a': 1}, 'other': 2}))
        self.assertEqual(pmconfig.section('k', self.path), {'a': 1})

    def test_a_bom_file_is_read(self):
        self.put(b'\xef\xbb\xbf' + json.dumps({'k': {'a': 1}}).encode(), 'wb')
        self.assertEqual(pmconfig.section('k', self.path), {'a': 1})

    def test_a_missing_file_a_missing_key_and_a_value_that_is_no_object_are_not_configured(self):
        self.assertIsNone(pmconfig.section('k', self.path))
        self.put(json.dumps({'other': {}}))
        self.assertIsNone(pmconfig.section('k', self.path))
        self.put(json.dumps({'k': ['not', 'an', 'object']}))
        self.assertIsNone(pmconfig.section('k', self.path))

    def test_a_file_that_cannot_be_used_is_a_config_error_naming_it(self):
        for text in ('{bad', '[1, 2]', ''):
            with self.subTest(text):
                self.put(text)
                with self.assertRaises(ConfigError) as cm:
                    pmconfig.section('k', self.path)
                self.assertIn(self.path, str(cm.exception))

    def test_a_file_that_cannot_be_read_is_a_config_error(self):
        os.makedirs(self.path)          # an OSError that is not FileNotFoundError
        for read in (lambda: pmconfig.section('k', self.path), lambda: pmconfig.machine_config(self.path)):
            with self.assertRaises(ConfigError) as cm:
                read()
            self.assertIn(self.path, str(cm.exception))

    def test_the_three_readers_follow_it(self):
        self.put('{bad')
        for read in (pmconfig.load_config, coordinatorstart.load_config, pmwatch.load_config,
                     pmwatch.configured_notice_dir):
            with self.subTest(read.__module__ + '.' + read.__name__):
                with self.assertRaises(ConfigError):
                    read(self.path)
        os.remove(self.path)
        self.assertEqual((pmconfig.load_config(self.path), coordinatorstart.load_config(self.path),
                          pmwatch.load_config(self.path), pmwatch.configured_notice_dir(self.path)),
                         (None, None, {}, None))


if __name__ == '__main__':
    unittest.main()
