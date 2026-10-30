"""Stage 2 of the Hawking skater build (plain Python 3): the baked model -> a THPS1 PC-port mod.

  python3 pipeline/build_mod.py --root PROJECT --finish pipeline/finish.json --bake assets/baked \
      --data GAME_DATA_DIR --out build/mod [--install MODS_DIR]

Writes OUT/<mod folder>/ (mod.ini, Skaters/<model>.ini, Data/Models/<model>.psx). The .psx is a
version 6 (Dreamcast-layout) skater file, which the port loads with its textures drawn by OpenGL
(see TonyWasAProSkater/docs/dreamcast-assets.md and docs/modding.md):

  - the mesh is spread over the skater's parts (at most 256 vertices each: faces index them with
    a byte), every part carrying the same transform: the chair and its rider move as one rigid
    thing;
  - the animations are the base skater's (GAME_DATA/Models/hawk.psx, read at build time, never
    copied into the repo), rewritten so every part follows one anchor per frame: the board
    while riding (flips, manuals and grinds move the whole chair), the pelvis in the bails;
  - faces are lit, textured triangles with 16-bit texture coordinates (512 = one repeat) on two
    RGB565 textures, one per source model.
"""
import argparse
import json
import math
import os
import shutil
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import thps_anim as A  # noqa: E402

HIER = 0x52454948      # 'HIER'
ANIM44 = 0x2C
VERSION_DC = 0x20006   # version 6 (Dreamcast faces and textures); 0x20000 as in the game's skaters
FACE_TRI = 0x1F        # textured (1|2), lit by the vertices (4|8), triangle (0x10)
FACE_WORDS = 11        # flags, verts, colour, normal, texture, u0u1, u2u3, v0v1, v2v3, 0, 0
COLOUR_TRI = 0x24808080


def log(*m):
    print('[mod]', *m, flush=True)


# ---- reading the base skater -------------------------------------------------------------------

def read_psx(path):
    d = open(path, 'rb').read()
    version, pk, nobj = struct.unpack_from('<3I', d, 0)
    nmod = struct.unpack_from('<I', d, 12 + 36 * nobj)[0]
    packets = {}
    q = pk
    while struct.unpack_from('<I', d, q)[0] != 0xFFFFFFFF:
        t, s = struct.unpack_from('<2I', d, q)
        packets[t] = d[q + 8:q + 8 + s]
        q += 8 + s
    return nmod, packets


# ---- the animations ----------------------------------------------------------------------------

def mat_mul(a, b):
    return tuple(tuple(sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)) for r in range(3))


def transpose(m):
    return tuple(tuple(m[c][r] for c in range(3)) for r in range(3))


def euler_yxz(m):
    """Angles (4096ths of a turn) whose RotMatrixYXZ is m (M = Ry * Rx * Rz)."""
    sx = max(-1.0, min(1.0, -m[1][2]))
    x = math.asin(sx)
    if abs(math.cos(x)) > 1e-6:
        y = math.atan2(m[0][2], m[2][2])
        z = math.atan2(m[1][0], m[1][1])
    else:  # gimbal lock: put it all in y
        y = math.atan2(-m[2][0], m[0][0])
        z = 0.0
    q = lambda a: int(round(a * 4096 / (2 * math.pi))) & 0xFFF
    s = lambda v: v - 0x1000 if v >= 0x800 else v
    return s(q(x)), s(q(y)), s(q(z))


def anchored_anims(base_anims, hier, num_models, root, pelvis_anims, rest_anim, board=0, pelvis=3):
    """Every part on one anchor per frame. Rotations in the anim data are absolute and a child's
    translation is its parent's plus its pivot turned by the parent's rotation, so: every part
    the anchor's Euler angles, the root's pivot the anchor's translation, the others' pivots 0."""
    r0 = A.frame_transforms(base_anims[rest_anim][0], hier)
    (rb, tb), (rp, tp) = r0[board], r0[pelvis]
    rel_r = mat_mul(transpose(rp), rb)
    rel_t = A.mat_vec(transpose(rp), tuple(tb[k] - tp[k] for k in range(3)))
    worst = 0.0
    out = []
    for a, frames in enumerate(base_anims):
        nf = []
        for fr in frames:
            T = A.frame_transforms(fr, hier)
            if a in pelvis_anims:
                r, t = T[pelvis]
                m = mat_mul(r, rel_r)
                e = euler_yxz(m)
                d = A.mat_vec(r, rel_t)
                t = tuple(t[k] + d[k] for k in range(3))
                back = A.rot_yxz(*e)
                worst = max(worst, max(abs(back[i][j] - m[i][j]) for i in range(3) for j in range(3)))
            else:
                e = fr[board][:3]
                t = T[board][1]
            t = tuple(max(-32768, min(32767, int(round(v)))) for v in t)
            nf.append([(e[0], e[1], e[2]) + (t if m_ == root else (0, 0, 0))
                       for m_ in range(num_models)])
        out.append(nf)
    log('anims: %d, pelvis-anchored %s, worst Euler round trip %.4f' % (len(out), sorted(pelvis_anims), worst))
    return out


