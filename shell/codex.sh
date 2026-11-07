unalias cc codex 2>/dev/null || true

_cf_launch_codex() {
  local launch="$1" binary arg
  shift
  binary="$(type -P codex)" || {
    printf 'codex: command not found\n' >&2
    return 127
  }
  local settings="${CODEX_HOME:-$HOME/.codex}/clusterfork-statusline.toml" overrides
  local -a config_args=()
  if [[ -r "$settings" ]]; then
    # Selecting a native --profile also redirects Codex's preference writes.
    # Read only these shared values; keep normal config writes local.
    overrides="$(python3 - "$settings" <<'PY'
import json, sys, tomllib

try:
    with open(sys.argv[1], "rb") as stream:
        tui = tomllib.load(stream).get("tui", {})
    if not isinstance(tui, dict):
        raise ValueError("tui must be a table")
    if "status_line" in tui:
        items = tui["status_line"]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise ValueError("tui.status_line must be an array of strings")
        print("tui.status_line=" + json.dumps(items, ensure_ascii=False))
    if "status_line_use_colors" in tui:
        colors = tui["status_line_use_colors"]
        if not isinstance(colors, bool):
            raise ValueError("tui.status_line_use_colors must be a boolean")
        print("tui.status_line_use_colors=" + json.dumps(colors))
except (OSError, ValueError) as exc:
    print(f"codex status line: {exc}", file=sys.stderr)
    sys.exit(1)
PY
)" || return "$?"
    while IFS= read -r arg; do
      [[ -n "$arg" ]] && config_args+=(--config "$arg")
    done <<< "$overrides"
  fi
  if [[ "$launch" == tmux ]]; then
    _cf_tmux "$binary" "${config_args[@]}" "$@"
  else
    "$binary" "${config_args[@]}" "$@"
  fi
}

codex() { _cf_launch_codex direct "$@"; }
cc() {
  local profile=""
  local -a launch_args=()
  while (( $# )); do
    case "$1" in
      --)
        launch_args+=("$@")
        break
        ;;
      --profile|-p)
        if [[ -n "$profile" ]]; then
          printf 'cc: --profile can be used only once\n' >&2
          return 2
        fi
        if [[ $# -lt 2 || -z "$2" || "$2" == -* ]]; then
          printf 'cc: --profile requires a profile name\n' >&2
          return 2
        fi
        profile="$2"
        shift 2
        ;;
      --profile=*|-p?*)
        if [[ -n "$profile" ]]; then
          printf 'cc: --profile can be used only once\n' >&2
          return 2
        fi
        if [[ "$1" == --profile=* ]]; then
          profile="${1#--profile=}"
        else
          profile="${1#-p}"
        fi
        if [[ -z "$profile" || "$profile" == -* ]]; then
          printf 'cc: --profile requires a profile name\n' >&2
          return 2
        fi
        shift
        ;;
      *)
        launch_args+=("$1")
        shift
        ;;
    esac
  done
  # Only a profile name selects an account. Other launch arguments stay with Codex.
  if [[ -n "$profile" ]]; then
    rotate-codex "$profile" || return "$?"
  else
    # shellcheck disable=SC2119
    rotate-codex || return "$?"
  fi
  _cf_launch_codex tmux resume --yolo "${launch_args[@]}"
}

# This public command also accepts profiles and flags when called directly.
# shellcheck disable=SC2120
rotate-codex() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" codex "$@"
}
