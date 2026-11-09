"""Civ V UI Scale installer logic (shared by the Windows .exe and the Linux installer).

Installs into the game folder, adding files only:
    msimg32.dll (Windows)            the mod (refuses to replace a foreign DLL); Windows
    version.dll (Linux / Proton)     applies compatibility shims to Civ V that preload the
                                     system version.dll, so there the mod uses msimg32.dll
    UIScale/Override/...             the UI Scale options (the DLL patches them into the
                                     options menu at startup, also EUI's)
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

MARKER = b"Civ V UI Scale"          # string inside our DLL
# File name of the mod DLL in the game folder. On Windows the game always runs with
# compatibility shims (apphelp + AcGenral.dll) that load the system version.dll before the
# exe's imports are resolved, so a version.dll proxy is never used there; msimg32.dll is
# imported by the exe and not preloaded. Wine has no such shims and is told to use the
# native version.dll via WINEDLLOVERRIDES, so Linux keeps that name.
DLL_NAME = "msimg32.dll" if sys.platform == "win32" else "version.dll"
OTHER_DLL_NAMES = [n for n in ("version.dll", "msimg32.dll") if n != DLL_NAME]
RELEASE_URL = "https://github.com/FabricatedMatter/CivV2k4kMod/releases/latest/download/"
PACK_NAME = "civ5-uiscale-art-x%d.pack"


# ------------------------------------------------------------------ payload
VERSION = "0.1.3"
DLL_BLOB = "version.bin"   # the DLL zlib-compressed: no raw DLL is ever unpacked to %TEMP%


def payload_dll():
    """Bytes of the mod DLL (built as version.dll, installed as DLL_NAME)."""
    import zlib
    p = os.path.join(BUNDLE, "payload", DLL_BLOB)
    if os.path.isfile(p):
        with open(p, "rb") as f:
            return zlib.decompress(f.read())
    p = os.path.join(BUNDLE, "native", "build", "version.dll")
    if os.path.isfile(p):
        with open(p, "rb") as f:
            return f.read()
    raise FileNotFoundError("version.dll is missing from the installer")


def make_payload(dll, out):
    """Build step: writes the compressed DLL the installer bundles."""
    import zlib
    with open(dll, "rb") as f, open(out, "wb") as o:
        o.write(zlib.compress(f.read(), 9))


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


def host_env():
    """Environment for programs of the host system (fc-list, xrdb). The Linux one-file build
    points LD_LIBRARY_PATH at its own bundled libraries; system programs must not load those
    (an old bundled libfontconfig cannot read a newer system's font configuration)."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


# CA bundles of the common distros (Debian/Ubuntu/Arch, Fedora/RHEL, openSUSE, Alpine/macOS)
CA_FILES = ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem", "/etc/ssl/ca-bundle.pem",
            "/etc/ssl/cert.pem")


def ssl_context():
    """TLS context with a CA bundle that exists on this system. The OpenSSL inside the
    Linux build looks for certificates where its build machine keeps them, which fails on
    other distros (CERTIFICATE_VERIFY_FAILED)."""
    import ssl
    if sys.platform == "win32":
        return ssl.create_default_context()
    for p in (os.environ.get("SSL_CERT_FILE"),) + CA_FILES:
        if p and os.path.isfile(p):
            return ssl.create_default_context(cafile=p)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def plain_log(msg, kind="info"):
    """Default log: kind is one of step, info, ok, warn, error (front ends may colour it)."""
    print(msg)


# ------------------------------------------------------------------ status
def dll_state(game, name=None):
    """'none', 'ours' or 'foreign' for the mod DLL (DLL_NAME) in the game folder."""
    p = os.path.join(game, name or DLL_NAME)
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
                                 capture_output=True, text=True, timeout=10,
                                 env=host_env()).stdout
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


FONT_HINT = ("For the font to render correctly at larger scales, you need to download and "
             "install the font on this computer or download it and point the mod at it. "
             "Otherwise your fonts will look wonky!")


# ------------------------------------------------------------------ install
class Cancelled(Exception):
    pass


class NeedsAdmin(Exception):
    """The game folder is write-protected; the installer has to run as administrator."""


AV_BLOCKED = ("Windows Security blocked %s. This is a false alarm: the mod loads "
              "into the game the same way some malware does, so the unsigned DLL gets flagged. "
              "Open Windows Security > Virus & threat protection > Protection history, "
              "choose Allow on device for %s, then click Install again." % (DLL_NAME, DLL_NAME))


def is_admin():
    if sys.platform != "win32":
        return os.geteuid() == 0
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def can_write(game):
    """True if files can be created in the game folder."""
    probe = os.path.join(game, ".uiscale-write-test")
    try:
        with open(probe, "wb"):
            pass
        os.remove(probe)
        return True
    except PermissionError:
        return False
    except OSError:
        return True  # some other problem: let the install report it


def relaunch_as_admin(args):
    """Starts this installer again elevated (Windows UAC prompt). True if it started."""
    import ctypes
    import subprocess
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, args
    else:
        exe, params = sys.executable, [os.path.abspath(sys.argv[0])] + args
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, subprocess.list2cmdline(params),
                                            None, 1)
    return r > 32


def open_protection_history():
    try:
        os.startfile("windowsdefender://threat/")
    except Exception:
        pass


