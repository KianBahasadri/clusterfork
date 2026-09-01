unalias gk 2>/dev/null || true
gk() { _cf_tmux grok "$@"; }

rotate-grok() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" grok "$@"
}
