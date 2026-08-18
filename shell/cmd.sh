cmd() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yolo|--dangerously-skip-permissions)
        command cmd "$@"
        return
        ;;
    esac
  done
  command cmd --resume --yolo "$@"
}
