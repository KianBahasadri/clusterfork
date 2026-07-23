# Launch Qwen Code against the Pioneer OpenAI-compatible gateway. Qwen reads
# the Pioneer model catalog and endpoint from ~/.qwen/settings.json, installed
# from this repo's agents/qwen.json.

PI_PIONEER_BASE_URL="https://api.pioneer.ai/v1"
PI_PIONEER_DEFAULT_MODEL="zai-org/GLM-5.2"

pi() {
  local arg
  local has_model=0
  local -a qwen_args=(
    --approval-mode yolo
    --auth-type openai
  )

  if [[ -z "${PIONEER_API_KEY:-}" ]]; then
    echo "pi: PIONEER_API_KEY is not set" >&2
    return 1
  fi

  for arg in "$@"; do
    case "$arg" in
      -m|--model|--model=*)
        has_model=1
        break
        ;;
    esac
  done
  if (( ! has_model )); then
    qwen_args+=(--model "$PI_PIONEER_DEFAULT_MODEL")
  fi

  (
    # Keep the wrapper self-contained if the user-level Qwen config has not yet
    # been installed or a raw model id is supplied on the command line.
    export OPENAI_API_KEY="$PIONEER_API_KEY"
    export OPENAI_BASE_URL="$PI_PIONEER_BASE_URL"
    export OPENAI_MODEL="$PI_PIONEER_DEFAULT_MODEL"

    qwen "${qwen_args[@]}" "$@"
  )
}
