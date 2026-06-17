# Launch Claude Code against the AgentRouter gateway (agentrouter.org), an
# Anthropic-compatible proxy. `ar [claude args]` starts on DeepSeek V4 Pro;
# switch models from the /model picker.
#
# AgentRouter's model ids are not claude/anthropic-named, so Claude Code's
# gateway discovery cannot list them. Instead the three models are pinned onto
# the opus/sonnet/haiku family aliases (AR_AGENTROUTER_ALIAS_MODELS below). That
# is what populates the /model picker and also routes "Default", background
# tasks, and subagents. There is no Fable model, so the fable alias is left
# alone and does not appear.

AR_AGENTROUTER_BASE_URL="https://agentrouter.org/"

# Available AgentRouter models. Short key -> model id (the exact id the gateway
# expects, taken from the AgentRouter model list).
declare -gA AR_AGENTROUTER_MODELS=(
  [deepseek_pro]="deepseek-v4-pro"
  [deepseek_flash]="deepseek-v4-flash"
  [glm]="glm-5.1"
)

# Short key -> display name shown in the /model picker.
declare -gA AR_AGENTROUTER_MODEL_NAMES=(
  [deepseek_pro]="DeepSeek V4 Pro"
  [deepseek_flash]="DeepSeek V4 Flash"
  [glm]="GLM 5.1"
)

# Model-family alias -> model key. These three are always in the picker and back
# background tasks, subagents, and "Default". Edit to change the mapping.
declare -gA AR_AGENTROUTER_ALIAS_MODELS=(
  [opus]="deepseek_pro"
  [sonnet]="deepseek_flash"
  [haiku]="glm"
)

# Model the `ar` function starts on.
AR_AGENTROUTER_START_MODEL="${AR_AGENTROUTER_MODELS[deepseek_pro]}"

_ar_export_alias_slots() {
  local model_alias key id name upper
  for model_alias in "${!AR_AGENTROUTER_ALIAS_MODELS[@]}"; do
    key="${AR_AGENTROUTER_ALIAS_MODELS[$model_alias]}"
    id="${AR_AGENTROUTER_MODELS[$key]}"
    name="${AR_AGENTROUTER_MODEL_NAMES[$key]}"
    [[ -n "$id" ]] || continue
    upper="${model_alias^^}"
    export "ANTHROPIC_DEFAULT_${upper}_MODEL=$id"
    export "ANTHROPIC_DEFAULT_${upper}_MODEL_NAME=$name"
  done
}

ar() {
  if [[ -z "${AGENT_ROUTER_API_KEY:-}" ]]; then
    echo "ar: AGENT_ROUTER_API_KEY is not set" >&2
    return 1
  fi

  (
    # AgentRouter authenticates with the sk- key as both a bearer token
    # (ANTHROPIC_AUTH_TOKEN) and x-api-key (ANTHROPIC_API_KEY), per its docs.
    # Clear any inherited subscription oauth token so it is not sent to the
    # third-party gateway.
    unset CLAUDE_CODE_OAUTH_TOKEN
    export ANTHROPIC_AUTH_TOKEN="$AGENT_ROUTER_API_KEY"
    export ANTHROPIC_API_KEY="$AGENT_ROUTER_API_KEY"
    export ANTHROPIC_BASE_URL="$AR_AGENTROUTER_BASE_URL"

    # Pin the three AgentRouter models onto the family aliases so they appear in
    # the /model picker (discovery cannot list non-claude ids).
    _ar_export_alias_slots

    claude \
      --dangerously-skip-permissions \
      --model "$AR_AGENTROUTER_START_MODEL" \
      "$@"
  )
}
