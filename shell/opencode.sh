unalias o oc 2>/dev/null || true
o() { _cf_tmux opencode "$@"; }
oc() { _cf_tmux opencode --continue "$@"; }

rotate-opencode() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" opencode "$@"
}
