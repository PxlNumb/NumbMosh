#!/usr/bin/env bash
# Datamosh for Fusion - macOS/Linux installer (untested in Resolve)
# Install: ./install.sh   |   Uninstall: ./install.sh --uninstall
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ "$(uname)" = "Darwin" ]; then
  FUSION="$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion"
else
  FUSION="$HOME/.local/share/DaVinciResolve/Fusion"
fi
ENGINE="$FUSION/DatamoshFusion"
if [ "$1" = "--uninstall" ]; then
  rm -rf "$ENGINE" "$FUSION/Scripts/Comp/Datamosher.lua" "$FUSION/Macros/Datamosh.setting"
  echo "Datamosh removed."; exit 0
fi
PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "Python 3 not found."; exit 1; }
FF="$(command -v ffmpeg || true)"
[ -z "$FF" ] && echo "WARNING: ffmpeg not found (macOS: brew install ffmpeg | Linux: apt install ffmpeg)."
mkdir -p "$ENGINE" "$FUSION/Scripts/Comp" "$FUSION/Macros"
cp "$HERE/datamosh_core.py" "$HERE/DatamoshRun.lua" "$ENGINE/"
cp "$HERE/Datamosher.lua" "$FUSION/Scripts/Comp/"
cp "$HERE/Datamosh.setting" "$FUSION/Macros/"
printf '%s\n%s\n' "$PY" "$FF" > "$ENGINE/config.txt"
echo "Installed. Restart Resolve. Fusion page: Effects > Macros > Datamosh (or Workspace > Scripts > Datamosher)."
