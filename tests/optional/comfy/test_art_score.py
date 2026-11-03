"""optional/comfy/art_score.py: the PNG reader and writer, the three numbers and the page.

usage: python -m unittest tests.optional.comfy.test_art_score -v
"""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CODE = os.path.join(HERE, '..', '..', '..', 'optional', 'comfy')
sys.path.insert(0, CODE)
import art_score as A  # noqa: E402


def chunk(kind, body):
    return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else (b if pb <= pc else c)


def png(w, h, rows, depth=8, ctype=2, filters=(0,), extra=b''):
    """A PNG of raw scanlines `rows` (bytes each), row y filtered with filters[y % len]."""
    n = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, n * depth // 8)
    raw, prev = b'', bytes(len(rows[0]))
    for y, line in enumerate(rows):
        f = filters[y % len(filters)]
        enc = bytearray()
        for i, v in enumerate(line):
            left = line[i - bpp] if i >= bpp else 0
            up_left = prev[i - bpp] if i >= bpp else 0
            pred = (0, left, prev[i], (left + prev[i]) >> 1, paeth(left, prev[i], up_left))[f]
            enc.append((v - pred) & 255)
        raw += bytes([f]) + bytes(enc)
        prev = line
    ihdr = struct.pack('>IIBBBBB', w, h, depth, ctype, 0, 0, 0)
    return (A.PNG_SIG + chunk(b'IHDR', ihdr) + extra + chunk(b'IDAT', zlib.compress(raw))
            + chunk(b'IEND', b''))


def solid(w, h, fn):
    """RGB rows from fn(x, y) -> (r, g, b)."""
    return [bytes(c for x in range(w) for c in fn(x, y)) for y in range(h)]


def write(path, w, h, fn):
    rows = solid(w, h, fn)
    with open(path, 'wb') as f:
        f.write(A.encode_png(w, h, b''.join(rows)))


class Png(unittest.TestCase):
    def test_every_filter_decodes_to_the_same_pixels(self):
        rows = solid(5, 5, lambda x, y: (x * 40, y * 50, (x * y * 13) % 256))
        want = A.decode_png(png(5, 5, rows))[2]
        for f in range(5):
            with self.subTest(filter=f):
                self.assertEqual(A.decode_png(png(5, 5, rows, filters=(f,)))[2], want)
        self.assertEqual(A.decode_png(png(5, 5, rows, filters=(0, 1, 2, 3, 4)))[2], want)
        self.assertEqual(tuple(want[(2 * 5 + 3) * 4:(2 * 5 + 3) * 4 + 4]), (120, 100, 78, 255))

    def test_the_writer_round_trips(self):
        w, h, rgba, alpha = A.decode_png(A.encode_png(4, 2, bytes(i % 256 for i in range(24))))
        self.assertEqual((w, h, alpha), (4, 2, False))
        self.assertEqual(bytes(rgba[0:4]), bytes((0, 1, 2, 255)))
        self.assertEqual(bytes(rgba[-4:]), bytes((21, 22, 23, 255)))

    def test_rgba_grey_alpha_and_sixteen_bit(self):
        rgba = A.decode_png(png(1, 1, [bytes((1, 2, 3, 4))], ctype=6))
        self.assertEqual((bytes(rgba[2]), rgba[3]), (bytes((1, 2, 3, 4)), True))
        ga = A.decode_png(png(1, 1, [bytes((9, 200))], ctype=4))
        self.assertEqual(bytes(ga[2]), bytes((9, 9, 9, 200)))
        deep = A.decode_png(png(1, 1, [bytes((0x12, 0x34, 0x56, 0x78, 0x9a, 0xbc))], depth=16))
        self.assertEqual(bytes(deep[2]), bytes((0x12, 0x56, 0x9a, 255)))

    def test_palette_with_transparency_and_low_bit_grey(self):
        plte = chunk(b'PLTE', bytes((255, 0, 0, 0, 0, 255))) + chunk(b'tRNS', bytes((0,)))
        w, h, rgba, alpha = A.decode_png(png(2, 1, [bytes((0b01000000,))], depth=2, ctype=3, extra=plte))
        # 0b01|00|...: index 1 (blue, opaque), then index 0 (red, alpha 0 from tRNS)
        self.assertEqual(bytes(rgba), bytes((0, 0, 255, 255, 255, 0, 0, 0)))
        self.assertTrue(alpha)
        grey = A.decode_png(png(4, 1, [bytes((0b00011011,))], depth=2, ctype=0))
        self.assertEqual([grey[2][i] for i in range(0, 16, 4)], [0, 85, 170, 255])

    def test_what_it_cannot_read_refuses(self):
        for data in (b'GIF89a....', png(1, 1, [b'\x00\x00\x00'])[:-30]):
            with self.assertRaises(A.ScoreError):
                A.decode_png(data)
        interlaced = bytearray(png(1, 1, [b'\x00\x00\x00']))
        interlaced[8 + 8 + 12] = 1          # IHDR's interlace byte
        with self.assertRaises(A.ScoreError):
            A.decode_png(bytes(interlaced))


