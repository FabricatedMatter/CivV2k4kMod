#!/usr/bin/env python3
"""Prototype of the UI clip-rect shader fix (the DLL does the same in C++).

Civ V's UI pixel shaders discard pixels outside a clip rect:
    add r0.xy, v_pos.xyxx, -cbN[k].xzxx ; lt ... ; discard_nz
SV_Position is in real pixels, the clip rect in UI units. Each such
`add` becomes `mad r, (+-)cb, cb13[0].xxxx, (+-)v_pos` = pos - clip*scale,
which has the same sign as pos/scale - clip.

usage: dxbc_clipfix.py IN.dxbc OUT.dxbc
"""
import struct
import sys

OP_ADD, OP_LT, OP_MAD, OP_DCL_CB, OP_DCL_INPUT_PS_SIV, OP_CUSTOMDATA = 0, 49, 50, 89, 100, 53
SCALE_SLOT = 13
SCALE_OPERAND = [0x00208006, SCALE_SLOT, 0]  # cb13[0].xxxx
SCALE_DCL = [0x04000059, 0x00208E46, SCALE_SLOT, 1]  # dcl_constantbuffer cb13[1]


def _md5_transform(state, block):
    import math
    K = [int(abs(math.sin(i + 1)) * 2**32) & 0xFFFFFFFF for i in range(64)]
    R = [7, 12, 17, 22] * 4 + [5, 9, 14, 20] * 4 + [4, 11, 16, 23] * 4 + [6, 10, 15, 21] * 4
    M = struct.unpack("<16I", block)
    a, b, c, d = state
    for i in range(64):
        if i < 16:
            f, g = (b & c) | (~b & d), i
        elif i < 32:
            f, g = (d & b) | (~d & c), (5 * i + 1) % 16
        elif i < 48:
            f, g = b ^ c ^ d, (3 * i + 5) % 16
        else:
            f, g = c ^ (b | ~d), (7 * i) % 16
        f = (f + a + K[i] + M[g]) & 0xFFFFFFFF
        a, d, c = d, c, b
        b = (b + ((f << R[i]) | (f >> (32 - R[i])))) & 0xFFFFFFFF
    return [(x + y) & 0xFFFFFFFF for x, y in zip(state, (a, b, c, d))]


def dxbc_checksum(blob):
    """DXBC container checksum: MD5 variant over blob[20:] with custom padding."""
    data = blob[20:]
    n = len(data)
    bits = n * 8
    st = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]
    full = n // 64
    for k in range(full):
        st = _md5_transform(st, data[64 * k:64 * k + 64])
    rest = data[64 * full:]
    if len(rest) >= 56:
        blk = rest + b"\x80" + b"\0" * (63 - len(rest))
        st = _md5_transform(st, blk)
        blk = struct.pack("<I", bits) + b"\0" * 56 + struct.pack("<I", (bits >> 2) | 1)
    else:
        blk = struct.pack("<I", bits) + rest + b"\x80"
        blk += b"\0" * (60 - len(blk)) + struct.pack("<I", (bits >> 2) | 1)
    st = _md5_transform(st, blk)
    return struct.pack("<4I", *st)


def instr_len(toks, i):
    t = toks[i]
    return toks[i + 1] if (t & 0x7FF) == OP_CUSTOMDATA else (t >> 24) & 0x7F


def parse_operand(toks, i):
    """Returns (length, type, index0) for an operand starting at toks[i]."""
    t = toks[i]
    n = 1
    if t & 0x80000000:
        n += 1  # extended operand (modifiers)
    otype = (t >> 12) & 0xFF
    dim = (t >> 20) & 3
    reps = [(t >> 22) & 7, (t >> 25) & 7, (t >> 28) & 7]
    if otype == 4:  # immediate32
        n += {0: 0, 1: 1, 2: 4}[t & 3]
        return n, otype, None
    idx0 = None
    for d in range(dim):
        if reps[d] != 0:
            raise ValueError("relative addressing not supported")
        if d == 0:
            idx0 = toks[i + n]
        n += 1
    return n, otype, idx0


def patch(blob):
    nch = struct.unpack_from("<I", blob, 28)[0]
    offs = [struct.unpack_from("<I", blob, 32 + 4 * c)[0] for c in range(nch)]
    sh = [o for o in offs if blob[o:o + 4] in (b"SHEX", b"SHDR")]
    if not sh:
        return None
    so = sh[0]
    size = struct.unpack_from("<I", blob, so + 4)[0]
    toks = list(struct.unpack_from("<%dI" % (size // 4), blob, so + 8))
    if (toks[0] >> 16) & 0xFFFF != 0:  # program type 0 = pixel shader
        return None

    pos_reg = None
    out = toks[:2]
    i, decl_inserted, changed = 2, False, 0
    instrs = []
    while i < len(toks):
        ln = instr_len(toks, i)
        instrs.append(toks[i:i + ln])
        i += ln
    for k, ins in enumerate(instrs):
        op = ins[0] & 0x7FF
        if op == OP_DCL_INPUT_PS_SIV and ins[-1] == 1:  # name 1 = position
            pos_reg = parse_operand(ins, 1)[2]
    if pos_reg is None:
        return None

    for k, ins in enumerate(instrs):
        op = ins[0] & 0x7FF
        if not decl_inserted and op not in range(88, 107) and op != OP_DCL_CB:
            out += SCALE_DCL  # before the first non-declaration instruction
            decl_inserted = True
        if (op == OP_ADD and not (ins[0] & 0x80000000) and k + 1 < len(instrs)
                and (instrs[k + 1][0] & 0x7FF) == OP_LT):
            p = 1
            ops = []
            for _ in range(3):
                n, ty, idx = parse_operand(ins, p)
                ops.append((ins[p:p + n], ty, idx))
                p += n
            dst, a, b = ops
            srcs = {(ty, idx): o for o, ty, idx in (a, b)}
            pos = next((o for o, ty, idx in (a, b) if ty == 1 and idx == pos_reg), None)
            cb = next((o for o, ty, idx in (a, b) if ty == 8), None)
            if pos is not None and cb is not None:
                body = dst[0] + cb + SCALE_OPERAND + pos
                out.append((ins[0] & ~0x7F0007FF) | OP_MAD | ((1 + len(body)) << 24))
                out += body
                changed += 1
                continue
        out += ins
    if not changed:
        return None
    out[1] = len(out)
    new_sh = struct.pack("<%dI" % len(out), *out)
    delta = len(new_sh) - size
    res = bytearray(blob[:so]) + blob[so:so + 4] + struct.pack("<I", len(new_sh)) + new_sh \
        + blob[so + 8 + size:]
    struct.pack_into("<I", res, 24, len(res))
    for c, o in enumerate(offs):
        if o > so:
            struct.pack_into("<I", res, 32 + 4 * c, o + delta)
    res[4:20] = dxbc_checksum(bytes(res))
    return bytes(res), changed


if __name__ == "__main__":
    r = patch(open(sys.argv[1], "rb").read())
    if not r:
        print("no change")
        sys.exit(1)
    open(sys.argv[2], "wb").write(r[0])
    print("patched", r[1], "instructions")
