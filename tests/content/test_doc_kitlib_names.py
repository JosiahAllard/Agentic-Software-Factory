"""Every kitlib module, package or file a kit doc names exists (a dotted `kitlib.a.b` or a path
`kitlib/a/b.py`), so a move like W-001's (kitlib.proc.shield to kitlib/platform/windows) cannot
leave a doc naming the old place.

A dotted name resolves to a module file, a package, or a symbol defined in its package (a `def`,
`class` or `NAME =` line in any file under it, or an import in its `__init__.py`). Blind spots, on
purpose: a stale name that matches a symbol elsewhere in its package passes, and nothing after a
module file is checked (kitlib.lock.api.<stale> passes)."""
import os
import re
import unittest

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOTTED = re.compile(r'(?<![\w./])kitlib(?:\.[A-Za-z_]\w*)+')
PATHED = re.compile(r'(?<![\w.])kitlib/[\w./-]*\.py')

# (doc, name): why the name is not a module. A row whose name the doc no longer holds fails.
ALLOWED = {
    ('docs/gate-landing.md', 'kitlib.gate.land'):
        'the text of the landing tree lock reason (merge.TREE_LOCK), kept from before the move to kitlib.landing',
    ('docs/test-tools.md', 'kitlib.a.b'): 'placeholder module in the import-graph example',
    ('docs/test-tools.md', 'kitlib/a/b.py'): 'placeholder path in the import-graph example',
    ('docs/test-tools.md', 'kitlib.x'): 'placeholder for a module directly in kitlib/',
}


def doc_files(root=KIT_ROOT):
    """Repo-relative paths of the docs scanned: docs/**/*.md except docs/ops, ARCHITECTURE.md, README.md."""
    found = ['ARCHITECTURE.md', 'README.md']
    docs = os.path.join(root, 'docs')
    for folder, dirs, names in os.walk(docs):
        if folder == docs:
            dirs[:] = [d for d in dirs if d != 'ops']
        found += [os.path.relpath(os.path.join(folder, n), root).replace(os.sep, '/')
                  for n in names if n.endswith('.md')]
    return sorted(found)


def defined_in(package_dir, symbol):
    """True when `symbol` is defined by a file under the package, or imported by its __init__.py."""
    word = re.escape(symbol)
    made = re.compile(r'^\s*(?:def|class)\s+%s\b|^%s\s*[=:]' % (word, word), re.M)
    imported = re.compile(r'^\s*(?:from|import)\b.*\b%s\b' % word, re.M)
    for folder, _dirs, names in os.walk(package_dir):
        for name in names:
            if name.endswith('.py'):
                with open(os.path.join(folder, name), encoding='utf-8') as f:
                    text = f.read()
                if made.search(text) or (name == '__init__.py' and imported.search(text)):
                    return True
    return False


def resolves(name, root=KIT_ROOT):
    """Whether a dotted `kitlib.a.b` or a `kitlib/a/b.py` path names something in the tree."""
    if '/' in name:
        return os.path.isfile(os.path.join(root, name))
    here = root
    for i, part in enumerate(name.split('.')):
        if os.path.isdir(os.path.join(here, part)):
            here = os.path.join(here, part)
        elif os.path.isfile(os.path.join(here, part + '.py')):
            return True
        else:
            return i >= 2 and defined_in(here, part)
    return True


def names_in(root=KIT_ROOT):
    """[(doc, line number, name)] for every kitlib name in the scanned docs."""
    found = []
    for doc in doc_files(root):
        with open(os.path.join(root, doc), encoding='utf-8') as f:
            for number, line in enumerate(f, 1):
                found += [(doc, number, n) for n in DOTTED.findall(line) + PATHED.findall(line)]
    return found


class DocKitlibNames(unittest.TestCase):
    def test_every_name_a_doc_gives_resolves(self):
        misses = ['%s:%d %s' % (doc, number, name) for doc, number, name in names_in()
                  if (doc, name) not in ALLOWED and not resolves(name)]
        self.assertEqual([], misses)

    def test_the_checker_rejects_a_moved_module(self):
        for name in ('kitlib.proc.shield', 'kitlib/proc/shield.py', 'kitlib.nope.thing'):
            self.assertFalse(resolves(name), name)
        for name in ('kitlib.platform.windows.shield', 'kitlib/platform/windows/shield.py',
                     'kitlib.backlog.open_store', 'kitlib.proc'):
            self.assertTrue(resolves(name), name)

    def test_every_allowed_row_is_still_in_its_doc(self):
        held = {(doc, name) for doc, _number, name in names_in()}
        self.assertEqual([], sorted(row for row in ALLOWED if row not in held))


if __name__ == '__main__':
    unittest.main()
