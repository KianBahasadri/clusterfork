#!/usr/bin/env bash
# Uninstall clusterfork's global tools (browser-tools).
#
# Removes:
#   - ~/.local/share/opencode-tools/browser-tools/
#   - ~/.local/bin/browser-{start,nav,eval,screenshot} symlinks
#   - ~/.config/opencode/skills/browser-tools/
#
# Usage: ./scripts/uninstall-opencode-tools.sh

set -euo pipefail

TOOLS_DIR="$HOME/.local/share/opencode-tools/browser-tools"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.config/opencode/skills/browser-tools"

echo "==> Uninstalling browser-tools"

# Remove symlinks
for script in browser-start browser-nav browser-eval browser-screenshot; do
  target="$BIN_DIR/$script"
  if [ -L "$target" ] || [ -f "$target" ]; then
    echo "  Removing $target"
    rm -f "$target"
  fi
done

# Remove tools directory
if [ -d "$TOOLS_DIR" ]; then
  echo "  Removing $TOOLS_DIR"
  rm -rf "$TOOLS_DIR"
fi

# Remove empty parent if we were the only tool
PARENT="$HOME/.local/share/opencode-tools"
if [ -d "$PARENT" ] && [ -z "$(ls -A "$PARENT")" ]; then
  rmdir "$PARENT"
fi

# Remove skill
if [ -d "$SKILL_DIR" ]; then
  echo "  Removing $SKILL_DIR"
  rm -rf "$SKILL_DIR"
fi

echo "==> Done. Browser tools uninstalled."
