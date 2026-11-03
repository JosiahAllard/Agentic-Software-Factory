"""Render GLB files to a contact sheet with Blender (headless, Workbench, no GPU raytracing).

  python render_glb.py [--grid COLS [--view 3/4] | --turnaround | --filmstrip [--frames N]
                        [--clips A,B]] [--keep-tiles] OUT.png A.glb [B.glb ...]
  blender --background --python render_glb.py -- (the same arguments)

Run by plain python, it finds Blender as blender_run.py does (--blender is not taken here: set
KIT_BLENDER for another binary). Each model is normalised to the same height, with its triangle
count in the label. Used to eyeball generated 3D before anyone spends time cleaning it up.

Default: one row per model, one column per view (front, 3/4, side).
--grid COLS: one view per model (--view, default 3/4), COLS models per row, for comparing many
  candidates at once.
--turnaround: one row of 8 views per model, every 45 degrees from the front (az000 ... az315).
--filmstrip: one row per animation clip of each model (--clips picks them by name): N sampled
  frames (--frames, default 8) from a fixed camera (--view, default side) framed on the whole
  clip, then an onion-skin tile that overlays them, earlier frames fainter.
  --turnaround and --filmstrip write OUT only inside a git checkout (blender_run.py's --out rule).
--keep-tiles: also keep each tile as OUT_<model>_<view>.png (3/4 is written 3q; a filmstrip tile
  is OUT_<model>_<clip>_f<frame>.png, its overlay _onion) beside OUT, so
  compare_sheet.py can put them on a labelled page. Two models with the same file name get
  _2, _3 ... after the later ones' names so no tile overwrites another; a model named twice is
  rendered once.

The render takes a render-kind machine-lock slot itself: it re-runs Blender under
tools/machine_lock.py (KIT_MACHINE_LOCK overrides the path) and refuses, exit 2, if that is missing.

The sheet itself has no text on it; the TILE lines this prints give the labels in sheet order
(left to right, top to bottom).
"""
import argparse
import math
import os
import shutil
import subprocess
import sys

try:
    import bpy
    from mathutils import Vector
except ImportError:     # imported outside Blender (the tests): the pure parts below still work
    bpy = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blender_run as B  # noqa: E402

VIEWS = [("front", 0.0), ("3/4", 35.0), ("side", 90.0)]
TURNAROUND = [("az%03d" % d, float(d)) for d in range(0, 360, 45)]
TILE = 360


def script_args(argv):
    """The arguments after Blender's `--`."""
    return argv[argv.index("--") + 1:] if "--" in argv else []


def _positive(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="render_glb.py")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--grid", type=_positive, metavar="COLS",
                      help="one view per model, COLS models per row")
    mode.add_argument("--turnaround", action="store_true", help="8 views per model, every 45 degrees")
    mode.add_argument("--filmstrip", action="store_true",
                      help="per clip: sampled frames from a fixed camera, then an onion-skin overlay")
    ap.add_argument("--view", choices=[v for v, _ in VIEWS],
                    help="the view --grid (default 3/4) or --filmstrip (default side) renders")
    ap.add_argument("--frames", type=_positive, default=8, help="frames per --filmstrip clip (default 8)")
    ap.add_argument("--clips", help="--filmstrip: the clips (actions) to render, comma-separated (default all)")
    ap.add_argument("--keep-tiles", action="store_true",
                    help="keep each tile as OUT_<model>_<view>.png")
    ap.add_argument("out")
    ap.add_argument("files", nargs="+")
    opts = ap.parse_args(argv)
    if opts.view is None:
        opts.view = "side" if opts.filmstrip else "3/4"
    return opts


def out_refusal(opts):
    """Why OUT may not be written, or None. The turnaround and filmstrip modes, made for the art
    loop, write only inside a git checkout; the older modes keep their old freedom."""
    if opts.turnaround or opts.filmstrip:
        return B.out_refusal(os.path.dirname(os.path.abspath(opts.out)))
    return None


