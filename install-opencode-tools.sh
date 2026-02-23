#!/usr/bin/env bash
# Install clusterfork OpenCode config + agents into ~/.config/opencode.
#
# What this does:
#   1. Copies .opencode/agents/*.md to ~/.config/opencode/agents/
#   2. Overwrites ~/.config/opencode/opencode.json from repo
#
# Usage: ./install-opencode-tools.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS_SRC_DIR="$REPO_DIR/.opencode/agents"
AGENTS_DIR="$HOME/.config/opencode/agents"
OPENCODE_CONFIG_DIR="$HOME/.config/opencode"
OPENCODE_CONFIG="$OPENCODE_CONFIG_DIR/opencode.json"

echo "==> Installing clusterfork OpenCode config from $REPO_DIR"

# 1. Verify pnpm is available for chrome-devtools MCP launcher
if ! command -v pnpm &>/dev/null; then
  echo "ERROR: pnpm not found. Install pnpm; OpenCode MCP uses 'pnpm dlx chrome-devtools-mcp@latest'."
  exit 1
fi

# 2. Install agent config files so local instructions match the repo
echo "  Installing agent configs to $AGENTS_DIR"
mkdir -p "$AGENTS_DIR"
cp "$AGENTS_SRC_DIR"/*.md "$AGENTS_DIR/"

# 3. Install repo OpenCode config
echo "  Installing OpenCode config to $OPENCODE_CONFIG"
mkdir -p "$OPENCODE_CONFIG_DIR"
cp "$REPO_DIR/opencode.json" "$OPENCODE_CONFIG"

echo "==> Done. Installed OpenCode config + agent definitions"
echo "    MCP servers: context7 (remote), linear (remote), chrome-devtools (local via pnpm dlx)"
echo "    OpenCode config: $OPENCODE_CONFIG"
echo ""
echo "    Restart OpenCode to reload MCP servers"
