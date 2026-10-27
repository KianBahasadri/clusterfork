_cf_tmux() (
  local agent="" kv
  local -a launch_env=() tmux_env=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --agent) agent="$2"; shift 2 ;;
      -e) launch_env+=("$2"); tmux_env+=(-e "$2"); shift 2 ;;
      --) shift; break ;;
      *) break ;;
    esac
  done
  if [[ -n "${CF_NO_TMUX:-}" ]] || [[ -n "${TMUX:-}" ]] || ! [[ -t 0 ]] || ! command -v tmux >/dev/null 2>&1; then
    for kv in "${launch_env[@]}"; do export "$kv"; done
    exec "$@"
  fi
  local base name orig i label
  agent="${agent:-${1##*/}}"
  case "$agent" in
    cursor-agent) agent=cursor ;;
    agy) agent=antigravity ;;
  esac
  label="$(python3 "$(dirname "${BASH_SOURCE[0]}")/../bin/clusterfork-tmux-label" "$agent" 2>/dev/null)" || label="$agent - unknown"
  base="$(basename "$PWD")"
  [[ "$base" == "/" || -z "$base" ]] && base="root"
  name="${base//./-}"
  name="${name//:/-}"
  [[ "$name" == -* ]] && name="_$name"
  [[ -z "$name" ]] && name="default"
  orig="$name"
  i=1
  while tmux has-session -t "$name" 2>/dev/null; do
    name="${orig}-${i}"
    ((i++))
  done
  # The window name is a snapshot, not a live lookup of the rotating account.
  exec tmux new-session -s "$name" -n "$label" -c "$PWD" "${tmux_env[@]}" -- "$@" \
    \; set-option -w -t "=$name:" automatic-rename off \
    \; set-option -w -t "=$name:" allow-rename off
)