class Colour(unittest.TestCase):
    # Sharma, Wu and Dalal's CIEDE2000 test data (pairs 1, 7, 13, 17 and 25)
    PAIRS = [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
             ((50, 0, 0), (50, -1, 2), 2.3669),
             ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
             ((50, 2.5, 0), (73, 25, -18), 27.1492),
             ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644)]

    def test_ciede2000_matches_the_published_pairs(self):
        for a, b, want in self.PAIRS:
            with self.subTest(a=a, b=b):
                self.assertAlmostEqual(A.delta_e(a, b), want, places=4)
                self.assertAlmostEqual(A.delta_e(b, a), want, places=4)

    def test_lab_of_white_black_and_red(self):
        for got, want in ((A.lab((255, 255, 255)), (100, 0, 0)), (A.lab((0, 0, 0)), (0, 0, 0)),
                          (A.lab((255, 0, 0)), (53.24, 80.09, 67.20))):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w, delta=0.05)

    def test_luminance(self):
        self.assertAlmostEqual(A.luminance(255, 255, 255), 1.0)
        self.assertEqual(A.luminance(0, 0, 0), 0.0)
        self.assertAlmostEqual(A.luminance(0, 255, 0), 0.7152)

    def test_palette_from_a_style_sheet_or_a_list(self):
        sheet = 'Sky #1E90FF (role: sky)\nGrass #228b22, again #1e90ff; not #12345 or #1234567'
        self.assertEqual(A.parse_palette(sheet), [(30, 144, 255), (34, 139, 34)])
        self.assertEqual(A.load_palette('ff0000, #00ff00'), [(255, 0, 0), (0, 255, 0)])
        with self.assertRaises(A.ScoreError):
            A.load_palette('red,green')

    def test_dominant_colours_biggest_first(self):
        px = [(250, 10, 10)] * 6 + [(10, 10, 250)] * 3 + [(10, 250, 10)]
        got = A.dominant_colours(px, 2)
        self.assertEqual([c for c, _ in got], [(250, 10, 10), (10, 10, 250)])
        self.assertEqual([s for _, s in got], [0.6, 0.3])

    def test_palette_distance_is_zero_on_the_palette_and_weighted(self):
        de, rows = A.palette_distance([((255, 0, 0), 0.5), ((0, 0, 255), 0.5)], [(255, 0, 0), (0, 0, 255)])
        self.assertAlmostEqual(de, 0.0)
        off = A.delta_e(A.lab((200, 0, 0)), A.lab((255, 0, 0)))
        de, rows = A.palette_distance([((255, 0, 0), 0.75), ((200, 0, 0), 0.25)], [(255, 0, 0)])
        self.assertAlmostEqual(de, off * 0.25 / 1.0)
        self.assertEqual(rows[1][3], (255, 0, 0))


def disc(cx, cy, r, fg, bg):
    return lambda x, y: fg if (x - cx) ** 2 + (y - cy) ** 2 <= r * r else bg


