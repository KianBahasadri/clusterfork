cmd() {
  local bin
  bin="$(type -P cmd 2>/dev/null)" || bin="cmd"
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yolo|--dangerously-skip-permissions)
        _cf_tmux "$bin" "$@"
        return
        ;;
    esac
  done
  _cf_tmux "$bin" --resume --yolo "$@"
}
