![Civ V 2k/4k UI Scaling mod](docs/banner.png)

Makes the Civilization V interface crisp and readable at 1440p, 4K and above.

- resolution-independent UI scaling: a **UI Scale** slider in *Options → Interface Options*
  (50–300 %, or automatic: height / 1080)
- hi-res fonts and hi-res UI art (frames, buttons, city banners, icons, portraits, ...) at 2× / 3×

Works on Windows and on Linux (Steam + Proton) with the Steam DX11 version of the game, and with
any combination of DLC. The native Linux port of Civ V (OpenGL) is not supported.

Works with Enhanced UI (EUI) and Vox Populi's EUI.

## Install

**Windows:** download `CivVUIScale-Setup.exe` from the latest release (to the right on this page) and run it. It fetches
the art pack (`civ5-uiscale-art-x2.pack`) itself, or uses one placed next to it.

On Windows the mod is installed as `msimg32.dll` next to the game exe (on Linux it is
`version.dll`, see below). Windows Security may flag it. That is a false alarm: the mod loads into
the game through a proxy DLL, the same way some malware does, and the DLL is not code-signed.
If the installer reports that it was blocked, open *Windows Security → Virus & threat protection
→ Protection history*, choose *Allow on device* for `msimg32.dll`, and click Install again.
I get that that might be scary. You can verify all the code yourself and build the dll yourself if you'd rather not use the pre-built one.

On Windows the strategic-view icon alignment is broken and cant be fixed, due to Steam DRM not allowing my workaround on windows for some reason (Thanks Valve/Microsoft).
On Linux it works no problem.

**Linux:** download `civ5-uiscale-setup-linux.tar.gz`, unpack it and run `./install.sh`.
Then set the Steam launch options of Civilization V to

```
WINEDLLOVERRIDES="version=n,b" %command%
```

(the installer shows this line with a copy button).

The installer finds the game in any Steam library (other drives, Flatpak / Snap Steam included);
you can also pick the folder yourself. Run it again to update, change options or uninstall.

## Screenshots

![City banner and unit flags](docs/panels_city_banner.jpg)
![Main menu](docs/panels_main_menu.jpg)
![Tech tree](docs/panels_tech_tree.jpg)
![Social policies](docs/panels_social_policies.jpg)
![UI Scale option](docs/ui_scale_option.jpg)
![UI Scale 100, 150 and 200 %](docs/scaling_100_150_200.jpg)
![EUI support](docs/eui_support.jpg)


### Font

Civ V uses the licensed font *Tw Cen MT*. For the text to render correctly at larger scales, install that font on
your computer (or download the .ttf and point the installer at it with *Choose .ttf file…*); the
installer then renders the game's text from it. I cannot bundle the font with these files.
Without it the fonts look wonky, since then they are just upscaled from the games own font gylphs which works badly.
The font is easy to find: search the web for "Tw Cen MT font download".

### Where does the hi-res art come from?

The upscaled art is made by using ML upscaling models on all of the game textures. 
I tried switching to appropriate models for different textures, since some worked better/worse for some than others.
This is not the same as AI-generating textures, rather all the textures are the games own assets just upscaled.
Unfortunately this means I cant distribute them freely as, say, png files, since I dont wanna step on any toes.
Instead, the textures are distributed as encrypted .pack files you can only decrypt if you have a legitimate copy of the game and thus have all the textures in non-upscaled form on you PC.

### Turning off AI art and where to find the log
Settings: `My Games/Sid Meier's Civilization 5/UIScale.ini` · Log: `Logs/UIScale.log`
(`AIArt=0` disables the art). If the mod cannot attach to the game's graphics it keeps the
interface at its normal size for that session and shows a warning instead of a broken UI.

### AI Use

As you may be able to tell, this project was, in large parts, vibe-coded by my good friend Claude Opus 5.5. 
It still took a while to get right and I took great care in ensuring all the details look how I wanted them.
I hope you enjoy the mod nonetheless, since I know people have been asking for such a thing for a long time.
If you find a bug, please report it in the issues page on this github page, or comment/DM me on reddit.
Attach the log (see above) in any bug report.

