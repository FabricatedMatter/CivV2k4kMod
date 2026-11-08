"""Window art for the setup program, read from the player's own Civilization V files.

The installer ships no game art. When it finds the game it reads the few textures it
draws its window with (the main menu panel frame, buttons, fields, close button, meter)
straight from the game's .fpk archives. Hi-res versions come from the installed mod
(UIScale/UIArt/x2) or an art pack next to the installer; without either the originals
are upscaled. Without the game the window falls back to drawn stand-ins.

All images are returned at 2x the game's 1x pixel size (PIL RGBA).
"""
import glob
import os
import struct
import sys

from PIL import Image

import setup_core as core

TAGS = {"UITextures": "", "Expansion1UITextures": "e1", "Expansion2UITextures": "e2"}

# element: candidates (archive, texture) in order of preference
ART = {
    "frame": [("Expansion1UITextures", "grid9detail162.dds")],
    "button": [("Expansion2UITextures", "grid9framebuttons.dds"),
               ("UITextures", "grid9framebuttons.dds")],
    "field": [("Expansion2UITextures", "grid9frame27.dds"), ("UITextures", "grid9frame27.dds")],
    "close": [("Expansion2UITextures", "closebutton.dds"), ("UITextures", "closebutton.dds")],
    "check": [("Expansion2UITextures", "checkbox.dds"), ("UITextures", "checkbox.dds")],
    "meter_trim": [("Expansion2UITextures", "loadingmetertrim.dds"),
                   ("Expansion1UITextures", "loadingmetertrim.dds"),
                   ("UITextures", "loadingmetertrim.dds")],
    "meter_fill": [("Expansion2UITextures", "loadingmeterhl.dds"),
                   ("Expansion1UITextures", "loadingmeterhl.dds"),
                   ("UITextures", "loadingmeterhl.dds")],
}


# ------------------------------------------------------------------ game archives
def fpk_read(path, names):
    """{name: bytes} for the wanted entries of a .fpk, reading only the index and those."""
    want = {n.lower() for n in names}
    out = {}
    with open(path, "rb") as f:
        head = f.read(1 << 20)
        if head[4:8] != b"FPK_":
            return out
        (count,) = struct.unpack_from("<I", head, 10)
        pos, found = 14, []
        for _ in range(count):
            if pos + 4 > len(head):
                head += f.read(1 << 20)
            (n,) = struct.unpack_from("<I", head, pos)
            pos += 4
            while pos + n + 20 > len(head):
                more = f.read(1 << 20)
                if not more:
                    return out
                head += more
            name = head[pos:pos + n].decode("latin-1").lower()
            pos += n + (4 - n % 4) % 4
            _a, _b, size, offset = struct.unpack_from("<IIII", head, pos)
            pos += 16
            if name in want:
                found.append((name, offset, size))
        for name, offset, size in found:
            f.seek(offset)
            out[name] = f.read(size)
    return out


# ------------------------------------------------------------------ DDS
def decode_dds(data):
    """RGBA uint8 array of mip 0 (32-bit uncompressed, BC1, BC3), or None."""
    import numpy as np
    if data[:4] != b"DDS ":
        return None
    h, w = struct.unpack_from("<II", data, 12)
    flags, four, bits = struct.unpack_from("<I4sI", data, 80)
    if flags & 4:
        import bcn
        r = bcn.parse_dds_bc(data)
        return bcn.decode(r[0], r[1], r[3], r[4]) if r else None
    if bits != 32:
        return None
    masks = struct.unpack_from("<4I", data, 92)
    px = np.frombuffer(data[128:128 + w * h * 4], np.uint8).reshape(h, w, 4)
    order = [{0xFF: 0, 0xFF00: 1, 0xFF0000: 2, 0xFF000000: 3}.get(m, i)
             for i, m in enumerate(masks)]          # channel byte of R, G, B, A
    out = px[:, :, order].copy()
    if not masks[3]:
        out[:, :, 3] = 255
    return out


