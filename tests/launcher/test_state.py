"""kitlib.launcher.state: the launcher's state files are written whole and fsynced."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from kitlib.launcher import state


class WriteJsonTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, 'coord-restart.json')

    def test_the_temp_file_is_fsynced_before_it_replaces_the_file(self):
        order = []
        real = os.replace
        with mock.patch('os.fsync', side_effect=lambda fd: order.append('fsync')), \
                mock.patch('os.replace', side_effect=lambda a, b: order.append('replace') or real(a, b)):
            state.write_json(self.path, {'kit': {'seen_at': 1.0}})
        self.assertEqual(order, ['fsync', 'replace'])
        with open(self.path, encoding='utf-8') as f:
            self.assertEqual(json.load(f), {'kit': {'seen_at': 1.0}})


if __name__ == '__main__':
    unittest.main()