class Maps(unittest.TestCase):
    def img(self, w, h, fn):
        return A.Image(*A.decode_png(A.encode_png(w, h, b''.join(solid(w, h, fn)))))

    def test_iou_and_value_difference(self):
        self.assertEqual(A.iou([1, 1, 0, 0], [1, 0, 1, 0]), 1 / 3)
        self.assertEqual(A.iou([0, 0], [0, 0]), 1.0)
        self.assertAlmostEqual(A.value_difference([0.0, 1.0], [0.5, 0.5]), 0.5)

    def test_the_same_image_scores_perfectly(self):
        im = self.img(64, 64, disc(32, 32, 20, (200, 40, 40), (128, 128, 128)))
        got = A.score(im, im, [(200, 40, 40)])
        self.assertEqual((got['palette_de'], got['value_diff'], got['silhouette_iou']), (0.0, 0.0, 1.0))
        self.assertEqual(got['dominant'][0]['colour'], '#c82828')

    def test_a_shifted_silhouette_scores_its_overlap(self):
        a = self.img(64, 64, lambda x, y: (0, 0, 0) if x < 32 else (255, 255, 255))
        b = self.img(64, 64, lambda x, y: (0, 0, 0) if x < 16 or x >= 48 else (255, 255, 255))
        la, ma, _ = A.grid_maps(a)
        lb, mb, _ = A.grid_maps(b)
        self.assertAlmostEqual(A.value_difference(la, lb), 0.5)

    def test_the_mask_ignores_the_background_and_scales_to_the_grid(self):
        big = self.img(128, 128, disc(64, 64, 40, (10, 200, 10), (128, 128, 128)))
        small = self.img(64, 64, disc(32, 32, 20, (10, 200, 10), (128, 128, 128)))
        self.assertGreater(A.iou(A.grid_maps(big)[1], A.grid_maps(small)[1]), 0.95)
        off = self.img(64, 64, disc(16, 32, 12, (10, 200, 10), (128, 128, 128)))
        self.assertLess(A.iou(A.grid_maps(off)[1], A.grid_maps(small)[1]), 0.4)

    def test_alpha_makes_the_mask_when_the_image_has_transparency(self):
        rows = [bytes(c for x in range(4) for c in ((50, 50, 50, 255) if x < 2 else (50, 50, 50, 0)))
                for _ in range(4)]
        im = A.Image(*A.decode_png(png(4, 4, rows, ctype=6)))
        _, mask, _ = A.grid_maps(im, n=4)
        self.assertEqual(mask[:4], [True, True, False, False])

    def test_difference_image_is_doubled_luminance_at_the_render_size(self):
        a = self.img(8, 4, lambda x, y: (0, 0, 0))
        b = self.img(4, 2, lambda x, y: (255, 255, 255) if x == 0 else (0, 0, 0))
        w, h, rgb = A.difference_image(a, b)
        self.assertEqual((w, h), (4, 2))
        self.assertEqual(tuple(rgb[0:6]), (255, 255, 255, 0, 0, 0))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(os.path.join(self.repo, '.git'))
        self.concept = os.path.join(self.repo, 'concept.png')
        self.render = os.path.join(self.repo, 'render.png')
        write(self.concept, 32, 32, disc(16, 16, 10, (200, 40, 40), (128, 128, 128)))
        write(self.render, 32, 32, disc(17, 16, 10, (190, 50, 40), (128, 128, 128)))

    def run_main(self, *args):
        with mock.patch('sys.stdout', new_callable=io.StringIO) as out, \
                mock.patch('sys.stderr', new_callable=io.StringIO) as err:
            code = A.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_writes_the_scores_the_difference_and_the_page(self):
        out = os.path.join(self.repo, 'art', 'round1')
        code, text, _ = self.run_main(self.concept, self.render, '--palette', '#c82828,#808080',
                                      '--out', out)
        self.assertEqual(code, 0)
        self.assertIn('SCORE palette_de=', text)
        with open(os.path.join(out, 'render_score.json'), encoding='utf-8') as f:
            got = json.load(f)
        self.assertGreater(got['silhouette_iou'], 0.8)
        self.assertLess(got['palette_de'], 5)
        self.assertEqual(got['palette'], ['#c82828', '#808080'])
        with open(os.path.join(out, 'render_diff.png'), 'rb') as f:
            self.assertEqual(A.decode_png(f.read())[:2], (32, 32))
        with open(os.path.join(out, 'render_compare.html'), encoding='utf-8') as f:
            page = f.read()
        self.assertEqual(page.count('<figure>'), 3)
        self.assertIn('silhouette IoU', page)

    def test_palette_from_the_style_sheet_file(self):
        sheet = os.path.join(self.repo, 'style.md')
        with open(sheet, 'w', encoding='utf-8') as f:
            f.write('| body | #c82828 |\n')
        code, _, _ = self.run_main(self.concept, self.render, '--palette', sheet, '--out',
                                   os.path.join(self.repo, 'o'), '--name', 'r1')
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, 'o', 'r1_compare.html')))

    def test_out_outside_a_checkout_refuses_and_writes_nothing(self):
        out = os.path.join(self.tmp, 'scratch')
        code, _, err = self.run_main(self.concept, self.render, '--palette', '#ffffff', '--out', out)
        self.assertEqual(code, 2)
        self.assertIn('not inside a git checkout', err)
        self.assertFalse(os.path.exists(out))

    def test_a_name_that_leaves_out_refuses_and_writes_nothing(self):
        out = os.path.join(self.repo, 'art', 'o')
        for name in ('../../x', '..', 'a/b', 'a\\b', 'C:x'):
            with self.subTest(name=name):
                code, _, err = self.run_main(self.concept, self.render, '--palette', '#ffffff',
                                             '--out', out, '--name', name)
                self.assertEqual(code, 2)
                self.assertIn('plain file stem', err)
        self.assertFalse(os.path.exists(out))
        self.assertEqual(sorted(os.listdir(self.tmp)), ['repo'])

    def test_a_non_png_refuses(self):
        bad = os.path.join(self.repo, 'notes.png')
        with open(bad, 'w') as f:
            f.write('a prompt, not an image')
        code, _, err = self.run_main(self.concept, bad, '--palette', '#ffffff', '--out',
                                     os.path.join(self.repo, 'o'))
        self.assertEqual(code, 2)
        self.assertIn('not a PNG', err)


if __name__ == '__main__':
    unittest.main()
