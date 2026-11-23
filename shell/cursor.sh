unalias ca 2>/dev/null || true

_cf_cursor_has_profiles() {
  local store="${ROTATE_CURSOR_AUTH_STORE_DIR:-$HOME/.local/share/clusterfork-auth/cursor}"
  local profile
  for profile in "$store"/auth.json.*; do
    [[ -f "$profile" ]] && return 0
  done
  return 1
}

if _cf_cursor_has_profiles; then
  export AGENT_CLI_CREDENTIAL_STORE=file
fi

ca() {
  local credential_store="${AGENT_CLI_CREDENTIAL_STORE:-}"
  if _cf_cursor_has_profiles; then
    credential_store="file"
  fi
  if [[ -n "$credential_store" ]]; then
    _cf_tmux -e "AGENT_CLI_CREDENTIAL_STORE=$credential_store" cursor-agent --yolo "$@"
  else
    _cf_tmux cursor-agent --yolo "$@"
  fi
}

rotate-cursor-cli() {
  python3 -S "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" cursor "$@" || return
  if _cf_cursor_has_profiles; then
    export AGENT_CLI_CREDENTIAL_STORE=file
  fi
}
