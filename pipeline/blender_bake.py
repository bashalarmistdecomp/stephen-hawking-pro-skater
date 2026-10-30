"""Stage 1 of the Hawking skater build (headless Blender): the two Meshy GLBs -> one posed, static,
decimated model, written as data for build_mod.py.

  blender -b --python pipeline/blender_bake.py -- --root PROJECT --finish pipeline/finish.json --out build/bake

Replays pipeline/finish.json:
  professor: delete the stray objects and the box under his feet, pose him from an action frame,
             IK his right hand onto the joystick and his feet onto the footplate, bake the pose
  chair:     turn it to face -Y like him, scale it, put its seat under him, drop the tiny loose
             bits, weld its fragments
  both:      decimate to budget, smooth by angle, downscale the textures

Writes OUT/baked.json (per mesh: triangles' corner positions, normals and uvs, in metres, Blender
axes), OUT/<mesh>.rgba (top-down RGBA8 rows), OUT/hawking_review.glb and check renders
OUT/review_*.png with a 1.75 m reference box.
"""
import bmesh
import bpy
import json
import math
import os
import sys

import numpy as np
from mathutils import Euler, Matrix, Vector


def args():
    a = sys.argv[sys.argv.index('--') + 1:]
    out = {}
    for i in range(0, len(a), 2):
        out[a[i].lstrip('-')] = a[i + 1]
    return out


A = args()
ROOT = A['root']
CFG = json.load(open(A['finish']))
OUT = A['out']
os.makedirs(OUT, exist_ok=True)


def log(*m):
    print('[bake]', *m, flush=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(ROOT, path))
    return [o for o in bpy.data.objects if o not in before]


def tris(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def loose_parts(bm):
    """Lists of vertex indices, one per connected piece."""
    bm.verts.ensure_lookup_table()
    seen = [False] * len(bm.verts)
    parts = []
    for v in bm.verts:
        if seen[v.index]:
            continue
        seen[v.index] = True
        stack, comp = [v], []
        while stack:
            x = stack.pop()
            comp.append(x.index)
            for e in x.link_edges:
                y = e.other_vert(x)
                if not seen[y.index]:
                    seen[y.index] = True
                    stack.append(y)
        parts.append(comp)
    return parts


def delete_parts(obj, pred):
    """Delete the loose pieces of obj's mesh for which pred(list of coords) is true."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    parts = loose_parts(bm)
    gone = []
    for comp in parts:
        if pred([bm.verts[i].co for i in comp]):
            gone += comp
    n = len([1 for comp in parts if pred([bm.verts[i].co for i in comp])])
    bmesh.ops.delete(bm, geom=[bm.verts[i] for i in gone], context='VERTS')
    bm.to_mesh(obj.data)
    bm.free()
    return n, len(parts)


def weld(obj, dist):
    """Merge vertices closer than dist (Meshy splits its meshes into many touching pieces).
    Returns how many went."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    n = before - len(bm.verts)
    bm.to_mesh(obj.data)
    bm.free()
    return n


def evaluated_copy(obj, name):
    """A new static object holding obj's evaluated mesh (pose, modifiers) in world space."""
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(deps), depsgraph=deps)
    me.transform(obj.matrix_world)
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    return o


def chair_matrix():
    c = CFG['chair']
    R = Matrix.Rotation(math.radians(c['rotate_z_deg']), 4, 'Z')
    S = Matrix.Scale(c['scale'], 4)
    RS = R @ S
    T = Vector(c['seat_world']) - RS @ Vector(c['seat_point'])
    return Matrix.Translation(T) @ RS


def chair_to_world(p):
    return chair_matrix() @ Vector(p)


# ---- the professor ---------------------------------------------------------------------------

