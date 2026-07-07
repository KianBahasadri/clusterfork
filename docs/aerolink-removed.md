# aerolink.sh (removed)

`aero` launched Claude Code against the Aerolink proxy (`https://capi.aerolink.lat/`)
using `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` and an inline settings JSON built
with `jq`. Required `AEROLINK_API_KEY`.

The function ran in a subshell that unset `ANTHROPIC_AUTH_TOKEN` and
`CLAUDE_CODE_OAUTH_TOKEN` before exporting the Aerolink key, so the proxy
credential took precedence over any cached OAuth tokens.

Removed from `shell/` — the proxy is no longer used.