def index_map(data):
    import numpy as np
    h, w = struct.unpack_from("<II", data, 12)
    bits = struct.unpack_from("<I", data, 88)[0]
    if bits not in (8, 16):
        return None
    dt = np.uint8 if bits == 8 else np.uint16
    return np.frombuffer(data[128:128 + w * h * bits // 8], dt).reshape(h, w).astype(int)


def assemble(tiles, index, k):
    """Full image from tile data (k = scale of tiles relative to the 1x index layout)."""
    import uisprite
    t = uisprite.tile_size(tiles.shape[1] // k, tiles.shape[0] // k, index)
    return uisprite.assemble(tiles, index, t * k) if t else None


# ------------------------------------------------------------------ loading
class Art:
    """Loads ART elements from a game folder. art[name] -> PIL image at 2x, or None."""

    def __init__(self, game):
        self.game = game
        self.images = {}
        self.hires = False
        if not game or not os.path.isdir(os.path.join(game, "Resource", "DX9")):
            return
        try:
            self._load()
        except Exception as e:  # never let art problems stop the installer
            print("skin: %s" % e, file=sys.stderr)

    def __getitem__(self, name):
        return self.images.get(name)

    def _packs(self):
        d = core.installer_dir()
        return [p for p in glob.glob(os.path.join(d, "civ5-uiscale-art-x2*.pack")) +
                glob.glob(os.path.join(d, "packs", "civ5-uiscale-art-x2*.pack"))
                if os.path.isfile(p)]

    def _load(self):
        import numpy as np
        wanted = {}
        for cands in ART.values():
            for arc, tex in cands:
                wanted.setdefault(arc, set()).update((tex, tex[:-4] + "-index.dds"))
        files = {}
        for arc, names in wanted.items():
            p = os.path.join(self.game, "Resource", "DX9", arc + ".fpk")
            if os.path.isfile(p):
                for n, d in fpk_read(p, names).items():
                    files[(arc, n)] = d
        chosen = {}
        for el, cands in ART.items():
            for arc, tex in cands:
                if (arc, tex) in files:
                    chosen[el] = (arc, tex)
                    break
        # hi-res: installed mod files, else the art pack
        hi = {}
        x2dir = os.path.join(self.game, "UIScale", "UIArt", "x2")
        for el, (arc, tex) in chosen.items():
            tag = TAGS[arc]
            fn = os.path.join(x2dir, "%s%s_x2.dds" % (tex[:-4], "." + tag if tag else ""))
            if os.path.isfile(fn):
                hi[el] = open(fn, "rb").read()
        missing = [el for el in chosen if el not in hi]
        packs = self._packs()
        if missing and packs:
            import pack
            for el, data in zip(missing, pack.unlock(packs, [files[chosen[el]] for el in missing])):
                if data:
                    hi[el] = data
        self.hires = bool(hi) and len(hi) == len(chosen)
        for el, (arc, tex) in chosen.items():
            lo = decode_dds(files[(arc, tex)])
            if lo is None:
                continue
            idx_data = files.get((arc, tex[:-4] + "-index.dds"))
            idx = index_map(idx_data) if idx_data else None
            if idx is not None:
                lo = assemble(lo, idx, 1)
                if lo is None:
                    continue
            img = None
            if el in hi:
                h = decode_dds(hi[el])
                if h is not None:
                    if h.shape[:2] == (lo.shape[0] * 2, lo.shape[1] * 2):
                        img = h                        # full image (or plain texture)
                    elif idx is not None:
                        img = assemble(h, idx, 2)      # tile data at 2x
                    if img is not None and img.shape[:2] != (lo.shape[0] * 2, lo.shape[1] * 2):
                        img = None
            if img is None:
                img = np.asarray(Image.fromarray(lo).resize(
                    (lo.shape[1] * 2, lo.shape[0] * 2), Image.LANCZOS))
            self.images[el] = Image.fromarray(np.ascontiguousarray(img), "RGBA")


# ------------------------------------------------------------------ stand-ins
# Drawn when the game (or one texture) is not available. Same layout as the textures.
STONE = [(250, 240, 228), (214, 202, 188), (150, 140, 130), (96, 90, 84)]


def _vgrad(w, h, top, bottom):
    img = Image.new("RGBA", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return img.resize((w, h))


def _rounded(size, radius, fill_top, fill_bottom, border, bw=2):
    from PIL import ImageDraw
    w, h = size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=255)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(Image.new("RGBA", size, border), (0, 0), mask)
    inner = Image.new("L", size, 0)
    ImageDraw.Draw(inner).rounded_rectangle((bw, bw, w - 1 - bw, h - 1 - bw), max(1, radius - bw),
                                            fill=255)
    out.paste(_vgrad(w, h, fill_top, fill_bottom), (0, 0), inner)
    return out


def fallback(name):
    from PIL import ImageDraw
    if name == "frame":
        img = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        img.paste(_vgrad(320, 30, STONE[0], STONE[2]), (0, 0))
        img.paste(_vgrad(308, 48, (24, 42, 66), (34, 64, 78)), (6, 30))
        img.paste(_vgrad(300, 16, STONE[1], STONE[3]), (10, 80))
        for x in (20, 270):  # capitals
            img.paste(_vgrad(30, 50, STONE[1], STONE[3]), (x, 28))
        img.paste(_vgrad(300, 34, STONE[0], STONE[3]), (10, 196))
        fan = Image.new("L", (260, 70), 0)
        ImageDraw.Draw(fan).pieslice((0, -70, 259, 69), 0, 180, fill=255)
        rim = Image.new("RGBA", (260, 70), STONE[2])
        img.paste(rim, (30, 228), fan)
        fan2 = fan.resize((240, 62))
        img.paste(_vgrad(240, 62, (28, 52, 76), (54, 108, 112)), (40, 228), fan2)
        return img
    if name == "button":
        img = Image.new("RGBA", (128, 512), (0, 0, 0, 0))
        looks = [((30, 70, 70), (14, 36, 38), (196, 164, 92)),
                 ((60, 130, 128), (24, 70, 72), (236, 206, 130)),
                 ((14, 40, 42), (24, 60, 62), (196, 164, 92)),
                 ((14, 16, 18), (8, 10, 12), (110, 104, 92))]
        for i, (a, b, c) in enumerate(looks):
            img.paste(_rounded((88, 88), 14, a, b, c + (255,), 3), (0, i * 90))
        return img
    if name == "field":
        img = Image.new("RGBA", (64, 256), (0, 0, 0, 0))
        img.paste(_rounded((54, 54), 12, (18, 34, 44), (26, 50, 60), (120, 134, 136, 255), 2),
                  (0, 0))
        return img
    if name == "close":
        img = Image.new("RGBA", (64, 256), (0, 0, 0, 0))
        for i, col in enumerate([(30, 50, 52), (90, 150, 150), (20, 36, 38), (40, 40, 40)]):
            d = ImageDraw.Draw(img)
            o = i * 64
            for wdt, cc in ((16, (200, 190, 170)), (10, col)):
                d.line((14, o + 14, 50, o + 50), fill=cc + (255,), width=wdt)
                d.line((50, o + 14, 14, o + 50), fill=cc + (255,), width=wdt)
        return img
    if name == "check":
        img = Image.new("RGBA", (64, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for i, col in enumerate([(14, 40, 30), (120, 230, 90), (80, 180, 60), (10, 12, 12)]):
            o = i * 64
            d.ellipse((10, o + 10, 54, o + 54), fill=(196, 164, 92, 255))
            d.ellipse((14, o + 14, 50, o + 50), fill=col + (255,))
        return img
    if name == "meter_trim":
        img = Image.new("RGBA", (600, 128), (0, 0, 0, 0))
        img.paste(_rounded((560, 60), 10, (16, 30, 36), (10, 18, 22), (196, 164, 92, 255), 3),
                  (20, 34))
        return img
    if name == "meter_fill":
        return _vgrad(554, 72, (70, 150, 160), (30, 80, 90))
    return None


def get(art, name):
    return (art[name] if art is not None else None) or fallback(name)


# ------------------------------------------------------------------ composition (2x px)
def nine(src, box, corner, size):
    """Nine-slice: box (x, y, w, h) of src with equal corners, stretched to size."""
    x, y, w, h = box
    c = corner
    W, H = size
    c2 = min(c, W // 2, H // 2)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    def piece(sx0, sy0, sx1, sy1, dx0, dy0, dx1, dy1):
        if dx1 > dx0 and dy1 > dy0 and sx1 > sx0 and sy1 > sy0:
            p = src.crop((x + sx0, y + sy0, x + sx1, y + sy1))
            out.alpha_composite(p.resize((dx1 - dx0, dy1 - dy0), Image.LANCZOS), (dx0, dy0))
    xs = [(0, c, 0, c2), (c, w - c, c2, W - c2), (w - c, w, W - c2, W)]
    ys = [(0, c, 0, c2), (c, h - c, c2, H - c2), (h - c, h, H - c2, H)]
    for sx0, sx1, dx0, dx1 in xs:
        for sy0, sy1, dy0, dy1 in ys:
            piece(sx0, sy0, sx1, sy1, dx0, dy0, dx1, dy1)
    return out


# rows of the Grid9Detail162 pieces (2x): band 0..98, bottom bar 194..230, ornament 228..304
BAND_H = 98
BAR = (194, 230)
ORN = (228, 304)
BOTTOM_H = ORN[1] - BAR[0]


def window(art, W, H, body_color):
    """Window background at 2x: body panel, main-menu frame band on top, bottom bar with
    the scroll ornament. W, H in 2x px."""
    fr = get(art, "frame")
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bot = H - BOTTOM_H
    # body panel
    bx0, bx1, by0, by1 = 10, W - 10, BAND_H - 20, bot + 20
    top, low = body_color
    img.paste(_vgrad(bx1 - bx0, by1 - by0, top, low), (bx0, by0))
    glow = Image.new("L", (W, 300), 0)
    from PIL import ImageDraw, ImageFilter
    ImageDraw.Draw(glow).ellipse((W * 0.1, -160, W * 0.9, 140), fill=46)
    glow = glow.filter(ImageFilter.GaussianBlur(40))
    tint = Image.new("RGBA", (W, 300), (70, 140, 150, 0))
    tint.putalpha(glow)
    img.alpha_composite(tint.crop((bx0, 0, bx1, min(300, by1 - by0))), (bx0, by0))
    d = ImageDraw.Draw(img)
    for x0, x1 in ((bx0, bx0 + 8), (bx1 - 8, bx1)):  # side trim: dark, metal, dark
        d.rectangle((x0, by0, x1 - 1, by1), fill=(6, 8, 10, 255))
        d.rectangle((x0 + 2, by0, x0 + 5, by1), fill=(120, 112, 98, 255))
        d.rectangle((x0 + 3, by0, x0 + 3, by1), fill=(196, 186, 168, 255))
    shade = _vgrad(bx1 - bx0 - 16, 30, (0, 0, 0), (0, 0, 0))
    shade.putalpha(_vgrad(1, 30, (150,) * 3, (0,) * 3).convert("L").resize((bx1 - bx0 - 16, 30)))
    img.alpha_composite(shade, (bx0 + 8, BAND_H - 4))
    # band: three-slice of the top piece
    left, right = fr.crop((0, 0, 160, BAND_H)), fr.crop((160, 0, 320, BAND_H))
    mid = fr.crop((160, 0, 162, BAND_H)).resize((max(1, W - 320), BAND_H))
    img.alpha_composite(left, (0, 0))
    img.alpha_composite(mid, (160, 0))
    img.alpha_composite(right, (W - 160, 0))
    # bottom bar, then the ornament centred under it
    bl, br = fr.crop((0, BAR[0], 60, BAR[1])), fr.crop((260, BAR[0], 320, BAR[1]))
    bm = fr.crop((160, BAR[0], 162, BAR[1])).resize((max(1, W - 120), BAR[1] - BAR[0]))
    img.alpha_composite(bl, (0, bot))
    img.alpha_composite(bm, (60, bot))
    img.alpha_composite(br, (W - 60, bot))
    orn = fr.crop((0, ORN[0], 320, ORN[1]))
    img.alpha_composite(orn, ((W - 320) // 2, bot + ORN[0] - BAR[0]))
    return img
