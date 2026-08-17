export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1

# After Opus 5, /model no longer lists 4.8. The custom-option env vars add one
# extra picker row for this launch; they stay inside the subshell.
unalias cl 2>/dev/null || true
cl() {
  (
    export ANTHROPIC_CUSTOM_MODEL_OPTION=claude-opus-4-8
    export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="Opus 4.8"
    export ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION="Previous-generation Opus"
    exec claude --dangerously-skip-permissions --effort xhigh "$@"
  )
}

rotate-claude() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" claude "$@"
}