def frame_samples(start, end, n):
    """`n` whole frames spread evenly from `start` to `end` inclusive, without repeats."""
    start, end = int(round(start)), int(round(end))
    if n <= 1 or end <= start:
        return [start]
    out = []
    for i in range(n):
        f = start + int(round(i * (end - start) / (n - 1)))
        if f not in out:
            out.append(f)
    return out


def onion_skin(frames, bg, eps=0.02):
    """One flat RGBA float list overlaying `frames` (same-length flat RGBA lists, oldest first) on
    the background colour `bg` (r, g, b): where a frame's pixel differs from `bg` by more than `eps`
    in any channel it is blended in, weight rising from 0.25 for the first frame to 1 for the last,
    so the last frame is drawn whole and earlier ones show through fainter."""
    if not frames:
        return []
    n = len(frames)
    out = list(frames[0])
    for i in range(0, len(out), 4):
        out[i:i + 3] = bg
    for k, px in enumerate(frames):
        w = 1.0 if n == 1 else 0.25 + 0.75 * k / (n - 1)
        for i in range(0, len(px), 4):
            r, g, b = px[i], px[i + 1], px[i + 2]
            if abs(r - bg[0]) > eps or abs(g - bg[1]) > eps or abs(b - bg[2]) > eps:
                out[i] += (r - out[i]) * w
                out[i + 1] += (g - out[i + 1]) * w
                out[i + 2] += (b - out[i + 2]) * w
    return out


def unique_models(files):
    """`files` in order with repeats of the same path left out."""
    seen, out = set(), []
    for f in files:
        key = os.path.normcase(os.path.abspath(f))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def model_stems(files):
    """{file: the name its kept tiles use}: the file's stem, with _2, _3 ... added to a later file
    whose stem an earlier one already has."""
    stems, taken = {}, set()
    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        stem, n = base, 1
        while stem.lower() in taken:
            n += 1
            stem = f"{base}_{n}"
        taken.add(stem.lower())
        stems[f] = stem
    return stems


def plan(files, grid, view, turnaround=False):
    """`([(file, view label, degrees)] in sheet order, cols, rows)`."""
    if grid:
        deg = dict(VIEWS)[view]
        tiles = [(f, view, deg) for f in files]
        cols = min(grid, len(files))
        return tiles, cols, -(-len(files) // cols)
    views = TURNAROUND if turnaround else VIEWS
    tiles = [(f, label, deg) for f in files for label, deg in views]
    return tiles, len(views), len(files)


def dest_index(i, y, cols, rows, tile):
    """The pixel index in the sheet where row `y` of tile `i` starts (Blender stores images
    bottom row first, so the first sheet row is the top one)."""
    r, c = divmod(i, cols)
    return ((rows - 1 - r) * tile + y) * cols * tile + c * tile


def tile_path(out_path, i, model, view, keep, model_stem=None):
    folder = os.path.dirname(out_path)
    if not keep:
        return os.path.join(folder, f"_tile_{i}.png")
    stem = os.path.splitext(os.path.basename(out_path))[0]
    if model_stem is None:
        model_stem = os.path.splitext(os.path.basename(model))[0]
    return os.path.join(folder, f"{stem}_{model_stem}_{view.replace('3/4', '3q')}.png")


def label(model, view, tris):
    return f"{os.path.basename(model)} {view} {tris // 1000}k tris"


def safe_name(text):
    """`text` with anything but letters, digits, _ and - turned into -, for a tile file name."""
    return "".join(c if c.isalnum() or c in "_-" else "-" for c in text).strip("-") or "clip"


def _setup_scene():
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x = TILE
    scene.render.resolution_y = TILE
    scene.render.film_transparent = False
    return scene


# the kit's machine lock entry point: <plugin root>/tools/machine_lock.py, or KIT_MACHINE_LOCK to override
MACHINE_LOCK_TOOL = os.environ.get("KIT_MACHINE_LOCK") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "machine_lock.py")

RERUN_ENV = "RENDER_GLB_LOCKED"     # set only for the Blender run machine_lock_rerun starts

usable_python = B.usable_python     # one copy, in blender_run.py


