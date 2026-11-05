"""A guard over tests/launcher/*.py: a launcher test that asserts PowerShell window text must run on
Linux too, so each class that holds a `$env:` or `Set-Location` literal is pinned to Windows (a
tests.platforms windows_only / unittest skip marker) or runs under fakes.windows_text or a
kitlib.platform.NAME patch, in itself or a base class; and every open_window override takes **kw
(Linux passes keyword arguments to it). A class or a base counts as pinned when any of its code
holds the marker or the patch; this guard does not tell which method it covers."""
import ast
import glob
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
POWERSHELL = ('$env:', 'Set-Location')
MARKS = ('windows_only', 'skipIf', 'skipUnless')


def parse():
    """{file name: ast} for each launcher test module but this one."""
    out = {}
    for path in sorted(glob.glob(os.path.join(HERE, '*.py'))):
        name = os.path.basename(path)
        if name == os.path.basename(__file__).replace('.pyc', '.py'):
            continue
        with open(path, encoding='utf-8') as f:
            out[name] = ast.parse(f.read(), path)
    return out


def classes(trees):
    """[(file, ClassDef)] over every class, nested ones included."""
    return [(n, c) for n, t in trees.items() for c in ast.walk(t) if isinstance(c, ast.ClassDef)]


def name_of(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call):
        return name_of(node.func)
    return None


def has_powershell(cls):
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str)
               and any(p in n.value for p in POWERSHELL) for n in ast.walk(cls))


def pins_itself(cls):
    for n in ast.walk(cls):
        if isinstance(n, ast.Call) and name_of(n) == 'windows_text':
            return True
        if isinstance(n, ast.Call) and name_of(n) in ('patch', 'object') \
                and any(isinstance(a, ast.Constant) and a.value == 'kitlib.platform.NAME' for a in n.args):
            return True
        if isinstance(n, (ast.ClassDef, ast.FunctionDef)):
            for d in n.decorator_list:
                if name_of(d) in MARKS:
                    return True
    return False


def pinned(cls, by_name, seen=()):
    if pins_itself(cls):
        return True
    for b in cls.bases:
        for base in by_name.get(name_of(b), ()):
            if base is not cls and base not in seen and pinned(base, by_name, seen + (cls,)):
                return True
    return False


class PlatformMarks(unittest.TestCase):
    def test_a_class_asserting_powershell_text_is_pinned_to_windows(self):
        found = classes(parse())
        by_name = {}
        for _, c in found:
            by_name.setdefault(c.name, []).append(c)
        loose = ['%s:%d %s' % (f, c.lineno, c.name) for f, c in found
                 if has_powershell(c) and not pinned(c, by_name)]
        self.assertEqual(loose, [], 'asserts `$env:` / `Set-Location` text with no windows_only mark, '
                                    'windows_text call or kitlib.platform.NAME patch')

    def test_the_scan_sees_the_launcher_classes(self):
        found = classes(parse())
        self.assertGreater(len([c for _, c in found if has_powershell(c)]), 5)

    def test_every_open_window_override_takes_kw(self):
        bad = ['%s:%d' % (f, fn.lineno) for f, t in parse().items() for fn in ast.walk(t)
               if isinstance(fn, ast.FunctionDef) and fn.name == 'open_window' and fn.args.kwarg is None]
        self.assertEqual(bad, [], 'an open_window override without **kw (Linux passes keywords)')


if __name__ == '__main__':
    unittest.main()
