#!/usr/bin/env python3
"""List / extract files from Civilization V .fpk archives.

usage: fpk.py ARCHIVE [--list] [--extract OUTDIR] [--match REGEX]
"""
import argparse
import os
import re
import struct


def entries(path):
    with open(path, "rb") as f:
        data = f.read()
    version, magic = struct.unpack_from("<I4s", data, 0)
    if magic != b"FPK_":
        raise SystemExit(f"{path}: not an FPK archive")
    (count,) = struct.unpack_from("<I", data, 10)
    pos = 14
    for _ in range(count):
        (n,) = struct.unpack_from("<I", data, pos)
        pos += 4
        name = data[pos:pos + n].decode("latin-1")
        pos += n + (4 - n % 4) % 4  # name is padded to a 4-byte boundary
        _a, _b, size, offset = struct.unpack_from("<IIII", data, pos)
        pos += 16
        yield name, offset, size, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("archive")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--extract", metavar="OUTDIR")
    ap.add_argument("--match", default=".*")
    a = ap.parse_args()
    rx = re.compile(a.match, re.I)
    for name, off, size, data in entries(a.archive):
        if not rx.search(name):
            continue
        if a.list or not a.extract:
            print(f"{size:10d}  {name}")
        if a.extract:
            os.makedirs(a.extract, exist_ok=True)
            with open(os.path.join(a.extract, os.path.basename(name)), "wb") as o:
                o.write(data[off:off + size])


if __name__ == "__main__":
    main()
