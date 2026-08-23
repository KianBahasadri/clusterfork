_cf_tmux() {
  if [[ -n "${CF_NO_TMUX:-}" ]] || [[ -n "${TMUX:-}" ]] || ! [[ -t 0 ]] || ! command -v tmux >/dev/null 2>&1; then
    "$@"
    return
  fi
  local base name orig i
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
  tmux new-session -s "$name" -c "$PWD" "$@"
}
