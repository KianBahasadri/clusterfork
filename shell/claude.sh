export CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1

# After Opus 5, /model no longer lists 4.8. The custom-option env vars add one
# extra picker row for this launch; they stay inside the subshell.
unalias cl 2>/dev/null || true
cl() {
  (
    local -a _cl_env=(
      "ANTHROPIC_CUSTOM_MODEL_OPTION=claude-opus-4-8"
      "ANTHROPIC_CUSTOM_MODEL_OPTION_NAME=Opus 4.8"
      "ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION=Previous-generation Opus"
    )
    local -a _cl_cmd=(claude --dangerously-skip-permissions --effort max)
    if [[ "$#" -eq 0 ]] || [[ "$1" != -* ]]; then
      _cl_cmd+=(--continue)
    fi
    _cl_cmd+=("$@")

    local kv
    local -a _cl_tmux_env=()
    for kv in "${_cl_env[@]}"; do _cl_tmux_env+=(-e "$kv"); done
    _cf_tmux "${_cl_tmux_env[@]}" -- "${_cl_cmd[@]}"
  )
}

rotate-claude() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" claude "$@"
}
