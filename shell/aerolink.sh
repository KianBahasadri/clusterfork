AERO_AEROLINK_BASE_URL="https://capi.aerolink.lat/"

aero() {
  if [[ -z "${AEROLINK_API_KEY:-}" ]]; then
    echo "aero: AEROLINK_API_KEY is not set" >&2
    return 1
  fi

  (
    unset ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_OAUTH_TOKEN
    export ANTHROPIC_API_KEY="$AEROLINK_API_KEY"
    export ANTHROPIC_BASE_URL="$AERO_AEROLINK_BASE_URL"

    local settings
    settings="$(jq -n \
      --arg api_key "$AEROLINK_API_KEY" \
      --arg base_url "$AERO_AEROLINK_BASE_URL" \
      '{
        env: {
          ANTHROPIC_API_KEY: $api_key,
          ANTHROPIC_BASE_URL: $base_url,
          CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"
        },
        apiKeyHelper: ("echo " + ($api_key | @sh))
      }')"

    claude --settings "$settings" "$@"
  )
}
