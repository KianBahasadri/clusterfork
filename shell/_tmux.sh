_cf_tmux() {
  if [[ -n "${CF_NO_TMUX:-}" ]] || [[ -n "${TMUX:-}" ]] || ! [[ -t 0 ]] || ! command -v tmux >/dev/null 2>&1; then
    "$@"
    return
  fi
  local base name
  base="$(basename "$PWD")"
  [[ "$base" == "/" || -z "$base" ]] && base="root"
  name="${base//./-}"
  name="${name//:/-}"
  [[ "$name" == -* ]] && name="_$name"
  [[ -z "$name" ]] && name="default"
  tmux new-session -A -s "$name" -c "$PWD" "$@"
}
