"""Civ V UI Scale installer logic (shared by the Windows .exe and the Linux installer).

Installs into the game folder, adding files only:
    version.dll                      the mod (refuses to replace a foreign version.dll)
    UIScale/Override/...             the modified options menu
    UIScale/UIArt/x<k>/              hi-res UI art, unlocked from the art pack with the
    UIScale/Fonts/x<k>/              player's own game files (see tools/pack.py)
Optionally the fonts are rendered from the player's own copy of Tw Cen MT instead.
"""
import glob
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# PyInstaller unpacks bundled data to sys._MEIPASS; in a checkout the repo is one level up
BUNDLE = getattr(sys, "_MEIPASS", os.path.dirname(HERE))
sys.path.insert(0, os.path.join(BUNDLE, "tools"))

import locate  # noqa: E402
import pack  # noqa: E402

MARKER = b"Civ V UI Scale"          # string inside our version.dll
RELEASE_URL = "https://github.com/FabricatedMatter/CivV2k4kMod/releases/latest/download/"
PACK_NAME = "civ5-uiscale-art-x%d.pack"


# ------------------------------------------------------------------ payload
def payload_dll():
    for p in (os.path.join(BUNDLE, "payload", "version.dll"),
              os.path.join(BUNDLE, "native", "build", "version.dll")):
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("version.dll is missing from the installer")


def payload_mod():
    for p in (os.path.join(BUNDLE, "payload", "mod"), os.path.join(BUNDLE, "mod")):
        if os.path.isdir(p):
            return p
    raise FileNotFoundError("mod files are missing from the installer")


