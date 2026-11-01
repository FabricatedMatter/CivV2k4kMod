#!/usr/bin/env python3
"""Encrypted art packs: hi-res UI art that only unlocks with the player's own game files.

Every output file is derived from exactly one original game file (a texture or font
atlas inside a .fpk archive, or a loose file). Its payload is encrypted with a key
derived from that original's bytes:

    key     = SHA-256(SALT || original)   original = mip 0 pixel data for DDS files (the
                                          bytes the game uploads; identical images in
                                          different archives share a key), else file
    id      = SHA-256("id" || key)                   (index entry; reveals nothing)
    payload = LZMA(meta json + data) XOR SHAKE-256(key || nonce)

Without the original file the payload is noise. A player who does not own a DLC
lacks its originals and simply cannot unlock that DLC's art.

Pack layout (little endian):
    "C5UIPAK1"  u32 count
    count x  { id[32]  u64 original_size  u64 offset  u64 length  u32 nonce }
    blobs

  pack.py build  GAME_DIR PACK_OUT --scale 2 --art build/UIArt --fonts FONT_DIR
  pack.py unpack GAME_DIR PACK [PACK ...] DEST     (DEST = <game>/UIScale)

Unpacking uses the Python standard library only.
"""
import glob
import hashlib
import json
import lzma
import os
import struct
import sys

MAGIC = b"C5UIPAK1"
SALT = b"civ5-uiscale-pack-v1"
INDEX_FMT = "<32sQQQI"
INDEX_SIZE = struct.calcsize(INDEX_FMT)


def canonical(data):
    """Mip 0 pixel bytes of a DDS (uncompressed or DXT1/DXT5), else the whole file."""
    if data[:4] != b"DDS " or len(data) < 128:
        return data
    h, w = struct.unpack_from("<II", data, 12)
    flags = struct.unpack_from("<I", data, 80)[0]
    if flags & 4:
        four = data[84:88]
        if four not in (b"DXT1", b"DXT5"):
            return data
        n = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * (8 if four == b"DXT1" else 16)
    else:
        n = w * h * struct.unpack_from("<I", data, 88)[0] // 8
    return data[128:128 + n] if 128 + n <= len(data) else data


def key_of(src):
    return hashlib.sha256(SALT + canonical(src)).digest()


def entry_id(key):
    return hashlib.sha256(b"id" + key).digest()


def _xor(data, key, nonce):
    ks = hashlib.shake_256(key + struct.pack("<I", nonce)).digest(len(data))
    return (int.from_bytes(data, "little") ^ int.from_bytes(ks, "little")).to_bytes(
        len(data), "little")


# ------------------------------------------------------------------ game file access
def fpk_entries(path):
    """(name, bytes) for every file in a Civ V .fpk archive."""
    with open(path, "rb") as f:
        data = f.read()
    if data[4:8] != b"FPK_":
        return
    (count,) = struct.unpack_from("<I", data, 10)
    pos = 14
    for _ in range(count):
        (n,) = struct.unpack_from("<I", data, pos)
        pos += 4
        name = data[pos:pos + n].decode("latin-1")
        pos += n + (4 - n % 4) % 4
        _a, _b, size, offset = struct.unpack_from("<IIII", data, pos)
        pos += 16
        yield name, data[offset:offset + size]


def game_sources(game, sizes=None):
    """(label, bytes) for every candidate original: entries of all DX9 archives and the
    DDS / ggxml files under Assets/. With sizes, only originals whose canonical length
    is in sizes are returned."""
    for path in sorted(glob.glob(os.path.join(game, "Resource", "DX9", "*.fpk"))):
        for name, data in fpk_entries(path):
            if sizes is None or len(canonical(data)) in sizes:
                yield "%s:%s" % (os.path.basename(path), name), data
    for root, _, files in os.walk(os.path.join(game, "Assets")):
        for fn in files:
            if not fn.lower().endswith((".dds", ".ggxml")):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if sizes is None or len(canonical(data)) in sizes:
                yield os.path.relpath(p, game), data


# ------------------------------------------------------------------ unpack
def read_index(path):
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:8] != MAGIC:
            raise ValueError("%s: not an art pack" % path)
        (count,) = struct.unpack("<I", head[8:12])
        raw = f.read(count * INDEX_SIZE)
    return [struct.unpack_from(INDEX_FMT, raw, i * INDEX_SIZE) for i in range(count)]


