"""Civ V "-index" tile sprites.

Many UI textures ship as a pair: <name>.dds holds the distinct tiles (t x t px each,
row-major), <name>-index.dds (8- or 16-bit) holds one tile id per tile of the full
image. The engine uploads both unchanged and assembles them in a shader, so a hi-res
version is made by assembling, upscaling the whole image and cutting it back into
k*t x k*t tiles in the same layout (the index stays as it is).
"""
import struct

import numpy as np


def parse_index(data):
    h, w = struct.unpack_from("<II", data, 12)
    bits = struct.unpack_from("<I", data, 88)[0]
    if bits not in (8, 16):
        return None
    dt = np.uint8 if bits == 8 else np.uint16
    return np.frombuffer(data[128:128 + w * h * bits // 8], dt).reshape(h, w).astype(np.int64)


def tile_size(data_w, data_h, index):
    """Tile edge length: the data texture holds exactly max(index)+1 tiles (or more)."""
    n = int(index.max()) + 1
    best = None
    for t in (1, 2, 4, 8, 16, 32, 64, 128, 256, 512):
        if data_w % t == 0 and data_h % t == 0 and (data_w // t) * (data_h // t) >= n and \
                (data_w // t) * (data_h // t) < 2 * n + data_w // t:
            best = t
    return best


def assemble(data_rgba, index, t):
    dh, dw = data_rgba.shape[:2]
    per = dw // t
    ih, iw = index.shape
    out = np.zeros((ih * t, iw * t, data_rgba.shape[2]), data_rgba.dtype)
    for y in range(ih):
        for x in range(iw):
            ty, tx = divmod(int(index[y, x]), per)
            out[y * t:(y + 1) * t, x * t:(x + 1) * t] = data_rgba[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
    return out


def disassemble(hi, index, t, k, data_shape, fallback):
    """Cut a k x assembled image back into a k x data texture. Each tile is taken from
    its first occurrence in the index; tiles that never occur come from fallback
    (the k x data texture rendered some other way)."""
    dh, dw = data_shape[:2]
    per = dw // t
    T = t * k
    out = fallback.copy()
    done = set()
    ih, iw = index.shape
    for y in range(ih):
        for x in range(iw):
            tid = int(index[y, x])
            if tid in done:
                continue
            done.add(tid)
            ty, tx = divmod(tid, per)
            out[ty * T:(ty + 1) * T, tx * T:(tx + 1) * T] = hi[y * T:(y + 1) * T, x * T:(x + 1) * T]
    return out
