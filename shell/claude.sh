export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
alias cl='claude --dangerously-skip-permissions --effort xhigh'

rotate-claude() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" claude "$@"
}
