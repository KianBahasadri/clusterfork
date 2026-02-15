#!/usr/bin/env bash
# Install clusterfork's global tools (browser-tools) so they're available
# system-wide for OpenCode agents, regardless of which project you're in.
#
# What this does:
#   1. Copies browser-tools scripts to ~/.local/share/opencode-tools/browser-tools/
#   2. Runs pnpm/npm install there for dependencies (puppeteer-core)
#   3. Adds browser-tools directory to PATH in ~/.bashrc
#   4. Copies SKILL.md to ~/.config/opencode/skills/browser-tools/ for agent discovery
#
# Usage: ./install-opencode-tools.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_DIR="$HOME/.local/share/opencode-tools/browser-tools"
SKILL_DIR="$HOME/.config/opencode/skills/browser-tools"
BASHRC="$HOME/.bashrc"
PATH_LINE='export PATH="$HOME/.local/share/opencode-tools/browser-tools:$PATH"'

echo "==> Installing browser-tools from $REPO_DIR"

# 1. Copy source files
echo "  Copying scripts to $TOOLS_DIR"
mkdir -p "$TOOLS_DIR"
cp "$REPO_DIR/tools/browser-tools/package.json" "$TOOLS_DIR/"
cp "$REPO_DIR/tools/browser-tools/browser-start.js" "$TOOLS_DIR/browser-start"
cp "$REPO_DIR/tools/browser-tools/browser-nav.js" "$TOOLS_DIR/browser-nav"
cp "$REPO_DIR/tools/browser-tools/browser-eval.js" "$TOOLS_DIR/browser-eval"
cp "$REPO_DIR/tools/browser-tools/browser-screenshot.js" "$TOOLS_DIR/browser-screenshot"
rm -f "$TOOLS_DIR"/browser-*.js
chmod +x "$TOOLS_DIR"/browser-*

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

# 3. Remove legacy symlinks from old install mode
echo "  Removing legacy ~/.local/bin symlinks (if present)"
for script in browser-start browser-nav browser-eval browser-screenshot; do
  rm -f "$HOME/.local/bin/$script"
done

# 4. Ensure PATH includes the tools directory via ~/.bashrc
echo "  Ensuring PATH entry exists in $BASHRC"
if ! grep -Fq "$PATH_LINE" "$BASHRC"; then
  {
    echo ""
    echo "# ----------- OPENCODE BROWSER TOOLS -----------------"
    echo "$PATH_LINE"
    echo "# -----------------------------------------------------"
  } >> "$BASHRC"
  echo "  Added PATH entry to ~/.bashrc"
else
  echo "  PATH entry already present"
fi

# 5. Install SKILL.md for OpenCode discovery
echo "  Installing SKILL.md to $SKILL_DIR"
mkdir -p "$SKILL_DIR"
cp "$REPO_DIR/tools/browser-tools/SKILL.md" "$SKILL_DIR/SKILL.md"

echo "==> Done. Installed commands: browser-start, browser-nav, browser-eval, browser-screenshot"
echo "    Skill file: $SKILL_DIR/SKILL.md"
echo ""
echo "    Reload shell: source ~/.bashrc"
echo "    Test it: browser-start && browser-nav https://example.com"
