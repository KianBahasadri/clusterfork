#!/usr/bin/env bash
# Uninstall clusterfork's global tools (browser-tools).
#
# Removes:
#   - ~/.local/share/opencode-tools/browser-tools/
#   - PATH line added to ~/.bashrc
#   - ~/.local/bin/browser-{start,nav,eval,screenshot} legacy symlinks
#   - ~/.config/opencode/skills/browser-tools/
#
# Usage: ./uninstall-opencode-tools.sh

set -euo pipefail

TOOLS_DIR="$HOME/.local/share/opencode-tools/browser-tools"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.config/opencode/skills/browser-tools"
BASHRC="$HOME/.bashrc"
PATH_LINE='export PATH="$HOME/.local/share/opencode-tools/browser-tools:$PATH"'

echo "==> Uninstalling browser-tools"

# Remove PATH line from ~/.bashrc
if [ -f "$BASHRC" ]; then
  tmp="$(mktemp)"
  grep -Fvx "$PATH_LINE" "$BASHRC" > "$tmp" || true
  mv "$tmp" "$BASHRC"
  echo "  Removed PATH entry from $BASHRC (if present)"
fi

# Remove legacy symlinks
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
