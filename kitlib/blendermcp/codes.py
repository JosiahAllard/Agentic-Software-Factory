"""The Python the bridge's tools send into Blender (docs/blender-mcp.md "Tools"). Each snippet sets
`result`, which the add-on sends back. Written for the kit; nothing here comes from the add-on.

inspect: a summary of the open file and its scene. render: a still to a PNG, with the scene's
render settings put back afterwards, so a render never changes what the agent built. stop: ends the
add-on's server, which ends a background Blender with exit code 0.
"""
from .addon import SERVER_MODULE

OBJECT_LIMIT = 500
ENGINES = {'workbench': ('BLENDER_WORKBENCH',), 'eevee': ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'),
           'cycles': ('CYCLES',)}

INSPECT = '''
import bpy
_sc = bpy.context.scene
_r = _sc.render


def _v(seq):
    return [round(float(x), 4) for x in seq]


_objs = []
for _o in list(_sc.objects)[:%(limit)d]:
    _d = {"name": _o.name, "type": _o.type, "location": _v(_o.location),
          "rotation": _v(_o.rotation_euler), "scale": _v(_o.scale), "dimensions": _v(_o.dimensions),
          "parent": _o.parent.name if _o.parent else None, "hide_render": _o.hide_render,
          "materials": [s.material.name for s in _o.material_slots if s.material],
          "modifiers": [m.type for m in _o.modifiers]}
    if _o.type == "MESH":
        _d["vertices"] = len(_o.data.vertices)
        _d["faces"] = len(_o.data.polygons)
    _objs.append(_d)
result = {"blender": bpy.app.version_string, "file": bpy.data.filepath, "scene": _sc.name,
          "frame": _sc.frame_current, "frame_range": [_sc.frame_start, _sc.frame_end],
          "camera": _sc.camera.name if _sc.camera else None, "engine": _r.engine,
          "resolution": [_r.resolution_x, _r.resolution_y, _r.resolution_percentage],
          "object_count": len(_sc.objects), "objects": _objs,
          "materials": [m.name for m in bpy.data.materials][:%(limit)d],
          "collections": [c.name for c in bpy.data.collections][:%(limit)d]}
''' % {'limit': OBJECT_LIMIT}

STOP = '''
import importlib
importlib.import_module(%r).stop()
result = {"stopping": True}
''' % SERVER_MODULE

_RENDER = '''
import os
import bpy
import mathutils

_want = %(engines)r
_have = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
_engine = next((e for e in _want if e in _have), None)
if _engine is None:
    raise RuntimeError("this Blender has none of the render engines %%s" %% (_want,))
_sc = bpy.context.scene
_r = _sc.render
_saved = (_r.engine, _r.resolution_x, _r.resolution_y, _r.resolution_percentage, _r.filepath,
          _r.image_settings.file_format, _sc.camera)
_tmp = None
try:
    _r.engine = _engine
    _r.resolution_x, _r.resolution_y, _r.resolution_percentage = %(width)d, %(height)d, 100
    _r.image_settings.file_format = "PNG"
    _r.filepath = %(path)r
    _named = %(camera)r
    if _named is not None:
        _cam = bpy.data.objects.get(_named)
        if _cam is None or _cam.type != "CAMERA":
            raise RuntimeError("no camera object named %%r" %% (_named,))
        _sc.camera = _cam
    if _sc.camera is None:
        _pts = [o.matrix_world @ mathutils.Vector(c) for o in _sc.objects
                if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"} and not o.hide_render
                for c in o.bound_box] or [mathutils.Vector((0.0, 0.0, 0.0))]
        _lo = mathutils.Vector([min(p[i] for p in _pts) for i in range(3)])
        _hi = mathutils.Vector([max(p[i] for p in _pts) for i in range(3)])
        _mid, _rad = (_lo + _hi) / 2, max((_hi - _lo).length / 2, 0.5)
        _data = bpy.data.cameras.new("pmkit_render_camera")
        _data.clip_end = max(1000.0, _rad * 20)
        _tmp = bpy.data.objects.new("pmkit_render_camera", _data)
        _sc.collection.objects.link(_tmp)
        _tmp.location = _mid + mathutils.Vector((1.0, -1.0, 0.7)).normalized() * _rad * 3.0
        _tmp.rotation_euler = (_mid - _tmp.location).to_track_quat("-Z", "Y").to_euler()
        _sc.camera = _tmp
    _used = _sc.camera.name
    bpy.ops.render.render(write_still=True)
    result = {"path": _r.filepath, "bytes": os.path.getsize(_r.filepath), "engine": _engine,
              "camera": None if _tmp is not None else _used, "framed_camera": _tmp is not None,
              "width": %(width)d, "height": %(height)d}
finally:
    (_r.engine, _r.resolution_x, _r.resolution_y, _r.resolution_percentage, _r.filepath,
     _r.image_settings.file_format, _sc.camera) = _saved
    if _tmp is not None:
        _data = _tmp.data
        bpy.data.objects.remove(_tmp)
        bpy.data.cameras.remove(_data)
'''


def render(path, width, height, engine='workbench', camera=None):
    """The snippet that renders the scene to the PNG at `path`, `width` x `height`, with `engine`
    (a key of ENGINES) from the object named `camera`, else the scene's camera, else a temporary
    camera that frames every renderable object."""
    if engine not in ENGINES:
        raise ValueError('engine must be one of %s' % ', '.join(sorted(ENGINES)))
    return _RENDER % {'engines': ENGINES[engine], 'width': int(width), 'height': int(height),
                      'path': str(path), 'camera': camera}