def build_professor():
    p = CFG['professor']
    new = import_glb(p['source'])
    for o in new:
        if o.name in p['delete_objects']:
            bpy.data.objects.remove(o)
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    body = next(o for o in bpy.data.objects if o.type == 'MESH' and o.parent == arm)
    log('professor: %d tris, %d bones' % (tris(body.data), len(arm.data.bones)))

    # the box under his feet (rest-pose mesh data)
    fb = p['delete_floor_box']
    n, of = delete_parts(body, lambda P: max(v.z for v in P) < fb['max_z'] and
                         max(max(v.x for v in P) - min(v.x for v in P),
                             max(v.y for v in P) - min(v.y for v in P)) > fb['min_span'])
    log('professor: deleted %d of %d loose parts as the floor box' % (n, of))
    if p.get('merge_distance'):
        log('professor: welded %d vertices' % weld(body, p['merge_distance']))

    # the action frame, frozen into the pose bones
    scene = bpy.context.scene
    arm.animation_data_create()
    arm.animation_data.action = bpy.data.actions[p['action']]
    scene.frame_set(p['frame'])
    basis = {pb.name: pb.matrix_basis.copy() for pb in arm.pose.bones}
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.matrix_basis = basis[pb.name]
    for name, deg in p.get('bone_rotate_deg', {}).items():
        pb = arm.pose.bones[name]
        pb.matrix_basis = pb.matrix_basis @ Euler([math.radians(d) for d in deg]).to_matrix().to_4x4()
    bpy.context.view_layer.update()

    # IK targets from the chair's landmarks
    for bone, ik in p.get('ik', {}).items():
        tgt = bpy.data.objects.new('IK_' + bone, None)
        scene.collection.objects.link(tgt)
        tgt.location = chair_to_world(ik['target_chair'])
        c = arm.pose.bones[bone].constraints.new('IK')
        c.target = tgt
        c.chain_count = ik.get('chain', 2)
        if 'pole_world' in ik:
            pole = bpy.data.objects.new('POLE_' + bone, None)
            scene.collection.objects.link(pole)
            pole.location = Vector(ik['pole_world'])
            c.pole_target = pole
            c.pole_angle = math.radians(ik.get('pole_angle_deg', 0))
        log('professor: IK %s -> world (%.3f, %.3f, %.3f)' % (bone, *tgt.location))

    # bones that keep their rest-pose world rotation (feet flat)
    for bone in p.get('keep_rest_rotation', []):
        rest = arm.matrix_world @ arm.data.bones[bone].matrix_local
        e = bpy.data.objects.new('REST_' + bone, None)
        scene.collection.objects.link(e)
        e.matrix_world = rest
        c = arm.pose.bones[bone].constraints.new('COPY_ROTATION')
        c.target = e
    bpy.context.view_layer.update()

    for bone, ik in p.get('ik', {}).items():
        pb = arm.pose.bones[bone]
        tip = arm.matrix_world @ pb.tail
        miss = (tip - chair_to_world(ik['target_chair'])).length
        log('professor: IK %s reaches within %.3f m' % (bone, miss))
    for bone in p.get('log_bones', []):
        h = arm.matrix_world @ arm.pose.bones[bone].head
        log('professor: bone %-10s at (%.3f, %.3f, %.3f)' % (bone, *h))

    prof = evaluated_copy(body, 'professor')
    prof.data.materials.clear()
    prof.data.materials.append(body.data.materials[0])
    for o in list(bpy.data.objects):
        if o is not prof:
            bpy.data.objects.remove(o)
    return prof


# ---- the chair -------------------------------------------------------------------------------

def build_chair():
    c = CFG['chair']
    new = import_glb(c['source'])
    src = next(o for o in new if o.type == 'MESH')
    log('chair: %d tris' % tris(src.data))
    small = c['delete_parts_smaller_than']
    n, of = delete_parts(src, lambda P: max(max(v[k] for v in P) - min(v[k] for v in P)
                                            for k in range(3)) < small)
    log('chair: deleted %d of %d loose parts smaller than %.3f m' % (n, of, small))
    log('chair: welded %d vertices' % weld(src, c['merge_distance']))
    src.data.transform(chair_matrix())
    src.name = 'chair'
    for o in new:
        if o is not src:
            bpy.data.objects.remove(o)
    return src


# ---- finishing -------------------------------------------------------------------------------

def decimate(obj, target):
    have = tris(obj.data)
    if have <= target:
        return
    m = obj.modifiers.new('dec', 'DECIMATE')
    m.decimate_type = 'COLLAPSE'
    m.ratio = target / have
    m.use_collapse_triangulate = True
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(deps), depsgraph=deps)
    obj.modifiers.remove(m)
    old = obj.data
    obj.data = me
    bpy.data.meshes.remove(old)
    log('%s: decimated %d -> %d tris' % (obj.name, have, tris(me)))


def smooth(obj, deg):
    me = obj.data
    if me.has_custom_normals:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
    for poly in me.polygons:
        poly.use_smooth = True
    me.set_sharp_from_angle(angle=math.radians(deg))


def texture_of(obj):
    for n in obj.data.materials[0].node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image:
            return n.image
    raise SystemExit('%s: no texture' % obj.name)


def write_texture(obj, size):
    img = texture_of(obj).copy()
    img.scale(size, size)
    px = np.empty(size * size * 4, np.float32)
    img.pixels.foreach_get(px)
    px = (np.clip(px, 0, 1) * 255 + 0.5).astype(np.uint8).reshape(size, size, 4)[::-1]
    path = os.path.join(OUT, obj.name + '.rgba')
    px.tofile(path)
    bpy.data.images.remove(img)
    return os.path.basename(path)


