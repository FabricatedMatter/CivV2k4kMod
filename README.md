# Civ V UI Scale

Makes the Civilization V interface crisp and readable at 1440p, 4K and above.

- resolution-independent UI scaling: a **UI Scale** slider in *Options → Interface Options*
  (50–300 %, or automatic: height / 1080)
- hi-res fonts and hi-res UI art (frames, buttons, city banners, icons, portraits, ...) at 2× / 3×

Works on Windows and on Linux (Steam + Proton) with the Steam DX11 version of the game, and with
any combination of DLC. The native Linux port of Civ V (OpenGL) is not supported.

Works with Enhanced UI (EUI) and Vox Populi's EUI in any install order: the UI Scale slider is
added to whichever options menu the game loads (`PatchOptionsMenu=0` in `UIScale.ini` limits
this to the base game's menu).

## Install

**Windows:** download `CivVUIScale-Setup.exe` from the latest release and run it.

**Linux:** download `civ5-uiscale-setup-linux.tar.gz`, unpack it and run `./install.sh`.
Then set the Steam launch options of Civilization V to

```
WINEDLLOVERRIDES="version=n,b" %command%
```

(the installer shows this line with a copy button).

The installer finds the game in any Steam library (other drives, Flatpak / Snap Steam included);
you can also pick the folder yourself. Run it again to update, change options or uninstall.

### Font

Civ V uses *Tw Cen MT*. If that font is installed on your computer, the installer offers to render
the game's text from it (the best quality). Without it the mod still ships crisp reconstructed
glyphs. The font is easy to find: search the web for "Tw Cen MT font download", install the .ttf
like any font, and run the installer again.

### Where does the hi-res art come from?

The upscaled art is derived from Firaxis' textures, so it is distributed encrypted: every file can
only be unlocked with the matching original from your own game files. Without the game the pack is
noise, and art of DLC you do not own stays locked.

Settings: `My Games/Sid Meier's Civilization 5/UIScale.ini` · Log: `Logs/UIScale.log`
(`AIArt=0` disables the art). If the mod cannot attach to the game's graphics it keeps the
interface at its normal size for that session and shows a warning instead of a broken UI.

## How it works

A `version.dll` proxy hooks the game without modifying any game code or files:

- the UI is laid out at a virtual resolution (real / scale) and drawn at the real one
- mouse input is scaled to match
- UI clip shaders are patched so scroll panels and text boxes keep working
- hi-res font atlases are served via a virtual file overlay; the options menus found under
  `Assets\` (base game, EUI's copy in `Assets\DLC\UI_bc1`) are patched at every launch into
  `My Games\...\UIScale\cache` and served the same way
- hi-res UI textures replace the originals on upload, matched by content hash; the tile-sprite
  shader is patched so larger sprite atlases are sampled at full resolution
- strategic-view icons are realigned via a hardware breakpoint

Only the Steam DX11 build is supported; on an unknown exe the mod logs it and stays inactive.

## Building (maintainers)

Requirements: `mingw-w64-gcc`, Python 3 with NumPy, Pillow (+ SciPy for the art tools), and for
the AI-upscaled art `realesrgan-ncnn-vulkan` / `realcugan-ncnn-vulkan` in `build/upscaler/`.

```
make -C native                                   # version.dll
./install.sh [--ttf "Tw Cen MT.ttf"]             # developer install into the game folder
```

### Hi-res UI art

```
G="<game dir>"
for c in frames banners resources fonticons units civs atlases rest compressed; do
  python3 -I tools/uiart.py build "$G" build/UIArt_parts/$c --category $c
done
python3 -I tools/uiart.py merge build/UIArt build/UIArt_parts/*
```

Builds 2× / 3× versions of the UI art (base game, G&K, BNW, patches and DLC). Each texture gets
the method that suits it:

- geometric art (9-slice panels, frames, trims, bars) is re-rendered: straight edges sharp at their
  sub-pixel positions, rounded corners fitted and redrawn as true arcs, thin circular outlines
  redrawn analytically (`tools/uirecon.py`)
- single-colour symbol masks get an exact mask upscale
- ornaments, icons and portraits are upscaled with local AI models (Remacri, Real-CUGAN) and then
  corrected so they average back to the original exactly
- `-index` tile sprites are assembled, upscaled as a whole and cut back into tiles
  (`tools/uisprite.py`); DXT1/DXT5 art is decoded, upscaled and re-encoded (`tools/bcn.py`)

Builds are resumable (`--resume`); `build/UIArt_parts/*/report/` has contact sheets and metrics.

### Release

```
python3 -I tools/fontgen.py "$G" build/Fonts_pack            # reconstructed fonts (no TTF!)
python3 -I tools/pack.py build "$G" build/packs/civ5-uiscale-art-x2.pack --scale 2 --fonts build/Fonts_pack
python3 -I tools/pack.py build "$G" build/packs/civ5-uiscale-art-x3.pack --scale 3 --fonts build/Fonts_pack
git tag vX.Y && git push --tags      # CI builds the DLL and both setup programs into a release
gh release upload vX.Y build/packs/*.pack
```

The installer (`installer/`) is a CustomTkinter app bundled with PyInstaller by
`.github/workflows/release.yml`; `installer/setup_app.py --cli` runs it in text mode.
