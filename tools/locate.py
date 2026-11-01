#!/usr/bin/env python3
"""Find the Civilization V install and its user-data folder (stdlib only).

Search order: explicit path / CIV5_DIR, then every Steam library of every Steam root
(registry on Windows; native, ~/.steam, Flatpak and Snap Steam on Linux), using the
libraries listed in libraryfolders.vdf and the app manifest of Civ V (app 8930).
"""
import os
import re
import sys

APPID = "8930"
EXE = "CivilizationV_DX11.exe"
DEFAULT_DIR = "Sid Meier's Civilization V"
MYGAMES = os.path.join("My Games", "Sid Meier's Civilization 5")


def is_game_dir(path):
    return bool(path) and os.path.isfile(os.path.join(path, EXE))


def steam_roots():
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE,
                                    r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam",
                                    "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(winreg.QueryValueEx(k, val)[0])
                except OSError:
                    pass
        except ImportError:
            pass
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                roots.append(os.path.join(os.environ[env], "Steam"))
    else:
        home = os.path.expanduser("~")
        roots += [os.path.join(home, p) for p in (
            ".local/share/Steam", ".steam/steam", ".steam/root", ".steam/debian-installation",
            ".var/app/com.valvesoftware.Steam/.local/share/Steam",
            ".var/app/com.valvesoftware.Steam/data/Steam",
            "snap/steam/common/.local/share/Steam")]
    seen, out = set(), []
    for r in roots:
        try:
            real = os.path.realpath(os.path.normpath(r))
        except (OSError, ValueError):
            continue
        if real not in seen and os.path.isdir(os.path.join(real, "steamapps")):
            seen.add(real)
            out.append(real)
    return out


def libraries(root):
    """Library folders of a Steam root (the root itself first)."""
    libs = [root]
    for vdf in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
        p = os.path.join(root, *vdf.split("/"))
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libs.append(m.group(1).replace("\\\\", "\\"))
        # very old format: "1" "D:\\Games\\Steam"
        for m in re.finditer(r'"\d+"\s+"([^"]+[\\/][^"]*)"', text):
            libs.append(m.group(1).replace("\\\\", "\\"))
    seen, out = set(), []
    for l in libs:
        real = os.path.realpath(l)
        if real not in seen and os.path.isdir(os.path.join(real, "steamapps")):
            seen.add(real)
            out.append(real)
    return out


def installdir(lib):
    """The game folder of library lib, from its app manifest (falls back to default)."""
    acf = os.path.join(lib, "steamapps", "appmanifest_%s.acf" % APPID)
    name = DEFAULT_DIR
    try:
        m = re.search(r'"installdir"\s+"([^"]+)"', open(acf, encoding="utf-8",
                                                         errors="replace").read())
        if m:
            name = m.group(1)
    except OSError:
        pass
    return os.path.join(lib, "steamapps", "common", name)


def find_games(explicit=None):
    """All Civ V installs found, best candidate first: [(game_dir, library or None)]."""
    found = []
    for p in (explicit, os.environ.get("CIV5_DIR")):
        if p and is_game_dir(os.path.expanduser(p)):
            found.append((os.path.realpath(os.path.expanduser(p)), None))
    for root in steam_roots():
        for lib in libraries(root):
            g = installdir(lib)
            if is_game_dir(g):
                found.append((os.path.realpath(g), lib))
    seen, out = set(), []
    for g, lib in found:
        if g not in seen:
            seen.add(g)
            # a library is also implied by .../steamapps/common/<game>
            if lib is None:
                parts = os.path.normpath(g).split(os.sep)
                if len(parts) > 3 and parts[-3] == "steamapps" and parts[-2] == "common":
                    lib = os.sep.join(parts[:-3]) or os.sep
            out.append((g, lib))
    return out


def user_dir(game_dir, library=None):
    """'My Games/Sid Meier's Civilization 5' (settings + logs) for this install.
    Windows: the real Documents folder (may be redirected, e.g. to OneDrive).
    Linux / Proton: inside the game's prefix, which lives in the game's library."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD),
                            ("d", ctypes.c_ubyte * 8)]
            docs = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                        (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
            p = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(docs), 0, None,
                                                          ctypes.byref(p)) == 0:
                path = p.value
                ctypes.windll.ole32.CoTaskMemFree(p)
                return os.path.join(path, MYGAMES)
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), "Documents", MYGAMES)
    libs = [library] if library else []
    libs += [l for r in steam_roots() for l in libraries(r)]
    for lib in libs:
        pfx = os.path.join(lib, "steamapps", "compatdata", APPID, "pfx")
        if os.path.isdir(pfx):
            return os.path.join(pfx, "drive_c", "users", "steamuser", "Documents", MYGAMES)
    return None


if __name__ == "__main__":
    for g, lib in find_games(sys.argv[1] if len(sys.argv) > 1 else None):
        print("game:", g)
        print("  library:", lib)
        print("  user dir:", user_dir(g, lib))
