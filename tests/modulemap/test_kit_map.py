"""The kit's own module map holds over this checkout (docs/module-map.md "The kit's own map").

Skipped while the owner's map file is absent: docs/module-map.json is owner-only. The map is
ARCHITECTURE.md's layer tables as data: each module's layer and Same layer names are read from
the tables here and must equal the map's.
"""
import os
import re
import unittest

from kitlib.modulemap import check as C
from kitlib.modulemap import load_ceilings, load_map

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP = os.path.join(KIT_ROOT, 'docs', 'module-map.json')
CEILINGS = os.path.join(KIT_ROOT, 'docs', 'module-ceilings.json')
PAGE = os.path.join(KIT_ROOT, 'docs', 'module-architecture.md')
ARCHITECTURE = os.path.join(KIT_ROOT, 'ARCHITECTURE.md')

HEADING = re.compile(r'^\*\*\d+\. (\w+)')       # "**3. Services** hold ..." -> services
NAMES = re.compile(r'`([^`]+)`')


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _same_layer(names, cell):
    """`{package: [same-layer names]}` for one row: '-' is none; "`a` uses `b`, `c`; ..." gives
    each named package its own list; any other text's names apply to every package in the row."""
    out = {n: [] for n in names}
    if cell == '-':
        return out
    if ' uses ' not in cell:
        return {n: NAMES.findall(cell) for n in names}
    for clause in cell.split(';'):
        user, used = clause.split(' uses ', 1)
        for n in NAMES.findall(user):
            out[n] = NAMES.findall(used)
    return out


def architecture_layers(text):
    """`{package: (layer, [same-layer names])}` from ARCHITECTURE.md's "Layers" section: each
    numbered heading starts a layer, named by its first word; each table row's Package cell names
    its packages."""
    out, layer, section = {}, None, False
    for line in text.splitlines():
        if line.startswith('## '):
            section = line.strip() == '## Layers'
            continue
        if not section:
            continue
        m = HEADING.match(line)
        if m:
            layer = m.group(1).lower()
            continue
        if not line.startswith('| `') or layer is None:
            continue
        cells = _cells(line)
        names = NAMES.findall(cells[0])
        for n, same in _same_layer(names, cells[2]).items():
            out[n] = (layer, same)
    return out


@unittest.skipUnless(os.path.isfile(MAP), 'docs/module-map.json (owner-only) is not in this checkout')
class KitMapTest(unittest.TestCase):
    def setUp(self):
        self.mm = load_map(MAP)
        self.ceilings = load_ceilings(CEILINGS)

    def test_no_crossing_is_refused(self):
        out = C.check(self.mm, self.ceilings, C.tree_files(KIT_ROOT), C.reader(KIT_ROOT))
        self.assertEqual(out.code, C.OK, '\n'.join(out.lines))

    def test_the_map_is_layered_and_holds_no_ratchet(self):
        # Loading a layered map refuses an edge that goes up, or across to a module the
        # source's row does not name, so every allowed edge is a down or a named one.
        self.assertTrue(self.mm.layers)
        self.assertEqual((self.mm.ratchets, self.mm.raises, self.ceilings), ([], [], {}))

    def test_each_modules_layer_and_same_layer_names_are_architecture_mds(self):
        with open(ARCHITECTURE, encoding='utf-8') as fh:
            rows = architecture_layers(fh.read())
        self.assertEqual(sorted(rows), sorted(self.mm.modules))
        self.assertEqual(self.mm.layers, list(dict.fromkeys(l for l, _ in rows.values())))
        for n in sorted(self.mm.modules):
            self.assertEqual((self.mm.layer[n], sorted(self.mm.same_layer[n])),
                             (rows[n][0], sorted(rows[n][1])), n)

    def test_the_architecture_page_matches_the_map(self):
        with open(PAGE, encoding='utf-8', newline='') as fh:
            current = fh.read()
        expected = C.render_page(KIT_ROOT, self.mm, MAP)
        self.assertEqual(current.replace('\r\n', '\n'), expected.replace('\r\n', '\n'))


if __name__ == '__main__':
    unittest.main()
