# Civ V UI Scale

Resolution-independent UI scaling for Civilization V (DX11, Steam/Proton).
Adds a **UI Scale** slider to *Options → Interface Options* (50–300 %, or automatic: height / 1080).

## Install

Requires `mingw-w64-gcc` and Python 3 with Pillow, NumPy and SciPy.

```
./install.sh [--ttf "/path/to/Tw Cen MT.ttf"]
```

Steam launch options:

```
WINEDLLOVERRIDES="version=n,b" %command%
```

`--ttf` is optional: with your own copy of Tw Cen MT the fonts are rendered natively at 2×/3×,
otherwise they are reconstructed from the game's atlases. Fonts are generated locally; none are shipped.

Uninstall with `./uninstall.sh`.

## How it works

A `version.dll` proxy hooks the game without modifying any game code or files:

- the UI is laid out at a virtual resolution (real / scale) and drawn at the real one
- mouse input is scaled to match
- UI clip shaders are patched so scroll panels and text boxes keep working
- hi-res font atlases and the modified options menu are served via a virtual file overlay
- strategic-view icons are realigned via a hardware breakpoint

Settings: `My Games/Sid Meier's Civilization 5/UIScale.ini` · Log: `Logs/UIScale.log`

Only the Steam DX11 build is supported; on an unknown exe the mod logs it and stays inactive.
