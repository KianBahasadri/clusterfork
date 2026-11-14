unalias cc codex 2>/dev/null || true

_cf_launch_codex() {
  local launch="$1" binary line label="" want_tmux=0
  shift
  binary="$(type -P codex)" || {
    printf 'codex: command not found\n' >&2
    return 127
  }
  if [[ "$launch" == tmux && -z "${CF_NO_TMUX:-}" && -z "${TMUX:-}" && -t 0 ]] && command -v tmux >/dev/null 2>&1; then
    want_tmux=1
  fi
  local settings="${CODEX_HOME:-$HOME/.codex}/clusterfork-statusline.toml" has_settings=0 overrides
  local -a config_args=()
  [[ -r "$settings" ]] && has_settings=1
  if (( has_settings || want_tmux )); then
    # Selecting a native --profile also redirects Codex's preference writes.
    # Read only these shared values; keep normal config writes local.
    local helper_dir
    helper_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    overrides="$(python3 -S - "$settings" "$has_settings" "$want_tmux" "$helper_dir" <<'PY'
import json, os, sys, tomllib

settings_path, has_settings, want_tmux, helper_dir = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1", sys.argv[4]

if has_settings:
    try:
        with open(settings_path, "rb") as stream:
            tui = tomllib.load(stream).get("tui", {})
        if not isinstance(tui, dict):
            raise ValueError("tui must be a table")
        if "status_line" in tui:
            items = tui["status_line"]
            if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
                raise ValueError("tui.status_line must be an array of strings")
            print("CONFIG:tui.status_line=" + json.dumps(items, ensure_ascii=False))
        if "status_line_use_colors" in tui:
            colors = tui["status_line_use_colors"]
            if not isinstance(colors, bool):
                raise ValueError("tui.status_line_use_colors must be a boolean")
            print("CONFIG:tui.status_line_use_colors=" + json.dumps(colors))
    except (OSError, ValueError) as exc:
        print(f"codex status line: {exc}", file=sys.stderr)
        sys.exit(1)

if want_tmux:
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader

    label_path = os.path.join(helper_dir, "bin", "clusterfork-tmux-label")
    spec = spec_from_loader("clusterfork_tmux_label", SourceFileLoader("clusterfork_tmux_label", label_path))
    helper = module_from_spec(spec)
    spec.loader.exec_module(helper)
    print("LABEL:" + helper.format_label("codex"))
PY
)" || return "$?"
    while IFS= read -r line; do
      if [[ "$line" == CONFIG:* ]]; then
        config_args+=(--config "${line#CONFIG:}")
      elif [[ "$line" == LABEL:* ]]; then
        label="${line#LABEL:}"
      fi
    done <<< "$overrides"
  fi
  if [[ "$launch" == tmux ]]; then
    if [[ -n "$label" ]]; then
      _cf_tmux --label "$label" "$binary" "${config_args[@]}" "$@"
    else
      _cf_tmux "$binary" "${config_args[@]}" "$@"
    fi
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
  python3 -S "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/account_sessions.py" || return "$?"
  _cf_launch_codex tmux resume --yolo "${launch_args[@]}"
}

# This public command also accepts profiles and flags when called directly.
# shellcheck disable=SC2120
rotate-codex() {
  python3 -S "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" codex "$@"
}
