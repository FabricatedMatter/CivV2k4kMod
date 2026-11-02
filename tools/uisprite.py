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


def index_bytes(data):
    """Raw mip 0 bytes of an index map (what the game uploads)."""
    h, w = struct.unpack_from("<II", data, 12)
    bits = struct.unpack_from("<I", data, 88)[0]
    return data[128:128 + w * h * bits // 8]


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


def disassemble(hi, index, t, k, data_shape, fallback, data=None):
    """Cut a k x assembled image back into a k x data texture.

    A tile used at several places is the average of all its occurrences (so upscaler
    bleed from one neighbourhood does not get copied everywhere the tile is reused),
    and a tile that is a single colour at 1x is exactly that colour at k x. Tiles that
    never occur come from fallback (the k x data texture rendered some other way)."""
    dh, dw = data_shape[:2]
    per = dw // t
    T = t * k
    out = fallback.copy()
    acc, cnt = {}, {}
    ih, iw = index.shape
    for y in range(ih):
        for x in range(iw):
            tid = int(index[y, x])
            blk = hi[y * T:(y + 1) * T, x * T:(x + 1) * T].astype(np.float64)
            if tid in acc:
                acc[tid] += blk
                cnt[tid] += 1
            else:
                acc[tid] = blk.copy()
                cnt[tid] = 1
    for tid, a in acc.items():
        ty, tx = divmod(tid, per)
        out[ty * T:(ty + 1) * T, tx * T:(tx + 1) * T] = np.clip(a / cnt[tid] + 0.5, 0, 255)
    if data is not None:
        fix_uniform_tiles(out, data, t, k)
    return out


def fix_uniform_tiles(hi_data, data, t, k):
    """In place: tiles that are one colour at 1x become exactly that colour at k x.
    Returns the number of tiles changed."""
    dh, dw = data.shape[:2]
    T = t * k
    n = 0
    for ty in range(dh // t):
        for tx in range(dw // t):
            lo = data[ty * t:(ty + 1) * t, tx * t:(tx + 1) * t]
            c = lo.reshape(-1, lo.shape[-1])
            if (c.max(axis=0).astype(int) - c.min(axis=0)).max() <= 1:
                blk = hi_data[ty * T:(ty + 1) * T, tx * T:(tx + 1) * T]
                if (np.abs(blk.astype(int) - c[0]).max() > 0):
                    blk[...] = c[0]
                    n += 1
    return n