def turned(frames, root, yaw_deg):
    """An anchored anim turned about the vertical (the front end's view of the riding pose)."""
    a = math.radians(yaw_deg)
    ry = ((math.cos(a), 0.0, math.sin(a)), (0.0, 1.0, 0.0), (-math.sin(a), 0.0, math.cos(a)))
    out = []
    for fr in frames:
        e = euler_yxz(mat_mul(ry, A.rot_yxz(*fr[root][:3])))
        t = tuple(int(round(v)) for v in A.mat_vec(ry, fr[root][3:]))
        out.append([e + (t if m == root else (0, 0, 0)) for m in range(len(fr))])
    return out


# ---- the mesh ----------------------------------------------------------------------------------

def to_game(baked, g):
    """Corners in game units (board part space, y down), triangles wound as the game's faces are
    (cross(v1 - v0, v2 - v0) points inward). The model faces -Y in Blender; it goes to the game's
    z axis on the side g['front_z'] (-1: the skater rides towards -z), turned about the vertical,
    never mirrored."""
    U, gy, s = g['units_per_metre'], g['ground_y'], g['front_z']
    ax, ay, az = baked['anchor']
    corners, faces = [], []
    for tex, m in enumerate(baked['meshes']):
        P, N, UV = m['positions'], m['normals'], m['uvs']
        for t in range(m['tris']):
            ids = []
            for c in (0, 2, 1):   # Blender's counter-clockwise -> the game's order
                i = 3 * t + c
                X, Y, Z = P[3 * i:3 * i + 3]
                nx, ny, nz = N[3 * i:3 * i + 3]
                pos = (int(round(-s * (X - ax) * U)), int(round(-(Z - az) * U + gy)),
                       int(round(-s * (Y - ay) * U)))
                ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                nrm = tuple(int(round(v / ln * 4096)) for v in (-s * nx, -nz, -s * ny))
                uv = (int(round(UV[2 * i] * 512)) & 0xFFFF, int(round(UV[2 * i + 1] * 512)) & 0xFFFF)
                ids.append(len(corners))
                corners.append((pos, nrm, uv))
            faces.append((ids, tex))
    return corners, faces


def morton(p, lo, scale):
    """Interleaved bits of a point's grid cell: nearby points sort near each other."""
    code = 0
    cell = [max(0, min(1023, int((p[k] - lo[k]) * scale))) for k in range(3)]
    for b in range(10):
        for k in range(3):
            code |= ((cell[k] >> b) & 1) << (3 * b + k)
    return code