def installer_dir():
    """Folder the installer was started from (packs may sit next to it)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(HERE)


# ------------------------------------------------------------------ status
def dll_state(game):
    """'none', 'ours' or 'foreign' for the version.dll in the game folder."""
    p = os.path.join(game, "version.dll")
    if not os.path.exists(p):
        return "none"
    try:
        return "ours" if MARKER in open(p, "rb").read() else "foreign"
    except OSError:
        return "foreign"


def detect_scale(screen_height):
    """Art scale for a screen height: 3x from 5K up, else 2x."""
    return 3 if screen_height and screen_height >= 2880 else 2


# ------------------------------------------------------------------ font
FONT_NAMES = ("tcm_____.ttf", "tw cen mt.ttf", "twcenmt.ttf", "tw-cen-mt.ttf", "tw_cen_mt.ttf")


def _is_tw_cen(path):
    try:
        from PIL import ImageFont
        fam, style = ImageFont.truetype(path, 12).getname()
        return fam.lower() == "tw cen mt" and style.lower() in ("regular", "normal", "")
    except Exception:  # Pillow missing or not a font: fall back to the file name
        return os.path.basename(path).lower() in FONT_NAMES


def find_font():
    """Path of an installed Tw Cen MT (regular), or None."""
    dirs = []
    if sys.platform == "win32":
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    else:
        try:
            import subprocess
            out = subprocess.run(["fc-list", ":family=Tw Cen MT", "file", "style"],
                                 capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                path = line.split(":")[0].strip()
                if path and "bold" not in line.lower() and "italic" not in line.lower() \
                        and _is_tw_cen(path):
                    return path
        except Exception:
            pass
        home = os.path.expanduser("~")
        dirs += [os.path.join(home, ".local/share/fonts"), os.path.join(home, ".fonts"),
                 "/usr/share/fonts", "/usr/local/share/fonts"]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                low = fn.lower()
                if low.endswith((".ttf", ".otf")) and ("tw" in low or "tcm" in low or "cen" in low):
                    p = os.path.join(root, fn)
                    if _is_tw_cen(p):
                        return p
    return None


FONT_HINT = ("Tw Cen MT is not installed. The mod works without it (crisp reconstructed glyphs "
             "are included), but the real font looks even better. It is easy to find: search the "
             "web for \"Tw Cen MT font download\", install the .ttf like any font (double-click "
             "it, then Install), and click Check again.")


# ------------------------------------------------------------------ install
class Cancelled(Exception):
    pass


def find_pack(scale, log, progress):
    """Local pack next to the installer, else download it from the release."""
    name = PACK_NAME % scale
    for d in (installer_dir(), os.path.join(installer_dir(), "packs"), os.getcwd()):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    dest = os.path.join(installer_dir(), name)
    log("Downloading %s ..." % name)
    try:
        with urllib.request.urlopen(RELEASE_URL + name, timeout=60) as r, \
                open(dest + ".part", "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if total:
                    progress("download", got, total)
        os.replace(dest + ".part", dest)
        return dest
    except Exception as e:
        log("Download failed: %s" % e)
        log("Put %s next to the installer and run it again." % name)
        return None


def install(game, scale, use_font=None, art=True, log=print, progress=lambda *a: None):
    """Installs into game. use_font: path of the user's Tw Cen MT or None.
    Returns a list of follow-up notes for the user."""
    notes = []
    if not locate.is_game_dir(game):
        raise ValueError("Not a Civilization V folder (CivilizationV_DX11.exe missing): " + game)
    state = dll_state(game)
    if state == "foreign":
        raise ValueError("A different version.dll is already in the game folder. "
                         "Remove it first (another mod?) and run the installer again.")
    dst = os.path.join(game, "UIScale")
    log("Installing to %s" % game)
    shutil.copyfile(payload_dll(), os.path.join(game, "version.dll"))
    # fresh mod files, keep nothing stale from older versions
    for sub in ("Override", "UIArt", "Fonts"):
        shutil.rmtree(os.path.join(dst, sub), ignore_errors=True)
    shutil.copytree(os.path.join(payload_mod(), "Override"), os.path.join(dst, "Override"))
    if art:
        p = find_pack(scale, log, progress)
        if p:
            log("Unlocking hi-res art with your game files ...")
            n, total = pack.unpack(game, [p], dst,
                                   lambda d, t: progress("unlock", d, t))
            log("Unlocked %d of %d files." % (n, total))
            if n < total:
                notes.append("%d files stay locked: they belong to DLC or versions this "
                             "install does not have (that is fine)." % (total - n))
        else:
            notes.append("Hi-res art was not installed (pack not available). The UI "
                         "scaling and fonts still work.")
    if use_font:
        log("Rendering fonts from %s ..." % use_font)
        render_fonts(game, dst, scale, use_font, log)
    if sys.platform != "win32":
        notes.append('Steam: right-click Civilization V > Properties > Launch Options:\n'
                     '    WINEDLLOVERRIDES="version=n,b" %command%')
    ud = locate.user_dir(game)
    if ud:
        notes.append("Settings and log: %s (UIScale.ini, Logs/UIScale.log)" % ud)
    log("Done.")
    return notes


def render_fonts(game, dst, scale, ttf, log):
    """Native-quality font atlases from the player's own Tw Cen MT (needs numpy/Pillow)."""
    import tempfile
    sys.path.insert(0, os.path.join(BUNDLE, "tools"))
    import fontgen
    with tempfile.TemporaryDirectory() as td:
        argv = sys.argv
        sys.argv = ["fontgen", game, td, "--scales", str(scale), "--ttf", ttf]
        try:
            fontgen.main()
        finally:
            sys.argv = argv
        out = os.path.join(dst, "Fonts", "x%d" % scale)
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(os.path.join(td, "x%d" % scale), out)
    log("Fonts rendered from your Tw Cen MT.")


def uninstall(game, log=print):
    state = dll_state(game)
    if state == "ours":
        os.remove(os.path.join(game, "version.dll"))
        log("Removed version.dll")
    elif state == "foreign":
        log("Left version.dll alone (it is not ours).")
    shutil.rmtree(os.path.join(game, "UIScale"), ignore_errors=True)
    log("Removed UIScale/. Uninstalled.")
    if sys.platform != "win32":
        log('You can remove the WINEDLLOVERRIDES launch option in Steam again.')