def mesh_record(obj, size):
    me = obj.data
    me.calc_loop_triangles()
    uv = me.uv_layers.active.data
    cn = me.corner_normals
    pos, nrm, uvs = [], [], []
    for t in me.loop_triangles:
        for li in t.loops:
            co = me.vertices[me.loops[li].vertex_index].co
            n = cn[li].vector
            u = uv[li].uv
            pos += [round(co.x, 5), round(co.y, 5), round(co.z, 5)]
            nrm += [round(n.x, 4), round(n.y, 4), round(n.z, 4)]
            uvs += [round(u.x, 5), round(1.0 - u.y, 5)]   # v from the image's top row
    return {'name': obj.name, 'texture': write_texture(obj, size), 'tex_w': size, 'tex_h': size,
            'positions': pos, 'normals': nrm, 'uvs': uvs, 'tris': len(me.loop_triangles),
            'unique_positions': len(me.vertices)}


def review(objs):
    """Check renders against a 1.75 m box, and a GLB of the result."""
    scene = bpy.context.scene
    pts = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    mn = Vector([min(p[k] for p in pts) for k in range(3)])
    mx = Vector([max(p[k] for p in pts) for k in range(3)])
    log('model bbox min (%.3f, %.3f, %.3f) max (%.3f, %.3f, %.3f)' % (*mn, *mx))
    # beside and behind the model, so no view looks through it
    bpy.ops.mesh.primitive_cube_add(size=1, location=(mx.x + 0.5, mx.y + 0.5, mn.z + 0.875))
    ref = bpy.context.active_object
    ref.scale = (0.25, 0.2, 1.75)
    ref.name = 'REF_175'
    with bpy.context.temp_override(selected_objects=objs, active_object=objs[0]):
        bpy.ops.export_scene.gltf(filepath=os.path.join(OUT, 'hawking_review.glb'), use_selection=True)
    scene.render.engine = 'BLENDER_WORKBENCH'
    scene.display.shading.light = 'STUDIO'
    scene.display.shading.color_type = 'TEXTURE'
    scene.render.resolution_x = scene.render.resolution_y = 800
    cd = bpy.data.cameras.new('cam')
    cd.type = 'ORTHO'
    cd.ortho_scale = max(mx.z - mn.z, mx.x - mn.x + 0.9, mx.y - mn.y) * 1.15
    cam = bpy.data.objects.new('cam', cd)
    scene.collection.objects.link(cam)
    scene.camera = cam
    c = (mn + mx) / 2
    c.x += 0.3
    views = {'front': (0, -1, 0.05), 'side': (1, 0, 0.05), 'right': (-1, 0, 0.05),
             'threequarter': (-0.8, -1, 0.6), 'top': (0, -0.15, 1), 'back': (0.4, 1, 0.3)}
    for name, d in views.items():
        d = Vector(d).normalized()
        cam.location = c + d * 8
        cam.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = os.path.join(OUT, 'review_%s.png' % name)
        bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(ref)
    # the launcher's icon: three-quarter view, no reference box, transparent background
    scene.render.film_transparent = True
    scene.render.resolution_x = scene.render.resolution_y = 256
    cd.ortho_scale = max(mx.z - mn.z, mx.x - mn.x, mx.y - mn.y) * 1.05
    d = Vector((-0.8, -1, 0.45)).normalized()
    cam.location = (mn + mx) / 2 + d * 8
    cam.rotation_euler = (-d).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = os.path.join(OUT, 'icon.png')
    bpy.ops.render.render(write_still=True)
    scene.render.film_transparent = False


def main():
    reset()
    prof = build_professor()
    chair = build_chair()
    for o, key in ((prof, 'professor'), (chair, 'chair')):
        decimate(o, CFG[key]['target_tris'])
        smooth(o, CFG[key]['smooth_angle_deg'])
    # where the model stands: the chair's lowest point, centred on its footprint
    cv = [chair.matrix_world @ v.co for v in chair.data.vertices]
    ground = min(v.z for v in cv)
    foot = [v for v in cv if v.z < ground + 0.08]
    anchor = [(min(v.x for v in foot) + max(v.x for v in foot)) / 2,
              (min(v.y for v in foot) + max(v.y for v in foot)) / 2, ground]
    log('anchor (ground under the wheels) (%.3f, %.3f, %.3f)' % tuple(anchor))
    size = CFG['texture_size']
    rec = {'anchor': anchor, 'meshes': [mesh_record(prof, size), mesh_record(chair, size)]}
    json.dump(rec, open(os.path.join(OUT, 'baked.json'), 'w'))
    for m in rec['meshes']:
        log('%s: %d tris, %d positions, texture %s' % (m['name'], m['tris'], m['unique_positions'],
                                                        m['texture']))
    review([prof, chair])
    log('done')


main()