def split_parts(corners, faces, maxv):
    """Faces into groups of at most maxv distinct vertices (position, normal), grown greedily over
    shared vertices: each step takes the face that adds the fewest new vertices, the nearest to the
    group's centre on ties. Loose pieces are reached by jumping to the next face in Morton order.
    Full, compact groups: few parts, and each part's shadow ellipsoid hugs its piece."""
    key = [tuple((corners[i][0], corners[i][1]) for i in ids) for ids, _ in faces]
    cen = [tuple(sum(corners[i][0][k] for i in ids) / 3 for k in range(3)) for ids, _ in faces]
    lo = [min(c[k] for c in cen) for k in range(3)]
    span = max(max(c[k] for c in cen) - lo[k] for k in range(3)) or 1
    order = sorted(range(len(faces)), key=lambda f: morton(cen[f], lo, 1023 / span))
    by_key = {}
    for f, ks in enumerate(key):
        for k in ks:
            by_key.setdefault(k, []).append(f)
    done = [False] * len(faces)
    groups, cursor = [], 0
    while True:
        while cursor < len(order) and done[order[cursor]]:
            cursor += 1
        if cursor == len(order):
            return groups
        group, verts, frontier = [], set(), set()
        sx = sy = sz = 0.0
        pos = cursor
        while True:
            best, best_cost, best_d = None, 4, 0
            cx, cy, cz = (sx / len(group), sy / len(group), sz / len(group)) if group else (0, 0, 0)
            for f in frontier:
                if done[f]:
                    continue
                cost = sum(1 for k in set(key[f]) if k not in verts)
                if len(verts) + cost > maxv:
                    continue
                d = (cen[f][0] - cx) ** 2 + (cen[f][1] - cy) ** 2 + (cen[f][2] - cz) ** 2
                if cost < best_cost or (cost == best_cost and d < best_d):
                    best, best_cost, best_d = f, cost, d
            if best is None:   # no neighbour fits: the next free face in Morton order that does
                while pos < len(order) and (done[order[pos]] or
                                            len(verts | set(key[order[pos]])) > maxv):
                    pos += 1
                if pos == len(order):
                    break
                best = order[pos]
            done[best] = True
            group.append(best)
            verts |= set(key[best])
            sx, sy, sz = sx + cen[best][0], sy + cen[best][1], sz + cen[best][2]
            frontier.discard(best)
            for k in key[best]:
                frontier.update(f for f in by_key[k] if not done[f])
            if len(verts) >= maxv:
                break
        groups.append(group)


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def build_model(corners, faces, group):
    """One SModel: header, vertices, normals (one per vertex, then one per face), faces."""
    vid, verts = {}, []
    tris = []
    for f in group:
        ids, tex = faces[f]
        local = []
        for i in ids:
            key = (corners[i][0], corners[i][1])
            if key not in vid:
                vid[key] = len(verts)
                verts.append(key)
            local.append(vid[key])
        tris.append((local, [corners[i][2] for i in ids], tex))
    nv, nf = len(verts), len(tris)
    assert nv <= 256
    fnorm = []
    for local, _, _ in tris:
        p = [verts[j][0] for j in local]
        c = cross(tuple(p[1][k] - p[0][k] for k in range(3)), tuple(p[2][k] - p[0][k] for k in range(3)))
        ln = math.sqrt(sum(v * v for v in c)) or 1.0
        fnorm.append(tuple(int(round(-v / ln * 4096)) for v in c))   # outward
    out = bytearray(model_header(verts, nv, nv + nf, nf))
    for (p, _) in verts:
        assert all(-32768 <= v <= 32767 for v in p), p
        out += struct.pack('<4h', *p, 0)
    for (_, n) in verts:
        out += struct.pack('<4h', *n, 0)
    for n in fnorm:
        out += struct.pack('<4h', *n, 0)
    for j, (local, uv, tex) in enumerate(tris):
        a, b, c = local
        (u0, v0), (u1, v1), (u2, v2) = uv
        out += struct.pack('<11I', FACE_TRI | (FACE_WORDS * 4) << 16,
                           a | b << 8 | c << 16 | c << 24, COLOUR_TRI, nv + j, tex,
                           u0 | u1 << 16, u2 | u2 << 16, v0 | v1 << 16, v2 | v2 << 16, 0, 0)
    return bytes(out), nv, nf


def model_header(verts, nv, nn, nf):
    """Flags, counts, radius (1/16 units << 12) and the box (1/16 units: x max, x min, y max, y min,
    z max, z min) that the shadow ellipsoids divide by: never thinner than 2."""
    if verts:
        P = [p for p, _ in verts]
        r = max(math.sqrt(sum(v * v for v in p)) for p in P)
        box = []
        for k in range(3):
            hi = math.ceil(max(p[k] for p in P) / 16)
            lo = math.floor(min(p[k] for p in P) / 16)
            if hi - lo < 2:
                hi, lo = hi + 1, lo - 1
            box += [hi, lo]
    else:
        r, box = 16, [1, -1, 1, -1, 1, -1]
    return struct.pack('<4Hi6hhH', 8, nv, nn, nf, math.ceil(r / 16) << 12, *box, 0x7FFF, 0xFFFF)


# ---- textures ----------------------------------------------------------------------------------