def machine_lock_rerun(opts, argv, binary):
    """Run this script in Blender (`binary`) under the machine-wide lock (kind render), then exit
    with its code: from inside Blender (a bare `blender --python` run) or from plain python, where
    `binary` is the Blender blender_run.find_blender found. Returns without re-running only in that
    re-run itself (RERUN_ENV, consumed here so nothing it starts inherits it). Inside another locked
    run (MACHINE_LOCK_RUN) it still goes through the tool, which runs it directly when that run
    holds gpu and takes gpu when it holds only cpu. With no tool it refuses (exit 2) rather than
    render on a slot nobody holds. The command is blender_run.lock_cmd's, shared with blender_run.py."""
    if os.environ.pop(RERUN_ENV, None) and os.environ.get("MACHINE_LOCK_RUN"):
        return
    if not os.path.isfile(MACHINE_LOCK_TOOL):
        print(f"[machine-lock] refused: no machine lock tool at {MACHINE_LOCK_TOOL}, and a render "
              "takes a render slot. Install the kit's tools/machine_lock.py or set "
              "KIT_MACHINE_LOCK to it. Exit 2.", file=sys.stderr)
        sys.exit(2)
    # inside Blender sys.executable may be Blender itself, so the lock tool runs under a python on PATH
    found = shutil.which("python") or sys.executable
    python = usable_python(found, binary)
    if not python:
        print(f"[machine-lock] refused: no usable python to run the lock tool (got {found!r}: missing, "
              "a WindowsApps Store stub, or Blender itself). Put a real python first on PATH. Exit 2.",
              file=sys.stderr)
        sys.exit(2)
    cmd = B.lock_cmd(python, MACHINE_LOCK_TOOL, B.slug("render", opts.out),
                     B.blender_argv(binary, __file__, argv))
    sys.exit(subprocess.call(cmd, env=dict(os.environ, **{RERUN_ENV: "1"})))


def _refuse(why):
    print(f"REFUSED: {why}", file=sys.stderr)
    sys.exit(2)


def launch(argv):
    """Plain python: check the arguments, find Blender and run this script in it under the lock."""
    opts = parse_args(argv)
    why = out_refusal(opts)
    if why:
        _refuse(why)
    try:
        binary = B.find_blender()
    except B.Refused as e:
        _refuse(e)
    machine_lock_rerun(opts, argv, binary)


def _load_model(model):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = _setup_scene()
    bpy.ops.import_scene.gltf(filepath=model)
    meshes = [o for o in scene.objects if o.type == "MESH"]
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    return scene, meshes, tris


def _frame(pts):
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return (lo + hi) / 2, max(hi - lo)


