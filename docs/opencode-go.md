# OpenCode Go and `occ`

`shell/opencode-claude.sh` runs Claude Code on `https://opencode.ai/zen/go` via
Anthropic Messages; `oc` runs OpenCode itself. `occ` was added for Claude's `/goal`.
August 2026 [catalog](archive/opencode-go-catalog.md), [effort](archive/opencode-go-effort.md)
and [unshipped Codex](archive/opencode-go-codex.md) findings are historical,
not current gateway/client compatibility guarantees.

## Authentication and model routing

Requires `jq`, Claude and model cache or explicit limits. Prefer `OPENCODE_API_KEY`,
else `opencode-go.key` from `${OCC_OPENCODE_AUTH_FILE:-~/.local/share/opencode/auth.json}`,
following `rotate-opencode`. Launch subshell clears `ANTHROPIC_AUTH_TOKEN`/
`CLAUDE_CODE_OAUTH_TOKEN`, sets `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL` for `x-api-key`,
and remaps main, opus/fable/sonnet/haiku, small-fast, subagent and classifier slots.
Unmapped slots returned unsupported-model 401s in the investigation.

## Defaults and overrides

| Variable | Default / use |
| --- | --- |
| `OCC_MODEL` | `deepseek-v4-pro`; main, opus/fable/haiku, subagent |
| `OCC_SONNET_MODEL` | `deepseek-v4-pro`; sonnet |
| `OCC_SMALL_MODEL` | `deepseek-v4-pro`; small-fast/classifier |
| `OCC_EFFORT` | `max` |
| `OPENCODE_GO_BASE_URL` | `https://opencode.ai/zen/go` |
| `OCC_CLAUDE_CONFIG_DIR` | `~/.config/clusterfork/occ-claude` |
| `OCC_MODELS_CACHE` | `~/.cache/opencode/models.json` |
| `OCC_MAX_CONTEXT_TOKENS`, `OCC_MAX_OUTPUT_TOKENS` | Override cached main-model limits |
| `OCC_GATEWAY_MODELS` | Static curated IDs from the shell script |
| `OCC_MODEL_DISCOVERY` | `1`; `0` skips picker discovery |

Model defaults are independent; Flash is picker-only. Caller `--effort`/`--effort=…`
or nonempty `CLAUDE_CODE_EFFORT_LEVEL` overrides effort. Always adds
`--dangerously-skip-permissions` and forwards arguments through [tmux](shell-modules.md#tmux-sessions-and-account-labels).
Limits use `.["opencode-go"].models[OCC_MODEL].limit` fields `context`/`output`,
exported as `CLAUDE_CODE_MAX_*_TOKENS`; absent values leave client defaults.
Session `/model` changes do not recompute them. Also sets
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`.

## Isolated settings and picker

Private `CLAUDE_CONFIG_DIR` links `skills`/`plugins` from `~/.claude`. Each launch
refreshes installed Claude settings but keeps a saved private `model`. Discovery
seeds `cache/gateway-models.json` from the curated list and exports
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`. One gateway uses raw IDs alongside
aliases. In the investigated client, raw selections persisted IDs; alias choices
persisted aliases. This is a static list, not a gateway scan; recheck new models
with the [tool-result probe](scripts.md#gateway-tool-loop-probe). Catalog history
explains unsupported/transient errors and insufficient tool-call-only evidence.

Claude may warn that API-key auth disables claude.ai connectors. The [statusline](statusline.md#opencode-go)
uses OpenCode data only when the base URL contains `opencode.ai`, not custom hosts.
