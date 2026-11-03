#!/usr/bin/env python3
"""Measure a rigged, animated character in Blender: per clip and per LOD, the numbers a critic and
the owner judge motion by.

  python motion_check.py MODEL.glb|MODEL.blend --out DIR [--clips A,B] [--feet BONE,BONE]
                         [--step N] [--contact CM]

Run by plain python it finds Blender as blender_run.py does and runs itself there under the
machine lock (kind render); `blender --background --python motion_check.py -- ...` does the same.
DIR must be inside a git checkout; it gets <model>_motion.json, and each number is printed.

Per clip (each action; --clips picks them by name):
- foot slide, cm per contact: a contact is a run of 2 or more frames where a foot bone's head is
  within --contact cm (default 2) of its lowest point in the clip; its slide is the horizontal
  path the head travels during the run. The feet are the deforming bones named *foot* (not
  *ik*, *ctrl*, *target* or *pole*) unless --feet names them.
- jerk spikes: the third difference of each deforming bone head's world position per frame
  (m/s^3); a spike is a frame where a bone's jerk is over 5 times that bone's median and over
  1 m/s^3. The count, and the worst (bone, frame, value).
- linear keys: the share of key segments (a key and the one after it on the same curve) whose
  interpolation is LINEAR; a constant (stepped) segment counts as not linear. A clip baked to a
  key per frame reads as its baking, not its authoring.
- skin gap per LOD, cm: the worst opening, in any sampled pose (every --step frames, default 2),
  between vertices that touch at rest: vertices of one mesh at the same place (split seams), or
  of two meshes within --contact cm. Measured as the pose distance less the rest distance.
Per LOD: the triangles. Meshes used as a bone's display shape are left out. A mesh's LOD is the number after "LOD" in its name; a mesh without one
is LOD0.

Scene units are taken as metres times the scene's unit scale. Exit codes: 0 written, 2 refused.
"""
import argparse
import json
import math
import os
import re
import sys

try:
    import bpy
    from mathutils import Vector
except ImportError:     # outside Blender: the pure parts below still work, and main relaunches
    bpy = None

HERE = os.path.dirname(os.path.abspath(__file__))
if bpy is None:
    # The kit library, when this file runs from the kit (its import installs the kit's
    # CreateProcess hook before blender_run starts Blender). Never imported inside Blender.
    KIT_ROOT = os.path.dirname(os.path.dirname(HERE))
    if KIT_ROOT not in sys.path:
        sys.path.insert(0, KIT_ROOT)
    try:
        import kitlib  # noqa: F401,E402
    except ImportError:
        pass
sys.path.insert(0, HERE)
import blender_run as B  # noqa: E402

RERUN_ENV = "MOTION_CHECK_LOCKED"   # set only for the Blender run blender_run.ensure_locked starts
CONTACT_CM = 2.0
STEP = 2
SPIKE_FACTOR = 5.0
SPIKE_FLOOR = 1.0       # m/s^3
SEAM_EPS = 1e-5         # scene units: vertices of one mesh this close at rest are one seam
FOOT = re.compile(r"foot", re.I)
NOT_FOOT = re.compile(r"ik|ctrl|control|target|pole|mch", re.I)
LOD = re.compile(r"lod[ _.\-]?(\d+)", re.I)


# --- pure maths -------------------------------------------------------------------------------

def contacts(heights, tol, min_len=2):
    """[(first, last)] index runs of at least `min_len` where the height is within `tol` of the
    lowest height."""
    if not heights:
        return []
    ground = min(heights)
    runs, start = [], None
    for i, h in enumerate(heights + [float("inf")]):
        if h <= ground + tol:
            if start is None:
                start = i
        elif start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs


def horizontal_path(points, first, last):
    """The length of the XY path through points[first..last]."""
    return sum(math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
               for i in range(first + 1, last + 1))


def foot_slide(points, tol, unit_cm):
    """[{first, last, slide_cm}] for each contact of one foot (`points` [(x, y, z)] per frame, in
    scene units; `tol` in scene units; `unit_cm` cm per scene unit)."""
    runs = contacts([p[2] for p in points], tol)
    return [{"first": a, "last": b, "slide_cm": round(horizontal_path(points, a, b) * unit_cm, 2)}
            for a, b in runs]


def jerk(points, dt):
    """|third difference| / dt^3 of `points` [(x, y, z)] sampled every `dt` seconds: one value per
    window of four frames, the value at index i covering frames i..i+3."""
    out = []
    for i in range(len(points) - 3):
        j = [points[i + 3][k] - 3 * points[i + 2][k] + 3 * points[i + 1][k] - points[i][k] for k in range(3)]
        out.append(math.sqrt(sum(c * c for c in j)) / dt ** 3)
    return out


