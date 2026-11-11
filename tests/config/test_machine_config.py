"""kitlib.config.pm.machine_config: the whole file as a dict, {} for a missing file, ConfigError for a
non-object or unparsable one."""
import json
import os
import tempfile
import unittest

from kitlib.config import ConfigError
from kitlib.config import pm as pmconfig


class MachineConfigTest(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config.json")

    def write(self, text, encoding="utf-8"):
        with open(self.path, "w", encoding=encoding) as f:
            f.write(text)

    def test_missing_file_gives_empty_dict(self):
        self.assertEqual(pmconfig.machine_config(self.path), {})

    def test_a_file_that_cannot_be_used_is_a_config_error_naming_it(self):
        for text in ("[1]", "{not json"):
            with self.subTest(text):
                self.write(text)
                with self.assertRaises(ConfigError) as cm:
                    pmconfig.machine_config(self.path)
                self.assertIn(self.path, str(cm.exception))

    def test_reads_the_whole_file_with_a_bom(self):
        data = {"launcher": {"accounts": {"2": ["~/.claude"]}}, "pm_start": {"hub": {}}}
        self.write(json.dumps(data), encoding="utf-8-sig")
        self.assertEqual(pmconfig.machine_config(self.path), data)


if __name__ == "__main__":
    unittest.main()
