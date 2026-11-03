#!/usr/bin/env python3
"""Score a render against its concept image: three measured numbers and a side-by-side page.

  python art_score.py CONCEPT.png RENDER.png --palette STYLE.md|#RRGGBB,... --out DIR
                      [--name STEM] [--threshold N]

- palette_de: the mean CIEDE2000 colour difference between the render's dominant colours (its
  foreground, quantised to 16 levels per channel, the biggest bins, as many as the palette has
  entries) and the nearest palette entry, weighted by how much of the foreground each covers.
  0 is a perfect match; about 2 is just noticeable, above 10 a clearly different colour.
- value_diff: the mean absolute difference of relative luminance (0 black to 1 white, from
  linear sRGB) between the two images, each averaged onto a 64x64 grid. 0 is identical.
- silhouette_iou: the intersection over union of the two foreground masks on that grid, 0 to 1.
  A pixel is foreground when its alpha is under 128 nowhere in the image and it differs from the
  image's background (the per-channel median of its border pixels) by more than --threshold
  (default 32 of 255) in some channel; in an image with transparency, when its alpha is 128 or more.

Both images are stretched onto the grid, so render from the concept's camera at its aspect ratio.
--palette is a file (every #RRGGBB in it is an entry: the style sheet) or a comma-separated list.

DIR must be inside a git checkout (blender_run.py's --out rule). It gets <STEM>_diff.png (the
luminance difference, doubled, at the render's size up to 512 px), <STEM>_score.json and
<STEM>_compare.html: concept, render and difference side by side, built by compare_sheet.py. STEM
defaults to the render's file name. Reads 8- and 16-bit PNG of any colour type, not interlaced.

Exit codes: 0 written, 2 refused.
"""
import argparse
import json
import math
import os
import re
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
# The kit library, when this file runs from the kit (its import installs the kit's CreateProcess
# hook; this tool starts nothing itself). Copied out of the kit, it is left out.
KIT_ROOT = os.path.dirname(os.path.dirname(HERE))
if KIT_ROOT not in sys.path:
    sys.path.insert(0, KIT_ROOT)
try:
    import kitlib  # noqa: F401,E402
except ImportError:
    pass
sys.path.insert(0, HERE)
import blender_run  # noqa: E402
import compare_sheet  # noqa: E402

GRID = 64
THRESHOLD = 32
PNG_SIG = b"\x89PNG\r\n\x1a\n"


class ScoreError(Exception):
    """Why the inputs cannot be scored."""


