#!/usr/bin/env bash
# Install clusterfork's global tools (browser-tools) so they're available
# system-wide for OpenCode agents, regardless of which project you're in.
#
# What this does:
#   1. Copies browser-tools scripts to ~/.local/share/opencode-tools/browser-tools/
#   2. Runs pnpm/npm install there for dependencies (puppeteer-core)
#   3. Symlinks the CLI scripts into ~/.local/bin/ (should already be on PATH)
#   4. Copies SKILL.md to ~/.config/opencode/skills/browser-tools/ for agent discovery
#
# Usage: ./install-opencode-tools.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_DIR="$HOME/.local/share/opencode-tools/browser-tools"
BIN_DIR="$HOME/.local/bin"
SKILL_DIR="$HOME/.config/opencode/skills/browser-tools"

echo "==> Installing browser-tools from $REPO_DIR"

# 1. Copy source files
echo "  Copying scripts to $TOOLS_DIR"
mkdir -p "$TOOLS_DIR"
cp "$REPO_DIR/tools/browser-tools/package.json" "$TOOLS_DIR/"
cp "$REPO_DIR/tools/browser-tools/browser-start.js" "$TOOLS_DIR/"
cp "$REPO_DIR/tools/browser-tools/browser-nav.js" "$TOOLS_DIR/"
cp "$REPO_DIR/tools/browser-tools/browser-eval.js" "$TOOLS_DIR/"
cp "$REPO_DIR/tools/browser-tools/browser-screenshot.js" "$TOOLS_DIR/"
chmod +x "$TOOLS_DIR"/browser-*.js

# 2. Install dependencies (prefer pnpm, fall back to npm)
echo "  Installing dependencies"
if command -v pnpm &>/dev/null; then
  (cd "$TOOLS_DIR" && pnpm install --prod 2>&1)
elif command -v npm &>/dev/null; then
  (cd "$TOOLS_DIR" && npm install --production --silent 2>&1)
else
  echo "ERROR: Neither pnpm nor npm found. Install one of them first."
  exit 1
fi

# 3. Symlink into PATH
echo "  Symlinking into $BIN_DIR"
mkdir -p "$BIN_DIR"
for script in browser-start browser-nav browser-eval browser-screenshot; do
  ln -sf "$TOOLS_DIR/${script}.js" "$BIN_DIR/$script"
done

# 4. Install SKILL.md for OpenCode discovery
echo "  Installing SKILL.md to $SKILL_DIR"
mkdir -p "$SKILL_DIR"
cp "$REPO_DIR/tools/browser-tools/SKILL.md" "$SKILL_DIR/SKILL.md"

# 5. Verify PATH
if ! echo "$PATH" | tr ':' '\n' | grep -q "^$BIN_DIR$"; then
  echo ""
  echo "  WARNING: $BIN_DIR is not on your PATH."
  echo "  Add this to your ~/.bashrc:"
  echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  echo ""
fi

echo "==> Done. Installed commands: browser-start, browser-nav, browser-eval, browser-screenshot"
echo "    Skill file: $SKILL_DIR/SKILL.md"
echo ""
echo "    Test it: browser-start && browser-nav https://example.com"
