#!/usr/bin/env bash
# Install clusterfork config into ~/.config/clusterfork.
#
# What this does:
#   1. Overwrites ~/.config/clusterfork/.env from repo-local .env
#   2. Overwrites ~/.config/clusterfork/bash_profile.sh and shell/*.sh from repo
#   3. Overwrites agent settings from repo-local agents/
#   4. Overwrites ~/.qwen/skills/, ~/.grok/skills/, ~/.claude/skills/, and
#      ~/.codex/skills/ (user skills only; preserves ~/.codex/skills/.system)
#      from repo-local skills/
#   5. Overwrites Claude/Cursor statusline scripts + usage fetchers from
#      repo-local statusline/
#   6. Ensures statusLine in ~/.cursor/cli-config.json (key only; does not
#      replace the whole file)
#   7. Appends a source line to ~/.bashrc if it is not already present
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
AGENTS_SRC_DIR="$REPO_DIR/agents"
OPENCODE_CONFIG_SRC="$AGENTS_SRC_DIR/opencode.json"
OPENCODE_CONFIG_DEST="$HOME/.config/opencode/opencode.json"
QWEN_CONFIG_SRC="$AGENTS_SRC_DIR/qwen.json"
QWEN_CONFIG_DEST="$HOME/.qwen/settings.json"
ANTIGRAVITY_CONFIG_SRC="$AGENTS_SRC_DIR/antigravity.json"
ANTIGRAVITY_CONFIG_DEST="$HOME/.gemini/antigravity-cli/settings.json"
SKILLS_SRC_DIR="$REPO_DIR/skills"
QWEN_SKILLS_DEST_DIR="$HOME/.qwen/skills"
GROK_SKILLS_DEST_DIR="$HOME/.grok/skills"
CLAUDE_SKILLS_DEST_DIR="$HOME/.claude/skills"
CODEX_SKILLS_DEST_DIR="$HOME/.codex/skills"
GROK_CONFIG_SRC="$AGENTS_SRC_DIR/grok.toml"
GROK_CONFIG_DEST="$HOME/.grok/config.toml"
CLAUDE_CONFIG_SRC="$AGENTS_SRC_DIR/claude.json"
CLAUDE_CONFIG_DEST="$HOME/.claude/settings.json"
STATUSLINE_SRC_DIR="$REPO_DIR/statusline"
CLAUDE_STATUSLINE_SRC="$STATUSLINE_SRC_DIR/claude/statusline.sh"
CLAUDE_STATUSLINE_DEST="$HOME/.claude/statusline-command.sh"
CLAUDE_USAGE_FETCH_SRC="$STATUSLINE_SRC_DIR/claude/usage-fetch.py"
CLAUDE_USAGE_FETCH_DEST="$HOME/.claude/claude-usage-fetch.py"
CURSOR_STATUSLINE_SRC="$STATUSLINE_SRC_DIR/cursor/statusline.sh"
CURSOR_STATUSLINE_DEST="$HOME/.cursor/statusline.sh"
CURSOR_USAGE_FETCH_SRC="$STATUSLINE_SRC_DIR/cursor/usage-fetch.py"
CURSOR_USAGE_FETCH_DEST="$HOME/.cursor/cursor-usage-fetch.py"
CURSOR_CLI_CONFIG="$HOME/.cursor/cli-config.json"
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

  rm -rf -- "$CLAUDE_SKILLS_DEST_DIR"
  mkdir -p "$(dirname "$CLAUDE_SKILLS_DEST_DIR")"
  cp -r "$SKILLS_SRC_DIR" "$CLAUDE_SKILLS_DEST_DIR"
  step "claude skills" "$CLAUDE_SKILLS_DEST_DIR"

  # Codex owns ~/.codex/skills/.system; only replace non-dot user skill dirs.
  mkdir -p "$CODEX_SKILLS_DEST_DIR"
  for d in "$CODEX_SKILLS_DEST_DIR"/*/; do
    [[ -d "$d" ]] || continue
    rm -rf -- "$d"
  done
  for d in "$SKILLS_SRC_DIR"/*/; do
    [[ -d "$d" ]] || continue
    cp -r "$d" "$CODEX_SKILLS_DEST_DIR/$(basename "$d")"
  done
  step "codex skills" "$CODEX_SKILLS_DEST_DIR"