def rgb565(path, w, h):
    raw = open(path, 'rb').read()
    assert len(raw) == w * h * 4, path
    out = bytearray(w * h * 2)
    for i in range(w * h):
        r, g, b = raw[4 * i], raw[4 * i + 1], raw[4 * i + 2]
        struct.pack_into('<H', out, 2 * i, (r >> 3) << 11 | (g >> 2) << 5 | b >> 3)
    return bytes(out)


def texture_record(idx, w, h, pixels):
    """A version 6 texture: no palette, 65536 colours, a PowerVR RGB565 (1) rectangle (9)."""
    return struct.pack('<4I2H2BHI', 0, 65536, 0, idx, w, h, 1, 9, 0, len(pixels) + 8) + pixels


# ---- the file ----------------------------------------------------------------------------------

def checksum(name):
    return zlib.crc32(name.encode()) & 0xFFFFFFFF


def write_psx(path, name, models, hier, anim_packet, textures):
    n = len(models)
    objects = b''.join(struct.pack('<HH3i3hHBBBB', 0, 0, 0, 0, 0, 0, 0, 0, i, 0, 0, 0, 0) + b'\0' * 8
                       for i in range(n))
    assert len(objects) == 36 * n
    head = 12 + len(objects) + 4 + 4 * n
    offs, body = [], bytearray()
    for m in models:
        offs.append(head + len(body))
        body += m
    pk_off = head + len(body)
    hier_b = struct.pack('<%dH' % n, *hier)
    hier_b += b'\0' * (-len(hier_b) % 4)
    packets = (struct.pack('<2I', ANIM44, len(anim_packet)) + anim_packet +
               struct.pack('<2I', HIER, len(hier_b)) + hier_b + struct.pack('<I', 0xFFFFFFFF))
    tail = bytearray(struct.pack('<%dI' % n, *[checksum('%s/part%d' % (name, i)) for i in range(n)]))
    tail += struct.pack('<I', len(textures)) + struct.pack('<%dI' % len(textures), *[t[0] for t in textures])
    tail += struct.pack('<2I', 0, 0)                                  # no palettes
    table_at = pk_off + len(packets) + len(tail)
    recs = [texture_record(i, w, h, px) for i, (_, w, h, px) in enumerate(textures)]
    pos = table_at + 4 + 4 * len(recs)
    toffs = []
    for r in recs:
        toffs.append(pos)
        pos += len(r)
    tail += struct.pack('<I', len(recs)) + struct.pack('<%dI' % len(recs), *toffs) + b''.join(recs)
    data = (struct.pack('<3I', VERSION_DC, pk_off, n) + objects + struct.pack('<I', n) +
            struct.pack('<%dI' % n, *offs) + body + packets + tail)
    open(path, 'wb').write(data)
    return len(data)


def voice_section(cfg, clips_dir, dest):
    """[Voice] for the skater's ini (the port's pc/voice.h), its clips copied to dest; None when
    there is no voice. A line whose clip hasn't been made (make_voice.py) is left out."""
    if 'voice' not in cfg:
        return None
    model, ev = cfg['mod']['model'], cfg['voice']['events']
    files = {k: ['%s_%s.wav' % (model, n) for n in v] for k, v in ev.items() if isinstance(v, list)}

    def have(f):
        return clips_dir and os.path.isfile(os.path.join(clips_dir, f))

    missing = sorted({f for fs in files.values() for f in fs if not have(f)})
    if missing:
        log('voice: no clip yet for %s (make_voice.py makes it): left out' % ', '.join(missing))
    files = {k: [f for f in fs if have(f)] for k, fs in files.items()}
    files = {k: fs for k, fs in files.items() if fs}
    if not files:
        log('voice: no clips: the skater stays quiet')
        return None
    os.makedirs(dest, exist_ok=True)
    for f in sorted({f for fs in files.values() for f in fs}):
        shutil.copyfile(os.path.join(clips_dir, f), os.path.join(dest, f))
    log('voice: %d clips' % len({f for fs in files.values() for f in fs}))
    return {k: ', '.join(files[k]) if k in files else v for k, v in ev.items()
            if k in files or not isinstance(v, list)}


def ini(path, sections):
    with open(path, 'w', newline='\r\n') as f:
        for sec, kv in sections:
            f.write('[%s]\n' % sec)
            for k, v in kv.items():
                f.write('%s = %s\n' % (k, v))
            f.write('\n')


