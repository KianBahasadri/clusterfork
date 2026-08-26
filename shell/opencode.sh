unalias oc 2>/dev/null || true
oc() { _cf_tmux opencode "$@"; }

rotate-opencode() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" opencode "$@"
}
