#!/usr/bin/env bash
set -euo pipefail
DEFAULT_GAME="$HOME/.local/share/Steam/steamapps/common/Sid Meier's Civilization V"
GAME="${CIV5_DIR:-$DEFAULT_GAME}"
for dll in version.dll msimg32.dll; do
  if grep -q "Civ V UI Scale" "$GAME/$dll" 2>/dev/null; then
    rm -f "$GAME/$dll"
  fi
done
rm -rf "$GAME/UIScale"
echo "Removed. You can also drop WINEDLLOVERRIDES from the Steam launch options."
