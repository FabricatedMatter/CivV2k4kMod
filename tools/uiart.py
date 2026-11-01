#!/usr/bin/env python3
"""Hi-res UI art for Civ V: geometric reconstruction where the art is geometric,
local AI upscaling where it is not.

  uiart.py merge  OUT_DIR PART_DIR...   (fold parallel category builds into OUT_DIR)
  uiart.py build  GAME_DIR OUT_DIR --category frames [--esrgan PATH] [--model remacri-4x]
                  [--only REGEX] [--force-method box|axis|ai|lanczos]
  -> OUT_DIR/x<k>/<name>_x<k>.dds   hi-res BGRA DDS with full mip chain
     OUT_DIR/x<k>/uiart.txt         manifest for the DLL:
                                     <fnv64 of original mip0> <w> <h> <k> <file>
     OUT_DIR/report/<category>.png  contact sheet: original | bilinear | upscaled
     OUT_DIR/report/<category>.txt  per-texture error metrics (round trip to 1x)

The DLL swaps textures on upload (matched by hash) and the UI's normalized UVs make
the k x texture line up exactly with the original layout.

Methods (chosen per texture by METHODS, first matching prefix wins):
  box      tools/uirecon.py: straight bands/edges re-rendered sharp + fitted rounded
           corners re-rendered as true arcs (9-slice panels, buttons, frames)
  axis     tools/uirecon.py without corners (trims, bars, square frames, fills)
  ai       Real-ESRGAN-ncnn 4x (default model: Remacri), resampled to k x and then
           projected so it averages back to the original exactly (ornaments, diagonals)
  lanczos  plain resampling (soft glows, where any sharpening would be wrong)
  mask     single-colour alpha masks (white symbol atlases): smooth upsampling, 50 % edge,
           anti-aliased and projected to the original coverage
  ring     tools/uirecon.py: thin half-circle strokes tangent to straight lines, fitted
           and redrawn analytically (city banner outlines)
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fpk  # noqa: E402
import uirecon  # noqa: E402
import uisprite  # noqa: E402
import bcn  # noqa: E402

CATEGORIES = {
    # Stage 1: frames, borders, separators, scroll bars and other window chrome.
    "frames": (
        "grid9", "windowgrid", "windowhlgrid", "frametopbottom", "corner16", "dec32x64",
        "dectop", "sideright", "horizontaltrim", "verticaltrim", "divider", "scrollbar",
        "slider", "dropdown", "gridsquare", "gridglass", "glassback", "bar300x2", "bar340x2",
        "bar400x2", "bar500x2", "bar900x2", "connect_", "selectedup", "selecteddown",
        "blankbutton", "newx",
    ),
    # Stage 2: city banners, button backgrounds, top panel, window buttons.
    "banners": (
        "citybanner", "citystatemeter", "garrisonframe", "citybombard", "buttonsides",
        "toppanel", "closeminbuttons", "civilopediatopbuttons", "civilopediasidebuttons",
        "civilopediabuttons", "64x64framebuttons", "45x45framebutton", "actionitemsbutton",
        "buttons_", "listbutton", "mainstrategicbutton", "maintechbutton", "unitactionbutton",
        "buttonmask", "oversupplylimit",
    ),
}
# Icon atlases (painted art: AI). Stage 3.
CATEGORIES.update({
    "resources": ("resource",),
    "fonticons": ("fonticon",),
    "units": ("unitportrait", "unit_", "unitflag", "unitaction"),
    "civs": ("civsymbol", "religioussymbol"),
    # everything else in the UI archives (buildings, wonders, leaders, religion, policies,
    # tech tree, notifications, ...)
    "rest": (),
    # icon / portrait atlases named in IconTextureAtlases XML (buildings, wonders,
    # leaders, techs, policies, promotions, ...)
    "atlases": (),
    # BC1/BC3-compressed UI art (wonder concept art, advisors, leader screens, ...)
    "compressed": (),
})
# Method for textures no METHODS prefix matches.
CATEGORY_DEFAULT = {"frames": "box", "banners": "ai", "resources": "ai", "fonticons": "ai",
                    "units": "ai", "civs": "ai", "rest": "ai",
                    "atlases": "ai", "compressed": "ai"}
# Per-texture method, by name prefix (first match wins); everything else: box.
METHODS = (
    # white symbol masks tinted by the game (auto-detection misses the small sizes)
    ("religioussymbolswhite", "mask"),
    # banners: straight / rounded 9-slice pieces and plain glows
    ("buttonsidesglow", "lanczos"), ("citybannerbuttonglow", "lanczos"),
    # round banner pieces (half discs, pill ends, arcs): box approximates the arcs with
    # visible steps; AI keeps them round
    ("citybannerframeleft", "ring"), ("citybannerframeright", "ring"),
    ("buttonsides", "ai"), ("citybannerframe", "axis"), ("citybannerbackground", "box"),
    ("citybannerleftbackground", "ai"), ("citybannerrightbackground", "ai"),
    ("citybannerbuttonbase", "ai"), ("citybannerstrengthback", "ai"),
    ("citybannergrowth", "ai"), ("citybannerproductionbackground", "ai"),
    ("citystatemeterbase", "box"), ("citystatemeterblack", "box"), ("toppanelbar", "axis"),
    ("listbutton", "box"),
    # ornaments with diagonals and arcs: no axis-aligned structure to reconstruct
    ("dec32x64", "ai"), ("dectop", "ai"), ("corner16", "ai"), ("newx", "ai"),
    ("slider32", "ai"), ("selectedup", "ai"), ("selecteddown", "ai"),
    ("grid9detail140", "ai"), ("grid9detailtwo140", "ai"), ("grid9detailthree140", "ai"),
    ("grid9detailfour140", "ai"), ("grid9detailfive140", "ai"), ("grid9detailsix140", "ai"),
    ("grid9frame8fade", "ai"),
    # soft glows: band-limited by design
    ("windowhlgrid27", "lanczos"), ("grid9frameredhl", "lanczos"),
    ("grid9frameturnshl", "lanczos"), ("glassback", "lanczos"),
    # straight structure only
    ("bar300x2", "axis"), ("bar340x2", "axis"), ("bar400x2", "axis"), ("bar500x2", "axis"),
    ("bar900x2", "axis"), ("frametopbottom", "axis"), ("horizontaltrim", "axis"),
    ("verticaltrim", "axis"), ("sideright", "axis"), ("scrollbar", "axis"),
    ("gridglass", "axis"), ("gridsquare", "axis"), ("grid9listitem", "axis"),
    ("grid9boxbuttons", "axis"), ("grid9frame27square", "axis"), ("grid9framered", "axis"),
    ("grid9framegreenfade", "axis"), ("grid9frameturns", "axis"), ("grid9tech", "ai"),
    ("grid9detail48", "axis"), ("dropdownbuttons", "axis"), ("windowgrid27", "axis"),
    ("divider", "axis"), ("blankbutton", "axis"),
)


def method_for(name, default="box"):
    name = name.split(".")[0]
    for prefix, m in METHODS:
        if name.startswith(prefix):
            return m
    return default


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


CUGAN_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build", "upscaler",
                         "cugan_dl", "realcugan-ncnn-vulkan-20220728-ubuntu",
                         "realcugan-ncnn-vulkan")


class Esrgan:
    def __init__(self, exe, model):
        self.exe, self.model = exe, model
        # Remacri / UltraSharp come in upscayl's ncnn format; same binary, same CLI
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
                src, dst = os.path.join(ind, "%05d.png" % i), os.path.join(outd, "%05d.png" % i)
                if self.model.startswith("cugan"):
                    # cugan[:variant[:noise]]: Real-CUGAN (models-se / models-pro), 4x
                    parts = (self.model.split(":") + ["models-se", "-1"])[1:3]
                    exe = CUGAN_EXE
                    cmd = [exe, "-i", src, "-o", dst, "-s", str(ESRGAN_SCALE), "-n", parts[1],
                           "-m", os.path.join(os.path.dirname(exe), parts[0]), "-f", "png"]
                else:
                    cmd = [self.exe, "-i", src, "-o", dst, "-n", self.model,
                           "-s", str(ESRGAN_SCALE), "-m", self.models, "-f", "png"]
                gpu = os.environ.get("UIART_GPU")
                if gpu is not None:
                    cmd += ["-g", gpu]
                for attempt in range(4):  # ncnn aborts when VRAM is short; retry
                    r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if r.returncode == 0 and os.path.exists(dst):
                        break
                    time.sleep(2 + 3 * attempt)
                else:
                    raise RuntimeError("upscaler failed: " + " ".join(cmd))
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
        # uniform alpha (opaque art) needs no AI pass: it stays uniform
        flat = [p[..., 3].min() == p[..., 3].max() for p in padded]
        work = [x for x, f in zip(alpha_in, flat) if not f]
        outs = up.run(rgb_in + work)
        rgb_out, it = outs[:len(rgb_in)], iter(outs[len(rgb_in):])
        alpha_out = [np.full((x.shape[0] * ESRGAN_SCALE, x.shape[1] * ESRGAN_SCALE, 3),
                             x[0, 0, 0], np.uint8) if f else next(it)
                     for x, f in zip(alpha_in, flat)]
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


def staircase(orig, hi, k):
    """Share of alpha-edge pixels whose k x k block in the hi-res image is uniform, i.e.
    looks like a nearest-neighbour copy of the 1x edge. Curved edges rendered by the
    axis/box methods without a fitted arc score high (visible stair steps)."""
    a = orig[..., 3].astype(np.float32)
    gy, gx = np.gradient(a)
    # partially covered pixels on diagonal / curved edges only: straight axis-aligned
    # edges legitimately become uniform k x k blocks
    diag = np.minimum(np.abs(gx), np.abs(gy)) > 0.35 * np.maximum(np.abs(gx), np.abs(gy))
    edge = (a > 20) & (a < 235) & diag & (np.hypot(gx, gy) > 20)
    if edge.sum() < 8:
        return 0.0
    h, w = a.shape
    blk = hi[..., 3].astype(np.float32).reshape(h, k, w, k)
    spread = blk.max(axis=(1, 3)) - blk.min(axis=(1, 3))
    return float((spread[edge] < 8).mean())


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
UI_ARCHIVES = {"uitextures": "", "expansion1uitextures": "e1", "expansion2uitextures": "e2"}


def load_sources(game):
    """[(tag, name, off, size, data)] from every DX9 texture archive plus loose DDS files
    under Assets/**/UI/. tag: "" / e1 / e2 for the three UI archives, the archive name
    for other archives, "loose-<dir>" for loose files. UI archives are flagged by is_ui()."""
    import glob
    out = []
    for path in sorted(glob.glob(os.path.join(game, "Resource", "DX9", "*.fpk"))):
        base = os.path.basename(path).lower()[:-4]
        tag = UI_ARCHIVES.get(base, base.replace("textures", ""))
        out += [(tag,) + e for e in fpk.entries(path)]
    for path in sorted(glob.glob(os.path.join(game, "Assets", "**", "*.dds"), recursive=True)):
        rel = os.path.relpath(path, os.path.join(game, "Assets")).replace(os.sep, "/")
        if "/UI/" not in "/" + rel or "/fonts/" in rel.lower():
            continue
        data = open(path, "rb").read()
        parts = rel.split("/")
        tag = "loose-" + (parts[1].lower() if parts[0] == "DLC" else "base")
        out.append((tag, os.path.basename(path), 0, len(data), data))
    return out


def is_ui(tag):
    return tag in ("", "e1", "e2", "upgrade1") or tag.startswith("loose-")


# names handled elsewhere (fontgen) or not UI art
NOT_ART = ("twcen", "arial", "atlas_environment", "missingtexture")


ATLAS_FILES = set()  # icon atlas file names from the game's IconTextureAtlases XML


def load_atlas_files(game):
    import glob
    import re
    for f in glob.glob(os.path.join(game, "Assets", "**", "*IconTextureAtlases*.xml"),
                       recursive=True):
        t = open(f, encoding="latin-1").read()
        ATLAS_FILES.update(m.lower() for m in re.findall(r"<Filename>([^<]+)</Filename>", t))
        ATLAS_FILES.update(m.lower() for m in re.findall(r'Filename="([^"]+)"', t))


def in_category(cat, name, tag):
    if cat == "compressed":  # any BC texture in the UI archives (format checked later)
        return is_ui(tag) and not name.startswith(NOT_ART)
    named = [c for c in CATEGORIES if c not in ("rest", "atlases", "compressed")]
    if cat in ("rest", "atlases"):
        if not is_ui(tag) or name.startswith(NOT_ART):
            return False
        if any(name.startswith(CATEGORIES[c]) for c in named):
            return False
        return (name in ATLAS_FILES) == (cat == "atlases")
    return name.startswith(CATEGORIES[cat])


def render(method, orig, k, ai4=None, fits=None):
    """orig: 1x RGBA uint8 -> (k x RGBA uint8, box fits)."""
    h, w = orig.shape[:2]
    pm = uirecon.premul(orig)
    if method == "lanczos":
        return resize_rgba(orig, w * k, h * k, Image.LANCZOS), None
    if method == "axis":
        return uirecon.unpremul(uirecon.axis_upscale(pm, k)), None
    if method == "box":
        hi, fits = uirecon.box_upscale(pm, k, radii=fits and [f for _, f in fits])
        return uirecon.unpremul(hi), fits
    if method == "mask":
        return uirecon.unpremul(uirecon.mask_upscale(orig, k)), None
    if method == "ring":
        return uirecon.unpremul(uirecon.ring_upscale(pm, k)[0]), None
    if method == "ai":
        hi = uirecon.premul(resize_rgba(ai4, w * k, h * k, Image.LANCZOS))
        return uirecon.unpremul(uirecon.project(hi, pm, k, smooth=True)), None
    raise ValueError(method)


def merge(out, parts):
    """Fold separately built category dirs (parallel builds) into out: textures are
    copied, manifests merged (later entries win per file name)."""
    import shutil
    for k in SCALES:
        d = os.path.join(out, "x%d" % k)
        os.makedirs(d, exist_ok=True)
        mf = os.path.join(d, "uiart.txt")
        entries = {}
        for src in [d] + [os.path.join(p, "x%d" % k) for p in parts]:
            m = os.path.join(src, "uiart.txt")
            if not os.path.exists(m):
                continue
            for line in open(m).read().splitlines():
                if line.strip():
                    fn = line.split()[4]
                    if src != d:
                        shutil.copy2(os.path.join(src, fn), os.path.join(d, fn))
                    entries[fn] = line
        open(mf, "w").write("\n".join(entries.values()) + "\n")
        print("x%d: %d textures" % (k, len(entries)))


def main():
    import re
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        return merge(sys.argv[2], sys.argv[3:])
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("game")
    ap.add_argument("out")
    ap.add_argument("--category", default="frames", choices=sorted(CATEGORIES))
    ap.add_argument("--esrgan", default=os.path.join(os.path.dirname(__file__), "..", "build",
                                                     "upscaler", "pkg", "realesrgan-ncnn-vulkan"))
    ap.add_argument("--model", default="remacri-4x")
    ap.add_argument("--only", help="regex: restrict to matching texture names")
    ap.add_argument("--force-method", choices=["box", "axis", "ai", "lanczos", "ring", "mask"])
    ap.add_argument("--list", action="store_true", help="only list textures and methods")
    ap.add_argument("--only-method", help="restrict to textures assigned this method")
    ap.add_argument("--scales", default="2,3", help="output scales, e.g. 2 or 2,3")
    ap.add_argument("--resume", action="store_true",
                    help="skip textures whose outputs already exist in OUT_DIR")
    ap.add_argument("--max-size", type=int, default=1024,
                    help="skip textures whose (assembled) image is larger than this")
    a = ap.parse_args()
    global SCALES
    SCALES = tuple(int(x) for x in a.scales.split(","))

    rx = re.compile(a.only) if a.only else None
    # Base game, G&K, BNW, patches and DLC each ship their own versions of many UI
    # textures; which one the game uploads depends on what the player has installed, so
    # build every distinct version (the DLL matches by content hash).
    entries = load_sources(a.game)
    load_atlas_files(a.game)
    # "-index" tile sprites: the data texture is replaced, the index map stays (uisprite.py)
    index_maps = {(tag, n.lower()[:-len("-index.dds")]): data[off:off + size]
                  for tag, n, off, size, data in entries if n.lower().endswith("-index.dds")}
    items, seen = [], set()
    for tag, name, off, size, data in entries:
        n = name.lower()
        if not n.endswith(".dds") or n.endswith("-index.dds") or not in_category(a.category, n, tag):
            continue
        if rx and not rx.search(n):
            continue
        raw = data[off:off + size]
        p = parse_dds(raw)
        bc = None
        if p is None:
            q = bcn.parse_dds_bc(raw) if a.category == "compressed" else None
            if q is None:
                continue
            w, h, _, bc, px = q
            rgba = bcn.decode(w, h, bc, px)
        else:
            if a.category == "compressed":
                continue
            w, h, _, px = p
            rgba = np.frombuffer(px, np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()
        if px in seen:
            continue
        # manifest fields are space-separated (and read with %s by the DLL)
        out_name = ((n[:-4] + "." + tag + ".dds") if tag else n).replace(" ", "_")
        it = {"name": out_name, "w": w, "h": h, "px": px,
              "data": rgba, "index": None, "bc": bc}
        ib = index_maps.get((tag, n[:-4]))
        if ib is not None:
            idx = uisprite.parse_index(ib)
            t = uisprite.tile_size(w, h, idx) if idx is not None else None
            if not t:
                print("skipping %s: unknown sprite layout" % it["name"])
                continue
            it.update(index=idx, t=t, src=uisprite.assemble(it["data"], idx, t))
        else:
            it["src"] = it["data"]
        if max(it["src"].shape[:2]) > a.max_size:
            continue
        if a.resume and all(os.path.exists(os.path.join(a.out, "x%d" % k, it["name"][:-4] +
                                                        "_x%d.dds" % k)) for k in SCALES):
            continue
        seen.add(px)
        items.append(it)
    default = CATEGORY_DEFAULT.get(a.category, "box")
    methods = [a.force_method or method_for(it["name"], default) for it in items]
    # single-colour masks (white symbol atlases): exact mask upscale instead of AI
    methods = [("mask" if not a.force_method and m == "ai" and uirecon.is_mask(it["src"]) else m)
               for it, m in zip(items, methods)]
    if a.only_method:
        keep = [i for i, m in enumerate(methods) if m == a.only_method]
        items, methods = [items[i] for i in keep], [methods[i] for i in keep]
    print("%s: %d textures, %d of them tile sprites (%s)" % (
        a.category, len(items), sum(it["index"] is not None for it in items),
        ", ".join("%s %d" % (m, methods.count(m)) for m in ("box", "axis", "ai", "lanczos"))))

    if a.list:
        for it, m in zip(items, methods):
            print("%-44s %4dx%-4d %-7s %s" % (it["name"], it["src"].shape[1], it["src"].shape[0], m,
                                             "sprite t=%d" % it["t"] if it["index"] is not None else ""))
        return
    os.makedirs(os.path.join(a.out, "report"), exist_ok=True)
    report, fits, rows = [], {}, []
    up = Esrgan(os.path.abspath(a.esrgan), a.model) if "ai" in methods else None
    # Chunks: upscale, write the textures and update the manifests, so an interrupted
    # build continues with --resume where it stopped.
    CHUNK = 24
    for c0 in range(0, len(items), CHUNK):
        citems, cmeth = items[c0:c0 + CHUNK], methods[c0:c0 + CHUNK]
        origs = [it["src"] for it in citems]
        ai_idx = [i for i, m in enumerate(cmeth) if m == "ai"]
        ai4 = dict(zip(ai_idx, upscale_textures([origs[i] for i in ai_idx], up))) if ai_idx else {}
        for k in SCALES:
            d = os.path.join(a.out, "x%d" % k)
            os.makedirs(d, exist_ok=True)
            manifest = []
            for i, (it, orig, m) in enumerate(zip(citems, origs, cmeth)):
                n, w, h, px = it["name"], it["w"], it["h"], it["px"]
                hi, f = render(m, orig, k, ai4.get(i), fits.get(n))
                if f:
                    fits[n] = f
                out = hi
                if it["index"] is not None:
                    fallback = np.repeat(np.repeat(it["data"], k, 0), k, 1)
                    out = uisprite.disassemble(hi, it["index"], it["t"], k, it["data"].shape,
                                               fallback)
                fn = n[:-4] + "_x%d.dds" % k
                if it["bc"]:
                    bcn.encode_dds(os.path.join(d, fn), out, it["bc"])
                elif it["index"] is not None:
                    # tile atlases are uploaded with a single level; mips would be wasted
                    write_dds_bgra(os.path.join(d, fn), [out])
                else:
                    write_dds_bgra(os.path.join(d, fn), mip_chain(out))
                manifest.append("%016x %d %d %d %s" % (fnv1a64(px), w, h, k, fn))
                if k == 2:
                    psnr, amae, bad = metrics(orig, hi)
                    stair = staircase(orig, hi, k)
                    ref, _, _ = metrics(orig, resize_rgba(orig, orig.shape[1] * k,
                                                          orig.shape[0] * k, Image.LANCZOS))
                    radii = "" if n not in fits else " ".join(
                        "/".join("%g" % float(v) for v in fc.values()) for _, fc in fits[n])
                    report.append((n, w, h, m, psnr, ref, amae, bad, "%s%s" % (
                        "STAIRS? %.0f%% " % (100 * stair) if stair > 0.15 else "", radii)))
                    # keep only what the contact sheet shows (it crops to 160 x 1x px)
                    rows.append(("%s [%s]" % (n, m), orig[:, :160].copy(),
                                 hi[:, :160 * k].copy()))
            # merge with manifests of other categories / earlier chunks
            mf = os.path.join(d, "uiart.txt")
            keep = []
            if os.path.exists(mf):
                mine = {l.split()[4] for l in manifest}
                keep = [l for l in open(mf).read().splitlines()
                        if l.strip() and l.split()[4] not in mine]
            open(mf, "w").write("\n".join(keep + manifest) + "\n")
        print("chunk %d-%d of %d done" % (c0, c0 + len(citems), len(items)), flush=True)
    if rows:
        contact_sheet(rows, os.path.join(a.out, "report", a.category), 2)

    lines = ["%-34s %8s %-8s %7s %8s %9s %6s  %s" % ("texture", "size", "method", "PSNR",
                                                    "Lanczos", "alphaMAE", ">48px", "corner radii")]
    for n, w, h, m, psnr, ref, amae, bad, radii in sorted(report, key=lambda r: (r[3], r[0])):
        lines.append("%-34s %8s %-8s %7.1f %8.1f %9.2f %6d  %s" % (n, "%dx%d" % (w, h), m, psnr,
                                                                 ref, amae, bad, radii))
    txt = "\n".join(lines)
    open(os.path.join(a.out, "report", a.category + ".txt"), "w").write(txt + "\n")
    print(txt)


if __name__ == "__main__":
    main()
