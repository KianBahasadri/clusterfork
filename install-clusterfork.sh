#!/usr/bin/env bash
# Install clusterfork config into ~/.config/clusterfork.
#
# What this does:
#   1. Overwrites ~/.config/clusterfork/.env from repo-local .env
#   2. Overwrites ~/.config/clusterfork/bash_profile.sh, shell/*.sh, bin/,
#      notify/, scripts/rotate_auth.py, and scripts/codeview/ from repo
#   3. Overwrites ~/.tmux.conf from repo-local tmux.conf
#   4. Overwrites agent settings from repo-local agents/ (Grok keeps existing
#      theme from ~/.grok/config.toml if set; Antigravity hooks.json is included)
#   5. Overwrites ~/.qwen/skills/, ~/.grok/skills/, ~/.claude/skills/, and
#      ~/.codex/skills/ (user skills only; preserves ~/.codex/skills/.system)
#      from repo-local skills/. Also installs normalized skills for Command
#      Code and Antigravity, plus OpenCode compatibility aliases.
#   6. Overwrites Claude/Cursor statusline scripts + usage fetchers from
#      repo-local statusline/
#   7. Overwrites ~/.cursor/mcp.json from agents/cursor-mcp.json (expands
#      ${ENV} placeholders from the clusterfork .env)
#   8. Overwrites ~/.commandcode/mcp.json from agents/command-code-mcp.json
#      (expands ${ENV} placeholders from the clusterfork .env)
#   9. Overwrites ~/.gemini/config/mcp_config.json from agents/antigravity-mcp.json
#      (expands ${ENV} placeholders from the clusterfork .env)
#  10. Ensures telemetry is disabled in ~/.commandcode/config.json from
#      agents/command-code.json (key only; does not replace the whole file)
#  11. Ensures the Stop turn-notification hook in ~/.commandcode/settings.json from
#      agents/command-code-settings.json (appends the hook if missing; preserves
#      the rest of the file)
#  12. Updates ~/.codex/config.toml from agents/codex.toml (merges top-level
#      settings, replaces mcp_servers and hook event tables, strips retired
#      keys like notify, stamps trusted_hash for the Stop notifier only; Codex owns
#      the rest of that file)
#  13. Installs agents/claude-plugins/* into ~/.claude/skills/ as skills-dir
#      plugins; agents/claude.json ships each one disabled
#  14. Ensures ElevenLabs in ~/.claude.json mcpServers (key only; does not
#      replace the whole file)
#  15. Ensures statusLine in ~/.cursor/cli-config.json (key only; does not
#      replace the whole file)
#  16. Appends a source line to ~/.bashrc if it is not already present
#  17. Best-effort: ensures Codex/Cursor/OpenCode auth.json links through
#      ~/.local/share/clusterfork-auth/<agent>/current when profiles exist
#
# Usage:
#   ./install-clusterfork.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CLUSTERFORK_CONFIG_DIR="$HOME/.config/clusterfork"
DOTENV_SRC="$REPO_DIR/.env"
DOTENV_DEST="$CLUSTERFORK_CONFIG_DIR/.env"
BELL_SOUND_SRC="$REPO_DIR/bell.mp3"
BELL_SOUND_DEST="$CLUSTERFORK_CONFIG_DIR/bell.mp3"
LOCAL_ENV_SRC="$REPO_DIR/bash_profile.sh"
LOCAL_ENV_DEST="$CLUSTERFORK_CONFIG_DIR/bash_profile.sh"
SHELL_SRC_DIR="$REPO_DIR/shell"
SHELL_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/shell"
BIN_SRC_DIR="$REPO_DIR/bin"
BIN_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/bin"
NOTIFY_SRC_DIR="$REPO_DIR/notify"
NOTIFY_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/notify"
TMUX_CONFIG_SRC="$REPO_DIR/tmux.conf"
TMUX_CONFIG_DEST="$HOME/.tmux.conf"
ROTATE_AUTH_SRC="$REPO_DIR/scripts/rotate_auth.py"
ROTATE_AUTH_DEST="$CLUSTERFORK_CONFIG_DIR/scripts/rotate_auth.py"
CODEVIEW_SRC_DIR="$REPO_DIR/scripts/codeview"
CODEVIEW_DEST_DIR="$CLUSTERFORK_CONFIG_DIR/scripts/codeview"
AGENTS_SRC_DIR="$REPO_DIR/agents"
OPENCODE_CONFIG_SRC="$AGENTS_SRC_DIR/opencode.json"
OPENCODE_CONFIG_DEST="$HOME/.config/opencode/opencode.json"
QWEN_CONFIG_SRC="$AGENTS_SRC_DIR/qwen.json"
QWEN_CONFIG_DEST="$HOME/.qwen/settings.json"
ANTIGRAVITY_CONFIG_SRC="$AGENTS_SRC_DIR/antigravity.json"
ANTIGRAVITY_CONFIG_DEST="$HOME/.gemini/antigravity-cli/settings.json"
ANTIGRAVITY_HOOKS_SRC="$AGENTS_SRC_DIR/antigravity-hooks.json"
ANTIGRAVITY_HOOKS_DEST="$HOME/.gemini/config/hooks.json"
ANTIGRAVITY_MCP_SRC="$AGENTS_SRC_DIR/antigravity-mcp.json"
ANTIGRAVITY_MCP_DEST="$HOME/.gemini/config/mcp_config.json"
SKILLS_SRC_DIR="$REPO_DIR/skills"
QWEN_SKILLS_DEST_DIR="$HOME/.qwen/skills"
GROK_SKILLS_DEST_DIR="$HOME/.grok/skills"
CLAUDE_SKILLS_DEST_DIR="$HOME/.claude/skills"
CLAUDE_PLUGINS_SRC_DIR="$AGENTS_SRC_DIR/claude-plugins"
CODEX_SKILLS_DEST_DIR="$HOME/.codex/skills"
COMMAND_CODE_SKILLS_DEST_DIR="$HOME/.commandcode/skills"
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
COMMAND_CODE_MCP_SRC="$AGENTS_SRC_DIR/command-code-mcp.json"
COMMAND_CODE_MCP_DEST="$HOME/.commandcode/mcp.json"
COMMAND_CODE_CONFIG_SRC="$AGENTS_SRC_DIR/command-code.json"
COMMAND_CODE_CONFIG_DEST="$HOME/.commandcode/config.json"
COMMAND_CODE_SETTINGS_SRC="$AGENTS_SRC_DIR/command-code-settings.json"
COMMAND_CODE_SETTINGS_DEST="$HOME/.commandcode/settings.json"
CODEX_CONFIG_SRC="$AGENTS_SRC_DIR/codex.toml"
CODEX_CONFIG_DEST="$HOME/.codex/config.toml"
SHARED_AUTH_ROOT="$HOME/.local/share/clusterfork-auth"
CODEX_AUTH_DIR="$HOME/.codex"
CODEX_AUTH_STORE_DIR="$SHARED_AUTH_ROOT/codex"
CURSOR_AUTH_DIR="$HOME/.config/cursor"
CURSOR_AUTH_STORE_DIR="$SHARED_AUTH_ROOT/cursor"
GROK_AUTH_DIR="$HOME/.grok"
GROK_AUTH_STORE_DIR="$SHARED_AUTH_ROOT/grok"
OPENCODE_AUTH_DIR="$HOME/.local/share/opencode"
OPENCODE_AUTH_STORE_DIR="$SHARED_AUTH_ROOT/opencode"
BASHRC="$HOME/.bashrc"

