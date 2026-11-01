#!/usr/bin/env python3
"""Disassemble DXBC shaders with libvkd3d-shader (ships with Steam's runtime / Proton).

usage: dxbcdis.py FILE.dxbc [...]   (VKD3D_SHADER_LIB=/path/to/libvkd3d-shader.so.1)
"""
import ctypes
import glob
import os
import sys

SOURCE_DXBC_TPF, TARGET_D3D_ASM = 1, 3


class Code(ctypes.Structure):
    _fields_ = [("code", ctypes.c_void_p), ("size", ctypes.c_size_t)]


class CompileInfo(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("next", ctypes.c_void_p), ("source", Code),
                ("source_type", ctypes.c_int), ("target_type", ctypes.c_int),
                ("options", ctypes.c_void_p), ("option_count", ctypes.c_uint),
                ("log_level", ctypes.c_int), ("source_name", ctypes.c_char_p)]


def find_lib():
    env = os.environ.get("VKD3D_SHADER_LIB")
    if env:
        return env
    pats = ["/usr/lib/libvkd3d-shader.so.1",
            os.path.expanduser("~/.local/share/Steam/steamapps/common/SteamLinuxRuntime_*/"
                               "*/files/lib/x86_64-linux-gnu/libvkd3d-shader.so.1*")]
    for p in pats:
        hits = sorted(glob.glob(p))
        if hits:
            return hits[-1]
    raise SystemExit("libvkd3d-shader not found; set VKD3D_SHADER_LIB")


def disassemble(data, lib=None):
    lib = lib or ctypes.CDLL(find_lib())
    buf = ctypes.create_string_buffer(data, len(data))
    info = CompileInfo(0, None, Code(ctypes.cast(buf, ctypes.c_void_p), len(data)),
                       SOURCE_DXBC_TPF, TARGET_D3D_ASM, None, 0, 3, b"shader")
    out = Code()
    msgs = ctypes.c_char_p()
    rc = lib.vkd3d_shader_compile(ctypes.byref(info), ctypes.byref(out), ctypes.byref(msgs))
    if rc != 0:
        raise RuntimeError("vkd3d_shader_compile failed %d: %s" % (rc, msgs.value))
    return ctypes.string_at(out.code, out.size).decode("latin-1")


if __name__ == "__main__":
    lib = ctypes.CDLL(find_lib())
    for f in sys.argv[1:]:
        print("//", f)
        print(disassemble(open(f, "rb").read(), lib))
