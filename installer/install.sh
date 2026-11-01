#!/usr/bin/env bash
# Civ V UI Scale - installer for Linux (Steam + Proton).
# Runs the bundled setup program (a small window, or text prompts without a desktop).
# Options are passed through, e.g.:  ./install.sh --cli   ./install.sh --uninstall
cd "$(dirname "$0")" || exit 1
if [ -x ./civ5-uiscale-setup ]; then
  exec ./civ5-uiscale-setup "$@"
fi
# from a source checkout: needs python3 (and tkinter for the window)
if command -v python3 >/dev/null 2>&1; then
  if [ -f installer/setup_app.py ]; then exec python3 installer/setup_app.py "$@"; fi
  if [ -f setup_app.py ]; then exec python3 setup_app.py "$@"; fi
fi
echo "civ5-uiscale-setup is missing next to this script. Download the full Linux release." >&2
exit 1
