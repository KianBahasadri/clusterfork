# Launch Qwen Code against the AgentRouter gateway (agentrouter.org), an
# OpenAI-compatible proxy. `ar [qwen args]` starts on DeepSeek V4 Pro.
#
# AgentRouter docs: https://docs.agentrouter.org/qwencode.html
# Qwen Code reads OPENAI_API_KEY, OPENAI_BASE_URL, and OPENAI_MODEL from the
# environment, but settings.model.name in ~/.qwen/settings.json overrides
# OPENAI_MODEL. Pass -m explicitly so AgentRouter is used instead of any
# provider configured in settings.json.

AR_AGENTROUTER_BASE_URL="https://agentrouter.org/v1"
AR_AGENTROUTER_MODEL="deepseek-v4-pro"

ar() {
  if [[ -z "${AGENT_ROUTER_API_KEY:-}" ]]; then
    echo "ar: AGENT_ROUTER_API_KEY is not set" >&2
    return 1
  fi

  (
    export OPENAI_API_KEY="$AGENT_ROUTER_API_KEY"
    export OPENAI_BASE_URL="$AR_AGENTROUTER_BASE_URL"
    export OPENAI_MODEL="$AR_AGENTROUTER_MODEL"
    qwen "$@"
  )
}
