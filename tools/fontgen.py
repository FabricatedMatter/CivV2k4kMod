#!/usr/bin/env python3
"""Generate high-resolution Civ V font atlases for the UI Scale mod.

For every texture referenced by the Tw Cen MT .ggxml files, the original atlas is
rebuilt from UITextures.fpk (sparse tiled format, see fontatlas.py), hashed
(FNV-1a 64 over the raw A8 pixels, the same bytes the game uploads), and
upscaled 2x / 3x:
  - glyph layers (plain, Base, Stroke): Lanczos resample, then the edge is
    re-sharpened to ~1 output pixel via a local signed-distance estimate
  - Shadow / Soft layers: plain Lanczos (they are blurred by design)
Output, per factor k, a file-override tree for the DLL:
  <out>/x<k>/Assets/UI/Fonts/Tw Cent MT/<Font>.ggxml   (texture src -> *_x<k>.dds)
  <out>/x<k>/Assets/UI/Fonts/Tw Cent MT/<Tex>_x<k>.dds (plain A8, k * declared size)
The game loads these instead of the sparse packed atlases; glyph metrics are
unchanged and UVs are normalized, so text is simply sampled at k x resolution.

With --ttf (the user's own copy of Tw Cen MT; never shipped with the mod) the
glyphs are instead rendered natively at k x size, and the Stroke / Shadow / Soft
layers are derived from that render with parameters fitted against the game's
1x atlases (stroke radius 1.75 px, shadow offset (0, 1) px, glow sigma 2 px x1.5).

usage: fontgen.py GAME_DIR OUT_DIR [--scales 2,3] [--ttf "Tw Cen MT.ttf"]
"""
import argparse
import os
import re
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontatlas  # noqa: E402
import fpk  # noqa: E402


def fnv1a64(data):
    h = 0xCBF29CE484222325
    for b in data:  # small atlases; speed is fine for an offline tool
        h ^= b
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def fnv1a64_np(arr):
    # Vectorized-enough: process in chunks with Python ints (exact FNV semantics).
    return fnv1a64(arr.tobytes())


def upscale_sharp(a, k, center=0.42, width=0.9):
    up = np.asarray(Image.fromarray(a).resize((a.shape[1] * k, a.shape[0] * k),
                                             Image.LANCZOS)).astype(np.float32) / 255
    gy, gx = np.gradient(up)
    g = np.sqrt(gx * gx + gy * gy) + 1e-4
    out = np.clip(0.5 + (up - center) / g / width, 0, 1)
    out = np.where(up < 0.02, 0, out)
    return (out * 255 + 0.5).astype(np.uint8)


# Layer parameters at 1x, fitted against the shipped atlases (see README).
STROKE_RADIUS = 1.75
SHADOW_OFFSET = (0, 1)  # dx, dy
SOFT_SIGMA, SOFT_GAIN = 2.0, 1.5


