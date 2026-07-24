#!/usr/bin/env bash
# Install clusterfork config into ~/.config/clusterfork.
#
# What this does:
#   1. Overwrites ~/.config/clusterfork/.env from repo-local .env
#   2. Overwrites ~/.config/clusterfork/bash_profile.sh, shell/*.sh, and bin/
#      from repo
#   3. Overwrites agent settings from repo-local agents/
#   4. Overwrites ~/.qwen/skills/, ~/.grok/skills/, ~/.claude/skills/, and
#      ~/.codex/skills/ (user skills only; preserves ~/.codex/skills/.system)
#      from repo-local skills/. Also installs normalized skills for
#      Antigravity and OpenCode compatibility.
#   5. Overwrites Claude/Cursor statusline scripts + usage fetchers from
#      repo-local statusline/
#   6. Overwrites ~/.cursor/mcp.json from agents/cursor-mcp.json (expands
#      ${ENV} placeholders from the clusterfork .env)
#   7. Ensures ElevenLabs in ~/.claude.json mcpServers (key only; does not
#      replace the whole file)
#   8. Ensures statusLine in ~/.cursor/cli-config.json (key only; does not
#      replace the whole file)
#   9. Appends a source line to ~/.bashrc if it is not already present
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
BIN_SRC_DIR="$REPO_DIR/bin"
BIN_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/bin"
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
OPENCODE_SKILLS_DEST_DIR="$HOME/.config/opencode/skills"
ANTIGRAVITY_SKILLS_DEST_DIR="$HOME/.gemini/antigravity-cli/skills"
GROK_CONFIG_SRC="$AGENTS_SRC_DIR/grok.toml"
GROK_CONFIG_DEST="$HOME/.grok/config.toml"
CLAUDE_CONFIG_SRC="$AGENTS_SRC_DIR/claude.json"
CLAUDE_CONFIG_DEST="$HOME/.claude/settings.json"
CLAUDE_USER_JSON="$HOME/.claude.json"
STATUSLINE_SRC_DIR="$REPO_DIR/statusline"
CLAUDE_STATUSLINE_SRC="$STATUSLINE_SRC_DIR/claude/statusline.sh"
CLAUDE_STATUSLINE_DEST="$HOME/.claude/statusline-command.sh"
CLAUDE_USAGE_FETCH_SRC="$STATUSLINE_SRC_DIR/claude/usage-fetch.py"
CLAUDE_USAGE_FETCH_DEST="$HOME/.claude/claude-usage-fetch.py"
CURSOR_STATUSLINE_SRC="$STATUSLINE_SRC_DIR/cursor/statusline.sh"
CURSOR_STATUSLINE_DEST="$HOME/.cursor/statusline.sh"
CURSOR_USAGE_FETCH_SRC="$STATUSLINE_SRC_DIR/cursor/usage-fetch.py"
CURSOR_USAGE_FETCH_DEST="$HOME/.cursor/cursor-usage-fetch.py"
CURSOR_MCP_SRC="$AGENTS_SRC_DIR/cursor-mcp.json"
CURSOR_MCP_DEST="$HOME/.cursor/mcp.json"
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

warn() {
  printf '  !  %s\n' "$1" >&2
  [[ -n "${2:-}" ]] && printf '       %s\n' "$2" >&2
}

# Copy skills for agents whose skill names must use hyphens. The shared source
# keeps its existing names for the agents that already use them as slash
# commands (for example, /commit_and_push). OpenCode discovers the valid names
# through ~/.claude/skills, so its native directory only needs incompatible
# aliases; Antigravity gets the complete normalized tree.
copy_normalized_skills() {
  local dest_dir="$1"
  local only_incompatible="$2"
  local source_dir source_name dest_name dest_skill_dir skill_file

  rm -rf -- "$dest_dir"
  mkdir -p "$dest_dir"

  for source_dir in "$SKILLS_SRC_DIR"/*/; do
    [[ -d "$source_dir" ]] || continue
    source_name="$(basename "$source_dir")"
    dest_name="${source_name//_/-}"

    if [[ "$only_incompatible" == 1 && "$source_name" == "$dest_name" ]]; then
      continue
    fi

    dest_skill_dir="$dest_dir/$dest_name"
    mkdir -p "$dest_skill_dir"
    cp -r "$source_dir"/. "$dest_skill_dir"/

    if [[ "$source_name" != "$dest_name" ]]; then
      skill_file="$dest_skill_dir/SKILL.md"
      sed -i \
        -e "s/^name: ${source_name}\$/name: ${dest_name}/" \
        -e "s#/${source_name}#/${dest_name}#g" \
        "$skill_file"
    fi
  done
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

[[ -d "$BIN_SRC_DIR" ]] || fail "missing $(tildify "$BIN_SRC_DIR")"
rm -rf -- "$BIN_DEST_DIR"
mkdir -p "$BIN_DEST_DIR"
cp -r "$BIN_SRC_DIR"/. "$BIN_DEST_DIR"/
chmod +x "$BIN_DEST_DIR"/*
step "bin helpers" "$BIN_DEST_DIR"

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

  # Antigravity CLI uses ~/.gemini/antigravity-cli/skills and expects
  # hyphenated skill IDs.
  copy_normalized_skills "$ANTIGRAVITY_SKILLS_DEST_DIR" 0
  step "antigravity skills" "$ANTIGRAVITY_SKILLS_DEST_DIR"

  # OpenCode also searches ~/.claude/skills. Keep its native directory for
  # aliases of the source skills whose underscore names OpenCode rejects.
  copy_normalized_skills "$OPENCODE_SKILLS_DEST_DIR" 1
  step "opencode skills" "$OPENCODE_SKILLS_DEST_DIR"
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

# Cursor MCP: expand ${VAR} from clusterfork .env so secrets stay out of the repo.
[[ -f "$CURSOR_MCP_SRC" ]] || fail "missing $(tildify "$CURSOR_MCP_SRC")"
mkdir -p "$(dirname "$CURSOR_MCP_DEST")"
python3 - "$CURSOR_MCP_SRC" "$CURSOR_MCP_DEST" "$DOTENV_DEST" <<'PY'
import json, re, sys
from pathlib import Path

src, dest, dotenv = map(Path, sys.argv[1:])
env = {}
if dotenv.is_file():
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")

def expand(value: str) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in env:
            raise SystemExit(f"cursor mcp: ${{{key}}} not set in {dotenv}")
        return env[key]
    return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", repl, value)

def walk(obj):
    if isinstance(obj, dict):
        return {k: walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v) for v in obj]
    if isinstance(obj, str):
        return expand(obj)
    return obj

data = walk(json.loads(src.read_text(encoding="utf-8")))
dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
step "cursor mcp" "$CURSOR_MCP_DEST"

# Ensure Claude Code user-scope MCP includes ElevenLabs. ~/.claude.json holds a lot
# of unrelated state, so only upsert this one server entry.
python3 - "$CLAUDE_USER_JSON" <<'PY'
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
server = {
    "command": "bash",
    "args": ["-c", 'exec "$HOME/.config/clusterfork/bin/elevenlabs-mcp"'],
}
if path.is_file():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {}
servers = data.setdefault("mcpServers", {})
if servers.get("ElevenLabs") != server:
    servers["ElevenLabs"] = server
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
step "claude mcp" "$CLAUDE_USER_JSON"

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