def unpack(game, packs, dest, progress=None):
    """Unlocks every entry whose original is present. Writes files under dest and the
    UIArt manifests; returns (unlocked, total)."""
    index = {}
    for p in packs:
        for eid, size, off, length, nonce in read_index(p):
            index[eid] = (p, size, off, length, nonce)
    sizes = {v[1] for v in index.values()}
    manifests = {}
    done = 0
    handles = {}
    for label, src in game_sources(game, sizes):
        key = key_of(src)
        hit = index.pop(entry_id(key), None)
        if hit is None:
            continue
        p, _, off, length, nonce = hit
        f = handles.get(p) or handles.setdefault(p, open(p, "rb"))
        f.seek(off)
        plain = lzma.decompress(_xor(f.read(length), key, nonce))
        (mlen,) = struct.unpack_from("<I", plain, 0)
        meta = json.loads(plain[4:4 + mlen])
        for path in meta["paths"]:
            out = os.path.join(dest, *path.split("/"))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as o:
                o.write(plain[4 + mlen:])
        if meta.get("manifest"):
            manifests.setdefault(os.path.dirname(out), []).append(meta["manifest"])
        done += 1
        if progress:
            progress(done, done + len(index))
    for f in handles.values():
        f.close()
    for d, lines in manifests.items():
        mf = os.path.join(d, "uiart.txt")
        old = []
        if os.path.exists(mf):
            mine = {l.split()[4] for l in lines}
            old = [l for l in open(mf).read().splitlines() if l.strip() and l.split()[4] not in mine]
        with open(mf, "w") as o:
            o.write("\n".join(old + lines) + "\n")
    return done, done + len(index)


# ------------------------------------------------------------------ build (maintainer)
def build(entries, out):
    """entries: [(original bytes, meta dict, data bytes)] -> pack file."""
    # outputs derived from identical originals share one entry (several paths)
    by_id, unique = {}, []
    for src, meta, data in entries:
        eid = entry_id(key_of(src))
        if eid in by_id:
            first = by_id[eid]
            if first[2] != data:
                print("warning: %s differs from %s although their originals are identical;"
                      " keeping the first" % (meta["path"], first[1]["paths"][0]))
            first[1]["paths"].append(meta["path"])
            continue
        meta = dict(meta)
        meta["paths"] = [meta.pop("path")]
        by_id[eid] = (src, meta, data)
        unique.append(by_id[eid])
    entries = unique
    blobs, index = [], []
    off = 12 + INDEX_SIZE * len(entries)
    for nonce, (src, meta, data) in enumerate(entries):
        key = key_of(src)
        eid = entry_id(key)
        m = json.dumps(meta, separators=(",", ":")).encode()
        blob = _xor(lzma.compress(struct.pack("<I", len(m)) + m + data, preset=6), key, nonce)
        index.append(struct.pack(INDEX_FMT, eid, len(canonical(src)), off, len(blob), nonce))
        blobs.append(blob)
        off += len(blob)
    with open(out, "wb") as f:
        f.write(MAGIC + struct.pack("<I", len(entries)))
        for i in index:
            f.write(i)
        for b in blobs:
            f.write(b)


def collect(game, scale, art_dir, font_dir):
    """Pairs every built output for one scale with the original it derives from."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import uiart  # maintainer side only (needs numpy)
    k = "x%d" % scale
    entries = []
    # UIArt: output names come from uiart's (tag, name) naming of the sources
    by_out = {}
    for tag, name, off, size, data in uiart.load_sources(game):
        n = name.lower()
        if n.endswith(".dds") and not n.endswith("-index.dds"):
            out = ((n[:-4] + "." + tag + ".dds") if tag else n).replace(" ", "_")
            by_out.setdefault(out[:-4], data[off:off + size])
    adir = os.path.join(art_dir, k)
    for line in open(os.path.join(adir, "uiart.txt")).read().splitlines():
        fn = line.split()[4]
        src = by_out.get(fn[:-len("_" + k + ".dds")])
        if src is None:
            print("no original for", fn)
            continue
        entries.append((src, {"path": "UIArt/%s/%s" % (k, fn), "manifest": line},
                        open(os.path.join(adir, fn), "rb").read()))
    # Fonts: atlases <- UITextures.fpk twcenmt*.dds, ggxml <- the game's own ggxml
    ui = dict((n.lower(), d) for n, d in fpk_entries(
        os.path.join(game, "Resource", "DX9", "UITextures.fpk")) if n.lower().startswith("twcenmt"))
    rel = "Assets/UI/Fonts/Tw Cent MT"
    fdir = os.path.join(font_dir, k, *rel.split("/"))
    for fn in sorted(os.listdir(fdir)):
        p = os.path.join(fdir, fn)
        if fn.endswith(".ggxml"):
            src = open(os.path.join(game, *rel.split("/"), fn), "rb").read()
        else:
            src = ui.get(fn[:-len("_" + k + ".dds")].replace("_", " ").lower() + ".dds") or \
                ui.get(fn[:-len("_" + k + ".dds")].lower() + ".dds")
        if src is None:
            print("no original for font file", fn)
            continue
        entries.append((src, {"path": "Fonts/%s/%s/%s" % (k, rel, fn)}, open(p, "rb").read()))
    return entries


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "unpack":
        game, packs, dest = sys.argv[2], sys.argv[3:-1], sys.argv[-1]
        n, total = unpack(game, packs, dest,
                          lambda d, t: print("\r%d / %d" % (d, t), end="", flush=True))
        print("\nunlocked %d of %d files" % (n, total))
        return
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("game")
    ap.add_argument("out")
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--art", default="build/UIArt")
    ap.add_argument("--fonts", required=True, help="fontgen output built WITHOUT --ttf")
    a = ap.parse_args()
    entries = collect(a.game, a.scale, a.art, a.fonts)
    build(entries, a.out)
    print("%s: %d files, %.0f MB" % (a.out, len(entries), os.path.getsize(a.out) / 1e6))


if __name__ == "__main__":
    main()
