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
# working: deepseek-v4-pro, deepseek-v4-flash, qwen3.5-plus, qwen3.6-plus,
# qwen3.7-plus, qwen3.7-max, qwen3.8-max, minimax-m2.5, minimax-m2.7,
# minimax-m3. Everything else rejects the tool schema outright. See
# docs/opencode-go.md for the matrix.

OPENCODE_GO_BASE_URL="${OPENCODE_GO_BASE_URL:-https://opencode.ai/zen/go}"
OCC_MODEL="${OCC_MODEL:-deepseek-v4-pro}"
OCC_SONNET_MODEL="${OCC_SONNET_MODEL:-deepseek-v4-pro}"
# Same model as the main slots: small-fast and bg-classifier join everything else
# on deepseek rather than a cheaper stand-in.
OCC_SMALL_MODEL="${OCC_SMALL_MODEL:-deepseek-v4-pro}"
# Graded effort on the deepseek models separates at the top rung — always max.
# Overridable; pass --effort <level> on the command line to override for one run.
OCC_EFFORT="${OCC_EFFORT:-max}"

# Everything /v1/messages can drive an agent loop with, in the order /model
# should list them. Perishable — re-verify with scripts/opencode_go_probe.py.
OCC_GATEWAY_MODELS="${OCC_GATEWAY_MODELS:-deepseek-v4-pro deepseek-v4-flash qwen3.8-max qwen3.7-max qwen3.7-plus qwen3.6-plus qwen3.5-plus minimax-m3 minimax-m2.7 minimax-m2.5}"
OCC_MODEL_DISCOVERY="${OCC_MODEL_DISCOVERY:-1}"

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

_occ_output_tokens() {
  # Claude Code defaults CLAUDE_CODE_MAX_OUTPUT_TOKENS to 32000 for model ids it
  # does not recognise — which is every opencode-go id. That ceiling was the
  # main reason high effort on flash looked inert in live sessions; flash
  # advertises 384000. Pull limit.output from the same models.dev cache.
  local cache="${OCC_MODELS_CACHE:-$HOME/.cache/opencode/models.json}"
  [[ -r "$cache" ]] || return 1
  jq -re --arg m "$1" '.["opencode-go"].models[$m].limit.output // empty' "$cache" 2>/dev/null
}

_occ_has_effort_flag() {
  local a
  for a in "$@"; do
    case "$a" in
      --effort|--effort=*) return 0 ;;
    esac
  done
  return 1
}

_occ_sync_model_options() {
  # Make every usable model selectable from /model. The picker offers only the
  # four alias slots (opus/sonnet/haiku/fable) plus a default row — five rows for
  # a catalog of ten — but Claude Code has a gateway-discovery path that adds
  # one row per model from a cache file, unlocked by the env var occ() exports.
  #
  # Its own fetcher never maintains that file for us: it GETs /v1/models and
  # keeps only ids matching /claude|anthropic/i, which drops this catalog whole,
  # so it gives up before writing. That cuts both ways — it will not overwrite
  # what we write here either.
  #
  # The file holds one gateway at a time (a baseUrl and its models), so writing
  # it discards any other gateway's entry. That is recoverable: for a gateway
  # serving claude-* ids the fetcher above repopulates it automatically, which is
  # exactly the case this catalog fails.
  #
  # Rows are labelled with the raw id, not the models.dev display name OpenCode
  # caches: Claude Code reuses display_name for the session header and the status
  # line, where "DeepSeek V4 Pro (New)" is both noisier and inconsistent with
  # the alias rows, which show ids.
  local cache="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/cache/gateway-models.json"
  local doc
  doc="$(jq -n --arg base "$OPENCODE_GO_BASE_URL" --arg ids "$OCC_GATEWAY_MODELS" '
    {baseUrl: $base,
     models: [$ids | split(" ") | .[] | select(length > 0) | {id: ., display_name: .}]}
  ' 2>/dev/null)" || return 0
  [[ -n "$doc" ]] || return 0

  # Nothing to do when the catalog already matches — leaves fetchedAt alone.
  if [[ -r "$cache" ]] && jq -e --argjson new "$doc" \
       '.baseUrl == $new.baseUrl and .models == $new.models' "$cache" >/dev/null 2>&1; then
    return 0
  fi

  mkdir -p "${cache%/*}" 2>/dev/null || return 0
  local tmp="$cache.occ.$$"
  if jq -n --argjson doc "$doc" --argjson now "$(date +%s)000" \
       '$doc + {fetchedAt: $now}' >"$tmp" 2>/dev/null; then
    mv -f "$tmp" "$cache" 2>/dev/null || rm -f "$tmp"
  else
    rm -f "$tmp"
  fi
}

occ() {
  local key context output
  if ! key="$(_occ_api_key)" || [[ -z "$key" ]]; then
    echo "occ: no OpenCode Go API key found" >&2
    echo "  Set OPENCODE_API_KEY in the clusterfork .env, or authenticate with:" >&2
    echo "    opencode auth login   # choose OpenCode Go" >&2
    return 1
  fi

  context="${OCC_MAX_CONTEXT_TOKENS:-$(_occ_context_tokens "$OCC_MODEL")}"
  output="${OCC_MAX_OUTPUT_TOKENS:-$(_occ_output_tokens "$OCC_MODEL")}"

  (
    # A cached OAuth token would otherwise outrank the gateway key.
    unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN

    [[ -n "$context" ]] && export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$context"
    # Without this, Claude Code pins gateway models at 32k max_tokens and
    # high-effort thinking hits the ceiling (see docs/opencode-go.md).
    [[ -n "$output" ]] && export CLAUDE_CODE_MAX_OUTPUT_TOKENS="$output"

    export ANTHROPIC_BASE_URL="$OPENCODE_GO_BASE_URL"
    export ANTHROPIC_API_KEY="$key"

    # Every slot must resolve to a real opencode-go id, including the aliases
    # ~/.claude/settings.json selects by name.
    export ANTHROPIC_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_OPUS_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_FABLE_MODEL="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_SONNET_MODEL="$OCC_SONNET_MODEL"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL="$OCC_MODEL"
    export ANTHROPIC_SMALL_FAST_MODEL="$OCC_SMALL_MODEL"
    export CLAUDE_CODE_SUBAGENT_MODEL="$OCC_MODEL"
    export CLAUDE_CODE_BG_CLASSIFIER_MODEL="$OCC_SMALL_MODEL"

    # Show the real model in /model and the status line, not "Opus"/"Sonnet".
    export ANTHROPIC_DEFAULT_OPUS_MODEL_NAME="$OCC_MODEL"
    export ANTHROPIC_DEFAULT_SONNET_MODEL_NAME="$OCC_SONNET_MODEL"
    export ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME="$OCC_MODEL"

    # Keep everything except inference off a third-party gateway.
    export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

    if [[ "$OCC_MODEL_DISCOVERY" != 0 ]]; then
      _occ_sync_model_options
      export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
    fi

    # Prefer --effort over CLAUDE_CODE_EFFORT_LEVEL so /effort still works
    # mid-session (the env var permanently outranks /effort).
    if [[ -z "${CLAUDE_CODE_EFFORT_LEVEL:-}" ]] && ! _occ_has_effort_flag "$@"; then
      exec claude --dangerously-skip-permissions --effort "${OCC_EFFORT:-max}" "$@"
    else
      exec claude --dangerously-skip-permissions "$@"
    fi
  )
}