def main():
    ap = argparse.ArgumentParser()
    for a in ('root', 'finish', 'bake', 'data', 'out'):
        ap.add_argument('--' + a, required=True)
    ap.add_argument('--install')
    ap.add_argument('--voice', help='the folder of his clips (assets/voice, make_voice.py)')
    o = ap.parse_args()
    cfg = json.load(open(o.finish))
    g, mod = cfg['game'], cfg['mod']
    baked = json.load(open(os.path.join(o.bake, 'baked.json')))

    corners, faces = to_game(baked, g)
    groups = split_parts(corners, faces, g['max_vertices_per_part'])
    n = g['num_models']
    slots = [i for i in range(n) if i not in g['reserved_parts']]
    if len(groups) > len(slots):
        raise SystemExit('%d parts needed, %d free: lower the triangle budgets in finish.json'
                         % (len(groups), len(slots)))
    models, nv_all, nf_all = [b''] * n, 0, 0
    filled = dict(zip(slots, groups))
    for i in range(n):
        m, nv, nf = build_model(corners, faces, filled.get(i, []))
        models[i] = m
        nv_all += nv
        nf_all += nf
    log('mesh: %d triangles, %d vertices in %d parts (slots %s)' % (nf_all, nv_all, len(groups),
                                                                    sorted(filled)))
    ys = [c[0][1] for c in corners]
    log('mesh: y from %d (top) to %d (ground %d); x %d..%d; z %d..%d' % (
        min(ys), max(ys), g['ground_y'], min(c[0][0] for c in corners), max(c[0][0] for c in corners),
        min(c[0][2] for c in corners), max(c[0][2] for c in corners)))

    base_n, packets = read_psx(os.path.join(o.data, mod['base_model']))
    base_hier = struct.unpack_from('<%dH' % base_n, packets[HIER], 0)
    base_anims = A.parse_anims(packets[ANIM44], base_n)
    anims = anchored_anims(base_anims, base_hier, n, g['root_part'], set(g['pelvis_anims']), g['rest_anim'])
    anim_packet = A.build_anims(anims, n)
    assert A.parse_anims(anim_packet, n) == anims
    hier = [g['root_part']] * n

    pixels = [(m['name'], m['tex_w'], m['tex_h'], rgb565(os.path.join(o.bake, m['texture']),
                                                         m['tex_w'], m['tex_h']))
              for m in baked['meshes']]

    def textures(model):   # checksums per file: both files can be loaded at once
        return [(checksum('%s/%s' % (model, nm)), w, h, px) for nm, w, h, px in pixels]

    root = os.path.join(o.out, mod['folder'])
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(os.path.join(root, 'Data', 'Models'))
    os.makedirs(os.path.join(root, 'Skaters'))
    size = write_psx(os.path.join(root, 'Data', 'Models', mod['model'] + '.psx'), mod['model'], models,
                     hier, anim_packet, textures(mod['model']))
    log('wrote %s.psx: %d bytes (anims %d)' % (mod['model'], size, len(anim_packet)))
    skater = {'Name': mod['skater']['Name'], 'Model': mod['model']}
    if mod.get('front_model'):
        fe = turned(anims[g['rest_anim']], g['root_part'], mod['front_yaw_deg'])
        size = write_psx(os.path.join(root, 'Data', 'Models', mod['front_model'] + '.psx'),
                         mod['front_model'], models, hier, A.build_anims([fe], n),
                         textures(mod['front_model']))
        log('wrote %s.psx: %d bytes (riding pose turned %g degrees)' % (mod['front_model'], size,
                                                                        mod['front_yaw_deg']))
        skater['FrontModel'] = mod['front_model']
    skater.update((k, v) for k, v in mod['skater'].items() if k != 'Name')
    ini(os.path.join(root, 'mod.ini'), [('Mod', {
        'Name': mod['name'], 'Author': mod['author'], 'Version': mod['version'],
        'Description': mod['description']})])
    sections = [('Skater', skater)]
    voice = voice_section(cfg, o.voice, os.path.join(root, 'Data', 'Voice'))
    if voice:
        sections.append(('Voice', voice))
    ini(os.path.join(root, 'Skaters', mod['model'] + '.ini'), sections)
    log('mod folder: %s' % root)
    if o.install:
        dst = os.path.join(o.install, mod['folder'])
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(root, dst)
        log('installed: %s' % dst)


main()
