"""BC1 (DXT1) / BC3 (DXT5) decoding with numpy, and encoding via ImageMagick."""
import os
import struct
import subprocess
import tempfile

import numpy as np
from PIL import Image


def parse_dds_bc(data):
    """(w, h, mips, 'dxt1'|'dxt5', mip0 bytes) for BC1/BC3 DDS files, else None."""
    if data[:4] != b"DDS ":
        return None
    h, w = struct.unpack_from("<II", data, 12)
    mips = max(1, struct.unpack_from("<I", data, 28)[0])
    flags = struct.unpack_from("<I", data, 80)[0]
    four = data[84:88]
    if not flags & 4 or four not in (b"DXT1", b"DXT5"):
        return None
    bs = 8 if four == b"DXT1" else 16
    size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs
    return w, h, mips, four.decode().lower(), data[128:128 + size]


def _565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.stack([r, g, b], -1).astype(np.int32)


def _color_block(blk, force4):
    c0 = blk[:, 0].astype(np.uint32) | (blk[:, 1].astype(np.uint32) << 8)
    c1 = blk[:, 2].astype(np.uint32) | (blk[:, 3].astype(np.uint32) << 8)
    idx = (blk[:, 4].astype(np.uint32) | (blk[:, 5].astype(np.uint32) << 8) |
           (blk[:, 6].astype(np.uint32) << 16) | (blk[:, 7].astype(np.uint32) << 24))
    p0, p1 = _565(c0), _565(c1)
    four = force4 | (c0 > c1)
    p2 = np.where(four[:, None], (2 * p0 + p1) // 3, (p0 + p1) // 2)
    p3 = np.where(four[:, None], (p0 + 2 * p1) // 3, 0)
    pal = np.stack([p0, p1, p2, p3], 1)  # n, 4, 3
    a_pal = np.stack([np.full_like(c0, 255)] * 3 + [np.where(four, 255, 0)], 1)
    sel = np.stack([(idx >> (2 * i)) & 3 for i in range(16)], 1)  # n, 16
    rgb = np.take_along_axis(pal, sel[..., None].astype(np.int64), 1)
    a = np.take_along_axis(a_pal, sel.astype(np.int64), 1)
    return rgb, a


def _alpha_block(blk):
    a0, a1 = blk[:, 0].astype(np.int32), blk[:, 1].astype(np.int32)
    bits = np.zeros(len(blk), np.uint64)
    for i in range(6):
        bits |= blk[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    sel = np.stack([(bits >> np.uint64(3 * i)) & np.uint64(7) for i in range(16)], 1).astype(np.int64)
    eight = a0 > a1
    pal = [a0, a1]
    for i in range(1, 7):
        pal.append(np.where(eight, ((7 - i) * a0 + i * a1) // 7,
                            np.where(i <= 4, ((5 - i) * a0 + i * a1) // 5, 0 if i == 5 else 255)))
    pal = np.stack(pal, 1)
    # 6-alpha mode: indices 6, 7 are 0 and 255
    pal[:, 6] = np.where(eight, pal[:, 6], 0)
    pal[:, 7] = np.where(eight, pal[:, 7], 255)
    return np.take_along_axis(pal, sel, 1)


def decode(w, h, kind, raw):
    """BC1/BC3 mip 0 -> RGBA uint8 (h, w, 4)."""
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    bs = 8 if kind == "dxt1" else 16
    blk = np.frombuffer(raw, np.uint8)[:bw * bh * bs].reshape(bw * bh, bs)
    if kind == "dxt1":
        rgb, a = _color_block(blk, np.zeros(len(blk), bool))
    else:
        a = _alpha_block(blk[:, :8])
        rgb, _ = _color_block(blk[:, 8:], np.ones(len(blk), bool))
    px = np.concatenate([rgb, a[..., None]], -1).clip(0, 255).astype(np.uint8)  # n, 16, 4
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:h, :w]


def encode_dds(path, rgba, kind):
    """Writes rgba as a BC1/BC3 DDS with a full mip chain: levels are box-filtered here
    and each is block-compressed by ImageMagick (which skips mips for non-power-of-two
    sizes on its own)."""
    h, w = rgba.shape[:2]
    levels = [rgba]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        cur = levels[-1]
        levels.append(np.asarray(Image.fromarray(cur, "RGBA").resize(
            (max(1, cur.shape[1] // 2), max(1, cur.shape[0] // 2)), Image.BOX)))
    blobs = []
    with tempfile.TemporaryDirectory() as td:
        for i, lv in enumerate(levels):
            src, dst = os.path.join(td, "%d.png" % i), os.path.join(td, "%d.dds" % i)
            Image.fromarray(lv, "RGBA").save(src)
            subprocess.run(["magick", src, "-define", "dds:compression=" + kind,
                            "-define", "dds:cluster-fit=true", "-define", "dds:mipmaps=0", dst],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            d = open(dst, "rb").read()
            lw, lh = lv.shape[1], lv.shape[0]
            size = max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4) * (8 if kind == "dxt1" else 16)
            blobs.append(d[128:128 + size])
    hdr = bytearray(128)
    bs = 8 if kind == "dxt1" else 16
    struct.pack_into("<4sIIIIIII", hdr, 0, b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000,
                     h, w, max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * bs, 0, len(levels))
    struct.pack_into("<II4s", hdr, 76, 32, 0x4, kind.upper().encode())
    struct.pack_into("<I", hdr, 108, 0x1000 | 0x400000 | 0x8)
    with open(path, "wb") as f:
        f.write(hdr)
        for bl in blobs:
            f.write(bl)


def mip_count(w, h):
    n = 1
    while w > 1 or h > 1:
        w, h = max(1, w // 2), max(1, h // 2)
        n += 1
    return n
