alias oc='opencode --continue'
alias o='opencode'

rotate-opencode() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" opencode "$@"
}
