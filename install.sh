#!/usr/bin/env bash
# Installs Civ V UI Scale into the Steam game folder.
# Adds files only: version.dll + UIScale/ next to the exe. Nothing is overwritten.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TTF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ttf) TTF="$2"; shift 2 ;;
    *) echo "usage: $0 [--ttf /path/to/Tw Cen MT.ttf]" >&2; exit 1 ;;
  esac
done
DEFAULT_GAME="$HOME/.local/share/Steam/steamapps/common/Sid Meier's Civilization V"
GAME="${CIV5_DIR:-$DEFAULT_GAME}"

if [[ ! -f "$GAME/CivilizationV_DX11.exe" ]]; then
  echo "Civ V not found at: $GAME  (set CIV5_DIR)" >&2
  exit 1
fi

make -C "$HERE/native" >/dev/null
if [[ -e "$GAME/version.dll" ]] && ! cmp -s "$GAME/version.dll" "$HERE/native/build/version.dll"; then
  if ! grep -q "Civ V UI Scale" "$GAME/version.dll" 2>/dev/null; then
    echo "A different version.dll already exists in the game folder; refusing to overwrite it." >&2
    exit 1
  fi
fi

install -m 644 "$HERE/native/build/version.dll" "$GAME/version.dll"
# Hi-res font atlases are generated locally from the user's own game files
# (and optionally their own copy of Tw Cen MT); nothing font-related is shipped.
echo "Generating hi-res font atlases${TTF:+ from $TTF}..."
rm -rf "$HERE/build/Fonts"
python3 -I "$HERE/tools/fontgen.py" "$GAME" "$HERE/build/Fonts" ${TTF:+--ttf "$TTF"} >/dev/null
rm -rf "$GAME/UIScale"
cp -r "$HERE/mod" "$GAME/UIScale"
cp -r "$HERE/build/Fonts" "$GAME/UIScale/Fonts"
# AI-upscaled UI art, if built (tools/uiart.py)
if [[ -d "$HERE/build/UIArt" ]]; then
  mkdir -p "$GAME/UIScale/UIArt"
  cp -r "$HERE/build/UIArt/x2" "$HERE/build/UIArt/x3" "$GAME/UIScale/UIArt/"
fi

echo "Installed to: $GAME"
echo
echo "Set the Steam launch options for Civilization V to:"
echo '  WINEDLLOVERRIDES="version=n,b" %command%'
echo
echo "Log: <prefix>/drive_c/users/steamuser/Documents/My Games/Sid Meier's Civilization 5/Logs/UIScale.log"
