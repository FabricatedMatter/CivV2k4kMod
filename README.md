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

### Hi-res UI art (optional, experimental)

```
G="<game dir>"
for c in frames banners resources fonticons units civs atlases rest compressed; do
  python3 -I tools/uiart.py build "$G" build/UIArt_parts/$c --category $c
done
python3 -I tools/uiart.py merge build/UIArt build/UIArt_parts/*
./install.sh
```

Builds 2x/3x versions of the UI art from your own game files (base game, G&K, BNW, patches and
DLC, so any DLC combination is covered). Each texture gets the method that suits it:

- geometric art (9-slice panels, frames, trims, bars) is re-rendered at the higher resolution:
  straight edges sharp at their sub-pixel positions, rounded corners fitted and redrawn as true
  arcs, thin circular outlines redrawn analytically (`tools/uirecon.py`)
- ornaments, icons and portraits are upscaled with local AI models (Remacri via
  realesrgan-ncnn-vulkan, Real-CUGAN for painted icons; `build/upscaler/`) and then corrected so
  they average back to the original exactly
- `-index` tile sprites (city banners, buttons, icon atlases) are assembled, upscaled as a whole
  and cut back into tiles (`tools/uisprite.py`); the DLL patches the sprite shader so the larger
  tiles are actually sampled
- DXT1/DXT5 art is decoded, upscaled and re-encoded (`tools/bcn.py`)

`build/UIArt_parts/*/report/` has contact sheets and per-texture metrics.

`UIScale.ini` switches: `AIArt=0` disables the art, `OverlayScale=2` (experimental) renders the
map overlay pages (borders, fog) at 2x, `DumpTextures=1|2` writes diagnostics.

## How it works

A `version.dll` proxy hooks the game without modifying any game code or files:

- the UI is laid out at a virtual resolution (real / scale) and drawn at the real one
- mouse input is scaled to match
- UI clip shaders are patched so scroll panels and text boxes keep working
- hi-res font atlases and the modified options menu are served via a virtual file overlay
- hi-res UI textures (if built) replace the originals on upload, matched by content hash
- strategic-view icons are realigned via a hardware breakpoint

Settings: `My Games/Sid Meier's Civilization 5/UIScale.ini` · Log: `Logs/UIScale.log`

Only the Steam DX11 build is supported; on an unknown exe the mod logs it and stays inactive.