def spikes(values, factor=SPIKE_FACTOR, floor=SPIKE_FLOOR):
    """Indices of `values` over `factor` times their median and over `floor`."""
    if not values:
        return []
    s = sorted(values)
    median = s[len(s) // 2]
    limit = max(floor, factor * median)
    return [i for i, v in enumerate(values) if v > limit]


def linear_share(curves):
    """(share of LINEAR segments, segment count) over `curves`, each a list of the interpolation
    names of its keys in time order (a key's interpolation shapes the segment after it, so the
    last key of a curve leads none). (None, 0) when there are no segments."""
    segs = [interp for keys in curves for interp in keys[:-1]]
    if not segs:
        return None, 0
    return sum(1 for s in segs if s == "LINEAR") / len(segs), len(segs)


def _cells(points, size):
    grid = {}
    for i, p in enumerate(points):
        grid.setdefault(tuple(int(math.floor(c / size)) for c in p), []).append(i)
    return grid


def touching_pairs(points, owners, seam_eps, contact):
    """[(i, j, rest distance)] of points that touch at rest: of one owner within `seam_eps`, of two
    owners within `contact`. `owners` gives each point's mesh."""
    size = max(seam_eps, contact) or 1e-9
    grid = _cells(points, size)
    pairs = []
    for cell, members in grid.items():
        near = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    near += grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), [])
        for i in members:
            for j in near:
                if j <= i:
                    continue
                d = math.dist(points[i], points[j])
                if d <= (seam_eps if owners[i] == owners[j] else contact):
                    pairs.append((i, j, d))
    return pairs


def worst_gap(pairs, posed):
    """(the largest pose distance less rest distance over `pairs`, its pair) in scene units;
    (0.0, None) when no pair opens."""
    worst, which = 0.0, None
    for i, j, rest in pairs:
        g = math.dist(posed[i], posed[j]) - rest
        if g > worst:
            worst, which = g, (i, j)
    return worst, which


def lod_of(name):
    m = LOD.search(name)
    return "LOD%d" % int(m.group(1)) if m else "LOD0"


def triangles(polygon_sizes):
    return sum(n - 2 for n in polygon_sizes if n >= 3)


def foot_bones(names, deform):
    """The foot bones among `names` (deform: the set of deforming ones), deforming ones first."""
    feet = [n for n in names if FOOT.search(n) and not NOT_FOOT.search(n)]
    return [n for n in feet if n in deform] or feet


# --- Blender ----------------------------------------------------------------------------------

def parse_args(argv):
    ap = argparse.ArgumentParser(prog="motion_check.py")
    ap.add_argument("model", help="a .glb, .gltf or .blend file")
    ap.add_argument("--out", required=True, help="the folder for <model>_motion.json, in the worktree")
    ap.add_argument("--clips", help="the clips (actions) to measure, comma-separated (default all)")
    ap.add_argument("--feet", help="the foot bones, comma-separated (default: found by name)")
    ap.add_argument("--step", type=int, default=STEP, help="frames between sampled poses (default 2)")
    ap.add_argument("--contact", type=float, default=CONTACT_CM, help="contact distance, cm (default 2)")
    opts = ap.parse_args(argv)
    if opts.step < 1 or opts.contact <= 0:
        ap.error("--step must be 1 or more and --contact over 0")
    return opts


def _names(text):
    return [c.strip() for c in text.split(",") if c.strip()] if text else None


def action_curves(act):
    """The F-curves of an action, legacy or layered (Blender 4.4 and later)."""
    curves = []
    for layer in getattr(act, "layers", []) or []:
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []) or []:
                curves += list(bag.fcurves)
    if not curves and hasattr(act, "fcurves"):
        curves = list(act.fcurves)
    return curves


def _load(path):
    if path.lower().endswith(".blend"):
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=path)
    return bpy.context.scene


def _world_verts(objs, dg):
    pts, owners = [], []
    for k, o in enumerate(objs):
        ev = o.evaluated_get(dg)
        mesh = ev.to_mesh()
        m = ev.matrix_world
        pts += [tuple(m @ v.co) for v in mesh.vertices]
        owners += [k] * len(mesh.vertices)
        ev.to_mesh_clear()
    return pts, owners