# Replace a leading $HOME with ~ for shorter display paths.
tildify() {
  if [[ "$1" == "$HOME" || "$1" == "$HOME/"* ]]; then
    printf '~%s' "${1#"$HOME"}"
  else
    printf '%s' "$1"
  fi
}

# A completed step: check, padded label, arrow, shortened dest, optional detail
# naming what the step installs inside that dest (hooks, servers, keys, ...).
step() {
  local detail="${3:-}"
  if [[ -n "$detail" ]]; then
    printf '  ✓  %-19s  →  %s  (%s)\n' "$1" "$(tildify "$2")" "$detail"
  else
    printf '  ✓  %-19s  →  %s\n' "$1" "$(tildify "$2")"
  fi
}

# An extra destination installed by the previous step, aligned under it.
substep() { printf '%-29s+  %s\n' '' "$(tildify "$1")"; }

# Comma-joined MCP server names from a repo MCP JSON file.
mcp_server_names() {
  python3 -c 'import json, sys; print(", ".join(json.load(open(sys.argv[1], encoding="utf-8"))["mcpServers"]))' "$1"
}

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

# Ensure agent auth.json points through the shared store/current link when
# multi-account profiles exist. Also migrates any legacy auth.json.* files
# still sitting in the agent directory. No-op for single-account installs
# (plain auth.json, no suffixed profiles). The relative agent link stays
# portable when the shared store is mounted into a home with a different name.
AUTH_STORE_CONFIGURED=0
configure_shared_auth() {
  local label="$1"
  local agent_dir="$2"
  local store_dir="$3"
  local store_root="${store_dir%/*}"
  local auth_path="$agent_dir/auth.json"
  local current_path="$store_dir/current"
  local agent_link_target
  local active_suffix="" stored_suffix="" only_suffix=""
  local path dest target target_base suffix tmp_link
  local -a legacy_profiles=()
  local -A available=()

  AUTH_STORE_CONFIGURED=0
  agent_link_target="$(realpath -ms --relative-to="$agent_dir" "$current_path")" ||
    return 1

  if [[ -L "$current_path" ]]; then
    target="$(readlink "$current_path")" || return 1
    target_base="${target##*/}"
    if [[ "$target_base" != auth.json.* ]]; then
      printf '%s auth: invalid current target: %s\n' "$label" "$target" >&2
      return 1
    fi
    stored_suffix="${target_base#auth.json.}"
  elif [[ -e "$current_path" ]]; then
    printf '%s auth: %s must be a symlink\n' "$label" "$current_path" >&2
    return 1
  fi

  if [[ -L "$auth_path" ]]; then
    target="$(readlink "$auth_path")" || return 1
    target_base="${target##*/}"
    case "$target_base" in
      auth.json.*)
        active_suffix="${target_base#auth.json.}"
        ;;
      current)
        active_suffix="$stored_suffix"
        ;;
      *)
        printf '%s auth: unsupported auth link target: %s\n' "$label" "$target" >&2
        return 1
        ;;
    esac
  fi

  for path in "$store_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    if [[ ! -f "$path" || -L "$path" ]]; then
      printf '%s auth: profile must be a regular file: %s\n' "$label" "$path" >&2
      return 1
    fi
    suffix="${path##*/auth.json.}"
    if [[ -z "$suffix" ]]; then
      printf '%s auth: profile suffix is empty: %s\n' "$label" "$path" >&2
      return 1
    fi
    available["$suffix"]=1
  done

  for path in "$agent_dir"/auth.json.*; do
    [[ -e "$path" || -L "$path" ]] || continue
    if [[ ! -f "$path" || -L "$path" ]]; then
      printf '%s auth: profile must be a regular file: %s\n' "$label" "$path" >&2
      return 1
    fi
    suffix="${path##*/auth.json.}"
    if [[ -z "$suffix" ]]; then
      printf '%s auth: profile suffix is empty: %s\n' "$label" "$path" >&2
      return 1
    fi
    dest="$store_dir/auth.json.$suffix"
    if [[ -e "$dest" || -L "$dest" ]]; then
      if [[ ! -f "$dest" || -L "$dest" ]] || ! cmp -s -- "$path" "$dest"; then
        printf '%s auth: conflicting profile exists: %s\n' "$label" "$dest" >&2
        return 1
      fi
    fi
    legacy_profiles+=("$path")
    available["$suffix"]=1
  done

  # A normal single-account installation has only auth.json and no suffixed
  # profiles. Leave it alone until the user opts into profile rotation.
  if (( ${#available[@]} == 0 )); then
    return 0
  fi

  if [[ -e "$auth_path" && ! -L "$auth_path" ]]; then
    printf '%s auth: %s must be a symlink before profiles can be migrated\n' \
      "$label" "$auth_path" >&2
    printf '  Save it with rotate-* --save NAME, then re-run the installer.\n' >&2
    return 1
  fi

  if [[ -z "$active_suffix" ]]; then
    active_suffix="$stored_suffix"
  fi
  if [[ -z "$active_suffix" ]]; then
    if (( ${#available[@]} != 1 )); then
      printf '%s auth: cannot determine the active profile\n' "$label" >&2
      return 1
    fi
    for only_suffix in "${!available[@]}"; do
      active_suffix="$only_suffix"
    done
  fi
  if [[ -z "${available[$active_suffix]:-}" ]]; then
    printf '%s auth: active profile is missing: auth.json.%s\n' \
      "$label" "$active_suffix" >&2
    return 1
  fi

  mkdir -p "$agent_dir" "$store_dir" || return 1
  chmod 700 "$store_root" "$store_dir" || return 1

  for path in "${legacy_profiles[@]}"; do
    suffix="${path##*/auth.json.}"
    dest="$store_dir/auth.json.$suffix"
    if [[ -e "$dest" ]]; then
      rm -- "$path" || return 1
    else
      mv -- "$path" "$dest" || return 1
    fi
    chmod 600 "$dest" || return 1
  done
  for path in "$store_dir"/auth.json.*; do
    [[ -f "$path" && ! -L "$path" ]] || continue
    chmod 600 "$path" || return 1
  done

  tmp_link="$store_dir/.current.tmp.$$"
  rm -f -- "$tmp_link" || return 1
  ln -s "auth.json.$active_suffix" "$tmp_link" || return 1
  mv -Tf -- "$tmp_link" "$current_path" || return 1

  tmp_link="$agent_dir/.auth.json.clusterfork.tmp.$$"
  rm -f -- "$tmp_link" || return 1
  ln -s "$agent_link_target" "$tmp_link" || return 1
  mv -Tf -- "$tmp_link" "$auth_path" || return 1

  AUTH_STORE_CONFIGURED=1
}

# Copy skills for agents whose skill names must use hyphens. The shared source
# keeps its existing names for the agents that already use them as slash
# commands (for example, /commit_and_push). OpenCode discovers the valid names
# through ~/.claude/skills, so its native directory only needs incompatible
# aliases; Command Code and Antigravity get complete normalized trees.
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

command -v python3 >/dev/null || fail "python3 is required" "Needed by the rotate-* commands."
[[ -f "$DOTENV_SRC" ]] || fail "missing $(tildify "$DOTENV_SRC")" "Create it in the repo root with your local environment values."
mkdir -p "$CLUSTERFORK_CONFIG_DIR"
cp "$DOTENV_SRC" "$DOTENV_DEST"
step "env file" "$DOTENV_DEST"

if [[ -f "$BELL_SOUND_SRC" ]]; then
  cp "$BELL_SOUND_SRC" "$BELL_SOUND_DEST"
  step "bell sound" "$BELL_SOUND_DEST"
fi

[[ -f "$LOCAL_ENV_SRC" ]] || fail "missing $(tildify "$LOCAL_ENV_SRC")" "Create it in the repo root with your local shell aliases/functions."
[[ -d "$SHELL_SRC_DIR" ]] || fail "missing $(tildify "$SHELL_SRC_DIR")"

cp "$LOCAL_ENV_SRC" "$LOCAL_ENV_DEST"
step "shell config" "$LOCAL_ENV_DEST"

rm -rf -- "$SHELL_DEST_DIR"
mkdir -p "$SHELL_DEST_DIR"
cp -r "$SHELL_SRC_DIR"/. "$SHELL_DEST_DIR"/
step "shell modules" "$SHELL_DEST_DIR"

[[ -f "$TMUX_CONFIG_SRC" ]] || fail "missing $(tildify "$TMUX_CONFIG_SRC")"
cp "$TMUX_CONFIG_SRC" "$TMUX_CONFIG_DEST"
step "tmux" "$TMUX_CONFIG_DEST"

[[ -d "$BIN_SRC_DIR" ]] || fail "missing $(tildify "$BIN_SRC_DIR")"
rm -rf -- "$BIN_DEST_DIR"
mkdir -p "$BIN_DEST_DIR"
cp -r "$BIN_SRC_DIR"/. "$BIN_DEST_DIR"/
rm -rf -- "$BIN_DEST_DIR/__pycache__"
chmod +x "$BIN_DEST_DIR"/*
bin_detail=""
for f in "$BIN_SRC_DIR"/*; do
  if [[ -f "$f" ]]; then
    bin_detail+=", $(basename "$f")"
  fi
done
step "bin helpers" "$BIN_DEST_DIR" "${bin_detail#, }"

[[ -d "$NOTIFY_SRC_DIR" ]] || fail "missing $(tildify "$NOTIFY_SRC_DIR")"
rm -rf -- "$NOTIFY_DEST_DIR"
mkdir -p "$NOTIFY_DEST_DIR"
cp -r "$NOTIFY_SRC_DIR"/. "$NOTIFY_DEST_DIR"/
step "notify service" "$NOTIFY_DEST_DIR" "ntfy compose"

[[ -f "$ROTATE_AUTH_SRC" ]] || fail "missing $(tildify "$ROTATE_AUTH_SRC")"
mkdir -p "$(dirname "$ROTATE_AUTH_DEST")"
cp "$ROTATE_AUTH_SRC" "$ROTATE_AUTH_DEST"
chmod +x "$ROTATE_AUTH_DEST"
step "rotate-auth" "$ROTATE_AUTH_DEST"

[[ -d "$CODEVIEW_SRC_DIR" ]] || fail "missing $(tildify "$CODEVIEW_SRC_DIR")"
rm -rf -- "$CODEVIEW_DEST_DIR"
mkdir -p "$(dirname "$CODEVIEW_DEST_DIR")"
cp -r "$CODEVIEW_SRC_DIR"/. "$CODEVIEW_DEST_DIR"/
find "$CODEVIEW_DEST_DIR" -type d -name __pycache__ -exec rm -rf -- {} +
step "codeview server" "$CODEVIEW_DEST_DIR"

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

[[ -f "$ANTIGRAVITY_HOOKS_SRC" ]] || fail "missing $(tildify "$ANTIGRAVITY_HOOKS_SRC")"
mkdir -p "$(dirname "$ANTIGRAVITY_HOOKS_DEST")"
cp "$ANTIGRAVITY_HOOKS_SRC" "$ANTIGRAVITY_HOOKS_DEST"
step "antigravity hooks" "$ANTIGRAVITY_HOOKS_DEST" "Stop → clusterfork-notify"

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
  step "codex skills" "$CODEX_SKILLS_DEST_DIR" "preserves .system"

  # Command Code uses ~/.commandcode/skills and requires hyphenated skill IDs.
  copy_normalized_skills "$COMMAND_CODE_SKILLS_DEST_DIR" 0
  step "command code skills" "$COMMAND_CODE_SKILLS_DEST_DIR" "hyphenated ids"

  # Antigravity CLI uses ~/.gemini/antigravity-cli/skills and expects
  # hyphenated skill IDs.
  copy_normalized_skills "$ANTIGRAVITY_SKILLS_DEST_DIR" 0
  step "antigravity skills" "$ANTIGRAVITY_SKILLS_DEST_DIR" "hyphenated ids"

  # OpenCode also searches ~/.claude/skills. Keep its native directory for
  # aliases of the source skills whose underscore names OpenCode rejects.
  copy_normalized_skills "$OPENCODE_SKILLS_DEST_DIR" 1
  step "opencode skills" "$OPENCODE_SKILLS_DEST_DIR" "incompatible-name aliases"
fi

# Claude Code has no global off switch for ~/.claude.json mcpServers entries —
# /mcp's toggle only writes a per-project opt-out — so servers that must default
# to off ship as plugins instead. A directory under ~/.claude/skills/ holding
# .claude-plugin/plugin.json auto-loads as the plugin <name>@skills-dir, and
# agents/claude.json turns each one off in enabledPlugins. This runs after the
# skills copy above, which wipes that directory.
if [[ -d "$CLAUDE_PLUGINS_SRC_DIR" ]]; then
  python3 - "$CLAUDE_CONFIG_SRC" "$CLAUDE_PLUGINS_SRC_DIR" <<'CLAUDE_PLUGINS_PY'
import json, pathlib, sys

settings_path, plugins_dir = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
enabled = json.loads(settings_path.read_text()).get("enabledPlugins", {})

for plugin in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
    for required in (".claude-plugin/plugin.json", ".mcp.json"):
        if not (plugin / required).is_file():
            sys.exit(f"claude mcp plugins: {plugin.name} is missing {required}")
    key = f"{plugin.name}@skills-dir"
    if enabled.get(key) is not False:
        sys.exit(f"claude mcp plugins: {settings_path.name} must set "
                 f'"{key}": false — plugins are on unless told otherwise')
CLAUDE_PLUGINS_PY

  mkdir -p "$CLAUDE_SKILLS_DEST_DIR"
  plugin_names=""
  for d in "$CLAUDE_PLUGINS_SRC_DIR"/*/; do
    [[ -d "$d" ]] || continue
    plugin_name="$(basename "$d")"
    rm -rf -- "${CLAUDE_SKILLS_DEST_DIR:?}/$plugin_name"
    cp -r "$d" "$CLAUDE_SKILLS_DEST_DIR/$plugin_name"
    plugin_names+=", $plugin_name"
  done
  plugin_detail="${plugin_names#, }"
  if [[ -n "$plugin_detail" ]]; then
    plugin_detail="disabled: $plugin_detail"
  fi
  step "claude mcp plugins" "$CLAUDE_SKILLS_DEST_DIR" "$plugin_detail"
fi

[[ -f "$GROK_CONFIG_SRC" ]] || fail "missing $(tildify "$GROK_CONFIG_SRC")"
mkdir -p "$(dirname "$GROK_CONFIG_DEST")"
# Keep the user's current theme; everything else comes from the repo.
grok_theme=""
if [[ -f "$GROK_CONFIG_DEST" ]]; then
  grok_theme="$(sed -n 's/^theme = "\(.*\)"/\1/p' "$GROK_CONFIG_DEST" | head -n1)"
fi
cp "$GROK_CONFIG_SRC" "$GROK_CONFIG_DEST"
if [[ -n "$grok_theme" ]]; then
  sed -i "s/^theme = \".*\"/theme = \"$grok_theme\"/" "$GROK_CONFIG_DEST"
fi
grok_detail="Stop → clusterfork-notify"
if [[ -n "$grok_theme" ]]; then
  grok_detail+="; theme preserved"
fi
step "grok config" "$GROK_CONFIG_DEST" "$grok_detail"

[[ -f "$CLAUDE_CONFIG_SRC" ]] || fail "missing $(tildify "$CLAUDE_CONFIG_SRC")"
mkdir -p "$(dirname "$CLAUDE_CONFIG_DEST")"
cp "$CLAUDE_CONFIG_SRC" "$CLAUDE_CONFIG_DEST"
step "claude" "$CLAUDE_CONFIG_DEST" "Stop → clusterfork-notify"

[[ -f "$CLAUDE_STATUSLINE_SRC" ]] || fail "missing $(tildify "$CLAUDE_STATUSLINE_SRC")"
[[ -f "$CLAUDE_USAGE_FETCH_SRC" ]] || fail "missing $(tildify "$CLAUDE_USAGE_FETCH_SRC")"
mkdir -p "$(dirname "$CLAUDE_STATUSLINE_DEST")"
cp "$CLAUDE_STATUSLINE_SRC" "$CLAUDE_STATUSLINE_DEST"
chmod +x "$CLAUDE_STATUSLINE_DEST"
cp "$CLAUDE_USAGE_FETCH_SRC" "$CLAUDE_USAGE_FETCH_DEST"
chmod +x "$CLAUDE_USAGE_FETCH_DEST"
step "claude status" "$CLAUDE_STATUSLINE_DEST"
substep "$CLAUDE_USAGE_FETCH_DEST"

[[ -f "$CURSOR_STATUSLINE_SRC" ]] || fail "missing $(tildify "$CURSOR_STATUSLINE_SRC")"
[[ -f "$CURSOR_USAGE_FETCH_SRC" ]] || fail "missing $(tildify "$CURSOR_USAGE_FETCH_SRC")"
mkdir -p "$(dirname "$CURSOR_STATUSLINE_DEST")"
cp "$CURSOR_STATUSLINE_SRC" "$CURSOR_STATUSLINE_DEST"
chmod +x "$CURSOR_STATUSLINE_DEST"
cp "$CURSOR_USAGE_FETCH_SRC" "$CURSOR_USAGE_FETCH_DEST"
chmod +x "$CURSOR_USAGE_FETCH_DEST"
step "cursor status" "$CURSOR_STATUSLINE_DEST"
substep "$CURSOR_USAGE_FETCH_DEST"

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
step "cursor mcp" "$CURSOR_MCP_DEST" "$(mcp_server_names "$CURSOR_MCP_SRC")"

# Command Code MCP: same ${VAR} expansion as Cursor so secrets stay out of the repo.
[[ -f "$COMMAND_CODE_MCP_SRC" ]] || fail "missing $(tildify "$COMMAND_CODE_MCP_SRC")"
mkdir -p "$(dirname "$COMMAND_CODE_MCP_DEST")"
python3 - "$COMMAND_CODE_MCP_SRC" "$COMMAND_CODE_MCP_DEST" "$DOTENV_DEST" <<'PY'
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
            raise SystemExit(f"command code mcp: ${{{key}}} not set in {dotenv}")
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
step "command code mcp" "$COMMAND_CODE_MCP_DEST" "$(mcp_server_names "$COMMAND_CODE_MCP_SRC")"

# Antigravity MCP: same ${VAR} expansion as Cursor/Command Code so secrets stay out of the repo.
[[ -f "$ANTIGRAVITY_MCP_SRC" ]] || fail "missing $(tildify "$ANTIGRAVITY_MCP_SRC")"
mkdir -p "$(dirname "$ANTIGRAVITY_MCP_DEST")"
python3 - "$ANTIGRAVITY_MCP_SRC" "$ANTIGRAVITY_MCP_DEST" "$DOTENV_DEST" <<'PY'
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
            raise SystemExit(f"antigravity mcp: ${{{key}}} not set in {dotenv}")
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
step "antigravity mcp" "$ANTIGRAVITY_MCP_DEST" "$(mcp_server_names "$ANTIGRAVITY_MCP_SRC")"

# Command Code config: ensure telemetry is disabled. Merge keys from the repo
# template into ~/.commandcode/config.json so existing user settings (provider,
# model, etc.) are preserved. If the file does not exist, create it.
[[ -f "$COMMAND_CODE_CONFIG_SRC" ]] || fail "missing $(tildify "$COMMAND_CODE_CONFIG_SRC")"
mkdir -p "$(dirname "$COMMAND_CODE_CONFIG_DEST")"
python3 - "$COMMAND_CODE_CONFIG_SRC" "$COMMAND_CODE_CONFIG_DEST" <<'PY'
import json, sys
from pathlib import Path

src, dest = map(Path, sys.argv[1:])
wanted = json.loads(src.read_text(encoding="utf-8"))
if dest.is_file():
    try:
        current = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"command code config: {dest} is not valid JSON: {exc}")
    if not isinstance(current, dict):
        raise SystemExit(f"command code config: {dest} must be a JSON object")
else:
    current = {}

# Merge wanted keys into current (repo is source of truth for those keys).
merged = {**current, **wanted}
# Only write if changed to keep timestamps stable.
if merged != current:
    dest.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
PY
step "command code config" "$COMMAND_CODE_CONFIG_DEST" "merge: $(python3 -c 'import json, sys; print(", ".join(json.load(open(sys.argv[1], encoding="utf-8"))))' "$COMMAND_CODE_CONFIG_SRC")"

# Command Code user settings: ensure the shared Stop notifier. settings.json
# holds other user-scope keys, so unrelated hooks and settings are preserved.
[[ -f "$COMMAND_CODE_SETTINGS_SRC" ]] || fail "missing $(tildify "$COMMAND_CODE_SETTINGS_SRC")"
mkdir -p "$(dirname "$COMMAND_CODE_SETTINGS_DEST")"
python3 - "$COMMAND_CODE_SETTINGS_SRC" "$COMMAND_CODE_SETTINGS_DEST" <<'PY'
import json, sys
from pathlib import Path

src, dest = map(Path, sys.argv[1:])
wanted_stop = json.loads(src.read_text(encoding="utf-8"))["hooks"]["Stop"]
if dest.is_file():
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"command code settings: {dest} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"command code settings: {dest} must be a JSON object")
else:
    data = {}

hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    raise SystemExit(f"command code settings: 'hooks' in {dest} must be a JSON object")
stop = hooks.setdefault("Stop", [])
if not isinstance(stop, list):
    raise SystemExit(f"command code settings: 'hooks.Stop' in {dest} must be a JSON array")

# Migrate the exact legacy clusterfork bell without touching user-owned hooks.
legacy_commands = {
    "mpv --no-video --no-terminal ~/.config/clusterfork/bell.mp3",
    "mpv --no-video --no-terminal ${HOME}/.config/clusterfork/bell.mp3",
}

def is_legacy_clusterfork_bell(group):
    if not isinstance(group, dict) or set(group) != {"hooks"}:
        return False
    handlers = group.get("hooks")
    return (
        isinstance(handlers, list)
        and len(handlers) == 1
        and isinstance(handlers[0], dict)
        and handlers[0].get("type") == "command"
        and handlers[0].get("command") in legacy_commands
        and set(handlers[0]) == {"type", "command"}
    )

changed = False
migrated = [group for group in stop if not is_legacy_clusterfork_bell(group)]
if migrated != stop:
    hooks["Stop"] = stop = migrated
    changed = True
for definition in wanted_stop:
    if definition not in stop:
        stop.append(definition)
        changed = True
if changed or not dest.is_file():
    dest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
step "command code hooks" "$COMMAND_CODE_SETTINGS_DEST" "Stop → clusterfork-notify"

# Codex config: update top-level settings, replace mcp_servers and hook event
# tables from agents/codex.toml, strip retired clusterfork keys (notify), and
# stamp trusted_hash for the Stop notifier only (other hooks.state entries kept).
# Other settings (approvals and per-project trust levels written by Codex) are
# preserved.
[[ -f "$CODEX_CONFIG_SRC" ]] || fail "missing $(tildify "$CODEX_CONFIG_SRC")"
mkdir -p "$(dirname "$CODEX_CONFIG_DEST")"
python3 - "$CODEX_CONFIG_SRC" "$CODEX_CONFIG_DEST" "$DOTENV_DEST" "$REPO_DIR/scripts" <<'PY'
import os, re, sys, tomllib
from pathlib import Path

src_path, dest_path, dotenv_path, scripts_dir = map(Path, sys.argv[1:])
sys.path.insert(0, str(scripts_dir))
import codex_hook_trust

env = dict(os.environ)
if dotenv_path.is_file():
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")

src_raw = src_path.read_text(encoding="utf-8")

def expand_match(m: re.Match[str]) -> str:
    var = m.group(1)
    if var not in env:
        raise SystemExit(f"codex config: ${{{var}}} not set in environment or {dotenv_path}")
    return env[var]

expanded_src = re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", expand_match, src_raw)

try:
    wanted = tomllib.loads(expanded_src)
except tomllib.TOMLDecodeError as exc:
    raise SystemExit(f"codex config: {src_path} is not valid TOML: {exc}")

current = dest_path.read_text(encoding="utf-8") if dest_path.is_file() else ""
if current.strip():
    try:
        before = tomllib.loads(current)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"codex config: {dest_path} is not valid TOML: {exc}")
else:
    before = {}


def table_root(line: str) -> str | None:
    """First path segment of a TOML table header, e.g. [a.b] -> a. Else None."""
    text = line.strip()
    if not text.startswith("["):
        return None
    text = text.lstrip("[").strip()
    if text[:1] in ('"', "'"):
        quote = text[0]
        end = text.find(quote, 1)
        return text[1:end] if end != -1 else None
    for index, char in enumerate(text):
        if char in ".]":
            return text[:index].strip()
    return None


src_lines = expanded_src.splitlines()
first_table_idx = None
for idx, line in enumerate(src_lines):
    if table_root(line) is not None:
        first_table_idx = idx
        break

if first_table_idx is not None:
    src_top_lines = src_lines[:first_table_idx]
    src_tables_block = "\n".join(src_lines[first_table_idx:]).strip("\n")
else:
    src_top_lines = src_lines
    src_tables_block = ""

wanted_top_keys = {k: v for k, v in wanted.items() if not isinstance(v, dict)}
wanted_tables = set(k for k, v in wanted.items() if isinstance(v, dict))
# Clusterfork-owned top-level keys no longer in the template. Strip them so a
# reinstall does not leave the old notify bell stacked on the Stop hook.
dropped_top_keys = {"notify"}
if dropped_top_keys & wanted_top_keys.keys():
    raise SystemExit("codex config: dropped top keys must not also be in the template")
replace_or_drop = wanted_top_keys.keys() | dropped_top_keys


def key_name(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("["):
        return None
    if "=" in stripped:
        return stripped.split("=", 1)[0].strip()
    return None


dest_lines = current.splitlines()
dest_first_table_idx = len(dest_lines)
for idx, line in enumerate(dest_lines):
    if table_root(line) is not None:
        dest_first_table_idx = idx
        break

dest_top_lines = dest_lines[:dest_first_table_idx]
dest_table_lines = dest_lines[dest_first_table_idx:]

src_key_chunks = {}
current_k = None
for line in src_top_lines:
    k = key_name(line)
    if k in wanted_top_keys:
        current_k = k
        src_key_chunks[current_k] = [line]
    elif current_k and (line.startswith(" ") or line.startswith("\t") or line.startswith("]") or line.startswith("}")):
        src_key_chunks[current_k].append(line)
    else:
        current_k = None

new_dest_top_lines = []
skip = False
for line in dest_top_lines:
    k = key_name(line)
    if k in replace_or_drop:
        skip = True
        continue
    elif skip and (line.startswith(" ") or line.startswith("\t") or line.startswith("]") or line.startswith("}")):
        continue
    else:
        skip = False
        new_dest_top_lines.append(line)

while new_dest_top_lines and not new_dest_top_lines[-1].strip():
    new_dest_top_lines.pop()

for k, chunk in src_key_chunks.items():
    if new_dest_top_lines:
        new_dest_top_lines.append("")
    new_dest_top_lines.extend(chunk)

kept_tables: list[str] = []
pending: list[str] = []
dropping = False
for line in dest_table_lines:
    root = table_root(line)
    if root is not None:
        dropping = root in wanted_tables
        if dropping:
            pending = []
        else:
            kept_tables.extend(pending)
            pending = []
            kept_tables.append(line)
        continue
    if not line.strip() or line.lstrip().startswith("#"):
        pending.append(line)
        continue
    if dropping:
        pending = []
        continue
    kept_tables.extend(pending)
    pending = []
    kept_tables.append(line)
kept_tables.extend(pending)

top_section = "\n".join(new_dest_top_lines).strip("\n")
table_section = "\n".join(kept_tables).strip("\n")

parts = [p for p in [top_section, table_section, src_tables_block] if p]
result = "\n\n".join(parts) + "\n"

# Stamp trust for the clusterfork Stop notifier only. hooks.state is Codex-managed;
# keep any other entries and overwrite just dest:stop:0:0.
stop_groups = (wanted.get("hooks") or {}).get("Stop") or []
if not (
    stop_groups
    and stop_groups[0].get("hooks")
    and stop_groups[0]["hooks"][0].get("type") == "command"
    and stop_groups[0]["hooks"][0].get("command")
):
    raise SystemExit("codex config: agents/codex.toml must define [[hooks.Stop]] command hook")
stop_handler = stop_groups[0]["hooks"][0]
stop_key = codex_hook_trust.stop_hook_state_key(dest_path)
stop_hash = codex_hook_trust.trust_hash_for_stop_handler(stop_handler)
before_state = {}
if isinstance((before.get("hooks") or {}).get("state"), dict):
    before_state = {
        k: dict(v) for k, v in before["hooks"]["state"].items() if isinstance(v, dict)
    }
stop_entry = dict(before_state.get(stop_key) or {})
stop_entry["trusted_hash"] = stop_hash
before_state[stop_key] = stop_entry
state_block = codex_hook_trust.format_hooks_state_toml(before_state)
if state_block:
    result = result.rstrip() + "\n\n" + state_block
    if not result.endswith("\n"):
        result += "\n"

def event_hooks(table):
    if not isinstance(table, dict):
        return {}
    return {k: v for k, v in table.items() if k != "state"}

# Verify parsed output
after = tomllib.loads(result)
for k, v in wanted_top_keys.items():
    if after.get(k) != v:
        raise SystemExit(f"codex config: {k} was not installed cleanly into {dest_path}")
for tbl in wanted_tables:
    if tbl == "hooks":
        if event_hooks(after.get("hooks")) != event_hooks(wanted.get("hooks")):
            raise SystemExit(f"codex config: table [{tbl}] was not installed cleanly into {dest_path}")
        continue
    if after.get(tbl) != wanted.get(tbl):
        raise SystemExit(f"codex config: table [{tbl}] was not installed cleanly into {dest_path}")
after_stop = ((after.get("hooks") or {}).get("state") or {}).get(stop_key) or {}
if after_stop.get("trusted_hash") != stop_hash:
    raise SystemExit(f"codex config: Stop hook trusted_hash was not installed into {dest_path}")
for k in dropped_top_keys:
    if k in after:
        raise SystemExit(f"codex config: retired key '{k}' was not removed from {dest_path}")
for k, v in before.items():
    if k in dropped_top_keys:
        continue
    if k not in wanted_top_keys and k not in wanted_tables:
        if after.get(k) != v:
            raise SystemExit(f"codex config: refusing to write, unrelated key '{k}' in {dest_path} would change")

if result != current:
    dest_path.write_text(result, encoding="utf-8")
PY
step "codex config" "$CODEX_CONFIG_DEST" "Sol Ultra; Stop → clusterfork-notify; mcp_servers: $(python3 -c 'import sys, tomllib; print(", ".join(tomllib.load(open(sys.argv[1], "rb")).get("mcp_servers", {})))' "$CODEX_CONFIG_SRC")"

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
step "claude mcp" "$CLAUDE_USER_JSON" "merge: mcpServers.ElevenLabs"

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
  step "cursor cli" "$CURSOR_CLI_CONFIG" "merge: statusLine"
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
  step "cursor cli" "$CURSOR_CLI_CONFIG" "merge: statusLine"
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

# Shared auth link repair depends on pre-existing home-directory state, so it
# is intentionally best-effort and runs after the deterministic install steps.
if configure_shared_auth "Codex" "$CODEX_AUTH_DIR" "$CODEX_AUTH_STORE_DIR"; then
  if (( AUTH_STORE_CONFIGURED )); then
    step "codex auth" "$CODEX_AUTH_STORE_DIR"
  fi
else
  warn \
    "Codex shared auth was not configured; the rest of the install is complete." \
    "Fix the reported auth state, then re-run the installer."
fi

if configure_shared_auth "Cursor" "$CURSOR_AUTH_DIR" "$CURSOR_AUTH_STORE_DIR"; then
  if (( AUTH_STORE_CONFIGURED )); then
    step "cursor auth" "$CURSOR_AUTH_STORE_DIR"
  fi
else
  warn \
    "Cursor shared auth was not configured; the rest of the install is complete." \
    "Fix the reported auth state, then re-run the installer."
fi

if configure_shared_auth "Grok" "$GROK_AUTH_DIR" "$GROK_AUTH_STORE_DIR"; then
  if (( AUTH_STORE_CONFIGURED )); then
    step "grok auth" "$GROK_AUTH_STORE_DIR"
  fi
else
  warn \
    "Grok shared auth was not configured; the rest of the install is complete." \
    "Fix the reported auth state, then re-run the installer."
fi

if configure_shared_auth "OpenCode" "$OPENCODE_AUTH_DIR" "$OPENCODE_AUTH_STORE_DIR"; then
  if (( AUTH_STORE_CONFIGURED )); then
    step "opencode auth" "$OPENCODE_AUTH_STORE_DIR"
  fi
else
  warn \
    "OpenCode shared auth was not configured; the rest of the install is complete." \
    "Fix the reported auth state, then re-run the installer."
fi

# Shell commands: one line per launcher, its target, and fixed flags.
printf '\n  Shell commands\n'
printf '    %-5s %-18s %s\n' cl "claude" "--dangerously-skip-permissions --effort max"
printf '    %-5s %-18s %s\n' cmd "cmd" "--resume --yolo (unless --yolo/--dangerously-skip-permissions given)"
printf '    %-5s %-18s %s\n' cc "codex resume" "--yolo; gpt-5.6-sol ultra"
printf '    %-5s %-18s %s\n' ca "cursor-agent" "--yolo"
printf '    %-5s %-18s %s\n' oc "opencode" ""
printf '    %-5s %-18s %s\n' occ "claude (Go)" "--dangerously-skip-permissions --effort \$OCC_EFFORT (max)"
printf '    %-5s %-18s %s\n' ag "agy" "--dangerously-skip-permissions"
printf '    %-5s %-18s %s\n' gk "grok" ""
printf '    %-5s %-18s %s\n' chrome "chromium" "--remote-debugging-port=9222 (background)"
printf '    %-5s %-18s %s\n' codeview "bin/codeview" "start|stop|restart|reload|status|open (no args = start)"
printf '    %-5s %-18s %s\n' "rotate-*" "rotate_auth.py" "[name] --save name --unhook --list --kickoff [names]"

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
