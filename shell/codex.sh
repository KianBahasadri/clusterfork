unalias cc 2>/dev/null || true
cc() {
  _cf_tmux codex resume --yolo \
    --config model=gpt-5.6-sol \
    --config model_reasoning_effort=ultra \
    "$@"
}

rotate-codex() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" codex "$@"
}
