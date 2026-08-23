unalias cc 2>/dev/null || true
cc() { _cf_tmux codex resume --yolo "$@"; }

rotate-codex() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" codex "$@"
}