# --- PNG --------------------------------------------------------------------------------------

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(raw, height, stride, bpp):
    """The image bytes from zlib-inflated scanlines, each led by its filter type byte."""
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if len(line) != stride:
            raise ScoreError("PNG data ends early")
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 255
        elif ftype != 0:
            raise ScoreError(f"unknown PNG filter {ftype}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def decode_png(data):
    """(width, height, RGBA bytes, has alpha) from PNG file bytes; ScoreError when it is not one
    this reads (bit depth 8 or 16, or 1-4 for grey and palette images; no interlace)."""
    if not data.startswith(PNG_SIG):
        raise ScoreError("not a PNG file")
    pos, chunks, idat = 8, {}, []
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"IDAT":
            idat.append(body)
        elif ctype == b"IEND":
            break
        else:
            chunks.setdefault(ctype, body)
    if b"IHDR" not in chunks:
        raise ScoreError("PNG has no IHDR")
    w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"])
    if ctype not in CHANNELS or interlace:
        raise ScoreError("interlaced PNG" if interlace else f"PNG colour type {ctype}")
    if depth not in (1, 2, 4, 8, 16) or (depth < 8 and ctype not in (0, 3)) or (depth == 16 and ctype == 3):
        raise ScoreError(f"PNG bit depth {depth} with colour type {ctype}")
    n = CHANNELS[ctype]
    bits = n * depth
    stride = (w * bits + 7) // 8
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ScoreError(f"PNG data does not inflate: {e}")
    img = _unfilter(raw, h, stride, max(1, bits // 8))
    if depth == 16:
        img = img[0::2]                     # the high byte of each sample
        stride = w * n
    elif depth < 8:
        img, stride = _unpack(img, h, w, stride, depth, ctype == 0), w
    trns = chunks.get(b"tRNS")
    rgba = bytearray(w * h * 4)
    alpha = ctype in (4, 6) or trns is not None
    if ctype == 3:
        plte = chunks.get(b"PLTE") or b""
        table = [tuple(plte[i:i + 3]) + (trns[i // 3] if trns and i // 3 < len(trns) else 255,)
                 for i in range(0, len(plte) - 2, 3)]
        for y in range(h):
            row = img[y * stride:y * stride + w]
            for x, k in enumerate(row):
                rgba[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes(table[k] if k < len(table) else (0, 0, 0, 255))
        return w, h, rgba, alpha
    key = None
    if trns is not None and depth == 8:
        key = tuple(trns[1::2]) if ctype in (0, 2) else None
    for y in range(h):
        row = img[y * stride:y * stride + w * n]
        for x in range(w):
            s = row[x * n:x * n + n]
            if n == 1:
                px = (s[0], s[0], s[0], 255)
            elif n == 2:
                px = (s[0], s[0], s[0], s[1])
            elif n == 3:
                px = (s[0], s[1], s[2], 255)
            else:
                px = tuple(s)
            if key is not None and tuple(s[:n]) == key[:n]:
                px = px[:3] + (0,)
            rgba[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes(px)
    return w, h, rgba, alpha


def _unpack(img, h, w, stride, depth, grey):
    """Sub-byte samples spread to one byte each; grey ones scaled to 0-255."""
    out = bytearray(w * h)
    per, mask = 8 // depth, (1 << depth) - 1
    scale = 255 // mask if grey else 1
    for y in range(h):
        for x in range(w):
            byte = img[y * stride + x // per]
            shift = 8 - depth * (x % per + 1)
            out[y * w + x] = ((byte >> shift) & mask) * scale
    return out


def encode_png(w, h, rgb):
    """PNG file bytes of an 8-bit RGB image (`rgb`: w*h*3 bytes, top row first)."""
    raw = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))

    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (PNG_SIG + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class Image:
    def __init__(self, w, h, rgba, alpha):
        self.w, self.h, self.rgba, self.alpha = w, h, rgba, alpha

    def px(self, x, y):
        i = (y * self.w + x) * 4
        return self.rgba[i], self.rgba[i + 1], self.rgba[i + 2], self.rgba[i + 3]


def read_image(path):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise ScoreError(f"cannot read {path}: {e.strerror or e}")
    try:
        return Image(*decode_png(data))
    except ScoreError as e:
        raise ScoreError(f"{path}: {e}")


# --- colour -----------------------------------------------------------------------------------

def _lin(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


LINEAR = [_lin(float(i)) for i in range(256)]


def luminance(r, g, b):
    """Relative luminance, 0-1, of an 8-bit sRGB colour."""
    return 0.2126 * LINEAR[r] + 0.7152 * LINEAR[g] + 0.0722 * LINEAR[b]


def lab(rgb):
    """CIE L*a*b* (D65) of an sRGB colour given as 0-255 numbers (not only whole ones)."""
    r, g, b = (_lin(float(c)) for c in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(lab1, lab2):
    """CIEDE2000 colour difference of two L*a*b* colours."""
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    c1, c2 = math.hypot(a1, b1), math.hypot(a2, b2)
    cm = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(cm ** 7 / (cm ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if c2p else 0.0
    dl, dc = L2 - L1, c2p - c1p
    if c1p * c2p == 0:
        dh = 0.0
    else:
        dh = h2p - h1p
        if dh > 180:
            dh -= 360
        elif dh < -180:
            dh += 360
    dH = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh) / 2)
    lm, cmp_ = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hm = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hm = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hm = (h1p + h2p + 360) / 2
    else:
        hm = (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hm - 30)) + 0.24 * math.cos(math.radians(2 * hm))
         + 0.32 * math.cos(math.radians(3 * hm + 6)) - 0.20 * math.cos(math.radians(4 * hm - 63)))
    d_theta = 30 * math.exp(-(((hm - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cmp_ ** 7 / (cmp_ ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (lm - 50) ** 2 / math.sqrt(20 + (lm - 50) ** 2)
    sc = 1 + 0.045 * cmp_
    sh = 1 + 0.015 * cmp_ * t
    rt = -math.sin(math.radians(2 * d_theta)) * rc
    return math.sqrt((dl / sl) ** 2 + (dc / sc) ** 2 + (dH / sh) ** 2 + rt * (dc / sc) * (dH / sh))


HEX = re.compile(r"#([0-9A-Fa-f]{6})\b")


def parse_palette(text):
    """[(r, g, b)] of every #RRGGBB in `text`, first appearance order, repeats left out."""
    out = []
    for m in HEX.finditer(text):
        h = m.group(1)
        rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        if rgb not in out:
            out.append(rgb)
    return out


def load_palette(arg):
    if os.path.isfile(arg):
        with open(arg, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = " ".join("#" + p.strip().lstrip("#") for p in arg.split(","))
    pal = parse_palette(text)
    if not pal:
        raise ScoreError(f"no #RRGGBB palette entry in {arg}")
    return pal


def dominant_colours(pixels, k):
    """The `k` biggest colour bins of `pixels` [(r, g, b)], 16 levels per channel:
    [(mean colour, share of pixels)], biggest first."""
    bins = {}
    for r, g, b in pixels:
        key = (r >> 4, g >> 4, b >> 4)
        s = bins.get(key)
        if s is None:
            bins[key] = [r, g, b, 1]
        else:
            s[0] += r
            s[1] += g
            s[2] += b
            s[3] += 1
    total = len(pixels)
    top = sorted(bins.values(), key=lambda s: -s[3])[:k]
    return [((s[0] / s[3], s[1] / s[3], s[2] / s[3]), s[3] / total) for s in top]


def palette_distance(dominant, palette):
    """(weighted mean ΔE, [(colour, share, ΔE to the nearest entry, that entry)]) of the dominant
    colours against the palette; the weights are the shares, renormalised over those given."""
    labs = [(p, lab(p)) for p in palette]
    rows, total, weight = [], 0.0, 0.0
    for colour, share in dominant:
        lc = lab(colour)
        de, entry = min((delta_e(lc, lp), p) for p, lp in labs)
        rows.append((colour, share, de, entry))
        total += de * share
        weight += share
    return (total / weight if weight else 0.0), rows


# --- masks and maps ---------------------------------------------------------------------------

def _median(values):
    s = sorted(values)
    return s[len(s) // 2]


def background(img):
    """The per-channel median of the border pixels."""
    border = [img.px(x, 0) for x in range(img.w)] + [img.px(x, img.h - 1) for x in range(img.w)]
    border += [img.px(0, y) for y in range(img.h)] + [img.px(img.w - 1, y) for y in range(img.h)]
    return tuple(_median([p[c] for p in border]) for c in range(3))


def is_foreground(px, bg, threshold, use_alpha):
    if use_alpha:
        return px[3] >= 128
    return max(abs(px[0] - bg[0]), abs(px[1] - bg[1]), abs(px[2] - bg[2])) > threshold


def grid_maps(img, n=GRID, threshold=THRESHOLD):
    """(n*n mean luminance, n*n foreground mask as bools, [(r, g, b)] of foreground pixels sampled
    to at most about 65k), the grid cells row-major from the top. A transparent pixel counts as
    mid-grey for luminance."""
    use_alpha = img.alpha and any(img.rgba[i] < 128 for i in range(3, len(img.rgba), 4))
    bg = background(img)
    lum = [0.0] * (n * n)
    fg = [0] * (n * n)
    count = [0] * (n * n)
    step = max(1, int(math.sqrt(img.w * img.h / 65536)))
    sample = []
    grey = luminance(128, 128, 128)
    for y in range(img.h):
        cy = y * n // img.h
        for x in range(img.w):
            i = cy * n + x * n // img.w
            px = img.px(x, y)
            fore = is_foreground(px, bg, threshold, use_alpha)
            a = px[3] / 255.0
            lum[i] += luminance(px[0], px[1], px[2]) * a + grey * (1 - a)
            count[i] += 1
            if fore:
                fg[i] += 1
                if x % step == 0 and y % step == 0:
                    sample.append(px[:3])
    lum = [lum[i] / count[i] if count[i] else grey for i in range(n * n)]
    mask = [count[i] > 0 and fg[i] * 2 >= count[i] for i in range(n * n)]
    return lum, mask, sample


def value_difference(lum_a, lum_b):
    return sum(abs(a - b) for a, b in zip(lum_a, lum_b)) / len(lum_a)


def iou(mask_a, mask_b):
    """Intersection over union of two masks; 1.0 when both are empty."""
    inter = sum(1 for a, b in zip(mask_a, mask_b) if a and b)
    union = sum(1 for a, b in zip(mask_a, mask_b) if a or b)
    return inter / union if union else 1.0


def difference_image(concept, render, limit=512):
    """(w, h, RGB bytes): |luminance difference| x2 as grey, at the render's size scaled to fit
    `limit`, each image sampled by nearest pixel."""
    scale = min(1.0, limit / max(render.w, render.h))
    w, h = max(1, int(render.w * scale)), max(1, int(render.h * scale))
    out = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            a = concept.px(x * concept.w // w, y * concept.h // h)
            b = render.px(x * render.w // w, y * render.h // h)
            d = abs(luminance(*a[:3]) - luminance(*b[:3]))
            v = min(255, int(d * 2 * 255 + 0.5))
            out[(y * w + x) * 3:(y * w + x) * 3 + 3] = bytes((v, v, v))
    return w, h, out


def score(concept, render, palette, threshold=THRESHOLD):
    """The three numbers and their detail, as a dict."""
    lum_c, mask_c, _ = grid_maps(concept, GRID, threshold)
    lum_r, mask_r, sample = grid_maps(render, GRID, threshold)
    if not sample:      # no foreground found: score the whole render's colours
        step = max(1, int(math.sqrt(render.w * render.h / 65536)))
        sample = [render.px(x, y)[:3] for y in range(0, render.h, step) for x in range(0, render.w, step)]
    de, rows = palette_distance(dominant_colours(sample, len(palette)), palette)
    return {
        "palette_de": round(de, 2),
        "value_diff": round(value_difference(lum_c, lum_r), 4),
        "silhouette_iou": round(iou(mask_c, mask_r), 4),
        "grid": GRID,
        "threshold": threshold,
        "dominant": [{"colour": "#%02x%02x%02x" % tuple(int(round(c)) for c in colour),
                      "share": round(share, 4), "de": round(d, 2),
                      "nearest": "#%02x%02x%02x" % entry} for colour, share, d, entry in rows],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="art_score.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("concept")
    ap.add_argument("render")
    ap.add_argument("--palette", required=True, help="a style sheet file, or #RRGGBB,#RRGGBB,...")
    ap.add_argument("--out", required=True, help="the folder to write into, inside the worktree")
    ap.add_argument("--name", help="the stem of the files written (default: the render's name)")
    ap.add_argument("--threshold", type=int, default=THRESHOLD,
                    help="foreground: a channel this far from the background (default 32)")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    out = os.path.abspath(args.out)
    stem = args.name or os.path.splitext(os.path.basename(args.render))[0]
    diff_path = os.path.join(out, stem + "_diff.png")
    html_path = os.path.join(out, stem + "_compare.html")
    try:
        if not stem or ".." in stem or any(c in stem for c in "/\\:"):
            raise ScoreError(f"--name {stem!r} must be a plain file stem: no folder separator, "
                             "drive colon or '..', so every file stays in --out")
        why = blender_run.out_refusal(out) or compare_sheet.out_refusal(
            html_path, [("concept", args.concept), ("render", args.render)])
        if why:
            raise ScoreError(why)
        palette = load_palette(args.palette)
        concept, render = read_image(args.concept), read_image(args.render)
        result = score(concept, render, palette, args.threshold)
    except ScoreError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    os.makedirs(out, exist_ok=True)
    w, h, rgb = difference_image(concept, render)
    with open(diff_path, "wb") as f:
        f.write(encode_png(w, h, rgb))
    result.update(concept=args.concept, render=args.render, palette=["#%02x%02x%02x" % p for p in palette])
    with open(os.path.join(out, stem + "_score.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    title = "%s: palette dE %.2f, value diff %.3f, silhouette IoU %.3f" % (
        stem, result["palette_de"], result["value_diff"], result["silhouette_iou"])
    tiles = compare_sheet.load([("concept", args.concept), ("render", args.render),
                                ("difference", diff_path)])
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(compare_sheet.render(tiles, 3, title))
    print("SCORE palette_de=%.2f value_diff=%.4f silhouette_iou=%.4f" % (
        result["palette_de"], result["value_diff"], result["silhouette_iou"]))
    print("SHEET", html_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
