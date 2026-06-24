# Launch Claude Code against the Pioneer gateway (api.pioneer.ai), which exposes
# an Anthropic-compatible Messages API at /v1/messages. `pi [claude args]` starts
# on the Pioneer auto router; switch models from the /model picker.
#
# Claude Code's /model picker holds at most four model-family aliases plus one
# custom entry, and gateway discovery only lists ids beginning with
# claude/anthropic. So the picker is populated three ways: the four aliases are
# repointed at Pioneer models (PI_PIONEER_ALIAS_MODELS below), the custom entry
# is the auto router, and discovery pulls in the claude-named Pioneer models
# (for example claude-opus-4-8).

PI_PIONEER_BASE_URL="https://api.pioneer.ai"

# Effort capabilities advertised for every Pioneer model. The gateway tops out
# at xhigh and rejects `max`, so do not list max_effort.
PI_PIONEER_CAPABILITIES="effort,xhigh_effort"

# Available Pioneer models (the pool PI_PIONEER_ALIAS_MODELS draws from).
# Short key -> Pioneer model id. Ids match opencode.json (the source of truth).
declare -gA PI_PIONEER_MODELS=(
  [auto]="pioneer/auto"
  [qwen]="qwen3.7-max"
  [deepseek]="deepseek-ai/DeepSeek-V4-Pro"
  [kimi]="moonshotai/Kimi-K2.7-Code"
  [glm]="zai-org/GLM-5.2"
  [minimax]="MiniMaxAI/MiniMax-M3"
)

# Short key -> display name shown in the /model picker.
declare -gA PI_PIONEER_MODEL_NAMES=(
  [auto]="Pioneer Auto"
  [qwen]="Qwen 3.7 Max"
  [deepseek]="DeepSeek V4 Pro"
  [kimi]="Kimi K2.7 Code"
  [glm]="GLM 5.2"
  [minimax]="MiniMax M3"
)

# Model-family alias -> model key. These four are always in the picker and back
# background tasks, subagents, and "Default". Edit to change which models are
# the always-available staples. (fable only shows if your account has Fable.)
declare -gA PI_PIONEER_ALIAS_MODELS=(
  [opus]="kimi"
  [sonnet]="deepseek"
  [haiku]="glm"
  [fable]="qwen"
)

_pi_export_alias_slots() {
  local model_alias key id name upper
  for model_alias in "${!PI_PIONEER_ALIAS_MODELS[@]}"; do
    key="${PI_PIONEER_ALIAS_MODELS[$model_alias]}"
    id="${PI_PIONEER_MODELS[$key]}"
    name="${PI_PIONEER_MODEL_NAMES[$key]}"
    [[ -n "$id" ]] || continue
    upper="${model_alias^^}"
    export "ANTHROPIC_DEFAULT_${upper}_MODEL=$id"
    export "ANTHROPIC_DEFAULT_${upper}_MODEL_NAME=$name"
    export "ANTHROPIC_DEFAULT_${upper}_MODEL_SUPPORTED_CAPABILITIES=$PI_PIONEER_CAPABILITIES"
  done
}

pi() {
  if [[ -z "${PIONEER_API_KEY:-}" ]]; then
    echo "pi: PIONEER_API_KEY is not set" >&2
    return 1
  fi

  (
    # Pioneer authenticates with x-api-key, which Claude Code sends from
    # ANTHROPIC_API_KEY when no auth token is present. Clear any inherited
    # subscription/bearer credentials so they are not sent to the gateway.
    unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
    export ANTHROPIC_API_KEY="$PIONEER_API_KEY"
    export ANTHROPIC_BASE_URL="$PI_PIONEER_BASE_URL"

    # Add Pioneer's claude/anthropic-named models to the picker from /v1/models.
    export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1

    # Repoint the four family aliases at Pioneer models.
    _pi_export_alias_slots

    # Custom picker entry and start model: the auto router. It is not one of the
    # aliases and not claude-named, so register it here; id validation is skipped.
    export ANTHROPIC_CUSTOM_MODEL_OPTION="${PI_PIONEER_MODELS[auto]}"
    export ANTHROPIC_CUSTOM_MODEL_OPTION_NAME="${PI_PIONEER_MODEL_NAMES[auto]}"
    export ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION="Pioneer: ${PI_PIONEER_MODEL_NAMES[auto]}"
    export ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES="$PI_PIONEER_CAPABILITIES"

    claude \
      --dangerously-skip-permissions \
      --model "${PI_PIONEER_MODELS[auto]}" \
      --effort xhigh \
      "$@"
  )
}
