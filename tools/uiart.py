#!/usr/bin/env python3
"""AI upscaling workflow for Civ V UI textures.

  uiart.py build  GAME_DIR OUT_DIR --category frames [--upscaler esrgan|lanczos]
                  [--esrgan PATH_TO_realesrgan-ncnn-vulkan] [--model realesrgan-x4plus-anime]
  -> OUT_DIR/x<k>/<name>_x<k>.dds   hi-res BGRA DDS with full mip chain
     OUT_DIR/x<k>/uiart.txt         manifest for the DLL:
                                     <fnv64 of original mip0> <w> <h> <k> <file>
     OUT_DIR/report/<category>.png  contact sheet: original | bilinear | upscaled
     OUT_DIR/report/<category>.txt  per-texture error metrics (round trip to 1x)

The DLL swaps textures on upload (matched by hash) and the UI's normalized UVs make
the k x texture line up exactly with the original layout.
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpk  # noqa: E402

CATEGORIES = {
    # Stage 1: frames, borders, separators, scroll bars and other window chrome.
    "frames": (
        "grid9", "windowgrid", "windowhlgrid", "frametopbottom", "corner16", "dec32x64",
        "dectop", "sideright", "horizontaltrim", "verticaltrim", "divider", "scrollbar",
        "slider", "dropdown", "gridsquare", "gridglass", "glassback", "bar300x2", "bar340x2",
        "bar400x2", "bar500x2", "bar900x2", "connect_", "selectedup", "selecteddown",
        "blankbutton", "newx",
    ),
}
SCALES = (2, 3)
PAD = 8          # replicated border (1x px) so 9-slice edges stay seamless
ESRGAN_SCALE = 4


def fnv1a64(b):
    h = 0xCBF29CE484222325
    arr = np.frombuffer(b, np.uint8)
    # chunked exact FNV-1a (pure python per byte would be slow for 1 MB textures)
    for x in arr.tolist():
        h = ((h ^ x) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


# ------------------------------------------------------------------ DDS I/O
def parse_dds(data):
    """Returns (w, h, mips, bgra mip0 bytes) for uncompressed 32-bit DDS, else None."""
    if data[:4] != b"DDS ":
        return None
    h, w = struct.unpack_from("<II", data, 12)
    mips = max(1, struct.unpack_from("<I", data, 28)[0])
    pf_flags, bits = struct.unpack_from("<I", data, 80)[0], struct.unpack_from("<I", data, 88)[0]
    masks = struct.unpack_from("<4I", data, 92)
    if pf_flags & 4 or bits != 32 or masks != (0xFF0000, 0xFF00, 0xFF, 0xFF000000):
        return None
    return w, h, mips, data[128:128 + w * h * 4]


def write_dds_bgra(path, levels):
    h, w = levels[0].shape[:2]
    hdr = bytearray(128)
    struct.pack_into("<4sIIIIIII", hdr, 0, b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | 0x20000,
                     h, w, w * 4, 0, len(levels))
    struct.pack_into("<IIIIIIII", hdr, 76, 32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000)
    struct.pack_into("<I", hdr, 108, 0x1000 | 0x400000 | 0x8)  # TEXTURE | MIPMAP | COMPLEX
    with open(path, "wb") as f:
        f.write(hdr)
        for lv in levels:
            f.write(np.ascontiguousarray(lv[..., [2, 1, 0, 3]], np.uint8).tobytes())


def mip_chain(rgba):
    levels = [rgba]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        cur = levels[-1]
        nh, nw = max(1, cur.shape[0] // 2), max(1, cur.shape[1] // 2)
        levels.append(np.asarray(Image.fromarray(cur, "RGBA").resize((nw, nh), Image.BOX)))
    return levels


# ------------------------------------------------------------------ upscaling
def bleed_rgb(rgba):
    """Give fully transparent pixels the colour of the nearest visible pixel."""
    a = rgba[..., 3]
    if a.min() > 0 or a.max() == 0:
        return rgba[..., :3]
    _, (iy, ix) = ndimage.distance_transform_edt(a == 0, return_indices=True)
    return rgba[..., :3][iy, ix]


def lanczos(img, k):
    mode = "RGB" if img.ndim == 3 else "L"
    return np.asarray(Image.fromarray(img, mode).resize((img.shape[1] * k, img.shape[0] * k),
                                                         Image.LANCZOS))


class Esrgan:
    def __init__(self, exe, model):
        self.exe, self.model = exe, model
        self.models = os.path.join(os.path.dirname(exe), "models")

    def run(self, images):
        """images: list of HxWx3 uint8 -> list of 4x upscaled arrays (one batch call)."""
        with tempfile.TemporaryDirectory() as td:
            ind, outd = os.path.join(td, "in"), os.path.join(td, "out")
            os.makedirs(ind)
            os.makedirs(outd)
            for i, im in enumerate(images):
                Image.fromarray(im, "RGB").save(os.path.join(ind, "%05d.png" % i))
            # one call per image: directory mode crashes the x4plus models on large batches
            for i in range(len(images)):
                subprocess.run([self.exe, "-i", os.path.join(ind, "%05d.png" % i),
                                "-o", os.path.join(outd, "%05d.png" % i), "-n", self.model,
                                "-s", str(ESRGAN_SCALE), "-m", self.models, "-f", "png"],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return [np.asarray(Image.open(os.path.join(outd, "%05d.png" % i)).convert("RGB"))
                    for i in range(len(images))]


def upscale_textures(textures, up):
    """textures: list of RGBA 1x arrays -> list of RGBA 4x arrays."""
    padded = [np.pad(t, ((PAD, PAD), (PAD, PAD), (0, 0)), mode="edge") for t in textures]
    rgb_in = [bleed_rgb(p) for p in padded]
    alpha_in = [np.repeat(p[..., 3:4], 3, axis=2) for p in padded]
    if up is None:
        rgb_out = [lanczos(x, ESRGAN_SCALE) for x in rgb_in]
        alpha_out = [lanczos(x, ESRGAN_SCALE) for x in alpha_in]
    else:
        outs = up.run(rgb_in + alpha_in)
        rgb_out, alpha_out = outs[:len(rgb_in)], outs[len(rgb_in):]
    res = []
    p4 = PAD * ESRGAN_SCALE
    for t, rgb, al in zip(textures, rgb_out, alpha_out):
        rgba = np.dstack([rgb, al.mean(axis=2).astype(np.uint8)])
        res.append(rgba[p4:p4 + t.shape[0] * ESRGAN_SCALE, p4:p4 + t.shape[1] * ESRGAN_SCALE])
    return res


def resize_rgba(rgba, w, h, flt):
    # premultiplied resampling avoids colour fringes at alpha edges
    f = rgba.astype(np.float32) / 255
    pm = np.dstack([f[..., :3] * f[..., 3:4], f[..., 3]])
    chans = [np.asarray(Image.fromarray(pm[..., c]).resize((w, h), flt)) for c in range(4)]
    out = np.dstack(chans)
    a = np.clip(out[..., 3:4], 0, 1)
    rgb = np.where(a > 1e-4, out[..., :3] / np.maximum(a, 1e-4), 0)
    return (np.clip(np.dstack([rgb, a]), 0, 1) * 255 + 0.5).astype(np.uint8)


# ------------------------------------------------------------------ verification
def composite(rgba, bg):
    f = rgba.astype(np.float32)
    a = f[..., 3:4] / 255
    return f[..., :3] * a + bg * (1 - a)


def metrics(orig, hi):
    """Visual round-trip check: hi-res drawn and averaged back to 1x vs the original
    drawn at 1x, over black and over white (what a player would actually see)."""
    h, w = orig.shape[:2]
    worst_psnr, worst_px = 99.0, 0
    for bg in (0.0, 255.0):
        ref = composite(orig, bg)
        drawn = composite(hi, bg).reshape(h, hi.shape[0] // h, w, hi.shape[1] // w, 3).mean(axis=(1, 3))
        err = drawn - ref
        mse = (err ** 2).mean()
        psnr = 99.0 if mse == 0 else 10 * np.log10(255 ** 2 / mse)
        worst_psnr = min(worst_psnr, psnr)
        worst_px = max(worst_px, int((np.abs(err).max(axis=2) > 48).sum()))
    alpha_mae = np.abs(hi[..., 3].reshape(h, hi.shape[0] // h, w, -1).mean(axis=(1, 3)) -
                       orig[..., 3].astype(np.float32)).mean()
    return worst_psnr, alpha_mae, worst_px


def checker(h, w, cell=8):
    y, x = np.mgrid[0:h, 0:w]
    c = (((y // cell) + (x // cell)) % 2) * 40 + 90
    return np.dstack([c, c, c]).astype(np.float32)


def over_checker(rgba):
    a = rgba[..., 3:4].astype(np.float32) / 255
    return (rgba[..., :3] * a + checker(*rgba.shape[:2]) * (1 - a)).astype(np.uint8)


def contact_sheet(rows, path_base, k, per_page=24, max_w=160):
    """rows: (name, orig, hi_k). Columns: original (nearest), bilinear (what the game shows
    without the mod's art), upscaled; all at k x. Wide textures are cropped to max_w (1x px)."""
    from PIL import ImageDraw
    pages = []
    for start in range(0, len(rows), per_page):
        tiles = []
        for name, orig, hi in rows[start:start + per_page]:
            h, w = orig.shape[:2]
            cw = min(w, max_w)
            o = orig[:, :cw]
            near = np.asarray(Image.fromarray(o, "RGBA").resize((cw * k, h * k), Image.NEAREST))
            bil = np.asarray(Image.fromarray(o, "RGBA").resize((cw * k, h * k), Image.BILINEAR))
            row = np.hstack([over_checker(x) for x in (near, bil, hi[:, :cw * k])])
            label = Image.fromarray(np.zeros((14, row.shape[1], 3), np.uint8))
            ImageDraw.Draw(label).text((2, 1), name + (" (cropped)" if cw < w else ""),
                                       fill=(255, 255, 0))
            tiles.append(np.vstack([np.asarray(label), row, np.zeros((6, row.shape[1], 3), np.uint8)]))
        width = max(t.shape[1] for t in tiles)
        sheet = np.vstack([np.pad(t, ((0, 0), (0, width - t.shape[1]), (0, 0))) for t in tiles])
        p = "%s_%02d.png" % (path_base, len(pages) + 1)
        Image.fromarray(sheet).save(p)
        pages.append(p)
    return pages


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("game")
    ap.add_argument("out")
    ap.add_argument("--category", default="frames", choices=sorted(CATEGORIES))
    ap.add_argument("--upscaler", default="esrgan", choices=["esrgan", "lanczos"])
    ap.add_argument("--esrgan", default=os.path.join(os.path.dirname(__file__), "..", "build",
                                                     "upscaler", "pkg", "realesrgan-ncnn-vulkan"))
    ap.add_argument("--model", default="realesrgan-x4plus-anime")
    a = ap.parse_args()

    keys = CATEGORIES[a.category]
    items = []  # (name, w, h, mips, mip0 bgra bytes)
    for name, off, size, data in fpk.entries(os.path.join(a.game, "Resource", "DX9", "UITextures.fpk")):
        n = name.lower()
        if not n.endswith(".dds") or n.endswith("-index.dds") or not n.startswith(keys):
            continue
        p = parse_dds(data[off:off + size])
        if p:
            items.append((n,) + p)
    print("%s: %d textures" % (a.category, len(items)))

    origs = [np.frombuffer(px, np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()
             for _, w, h, _, px in items]
    up = Esrgan(os.path.abspath(a.esrgan), a.model) if a.upscaler == "esrgan" else None
    big = upscale_textures(origs, up)
    base = upscale_textures(origs, None) if up else big  # Lanczos reference for the report

    os.makedirs(os.path.join(a.out, "report"), exist_ok=True)
    report = []
    for k in SCALES:
        d = os.path.join(a.out, "x%d" % k)
        os.makedirs(d, exist_ok=True)
        manifest = []
        rows = []
        for (n, w, h, mips, px), orig, b4, l4 in zip(items, origs, big, base):
            hi = resize_rgba(b4, w * k, h * k, Image.LANCZOS)
            fn = n[:-4] + "_x%d.dds" % k
            write_dds_bgra(os.path.join(d, fn), mip_chain(hi))
            manifest.append("%016x %d %d %d %s" % (fnv1a64(px), w, h, k, fn))
            if k == 2:
                psnr, amae, bad = metrics(orig, hi)
                ref, _, _ = metrics(orig, resize_rgba(l4, w * k, h * k, Image.LANCZOS))
                report.append((n, w, h, psnr, ref, amae, bad))
                rows.append((n, orig, hi))
        with open(os.path.join(d, "uiart.txt"), "a") as f:
            f.write("\n".join(manifest) + "\n")
        if rows:
            contact_sheet(rows, os.path.join(a.out, "report", a.category), k)

    # Thin / tiny textures cannot round-trip exactly with any smooth upscaler, so the
    # AI result is judged against a plain Lanczos baseline: flag it when it is clearly
    # worse than Lanczos or outright broken.
    lines = ["%-34s %8s %9s %9s %9s %7s" % ("texture", "size", "PSNR", "Lanczos", "alphaMAE",
                                            ">48px")]
    flagged = 0
    for n, w, h, psnr, ref, amae, bad in sorted(report, key=lambda r: r[3] - r[4]):
        bad_flag = psnr < 20 or psnr < ref - 6
        flagged += bad_flag
        lines.append("%-34s %8s %9.1f %9.1f %9.2f %7d%s" % (n, "%dx%d" % (w, h), psnr, ref, amae,
                                                           bad, "  <-- check" if bad_flag else ""))
    lines.append("%d of %d textures flagged" % (flagged, len(report)))
    txt = "\n".join(lines)
    open(os.path.join(a.out, "report", a.category + ".txt"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
