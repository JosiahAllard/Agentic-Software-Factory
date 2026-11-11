"""The guard's import budget: every guarded hook call imports kitlib.guard afresh (hookrun.run),
so a module it pulls in but rarely uses costs every call. ctypes (with subprocess, through
kitlib.proc.job) and shutil (through tempfile, and kitlib.proc.bash's default argument) cost about
40 ms together (measured with -X importtime); they are imported where they
are used, never at import time."""
import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEPT_OUT = ('ctypes', 'shutil')


class ImportBudgetTests(unittest.TestCase):
    def test_importing_the_guard_leaves_ctypes_and_shutil_unloaded(self):
        code = ('import json, sys\n'
                'import kitlib.guard\n'
                'print(json.dumps(sorted(m for m in %r if m in sys.modules)))\n' % (KEPT_OUT,))
        p = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1]), [],
                         'importing kitlib.guard loaded these at import time')


if __name__ == '__main__':
    unittest.main()
