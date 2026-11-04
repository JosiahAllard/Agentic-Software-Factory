"""A bpy script for the art tools' smoke check: a two-legged rig with skinned legs and boots, an
LOD1 copy, a Bezier "walk" clip with one popped frame and a linear "idle" clip, exported as
<out>/walker.glb.

  python optional/comfy/blender_run.py tests/optional/comfy/fixtures/make_walker.py --out DIR
"""
import math
import os
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[argv.index("--out") + 1]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = 24

arm_data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", arm_data)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
hips = arm_data.edit_bones.new("hips")
hips.head, hips.tail = (0, 0, 1.0), (0, 0, 1.2)
for side, x in (("L", 0.2), ("R", -0.2)):
    thigh = arm_data.edit_bones.new(f"thigh.{side}")
    thigh.head, thigh.tail, thigh.parent = (x, 0, 1.0), (x, 0, 0.1), hips
    foot = arm_data.edit_bones.new(f"foot.{side}")
    foot.head, foot.tail, foot.parent = (x, 0, 0.1), (x, -0.2, 0.0), thigh
    foot.use_connect = True
bpy.ops.object.mode_set(mode="OBJECT")


def box(name, lo, hi, groups):
    """A box mesh from `lo` to `hi`, every vertex in vertex group groups[0]."""
    xs, ys, zs = zip(lo, hi)
    verts = [(x, y, z) for x in xs for y in ys for z in zs]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    ob = bpy.data.objects.new(name, me)
    scene.collection.objects.link(ob)
    g = ob.vertex_groups.new(name=groups)
    g.add(list(range(len(verts))), 1.0, "REPLACE")
    ob.parent = rig
    ob.modifiers.new("Armature", "ARMATURE").object = rig
    return ob


for lod, w in (("", 0.08), ("_LOD1", 0.08)):
    for side, x in (("L", 0.2), ("R", -0.2)):
        box(f"Leg.{side}{lod}", (x - w, -w, 0.1), (x + w, w, 1.0), f"thigh.{side}")
        # the boot's top face touches the leg's bottom face at rest (z = 0.1)
        box(f"Boot.{side}{lod}", (x - w, -0.22, 0.0), (x + w, w, 0.1), f"foot.{side}")

ad = rig.animation_data_create()


def key(bone, frame, angle=None, loc=None):
    pb = rig.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    if angle is not None:
        pb.rotation_euler = (math.radians(angle), 0, 0)
        pb.keyframe_insert("rotation_euler", frame=frame)
    if loc is not None:
        pb.location = loc
        pb.keyframe_insert("location", frame=frame)


def stash(name):
    act = ad.action
    act.name = name
    track = ad.nla_tracks.new()
    track.name = name
    track.strips.new(name, int(act.frame_range[0]), act)
    ad.action = None


for f in range(1, 25, 4):
    swing = 30 * math.sin(2 * math.pi * (f - 1) / 24)
    key("thigh.L", f, angle=swing)
    key("thigh.R", f, angle=-swing)
    key("hips", f, loc=(0, 0, 0.6 if f == 13 else 0))   # frame 13 pops the hips up
stash("walk")

bpy.context.preferences.edit.keyframe_new_interpolation_type = "LINEAR"
for f in (1, 12, 24):
    key("hips", f, loc=(0, 0, 0.02 * (f % 2)))
stash("idle")

os.makedirs(OUT, exist_ok=True)
bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, "walker.glb"), export_format="GLB",
                          export_animations=True, export_animation_mode="ACTIONS")
print("WROTE", os.path.join(OUT, "walker.glb"))