def measure(opts):
    scene = _load(os.path.abspath(opts.model))
    unit_cm = (scene.unit_settings.scale_length or 1.0) * 100.0
    contact = opts.contact / unit_cm
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    arms = [o for o in scene.objects if o.type == "ARMATURE"]
    # bone display shapes (the glTF importer adds an Icosphere) are not the character
    shapes = {pb.custom_shape.name for a in arms for pb in a.pose.bones if pb.custom_shape}
    meshes = [o for o in scene.objects if o.type == "MESH" and o.name not in shapes]
    lods = {}
    for o in meshes:
        lods.setdefault(lod_of(o.name), []).append(o)
    report = {"model": opts.model, "fps": fps, "unit_cm": unit_cm, "lods": {}, "clips": []}
    for name, objs in sorted(lods.items()):
        report["lods"][name] = {"meshes": [o.name for o in objs],
                                "triangles": sum(triangles([len(p.vertices) for p in o.data.polygons])
                                                 for o in objs)}
    # rest contacts per LOD
    for a in arms:
        a.data.pose_position = "REST"
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    rest = {}
    for name, objs in lods.items():
        pts, owners = _world_verts(objs, dg)
        rest[name] = touching_pairs(pts, owners, SEAM_EPS, contact)
    for a in arms:
        a.data.pose_position = "POSE"
    wanted = _names(opts.clips)
    feet_given = _names(opts.feet)
    targets = [o for o in scene.objects if o.animation_data or o.type == "ARMATURE"]
    for act in bpy.data.actions:
        if wanted is not None and act.name not in wanted:
            continue
        B.play_action(targets, act)
        start, end = int(round(act.frame_range[0])), int(round(act.frame_range[1]))
        frames = list(range(start, end + 1))
        bones = {}      # "armature:bone" -> [(x, y, z)] per frame
        feet = []
        for a in arms:
            names = [b.name for b in a.pose.bones]
            deform = {b.name for b in a.data.bones if b.use_deform}
            for b in deform:
                bones[f"{a.name}:{b}"] = []
            for f in feet_given or foot_bones(names, deform):
                if f in names:
                    feet.append(f"{a.name}:{f}")
                    bones.setdefault(f"{a.name}:{f}", [])
        gaps = {name: (0.0, None) for name in lods}
        for f in frames:
            scene.frame_set(f)
            for a in arms:
                for b in a.pose.bones:
                    key = f"{a.name}:{b.name}"
                    if key in bones:
                        bones[key].append(tuple(a.matrix_world @ b.head))
            if (f - start) % opts.step == 0 or f == end:
                dg = bpy.context.evaluated_depsgraph_get()
                for name, objs in lods.items():
                    if rest[name]:
                        g, _ = worst_gap(rest[name], _world_verts(objs, dg)[0])
                        if g > gaps[name][0]:
                            gaps[name] = (g, f)
        slide = {k: foot_slide(bones[k], contact, unit_cm) for k in feet}
        worst_slide = max([c["slide_cm"] for cs in slide.values() for c in cs], default=0.0)
        n_spikes, worst_j = 0, None
        for key, pts in bones.items():
            js = jerk(pts, 1.0 / fps)
            hits = spikes(js)
            n_spikes += len(hits)
            for i in hits:
                if worst_j is None or js[i] > worst_j["value"]:
                    worst_j = {"bone": key, "frame": start + i, "value": round(js[i], 1)}
        share, segs = linear_share(
            [[k.interpolation for k in sorted(c.keyframe_points, key=lambda k: k.co[0])]
             for c in action_curves(act)])
        report["clips"].append({
            "clip": act.name, "frames": [start, end],
            "foot_slide_cm": slide, "foot_slide_worst_cm": worst_slide,
            "jerk_spikes": n_spikes, "jerk_worst": worst_j,
            "linear_key_share": None if share is None else round(share, 3), "key_segments": segs,
            "skin_gap_cm": {name: {"worst": round(g * unit_cm, 2), "frame": fr}
                            for name, (g, fr) in sorted(gaps.items())},
        })
    return report


def main(argv):
    opts = parse_args(argv)
    why = B.out_refusal(opts.out)
    if why:
        print(f"REFUSED: {why}", file=sys.stderr)
        sys.exit(2)
    if bpy is None:
        try:
            binary = B.find_blender()
        except B.Refused as e:
            print(f"REFUSED: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        binary = bpy.app.binary_path
    B.ensure_locked(__file__, argv, B.slug("motion", opts.model), RERUN_ENV, binary)
    report = measure(opts)
    out = os.path.abspath(opts.out)
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, os.path.splitext(os.path.basename(opts.model))[0] + "_motion.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    for name, lod in report["lods"].items():
        print(f"LOD {name} triangles={lod['triangles']} meshes={len(lod['meshes'])}")
    for c in report["clips"]:
        gap = " ".join(f"{k}={v['worst']}" for k, v in c["skin_gap_cm"].items())
        print(f"CLIP {c['clip']} frames={c['frames'][0]}-{c['frames'][1]} "
              f"foot_slide_worst_cm={c['foot_slide_worst_cm']} jerk_spikes={c['jerk_spikes']} "
              f"linear_key_share={c['linear_key_share']} skin_gap_cm {gap}")
    if not report["clips"]:
        print("NO CLIPS")
    print("REPORT", path)


if __name__ == "__main__":
    main(B.script_args(sys.argv) if bpy is not None else sys.argv[1:])
