unalias ag 2>/dev/null || true
ag() { _cf_tmux agy --dangerously-skip-permissions "$@"; }

rotate-antigravity() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" antigravity "$@"
}
