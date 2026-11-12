"""kitlib.pluginsource.layout's import budget: every guarded hook call imports it after the verdict
(kitlib.hookrun.headerfollow.line), so shutil, which only its rmtree helper uses and which pulls in
bz2, lzma and compression.zstd, is imported in that helper, never at import time; at the top it cost
about 17 ms of each hook call. The guard's own budget: tests/guard/test_import_budget.py."""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class LayoutImportTests(unittest.TestCase):
    def test_importing_layout_leaves_shutil_unloaded(self):
        code = 'import sys\nimport kitlib.pluginsource.layout\nprint("shutil" in sys.modules)\n'
        p = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1], 'False',
                         'importing kitlib.pluginsource.layout loaded shutil at import time')


if __name__ == '__main__':
    unittest.main()