fi

[[ -f "$GROK_CONFIG_SRC" ]] || fail "missing $(tildify "$GROK_CONFIG_SRC")"
mkdir -p "$(dirname "$GROK_CONFIG_DEST")"
cp "$GROK_CONFIG_SRC" "$GROK_CONFIG_DEST"
step "grok config" "$GROK_CONFIG_DEST"

[[ -f "$CLAUDE_CONFIG_SRC" ]] || fail "missing $(tildify "$CLAUDE_CONFIG_SRC")"
mkdir -p "$(dirname "$CLAUDE_CONFIG_DEST")"
cp "$CLAUDE_CONFIG_SRC" "$CLAUDE_CONFIG_DEST"
step "claude" "$CLAUDE_CONFIG_DEST"

[[ -f "$CLAUDE_STATUSLINE_SRC" ]] || fail "missing $(tildify "$CLAUDE_STATUSLINE_SRC")"
[[ -f "$CLAUDE_USAGE_FETCH_SRC" ]] || fail "missing $(tildify "$CLAUDE_USAGE_FETCH_SRC")"
mkdir -p "$(dirname "$CLAUDE_STATUSLINE_DEST")"
cp "$CLAUDE_STATUSLINE_SRC" "$CLAUDE_STATUSLINE_DEST"
chmod +x "$CLAUDE_STATUSLINE_DEST"
cp "$CLAUDE_USAGE_FETCH_SRC" "$CLAUDE_USAGE_FETCH_DEST"
chmod +x "$CLAUDE_USAGE_FETCH_DEST"
step "claude status" "$CLAUDE_STATUSLINE_DEST"

[[ -f "$CURSOR_STATUSLINE_SRC" ]] || fail "missing $(tildify "$CURSOR_STATUSLINE_SRC")"
[[ -f "$CURSOR_USAGE_FETCH_SRC" ]] || fail "missing $(tildify "$CURSOR_USAGE_FETCH_SRC")"
mkdir -p "$(dirname "$CURSOR_STATUSLINE_DEST")"
cp "$CURSOR_STATUSLINE_SRC" "$CURSOR_STATUSLINE_DEST"
chmod +x "$CURSOR_STATUSLINE_DEST"
cp "$CURSOR_USAGE_FETCH_SRC" "$CURSOR_USAGE_FETCH_DEST"
chmod +x "$CURSOR_USAGE_FETCH_DEST"
step "cursor status" "$CURSOR_STATUSLINE_DEST"

# Ensure Cursor CLI statusLine points at the installed script. Unlike other
# agent configs, cli-config.json holds session/auth caches we must not replace.
if [[ -f "$CURSOR_CLI_CONFIG" ]]; then
  python3 - "$CURSOR_CLI_CONFIG" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
wanted = {
    "type": "command",
    "command": "~/.cursor/statusline.sh",
    "padding": 2,
}
if data.get("statusLine") != wanted:
    data["statusLine"] = wanted
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
PY
  step "cursor cli" "$CURSOR_CLI_CONFIG"
else
  mkdir -p "$(dirname "$CURSOR_CLI_CONFIG")"
  python3 - "$CURSOR_CLI_CONFIG" <<'PY'
import json, sys
path = sys.argv[1]
data = {
    "permissions": {"allow": [], "deny": []},
    "version": 1,
    "statusLine": {
        "type": "command",
        "command": "~/.cursor/statusline.sh",
        "padding": 2,
    },
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
  step "cursor cli" "$CURSOR_CLI_CONFIG"
fi

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
