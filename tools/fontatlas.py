#!/usr/bin/env python3
"""Civ V font atlas tools.

The UI font atlases in UITextures.fpk are stored as sparse tiled textures:
  NAME.dds        A8, the deduplicated non-empty tiles (4x4 or 8x8), packed row-major
  NAME-index.dds  L16, one entry per tile of the full atlas: 0-based index
                  of a (deduplicated) tile in NAME.dds

  fontatlas.py reconstruct NAME.dds NAME-index.dds OUT.png
"""
import struct
import sys

import numpy as np
from PIL import Image

def read_dds(path):
    d = open(path, "rb").read()
    height, width = struct.unpack_from("<II", d, 12)
    bits = struct.unpack_from("<I", d, 88)[0]
    dtype = np.uint8 if bits == 8 else np.uint16
    px = np.frombuffer(d, dtype=dtype, count=width * height, offset=128)
    return px.reshape(height, width)


def reconstruct(packed, index, atlas_w=512, atlas_h=512):
    """Tile size = atlas size / index size (4 or 8 px in the shipped fonts)."""
    th, tw = index.shape
    tile = atlas_w // tw
    assert atlas_h // th == tile, "non-square tiles"
    out = np.zeros((th * tile, tw * tile), np.uint8)
    per_row = packed.shape[1] // tile
    for ty in range(th):
        for tx in range(tw):
            t = int(index[ty, tx])  # 0-based; tiles are deduplicated, tile 0 is blank
            sy, sx = (t // per_row) * tile, (t % per_row) * tile
            out[ty * tile:(ty + 1) * tile, tx * tile:(tx + 1) * tile] = \
                packed[sy:sy + tile, sx:sx + tile]
    return out


if __name__ == "__main__":
    if sys.argv[1] == "reconstruct":
        img = reconstruct(read_dds(sys.argv[2]), read_dds(sys.argv[3]))
        Image.fromarray(img).save(sys.argv[4])
        print(img.shape, "nonzero", int((img > 0).sum()))


def parse_ggxml(path):
    """Returns (texture_w, texture_h, spacing, ascent, {char: glyph dict})."""
    import re
    xml = open(path, "rb").read().decode("utf-8", "replace")
    tw, th = map(int, re.search(r'<textures width="(\d+)" height="(\d+)"', xml).groups())
    g = re.search(r'<glyphs [^>]*spacing="(-?\d+)"[^>]*ascent="(-?\d+)"', xml)
    spacing, ascent = int(g.group(1)), int(g.group(2))
    glyphs = {}
    for m in re.finditer(r'<glyph ([^>]*)/>', xml):
        attrs = dict(re.findall(r'(\w+)="(-?\d+)"', m.group(1)))
        glyphs[int(attrs["ch"])] = {k: int(v) for k, v in attrs.items()}
    return tw, th, spacing, ascent, glyphs


def render_text(atlas, ggxml, text, k=1):
    """Draws `text` with an atlas (already k x upscaled) like the game would at scale k."""
    tw, th, spacing, ascent, glyphs = ggxml
    width = sum(glyphs.get(ord(c), glyphs[32])["a"] + glyphs.get(ord(c), glyphs[32])["b"] +
                glyphs.get(ord(c), glyphs[32])["c"] for c in text) + 8
    out = np.zeros(((ascent + 12) * k, width * k), np.float32)
    pen = 4
    for c in text:
        gl = glyphs.get(ord(c), glyphs[32])
        if gl["width"] and gl["height"]:
            u, v, w, h = (x * k for x in (gl["u"], gl["v"], gl["width"], gl["height"]))
            x0 = (pen + gl["a"]) * k
            y0 = (ascent + 2 - gl["originy"]) * k
            src = atlas[v:v + h, u:u + w].astype(np.float32)
            dst = out[y0:y0 + h, x0:x0 + w]
            np.maximum(dst, src[:dst.shape[0], :dst.shape[1]], out=dst)
        pen += gl["a"] + gl["b"] + gl["c"]
    return np.clip(out, 0, 255).astype(np.uint8)