def _camera(scene, size):
    cam_data = bpy.data.cameras.new("cam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = size * 1.15
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    return cam


def _aim(cam, centre, size, deg):
    a = math.radians(deg)
    d = size * 3
    cam.location = centre + Vector((d * math.sin(a), -d * math.cos(a), size * 0.25))
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()


def _render(scene, path):
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def render_views(opts, stems):
    """The default, --grid and --turnaround sheets: [(tile path, label)], cols, rows."""
    planned, cols, rows = plan(opts.files, opts.grid, opts.view, opts.turnaround)
    by_model = {}
    for model, view, deg in planned:
        by_model.setdefault(model, []).append((view, deg))
    tiles = []
    for model in opts.files:
        scene, meshes, tris = _load_model(model)
        centre, size = _frame([o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box])
        cam = _camera(scene, size)
        for view, deg in by_model[model]:
            _aim(cam, centre, size, deg)
            tile = tile_path(opts.out, len(tiles), model, view, opts.keep_tiles, stems[model])
            _render(scene, tile)
            tiles.append((tile, label(model, view, tris)))
    return tiles, cols, rows


def strip_tile_path(out_path, i, model_stem, clip, frame, keep):
    """A filmstrip tile's file: OUT_<model>_<clip>_f<frame>.png, or _onion for the overlay
    (frame None); a numbered scratch tile when the tiles are not kept."""
    folder = os.path.dirname(out_path)
    if not keep:
        return os.path.join(folder, f"_tile_{i}.png")
    stem = os.path.splitext(os.path.basename(out_path))[0]
    tail = "onion" if frame is None else "f%04d" % frame
    return os.path.join(folder, f"{stem}_{model_stem}_{safe_name(clip)}_{tail}.png")


def _save_pixels(name, pixels, path):
    img = bpy.data.images.new(name, TILE, TILE)
    img.pixels = pixels
    img.filepath_raw = path
    img.file_format = "PNG"
    img.save()


def render_filmstrips(opts, stems):
    """The --filmstrip sheet: one row per clip, --frames tiles then the onion skin."""
    wanted = [c.strip() for c in opts.clips.split(",") if c.strip()] if opts.clips else None
    deg = dict(VIEWS)[opts.view]
    tiles, rows, cols = [], 0, opts.frames + 1
    for model in opts.files:
        scene, meshes, tris = _load_model(model)
        actions = [a for a in bpy.data.actions if wanted is None or a.name in wanted]
        if not actions:
            print("NO CLIPS", model)
            continue
        targets = [o for o in scene.objects if o.animation_data or o.type == "ARMATURE"]
        cam = None
        for act in actions:
            B.play_action(targets, act)
            frames = frame_samples(act.frame_range[0], act.frame_range[1], opts.frames)
            pts = []
            for f in frames:
                scene.frame_set(f)
                dg = bpy.context.evaluated_depsgraph_get()
                for o in meshes:
                    ev = o.evaluated_get(dg)
                    pts += [ev.matrix_world @ Vector(c) for c in ev.bound_box]
            centre, size = _frame(pts)
            if cam is None:
                cam = _camera(scene, size)
            cam.data.ortho_scale = size * 1.15
            _aim(cam, centre, size, deg)
            row = []
            for f in frames:
                scene.frame_set(f)
                tile = strip_tile_path(opts.out, len(tiles) + len(row), stems[model], act.name, f,
                                       opts.keep_tiles)
                _render(scene, tile)
                row.append((tile, f"{os.path.basename(model)} {act.name} f{f} {tris // 1000}k tris"))
            shots = [list(bpy.data.images.load(t).pixels) for t, _ in row]
            onion = strip_tile_path(opts.out, len(tiles) + cols - 1, stems[model], act.name, None,
                                    opts.keep_tiles)
            _save_pixels("onion", onion_skin(shots, shots[0][:3]), onion)
            row += [(None, "")] * (cols - 1 - len(row))
            tiles += row + [(onion, f"{os.path.basename(model)} {act.name} onion {len(frames)} frames")]
            rows += 1
    if not rows:
        _refuse("no animation clips to render" + (f" named {opts.clips}" if wanted else ""))
    return tiles, cols, rows


def main(argv):
    opts = parse_args(argv)
    why = out_refusal(opts)
    if why:
        _refuse(why)
    machine_lock_rerun(opts, argv, bpy.app.binary_path)
    opts.files = unique_models(opts.files)
    stems = model_stems(opts.files)
    if opts.filmstrip:
        tiles, cols, rows = render_filmstrips(opts, stems)
    else:
        tiles, cols, rows = render_views(opts, stems)

    sheet = bpy.data.images.new("sheet", cols * TILE, rows * TILE)
    pixels = [0.15] * (cols * TILE * rows * TILE * 4)
    for i, (tile, _) in enumerate(tiles):
        if tile is None:
            continue
        img = bpy.data.images.load(tile)
        src = list(img.pixels)
        for y in range(TILE):
            at = dest_index(i, y, cols, rows, TILE)
            pixels[at * 4:(at + TILE) * 4] = src[y * TILE * 4:(y + 1) * TILE * 4]
        if not opts.keep_tiles:
            os.remove(tile)
    sheet.pixels = pixels
    sheet.filepath_raw = opts.out
    sheet.file_format = "PNG"
    sheet.save()
    for tile, text in tiles:
        if tile is not None:
            print("TILE", text + (f" -> {tile}" if opts.keep_tiles else ""))
    print("SHEET", opts.out)


if __name__ == "__main__":
    if bpy is not None:
        main(script_args(sys.argv))
    else:
        launch(sys.argv[1:])
