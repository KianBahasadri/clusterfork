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
cc() { _cf_launch_codex tmux resume --yolo "$@"; }

rotate-codex() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" codex "$@"
}