def write_dll(game):
    """Copies the mod DLL into the game folder and checks it arrived intact."""
    data = payload_dll()
    dst = os.path.join(game, DLL_NAME)
    try:
        with open(dst, "wb") as f:
            f.write(data)
        with open(dst, "rb") as f:
            ok = f.read() == data
    except PermissionError as e:
        if getattr(e, "winerror", 0) == 32:  # sharing violation: the game has it loaded
            raise ValueError("Civilization V is running. Close it and try again.")
        if is_admin():
            raise
        raise NeedsAdmin(game)
    except OSError as e:
        # an antivirus block reaches Python as EINVAL ("Invalid argument") or ERROR_VIRUS_*
        if sys.platform == "win32" and (e.errno == 22 or getattr(e, "winerror", 0) in (225, 226)):
            raise ValueError(AV_BLOCKED)
        raise
    if not ok:
        raise ValueError(AV_BLOCKED if sys.platform == "win32" else
                         "%s could not be written completely: %s" % (DLL_NAME, dst))


def remove_other_dlls(game, log):
    """Removes our DLL under the name the other platform uses (an older Windows install
    used version.dll, which never loads there)."""
    for n in OTHER_DLL_NAMES:
        if dll_state(game, n) == "ours":
            try:
                os.remove(os.path.join(game, n))
                log("Removed the old %s (the mod now uses %s)" % (n, DLL_NAME), "info")
            except OSError as e:
                log("Could not remove the old %s: %s" % (n, e), "warn")


def find_pack(scale, log, progress):
    """Local pack next to the installer, else download it from the release."""
    name = PACK_NAME % scale
    for d in (installer_dir(), os.path.join(installer_dir(), "packs"), os.getcwd()):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            log("Using the art pack next to the installer: %s" % p, "info")
            return p
    dest = os.path.join(installer_dir(), name)
    log("Downloading %s from the latest GitHub release ..." % name, "step")
    try:
        with urllib.request.urlopen(RELEASE_URL + name, timeout=60,
                                    context=ssl_context()) as r, \
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
        log("Downloaded %s" % dest, "ok")
        return dest
    except Exception as e:
        try:
            os.remove(dest + ".part")
        except OSError:
            pass
        log("Download failed: %s" % e, "error")
        log("Download %s from %s yourself, put it next to the installer and run it "
            "again." % (name, RELEASE_URL.rsplit("/latest/", 1)[0] + "/latest"), "warn")
        return None


def install(game, scale, use_font=None, art=True, log=plain_log, progress=lambda *a: None):
    """Installs into game. use_font: path of the user's Tw Cen MT or None.
    Returns a list of follow-up notes for the user."""
    notes = []
    if not locate.is_game_dir(game):
        raise ValueError("Not a Civilization V folder (CivilizationV_DX11.exe missing): " + game)
    state = dll_state(game)
    if state == "foreign":
        raise ValueError("A different %s is already in the game folder. "
                         "Remove it first (another mod?) and run the installer again." % DLL_NAME)
    if sys.platform == "win32" and not can_write(game):
        raise NeedsAdmin(game)
    dst = os.path.join(game, "UIScale")
    log("Installing to %s" % game, "step")
    write_dll(game)
    log("Installed %s" % DLL_NAME, "ok")
    remove_other_dlls(game, log)
    # fresh mod files, keep nothing stale from older versions
    for sub in ("Override", "UIArt", "Fonts"):
        shutil.rmtree(os.path.join(dst, sub), ignore_errors=True)
    shutil.copytree(os.path.join(payload_mod(), "Override"), os.path.join(dst, "Override"))
    if art:
        p = find_pack(scale, log, progress)
        if p:
            log("Unlocking hi-res art with your game files ...", "step")
            n, total = pack.unpack(game, [p], dst,
                                   lambda d, t: progress("unlock", d, t))
            log("Unlocked %d of %d files." % (n, total), "ok" if n else "error")
            if n < total:
                log("%d files stay locked: they belong to DLC or game versions this install "
                    "does not have (that is fine)." % (total - n), "info")
        else:
            notes.append("Hi-res art was not installed (art pack not found). Put %s next "
                         "to the installer and install again." % (PACK_NAME % scale))
    if use_font:
        log("Rendering fonts from %s ..." % use_font, "step")
        render_fonts(game, dst, scale, use_font, log)
    if os.path.isdir(os.path.join(game, "Assets", "DLC", "UI_bc1")):
        log("Enhanced UI detected: the UI Scale slider is added to EUI's options menu "
            "automatically.", "ok")
    if sys.platform != "win32":
        notes.append('Steam: right-click Civilization V > Properties > Launch Options:\n'
                     '    WINEDLLOVERRIDES="version=n,b" %command%')
    ud = locate.user_dir(game)
    if ud:
        log("Settings and log: %s (UIScale.ini, Logs/UIScale.log)" % ud, "info")
    log("Done.", "ok")
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
    log("Fonts rendered from your Tw Cen MT.", "ok")


def uninstall(game, log=plain_log):
    if sys.platform == "win32" and not can_write(game):
        raise NeedsAdmin(game)
    for n in [DLL_NAME] + OTHER_DLL_NAMES:
        state = dll_state(game, n)
        if state == "ours":
            os.remove(os.path.join(game, n))
            log("Removed %s" % n, "ok")
        elif state == "foreign" and n == DLL_NAME:
            log("Left %s alone (it is not ours)." % n, "warn")
    shutil.rmtree(os.path.join(game, "UIScale"), ignore_errors=True)
    log("Removed UIScale/. Uninstalled.", "ok")
    if sys.platform != "win32":
        log('You can remove the WINEDLLOVERRIDES launch option in Steam again.', "info")
