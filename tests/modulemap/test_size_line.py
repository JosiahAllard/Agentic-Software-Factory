"""The size line: no kitlib .py file passes SIZE_LINE lines (ARCHITECTURE.md "Dependency rules" 7).

Lines are len(text.splitlines()), as in tests/guard/test_size_ceiling.py. EXEMPT lists the files
allowed past the line, each with its reason; an entry whose file is gone or no longer over the
line fails, so the list cannot go stale."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KITLIB = os.path.join(ROOT, 'kitlib')
SIZE_LINE = 1500
MIN_FILES = 100  # a walk finding fewer kitlib files is a wrong path or an empty scan, not a pass
EXEMPT = {
    'kitlib/git/fake/plumbing.py': 'the fake git: its size follows git plumbing, a fixed surface',
}


def counts():
    out = {}
    for folder, dirs, files in os.walk(KITLIB):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for name in files:
            if name.endswith('.py'):
                path = os.path.join(folder, name)
                with open(path, encoding='utf-8') as f:
                    out[os.path.relpath(path, ROOT).replace(os.sep, '/')] = len(f.read().splitlines())
    return out


class SizeLineTests(unittest.TestCase):
    def test_no_file_is_over_the_line_unless_exempt(self):
        found = counts()
        self.assertGreaterEqual(len(found), MIN_FILES, 'the scan found too few kitlib files')
        over = ['%s: %d lines' % (p, n) for p, n in sorted(found.items())
                if n > SIZE_LINE and p not in EXEMPT]
        self.assertEqual(over, [], 'split by responsibility, or list with a reason in EXEMPT')

    def test_every_exempt_file_exists_and_is_over_the_line(self):
        found = counts()
        stale = [p for p in EXEMPT if found.get(p, 0) <= SIZE_LINE]
        self.assertEqual(stale, [], 'an exempt file is gone or under the line: drop its entry')


if __name__ == '__main__':
    unittest.main()
