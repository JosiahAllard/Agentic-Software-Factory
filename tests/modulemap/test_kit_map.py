"""The kit's own module map holds over this checkout (docs/module-map.md "The kit's own map").

Skipped while the owner's map file is absent: docs/module-map.json is owner-only.
"""
import os
import unittest

from kitlib.modulemap import check as C
from kitlib.modulemap import load_ceilings, load_map

KIT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAP = os.path.join(KIT_ROOT, 'docs', 'module-map.json')
CEILINGS = os.path.join(KIT_ROOT, 'docs', 'module-ceilings.json')
PAGE = os.path.join(KIT_ROOT, 'docs', 'module-architecture.md')


@unittest.skipUnless(os.path.isfile(MAP), 'docs/module-map.json (owner-only) is not in this checkout')
class KitMapTest(unittest.TestCase):
    def setUp(self):
        self.mm = load_map(MAP)
        self.ceilings = load_ceilings(CEILINGS)

    def test_no_crossing_is_refused_and_no_ratchet_is_over_its_ceiling(self):
        out = C.check(self.mm, self.ceilings, C.tree_files(KIT_ROOT), C.reader(KIT_ROOT))
        self.assertEqual(out.code, C.OK, '\n'.join(l for l in out.lines if not l.startswith('ratchet')))

    def test_every_ratchet_has_a_ceiling_and_every_ceiling_a_ratchet(self):
        ids = sorted(r['id'] for r in self.mm.ratchets)
        self.assertEqual(ids, sorted(self.ceilings))

    def test_the_architecture_page_matches_the_map(self):
        with open(PAGE, encoding='utf-8', newline='') as fh:
            current = fh.read()
        expected = C.render_page(KIT_ROOT, self.mm, MAP)
        self.assertEqual(current.replace('\r\n', '\n'), expected.replace('\r\n', '\n'))


if __name__ == '__main__':
    unittest.main()
