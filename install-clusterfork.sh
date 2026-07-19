#!/usr/bin/env bash
# Install clusterfork config into ~/.config/clusterfork.
#
# What this does:
#   1. Overwrites ~/.config/clusterfork/.env from repo-local .env
#   2. Overwrites ~/.config/clusterfork/bash_profile.sh and shell/*.sh from repo
#   3. Overwrites ~/.config/opencode/opencode.json from repo-local opencode.json
#   4. Overwrites ~/.qwen/settings.json from repo-local qwen.json
#   5. Overwrites ~/.gemini/antigravity-cli/settings.json from repo-local antigravity.json
#   6. Overwrites ~/.qwen/skills/ and ~/.grok/skills/ from repo-local skills/
#   7. Overwrites ~/.grok/config.toml from repo-local grok.toml
#   8. Appends a source line to ~/.bashrc if it is not already present
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
SKILLS_SRC_DIR="$REPO_DIR/skills"
QWEN_SKILLS_DEST_DIR="$HOME/.qwen/skills"
GROK_SKILLS_DEST_DIR="$HOME/.grok/skills"
GROK_CONFIG_SRC="$REPO_DIR/grok.toml"
GROK_CONFIG_DEST="$HOME/.grok/config.toml"
BASHRC="$HOME/.bashrc"

# Replace a leading $HOME with ~ for shorter display paths.
tildify() {
  if [[ "$1" == "$HOME" || "$1" == "$HOME/"* ]]; then
    printf '~%s' "${1#"$HOME"}"
  else
    printf '%s' "$1"
  fi
}

# A completed step: check, padded label, arrow, shortened dest.
step() { printf '  ✓  %-13s  →  %s\n' "$1" "$(tildify "$2")"; }

# A fatal error with an optional hint line, then exit.
fail() {
  printf '  ✗  %s\n' "$1" >&2
  [[ -n "${2:-}" ]] && printf '       %s\n' "$2" >&2
  exit 1
}

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

printf '  clusterfork  ›  installing config\n'
printf '  from  %s\n\n' "$(tildify "$REPO_DIR")"

[[ -f "$DOTENV_SRC" ]] || fail "missing $(tildify "$DOTENV_SRC")" "Create it in the repo root with your local environment values."
mkdir -p "$CLUSTERFORK_CONFIG_DIR"
cp "$DOTENV_SRC" "$DOTENV_DEST"
step "env file" "$DOTENV_DEST"

[[ -f "$LOCAL_ENV_SRC" ]] || fail "missing $(tildify "$LOCAL_ENV_SRC")" "Create it in the repo root with your local shell aliases/functions."
[[ -d "$SHELL_SRC_DIR" ]] || fail "missing $(tildify "$SHELL_SRC_DIR")"

cp "$LOCAL_ENV_SRC" "$LOCAL_ENV_DEST"
step "shell config" "$LOCAL_ENV_DEST"

rm -rf -- "$SHELL_DEST_DIR"
mkdir -p "$SHELL_DEST_DIR"
cp -r "$SHELL_SRC_DIR"/. "$SHELL_DEST_DIR"/
step "shell modules" "$SHELL_DEST_DIR"

[[ -f "$OPENCODE_CONFIG_SRC" ]] || fail "missing $(tildify "$OPENCODE_CONFIG_SRC")"
mkdir -p "$(dirname "$OPENCODE_CONFIG_DEST")"
cp "$OPENCODE_CONFIG_SRC" "$OPENCODE_CONFIG_DEST"
step "opencode" "$OPENCODE_CONFIG_DEST"

[[ -f "$QWEN_CONFIG_SRC" ]] || fail "missing $(tildify "$QWEN_CONFIG_SRC")"
mkdir -p "$(dirname "$QWEN_CONFIG_DEST")"
cp "$QWEN_CONFIG_SRC" "$QWEN_CONFIG_DEST"
step "qwen code" "$QWEN_CONFIG_DEST"

[[ -f "$ANTIGRAVITY_CONFIG_SRC" ]] || fail "missing $(tildify "$ANTIGRAVITY_CONFIG_SRC")"
mkdir -p "$(dirname "$ANTIGRAVITY_CONFIG_DEST")"
cp "$ANTIGRAVITY_CONFIG_SRC" "$ANTIGRAVITY_CONFIG_DEST"
step "antigravity" "$ANTIGRAVITY_CONFIG_DEST"

if [[ -d "$SKILLS_SRC_DIR" ]]; then
  rm -rf -- "$QWEN_SKILLS_DEST_DIR"
  mkdir -p "$(dirname "$QWEN_SKILLS_DEST_DIR")"
  cp -r "$SKILLS_SRC_DIR" "$QWEN_SKILLS_DEST_DIR"
  step "qwen skills" "$QWEN_SKILLS_DEST_DIR"

  rm -rf -- "$GROK_SKILLS_DEST_DIR"
  mkdir -p "$(dirname "$GROK_SKILLS_DEST_DIR")"
  cp -r "$SKILLS_SRC_DIR" "$GROK_SKILLS_DEST_DIR"
  step "grok skills" "$GROK_SKILLS_DEST_DIR"
fi

[[ -f "$GROK_CONFIG_SRC" ]] || fail "missing $(tildify "$GROK_CONFIG_SRC")"
mkdir -p "$(dirname "$GROK_CONFIG_DEST")"
cp "$GROK_CONFIG_SRC" "$GROK_CONFIG_DEST"
step "grok config" "$GROK_CONFIG_DEST"

# Ensure ~/.bashrc sources clusterfork. Keep $HOME literal so it stays portable,
# and skip if the source line is already present so re-running is idempotent.
SOURCE_LINE="source \"\$HOME${LOCAL_ENV_DEST#"$HOME"}\""
if [[ -f "$BASHRC" ]] && grep -qF 'clusterfork/bash_profile.sh' "$BASHRC"; then
  step "bashrc" "already sourced"
else
  printf '\n# clusterfork\n%s\n' "$SOURCE_LINE" >> "$BASHRC"
  step "bashrc" "$BASHRC"
fi

# Shell modules: the basename (sans .sh) of each module under shell/.
modules=()
for f in "$SHELL_SRC_DIR"/*.sh; do
  [[ -e "$f" ]] || continue
  modules+=("$(basename "$f" .sh)")
done
if (( ${#modules[@]} > 0 )); then
  mid=$(( (${#modules[@]} + 1) / 2 ))
  printf '\n  Shell modules\n'
  printf '    %s\n' "${modules[*]:0:$mid}"
  (( ${#modules[@]} > mid )) && printf '    %s\n' "${modules[*]:$mid}"
fi

# Skills: list each subdirectory name under skills/.
skills=()
if [[ -d "$SKILLS_SRC_DIR" ]]; then
  for d in "$SKILLS_SRC_DIR"/*/; do
    [[ -d "$d" ]] || continue
    skills+=("$(basename "$d")")
  done
fi
if (( ${#skills[@]} > 0 )); then
  printf '\n  Skills\n'
  printf '    %s\n' "${skills[*]}"
fi

printf '\n  ✓  done\n\n'
printf '  Restart your shell or run:\n'
printf '    source ~/.bashrc\n'
printf '\n'
