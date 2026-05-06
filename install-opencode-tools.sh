#!/usr/bin/env bash
# Install clusterfork OpenCode config into ~/.config/opencode.
#
# What this does:
#   1. Overwrites ~/.config/opencode/opencode.json from repo
#   2. Overwrites ~/.config/opencode/.env from repo-local .env
#   3. Overwrites ~/.config/opencode/bash_profile.sh from repo-local file
#
# Usage:
#   ./install-opencode-tools.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
OPENCODE_CONFIG_DIR="$HOME/.config/opencode"
OPENCODE_CONFIG="$OPENCODE_CONFIG_DIR/opencode.json"
DOTENV_SRC="$REPO_DIR/.env"
DOTENV_DEST="$OPENCODE_CONFIG_DIR/.env"
LOCAL_ENV_SRC="$REPO_DIR/bash_profile.sh"
LOCAL_ENV_DEST="$OPENCODE_CONFIG_DIR/bash_profile.sh"

usage() {
  cat <<'EOF'
Install clusterfork OpenCode config.

Usage:
  ./install-opencode-tools.sh

Options:
  -h, --help              Show this help message

Examples:
  ./install-opencode-tools.sh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

echo "==> Installing clusterfork OpenCode config from $REPO_DIR"

# 1. Verify pnpm is available for chrome-devtools MCP launcher
if ! command -v pnpm &>/dev/null; then
  echo "ERROR: pnpm not found. Install pnpm; OpenCode MCP uses 'pnpm dlx chrome-devtools-mcp@latest'."
  exit 1
fi

# 2. Install repo OpenCode config
echo "  Installing OpenCode config to $OPENCODE_CONFIG"
mkdir -p "$OPENCODE_CONFIG_DIR"
cp "$REPO_DIR/opencode.json" "$OPENCODE_CONFIG"

# 3. Install repo-local environment file
if [[ ! -f "$DOTENV_SRC" ]]; then
  echo "ERROR: missing $DOTENV_SRC"
  echo "Create it in the repo root with your local OpenCode environment values."
  exit 1
fi

echo "  Installing local env file to $DOTENV_DEST"
cp "$DOTENV_SRC" "$DOTENV_DEST"

# 4. Install local shell env config used by ~/.bashrc
if [[ ! -f "$LOCAL_ENV_SRC" ]]; then
  echo "ERROR: missing $LOCAL_ENV_SRC"
  echo "Create it in the repo root with your local OpenCode shell aliases/functions."
  exit 1
fi

echo "  Installing local shell config to $LOCAL_ENV_DEST"
cp "$LOCAL_ENV_SRC" "$LOCAL_ENV_DEST"

echo "==> Done. Installed OpenCode config"
echo "    MCP servers: context7 (remote), linear (remote), chrome-devtools (local via pnpm dlx)"
echo "    OpenCode config: $OPENCODE_CONFIG"
echo "    Local env file: $DOTENV_DEST"
echo "    Local shell config: $LOCAL_ENV_DEST"
echo ""
echo "    Ensure ~/.bashrc sources: source \"$LOCAL_ENV_DEST\""
echo "    Restart OpenCode to reload MCP servers"
