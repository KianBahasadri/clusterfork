# Launch Claude Code against the OpenCode Go subscription.
#
# OpenCode Go serves an Anthropic-compatible /v1/messages endpoint, so Claude
# Code talks to it directly with no proxy. Two constraints drive this module:
#
#   1. The catalog has no Claude models, so every model slot Claude Code can
#      route to must be remapped to an opencode-go id. An unmapped id comes
#      back as 401 "Model ... is not supported", not a 404.
#   2. Only x-api-key authenticates. Bearer tokens are rejected, so the key
#      goes in ANTHROPIC_API_KEY (never ANTHROPIC_AUTH_TOKEN).
#
# Most of the catalog cannot drive an agent loop over this endpoint. Verified
# working: deepseek-v4-flash, qwen3.5-plus, qwen3.6-plus, qwen3.7-plus,
# qwen3.7-max, qwen3.8-max, minimax-m2.5, minimax-m2.7, minimax-m3. Everything
# else rejects the tool schema outright. See docs/opencode-go.md for the matrix.

OPENCODE_GO_BASE_URL="${OPENCODE_GO_BASE_URL:-https://opencode.ai/zen/go}"
OCC_MODEL="${OCC_MODEL:-deepseek-v4-flash}"
OCC_SONNET_MODEL="${OCC_SONNET_MODEL:-deepseek-v4-flash}"
# Kept on a separate cheap model: this slot drives the background classifier and
# other trivial calls, where deepseek's 384k output ceiling buys nothing.
OCC_SMALL_MODEL="${OCC_SMALL_MODEL:-minimax-m3}"

_occ_api_key() {
  # Prefer the clusterfork .env; otherwise read the live OpenCode auth store so
  # occ follows whichever account rotate-opencode currently has selected.
  if [[ -n "${OPENCODE_API_KEY:-}" ]]; then
    printf '%s\n' "$OPENCODE_API_KEY"
    return 0
  fi

  local auth="${OCC_OPENCODE_AUTH_FILE:-$HOME/.local/share/opencode/auth.json}"
  [[ -r "$auth" ]] || return 1
  jq -re '.["opencode-go"].key // empty' "$auth" 2>/dev/null
}

_occ_context_tokens() {
  # Claude Code assumes a 200k window for models it doesn't ship metadata for,
  # which would auto-compact these sessions far too early — the working models
  # range from 204k to 1M. Read the real window from the models.dev cache
  # OpenCode already maintains, so an OCC_MODEL override stays correct.
  local cache="${OCC_MODELS_CACHE:-$HOME/.cache/opencode/models.json}"
  [[ -r "$cache" ]] || return 1
  jq -re --arg m "$1" '.["opencode-go"].models[$m].limit.context // empty' "$cache" 2>/dev/null
}

occ() {
  local key context
  if ! key="$(_occ_api_key)" || [[ -z "$key" ]]; then
    echo "occ: no OpenCode Go API key found" >&2
    echo "  Set OPENCODE_API_KEY in the clusterfork .env, or authenticate with:" >&2
    echo "    opencode auth login   # choose OpenCode Go" >&2
    return 1
  fi

  context="${OCC_MAX_CONTEXT_TOKENS:-$(_occ_context_tokens "$OCC_MODEL")}"

  (
    # A cached OAuth token would otherwise outrank the gateway key.
    unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN

    [[ -n "$context" ]] && export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$context"

    export ANTHROPIC_BASE_URL="$OPENCODE_GO_BASE_URL"
    export ANTHROPIC_API_KEY="$key"

    # Every slot must resolve to a real opencode-go id, including the aliases
    # ~/.claude/settings.json selects by name.
    export ANTHROPIC_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_FABLE_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$OCC_SONNET_MODEL"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$OCC_SMALL_MODEL"
    export ANTHROPIC_SMALL_FAST_MODEL="$OCC_SMALL_MODEL"
    export CLAUDE_CODE_SUBAGENT_MODEL="$OCC_MODEL"
    export CLAUDE_CODE_BG_CLASSIFIER_MODEL="$OCC_SMALL_MODEL"

    # Show the real model in /model and the status line, not "Opus"/"Sonnet".
    export ANTHROPIC_DEFAULT_OPUS_MODEL_NAME="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME="$OCC_SONNET_MODEL"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME="$OCC_SMALL_MODEL"

    # Keep everything except inference off a third-party gateway.
    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

    exec claude --dangerously-skip-permissions "$@"
  )
}
