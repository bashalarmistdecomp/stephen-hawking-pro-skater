"""THPS1 skater animations: the type-44 anim packet of a skater .psx (compressed Euler/pivot
streams per part), its hierarchy packet, and the per-frame part transforms.

Mirrors the port's game code:
  - DecompressStream / Decomp_GetAnimTransform  (TonyWasAProSkater/game/src/decomp.cpp)
  - M3dMaths_RotMatrixYXZ: M = Ry * Rx * Rz, 4096 = 1.0, angles in 4096ths of a turn
    (TonyWasAProSkater/game/src/m3dasm/m3dmaths.c)

Packet layout (type 0x2c): u32 NumAnims, then per anim {u32 stream offset (from the packet
start), u32 NumFrames}, then the streams. Each anim holds, per part in model order, six streams
(Euler vx, vy, vz, pivot px, py, pz), each NumFrames long. A part's rotation is absolute (in the
skater's object space); its translation is the parent's translation plus the pivot turned by the
parent's rotation. The root part is its own parent in the hierarchy packet.
"""
import math
import struct

CHANNELS = 6  # vx, vy, vz (Euler), px, py, pz (pivot)


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def decompress_stream(data, pos, length):
    """One stream of `length` values starting at data[pos]: (values, next pos)."""
    t = data[pos]
    pos += 1
    interval = (t >> 4) + 1
    t &= 15
    if interval >= 2:
        segs = (length - 1) // interval
        rem = (length - 1) - segs * interval
    else:
        segs, rem = length - 1, 0
    out = []
    if t == 0:
        v = _s16(data[pos] | data[pos + 1] << 8)
        pos += 2
        out.append(v)
        for _ in range(segs):
            nv = _s16(data[pos] | data[pos + 1] << 8)
            pos += 2
            inc = int((nv - v) / interval)  # C division truncates toward zero
            for _ in range(interval - 1):
                v = _s16(v + inc)
                out.append(v)
            v = nv
            out.append(v)
        if rem:
            nv = _s16(data[pos] | data[pos + 1] << 8)
            pos += 2
            inc = _s16(int((nv - v) / rem))
            for _ in range(rem - 1):
                v = _s16(v + inc)
                out.append(v)
            out.append(nv)
    elif 1 <= t <= 13:
        val = data[pos] | data[pos + 1] << 8
        pos += 2
        off = 0
        bits = t + 1
        out.append(_s16(val))

        def step(n, val, pos, off):
            k = data[pos] << 16 | data[pos + 1] << 8 | data[pos + 2]
            k = (k << (off + 8)) & 0xFFFFFFFF
            k = (k - (1 << 32) if k & 0x80000000 else k) >> (32 - bits)
            nv = _s16(val + k)
            d = off + bits
            pos += d >> 3
            off = d & 7
            inc = _s16(int(k / n))
            for _ in range(n - 1):
                val = _s16(val + inc)
                out.append(val)
            return nv, pos, off

        for _ in range(segs):
            val, pos, off = step(interval, val, pos, off)
            out.append(val)
        if rem:
            val, pos, off = step(rem, val, pos, off)
            out.append(val)
        if off:
            pos += 1
    elif t == 14:
        v = _s16(data[pos] | data[pos + 1] << 8)
        pos += 2
        out = [v] * length
    else:  # 15
        out = [0] * length
    return out[:length], pos


def encode_stream(values):
    """Encode a stream: zeros (15), a constant (14) or raw 16-bit keys every frame (type 0,
    interval 1). Lossless and simple; the game's packets are smaller, ours needn't be."""
    if all(v == 0 for v in values):
        return bytes([15])
    if all(v == values[0] for v in values):
        return bytes([14]) + struct.pack('<h', values[0])
    return bytes([0]) + b''.join(struct.pack('<h', v) for v in values)


def parse_anims(packet, num_models):
    """packet: the anim packet's bytes (after its type and size words). Returns a list of anims;
    each anim is a list of frames; each frame a list of num_models (vx, vy, vz, px, py, pz)."""
    n = struct.unpack_from('<I', packet, 0)[0]
    anims = []
    for a in range(n):
        off, nframes = struct.unpack_from('<II', packet, 4 + 8 * a)
        pos = off
        chans = []
        for m in range(num_models):
            part = []
            for c in range(CHANNELS):
                vals, pos = decompress_stream(packet, pos, nframes)
                part.append(vals)
            chans.append(part)
        anims.append([[tuple(chans[m][c][f] for c in range(CHANNELS)) for m in range(num_models)]
                      for f in range(nframes)])
    return anims


def build_anims(anims, num_models):
    """The inverse of parse_anims: an anim packet (without its type and size words)."""
    n = len(anims)
    head = bytearray(struct.pack('<I', n) + b'\0' * (8 * n))
    body = bytearray()
    base = len(head)
    for a, frames in enumerate(anims):
        struct.pack_into('<II', head, 4 + 8 * a, base + len(body), len(frames))
        for m in range(num_models):
            for c in range(CHANNELS):
                body += encode_stream([frames[f][m][c] for f in range(len(frames))])
    out = bytes(head + body)
    return out + b'\0' * (-len(out) % 4)


SIN = [round(4096 * math.sin(2 * math.pi * i / 4096)) for i in range(4096)]
COS = [round(4096 * math.cos(2 * math.pi * i / 4096)) for i in range(4096)]


def rot_yxz(vx, vy, vz):
    """M3dMaths_RotMatrixYXZ in floats (1.0 = 1): rows of M = Ry * Rx * Rz."""
    sx, cx = SIN[vx & 4095] / 4096, COS[vx & 4095] / 4096
    sy, cy = SIN[vy & 4095] / 4096, COS[vy & 4095] / 4096
    sz, cz = SIN[vz & 4095] / 4096, COS[vz & 4095] / 4096
    return ((sy * sx * sz + cy * cz, sy * sx * cz - cy * sz, sy * cx),
            (cx * sz, cx * cz, -sx),
            (cy * sx * sz - sy * cz, cy * sx * cz + sy * sz, cy * cx))


def mat_vec(m, v):
    return tuple(m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3))


def frame_transforms(frame, hierarchy):
    """The parts' (rotation rows, translation) in object space for one frame, as
    Decomp_GetAnimTransform builds them (parents before children)."""
    n = len(frame)
    root = next(i for i in range(n) if hierarchy[i] == i)
    order = [root]
    k = 0
    while len(order) < n:
        p = order[k]
        order += [i for i in range(n) if i != root and hierarchy[i] == p]
        k += 1
    out = [None] * n
    for m in order:
        vx, vy, vz, px, py, pz = frame[m]
        r = rot_yxz(vx, vy, vz)
        if m == root:
            t = (px, py, pz)
        else:
            pr, pt = out[hierarchy[m]]
            d = mat_vec(pr, (px, py, pz))
            t = (pt[0] + d[0], pt[1] + d[1], pt[2] + d[2])
        out[m] = (r, t)
    return out