def render_ttf_base(ttf, size, k, ggxml, fallback):
    """Native k x render of every glyph of a .ggxml font into its atlas layout.

    Glyph boxes the TTF cannot render keep the `fallback` (reconstructed) pixels."""
    from PIL import ImageFont
    tw, th, spacing, ascent, glyphs = ggxml
    font = ImageFont.truetype(ttf, size * k)
    out = fallback.astype(np.float32).copy()
    for ch, g in glyphs.items():
        if not g["width"] or ch < 32:
            continue
        try:
            mask, off = font.getmask2(chr(ch), "L", anchor="ls")
        except Exception:
            continue
        if mask.size[0] == 0 or not font.getbbox(chr(ch)):
            continue
        a = np.asarray(mask, np.float32).reshape(mask.size[1], mask.size[0])
        # clear the original glyph box (incl. padding) and draw the native glyph
        bx0, by0 = (g["u"] - spacing // 2) * k, (g["v"] - spacing // 2) * k
        bx1, by1 = (g["u"] + g["width"] + spacing // 2) * k, (g["v"] + g["height"] + spacing // 2) * k
        out[max(by0, 0):by1, max(bx0, 0):bx1] = 0
        ox = (g["u"] - g["originx"]) * k + off[0]
        oy = (g["v"] + g["originy"]) * k + off[1]
        y0, x0 = max(oy, 0), max(ox, 0)
        y1, x1 = min(oy + a.shape[0], out.shape[0]), min(ox + a.shape[1], out.shape[1])
        if y1 > y0 and x1 > x0:
            out[y0:y1, x0:x1] = np.maximum(out[y0:y1, x0:x1], a[y0 - oy:y1 - oy, x0 - ox:x1 - ox])
    return out / 255.0


def _near_distance(mask, radius):
    """Euclidean distance from each pixel to the nearest True pixel of mask, exact up to
    radius (larger distances come back as radius + 1). numpy only."""
    R = int(np.ceil(radius))
    h, w = mask.shape
    p = np.pad(mask, R)
    d = np.full(mask.shape, radius + 1.0, np.float32)
    for dy in range(-R, R + 1):
        for dx in range(-R, R + 1):
            r = (dx * dx + dy * dy) ** 0.5
            if r <= radius:
                hit = p[R + dy:R + dy + h, R + dx:R + dx + w]
                d = np.where(hit & (d > r), np.float32(r), d)
    return d


def _gaussian(img, sigma, truncate=4.0):
    """Separable Gaussian blur with reflected edges (= scipy.ndimage.gaussian_filter)."""
    r = int(truncate * sigma + 0.5)
    x = np.arange(-r, r + 1, dtype=np.float64)
    kern = np.exp(-0.5 * (x / sigma) ** 2)
    kern /= kern.sum()
    out = img.astype(np.float64)
    for axis in (0, 1):
        pad = [(r, r) if a == axis else (0, 0) for a in (0, 1)]
        p = np.pad(out, pad, mode="symmetric")
        acc = np.zeros_like(out)
        n = out.shape[axis]
        for i, wgt in enumerate(kern):
            acc += wgt * (p[i:i + n] if axis == 0 else p[:, i:i + n])
        out = acc
    return out.astype(np.float32)


def derive_layer(kind, base, k):
    if kind == "stroke":
        reach = STROKE_RADIUS * k + 0.5  # beyond this the stroke is fully transparent
        d = _near_distance(base >= 0.5, reach + 1)
        return np.clip(STROKE_RADIUS * k - d + 0.5, 0, 1)
    if kind == "shadow":
        dx, dy = SHADOW_OFFSET
        return np.maximum(base, np.roll(np.roll(base, dy * k, 0), dx * k, 1))
    if kind == "soft":
        return np.maximum(base, np.clip(_gaussian(base, SOFT_SIGMA * k) * SOFT_GAIN, 0, 1))
    return base


def layer_kind(stem):
    for kind in ("stroke", "shadow", "soft"):
        if kind in stem:
            return kind
    return "base"


def write_dds_a8(path, img):
    h, w = img.shape
    hdr = bytearray(128)
    struct.pack_into("<4sIIIIIII", hdr, 0, b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8,
                     h, w, w, 0, 1)
    # DDS_PIXELFORMAT at 76: size, flags=ALPHA(0x2), fourcc, bits, r,g,b,a masks
    struct.pack_into("<IIIIIIII", hdr, 76, 32, 0x2, 0, 8, 0, 0, 0, 0xFF)
    struct.pack_into("<I", hdr, 108, 0x1000)  # caps: TEXTURE
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(np.ascontiguousarray(img, np.uint8).tobytes())


def upscale_smooth(a, k):
    return np.asarray(Image.fromarray(a).resize((a.shape[1] * k, a.shape[0] * k), Image.LANCZOS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("game")
    ap.add_argument("out")
    ap.add_argument("--scales", default="2,3")
    ap.add_argument("--ttf", help="your own Tw Cen MT .ttf for native-quality glyphs")
    a = ap.parse_args()
    scales = [int(s) for s in a.scales.split(",")]
    os.makedirs(a.out, exist_ok=True)

    # All font atlas DDS files from the UI texture pack, by lower-case name.
    pack = {}
    for name, off, size, data in fpk.entries(os.path.join(a.game, "Resource", "DX9", "UITextures.fpk")):
        if name.lower().startswith("twcenmt"):
            pack[name.lower()] = data[off:off + size]

    def dds_array(name):
        tmp = os.path.join(a.out, ".tmp.dds")
        open(tmp, "wb").write(pack[name])
        arr = fontatlas.read_dds(tmp)
        os.remove(tmp)
        return arr

    font_dir = os.path.join(a.game, "Assets", "UI", "Fonts", "Tw Cent MT")
    rel = os.path.join("Assets", "UI", "Fonts", "Tw Cent MT")
    done = {}
    for gg in sorted(os.listdir(font_dir)):
        if not gg.lower().endswith(".ggxml"):
            continue
        xml = open(os.path.join(font_dir, gg), "rb").read().decode("utf-8", "replace")
        tw, th = map(int, re.search(r'<textures width="(\d+)" height="(\d+)"', xml).groups())
        size = int(re.search(r"(\d+)", gg).group(1))
        ggxml = fontatlas.parse_ggxml(os.path.join(font_dir, gg))
        native = {}  # k -> native base layer (float 0..1), TTF mode only
        new_xml = {k: xml for k in scales}
        for src in sorted(set(re.findall(r'<texture [^>]*src="([^"]+)"', xml))):
            base = src.lower()
            stem = base[:-4]
            if base not in pack or stem + "-index.dds" not in pack:
                print("  missing in pack:", src)
                continue
            atlas = fontatlas.reconstruct(dds_array(base), dds_array(stem + "-index.dds"), tw, th)
            blurred = "shadow" in stem or "soft" in stem
            out_name = src[:-4].replace(" ", "_") + "_x%d.dds"
            for k in scales:
                d = os.path.join(a.out, "x%d" % k, rel)
                os.makedirs(d, exist_ok=True)
                fn = out_name % k
                if fn not in done:
                    if a.ttf:
                        if k not in native:
                            plain = fontatlas.reconstruct(dds_array(gg[:-6].lower() + ".dds"),
                                                          dds_array(gg[:-6].lower() + "-index.dds"),
                                                          tw, th)
                            native[k] = render_ttf_base(a.ttf, size, k, ggxml,
                                                        upscale_sharp(plain, k))
                        layer = derive_layer(layer_kind(stem), native[k], k)
                        hi = (np.clip(layer, 0, 1) * 255 + 0.5).astype(np.uint8)
                    else:
                        hi = upscale_smooth(atlas, k) if blurred else upscale_sharp(atlas, k)
                    write_dds_a8(os.path.join(d, fn), hi)
                    done[fn] = True
                new_xml[k] = new_xml[k].replace('src="%s"' % src, 'src="%s"' % fn)
            print("%-24s %s" % (src, ("ttf/" + layer_kind(stem)) if a.ttf
                                    else ("smooth" if blurred else "sharp")))
        for k in scales:
            d = os.path.join(a.out, "x%d" % k, rel)
            open(os.path.join(d, gg), "wb").write(new_xml[k].encode("utf-8"))
    print(len(done), "hi-res atlases written")


if __name__ == "__main__":
    main()
