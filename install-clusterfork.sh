#!/usr/bin/env bash
# Install clusterfork config into ~/.config/clusterfork.
#
# What this does:
#   1. Overwrites ~/.config/clusterfork/.env from repo-local .env
#   2. Overwrites ~/.config/clusterfork/bash_profile.sh and shell/*.sh from repo
#   3. Overwrites ~/.config/opencode/opencode.json from repo-local opencode.json
#   4. Overwrites ~/.qwen/settings.json from repo-local qwen.json
#   5. Overwrites ~/.gemini/antigravity-cli/settings.json from repo-local antigravity.json
#
# Usage:
#   ./install-clusterfork.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CLUSTERFORK_CONFIG_DIR="$HOME/.config/clusterfork"
DOTENV_SRC="$REPO_DIR/.env"
DOTENV_DEST="$CLUSTERFORK_CONFIG_DIR/.env"
LOCAL_ENV_SRC="$REPO_DIR/bash_profile.sh"
LOCAL_ENV_DEST="$CLUSTERFORK_CONFIG_DIR/bash_profile.sh"
SHELL_SRC_DIR="$REPO_DIR/shell"
SHELL_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/shell"
OPENCODE_CONFIG_SRC="$REPO_DIR/opencode.json"
OPENCODE_CONFIG_DEST="$HOME/.config/opencode/opencode.json"
QWEN_CONFIG_SRC="$REPO_DIR/qwen.json"
QWEN_CONFIG_DEST="$HOME/.qwen/settings.json"
ANTIGRAVITY_CONFIG_SRC="$REPO_DIR/antigravity.json"
ANTIGRAVITY_CONFIG_DEST="$HOME/.gemini/antigravity-cli/settings.json"

usage() {
  cat <<'EOF'
Install clusterfork config.

Usage:
  ./install-clusterfork.sh

Options:
  -h, --help              Show this help message

Examples:
  ./install-clusterfork.sh
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

echo "==> Installing clusterfork config from $REPO_DIR"

if [[ ! -f "$DOTENV_SRC" ]]; then
  echo "ERROR: missing $DOTENV_SRC"
  echo "Create it in the repo root with your local environment values."
  exit 1
fi

mkdir -p "$CLUSTERFORK_CONFIG_DIR"
echo "  Installing local env file to $DOTENV_DEST"
cp "$DOTENV_SRC" "$DOTENV_DEST"

if [[ ! -f "$LOCAL_ENV_SRC" ]]; then
  echo "ERROR: missing $LOCAL_ENV_SRC"
  echo "Create it in the repo root with your local shell aliases/functions."
  exit 1
fi

if [[ ! -d "$SHELL_SRC_DIR" ]]; then
  echo "ERROR: missing $SHELL_SRC_DIR"
  exit 1
fi

echo "  Installing local shell config to $LOCAL_ENV_DEST"
cp "$LOCAL_ENV_SRC" "$LOCAL_ENV_DEST"
echo "  Installing shell modules to $SHELL_DEST_DIR"
rm -rf -- "$SHELL_DEST_DIR"
mkdir -p "$SHELL_DEST_DIR"
cp -r "$SHELL_SRC_DIR"/. "$SHELL_DEST_DIR"/

if [[ ! -f "$OPENCODE_CONFIG_SRC" ]]; then
  echo "ERROR: missing $OPENCODE_CONFIG_SRC"
  exit 1
fi

echo "  Installing opencode config to $OPENCODE_CONFIG_DEST"
mkdir -p "$(dirname "$OPENCODE_CONFIG_DEST")"
cp "$OPENCODE_CONFIG_SRC" "$OPENCODE_CONFIG_DEST"

if [[ ! -f "$QWEN_CONFIG_SRC" ]]; then
  echo "ERROR: missing $QWEN_CONFIG_SRC"
  exit 1
fi

echo "  Installing Qwen Code config to $QWEN_CONFIG_DEST"
mkdir -p "$(dirname "$QWEN_CONFIG_DEST")"
cp "$QWEN_CONFIG_SRC" "$QWEN_CONFIG_DEST"

if [[ ! -f "$ANTIGRAVITY_CONFIG_SRC" ]]; then
  echo "ERROR: missing $ANTIGRAVITY_CONFIG_SRC"
  exit 1
fi

echo "  Installing Antigravity CLI config to $ANTIGRAVITY_CONFIG_DEST"
mkdir -p "$(dirname "$ANTIGRAVITY_CONFIG_DEST")"
cp "$ANTIGRAVITY_CONFIG_SRC" "$ANTIGRAVITY_CONFIG_DEST"

echo "==> Done. Installed clusterfork config"
echo "    Local env file: $DOTENV_DEST"
echo "    Local shell config: $LOCAL_ENV_DEST"
echo "    OpenCode config: $OPENCODE_CONFIG_DEST"
echo "    Qwen Code config: $QWEN_CONFIG_DEST"
echo "    Antigravity CLI config: $ANTIGRAVITY_CONFIG_DEST"
echo ""
echo "    Ensure ~/.bashrc sources: source \"$LOCAL_ENV_DEST\""
