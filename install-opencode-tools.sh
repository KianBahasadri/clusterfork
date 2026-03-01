#!/usr/bin/env bash
# Install clusterfork OpenCode config + agents into ~/.config/opencode.
#
# What this does:
#   1. Copies opencode/agents/*.md to ~/.config/opencode/agents/
#   2. Overwrites ~/.config/opencode/opencode.json from repo
#   3. Overwrites ~/.config/opencode/.env from repo-local .env
#   4. Overwrites ~/.config/opencode/bash_profile.sh from repo-local file
#
# Usage:
#   ./install-opencode-tools.sh                    # install all agents (default)
#   ./install-opencode-tools.sh --agent orchestrator --agent mini-tester
#   ./install-opencode-tools.sh --agents orchestrator,mini-tester

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS_SRC_DIR="$REPO_DIR/opencode/agents"
AGENTS_DIR="$HOME/.config/opencode/agents"
OPENCODE_CONFIG_DIR="$HOME/.config/opencode"
OPENCODE_CONFIG="$OPENCODE_CONFIG_DIR/opencode.json"
DOTENV_SRC="$REPO_DIR/.env"
DOTENV_DEST="$OPENCODE_CONFIG_DIR/.env"
LOCAL_ENV_SRC="$REPO_DIR/bash_profile.sh"
LOCAL_ENV_DEST="$OPENCODE_CONFIG_DIR/bash_profile.sh"

usage() {
  cat <<'EOF'
Install clusterfork OpenCode config + selected agents.

Usage:
  ./install-opencode-tools.sh [options]

Options:
  -a, --agent <name>      Install one agent by name (repeatable)
      --agents <list>     Install agents from comma-separated list
  -h, --help              Show this help message

Examples:
  ./install-opencode-tools.sh
  ./install-opencode-tools.sh --agent orchestrator --agent mini-tester
  ./install-opencode-tools.sh --agents orchestrator,mini-tester

Behavior:
  - If no --agent/--agents flags are provided, all repo agents are installed.
  - If one or more agents are specified, only those agents are installed.
  - Any clusterfork-managed agent files not selected are removed from
    ~/.config/opencode/agents so omitted agents are not left behind.
EOF
}

shopt -s nullglob

declare -a AVAILABLE_AGENTS=()
for file in "$AGENTS_SRC_DIR"/*.md; do
  AVAILABLE_AGENTS+=("$(basename "$file" .md)")
done

if [[ ${#AVAILABLE_AGENTS[@]} -eq 0 ]]; then
  echo "ERROR: no agent files found in $AGENTS_SRC_DIR"
  exit 1
fi

declare -A AVAILABLE_SET=()
for agent in "${AVAILABLE_AGENTS[@]}"; do
  AVAILABLE_SET["$agent"]=1
done

declare -a REQUESTED_AGENTS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--agent)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "ERROR: $1 requires an agent name"
        usage
        exit 1
      fi
      REQUESTED_AGENTS+=("$2")
      shift 2
      ;;
    --agents)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "ERROR: --agents requires a comma-separated list"
        usage
        exit 1
      fi
      IFS=',' read -r -a split_agents <<< "$2"
      for agent in "${split_agents[@]}"; do
        agent="${agent//[[:space:]]/}"
        [[ -n "$agent" ]] && REQUESTED_AGENTS+=("$agent")
      done
      shift 2
      ;;
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

declare -a INSTALL_AGENTS=()

if [[ ${#REQUESTED_AGENTS[@]} -eq 0 ]]; then
  INSTALL_AGENTS=("${AVAILABLE_AGENTS[@]}")
else
  declare -A REQUESTED_SET=()
  for agent in "${REQUESTED_AGENTS[@]}"; do
    REQUESTED_SET["$agent"]=1
  done

  for agent in "${!REQUESTED_SET[@]}"; do
    if [[ -z "${AVAILABLE_SET[$agent]:-}" ]]; then
      echo "ERROR: unknown agent '$agent'"
      echo "Available agents: ${AVAILABLE_AGENTS[*]}"
      exit 1
    fi
  done

  for agent in "${AVAILABLE_AGENTS[@]}"; do
    if [[ -n "${REQUESTED_SET[$agent]:-}" ]]; then
      INSTALL_AGENTS+=("$agent")
    fi
  done
fi

if [[ ${#INSTALL_AGENTS[@]} -eq 0 ]]; then
  echo "ERROR: no agents selected for installation"
  exit 1
fi

echo "==> Installing clusterfork OpenCode config from $REPO_DIR"

# 1. Verify pnpm is available for chrome-devtools MCP launcher
if ! command -v pnpm &>/dev/null; then
  echo "ERROR: pnpm not found. Install pnpm; OpenCode MCP uses 'pnpm dlx chrome-devtools-mcp@latest'."
  exit 1
fi

# 2. Install selected agent config files so local instructions match the repo
echo "  Installing selected agent configs to $AGENTS_DIR"
mkdir -p "$AGENTS_DIR"

# Remove all clusterfork-managed agent files first so omitted agents are not left behind.
for agent in "${AVAILABLE_AGENTS[@]}"; do
  rm -f "$AGENTS_DIR/$agent.md"
done

for agent in "${INSTALL_AGENTS[@]}"; do
  cp "$AGENTS_SRC_DIR/$agent.md" "$AGENTS_DIR/"
done

# 3. Install repo OpenCode config
echo "  Installing OpenCode config to $OPENCODE_CONFIG"
mkdir -p "$OPENCODE_CONFIG_DIR"
cp "$REPO_DIR/opencode.json" "$OPENCODE_CONFIG"

# 4. Install repo-local environment file
if [[ ! -f "$DOTENV_SRC" ]]; then
  echo "ERROR: missing $DOTENV_SRC"
  echo "Create it in the repo root with your local OpenCode environment values."
  exit 1
fi

echo "  Installing local env file to $DOTENV_DEST"
cp "$DOTENV_SRC" "$DOTENV_DEST"

# 5. Install local shell env config used by ~/.bashrc
if [[ ! -f "$LOCAL_ENV_SRC" ]]; then
  echo "ERROR: missing $LOCAL_ENV_SRC"
  echo "Create it in the repo root with your local OpenCode shell aliases/functions."
  exit 1
fi

echo "  Installing local shell config to $LOCAL_ENV_DEST"
cp "$LOCAL_ENV_SRC" "$LOCAL_ENV_DEST"

echo "==> Done. Installed OpenCode config + agent definitions"
echo "    Installed agents: ${INSTALL_AGENTS[*]}"
echo "    MCP servers: context7 (remote), linear (remote), chrome-devtools (local via pnpm dlx)"
echo "    OpenCode config: $OPENCODE_CONFIG"
echo "    Local env file: $DOTENV_DEST"
echo "    Local shell config: $LOCAL_ENV_DEST"
echo ""
echo "    Ensure ~/.bashrc sources: source \"$LOCAL_ENV_DEST\""
echo "    Restart OpenCode to reload MCP servers"
