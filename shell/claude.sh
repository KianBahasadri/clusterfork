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
    local -a _cl_cmd=(claude --dangerously-skip-permissions --effort xhigh "$@")

    if [[ -n "${CF_NO_TMUX:-}" ]] || [[ -n "${TMUX:-}" ]] || ! [[ -t 0 ]] || ! command -v tmux >/dev/null 2>&1; then
      for kv in "${_cl_env[@]}"; do export "$kv"; done
      exec "${_cl_cmd[@]}"
    fi

    local base name orig i
    base="$(basename "$PWD")"
    [[ "$base" == "/" || -z "$base" ]] && base="root"
    name="${base//./-}"
    name="${name//:/-}"
    [[ "$name" == -* ]] && name="_$name"
    [[ -z "$name" ]] && name="default"
    orig="$name"
    i=1
    while tmux has-session -t "$name" 2>/dev/null; do
      name="${orig}-${i}"
      ((i++))
    done
    local -a _cl_tmux_env=()
    for kv in "${_cl_env[@]}"; do _cl_tmux_env+=(-e "$kv"); done
    exec tmux new-session -s "$name" -c "$PWD" "${_cl_tmux_env[@]}" -- "${_cl_cmd[@]}"
  )
}

rotate-claude() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" claude "$@"
}
