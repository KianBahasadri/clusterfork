cmd() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yolo|--dangerously-skip-permissions)
        _cf_tmux command cmd "$@"
        return
        ;;
    esac
  done
  _cf_tmux command cmd --resume --yolo "$@"
}
