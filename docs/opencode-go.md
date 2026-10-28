# OpenCode Go and `occ`

`shell/opencode-claude.sh` launches Claude Code against OpenCode Go using
`occ`. It talks directly to `https://opencode.ai/zen/go` through the Anthropic
Messages route. `oc` runs OpenCode itself; the additional launcher was
introduced to use Claude Code's `/goal` workflow.

This page describes the shipped wrapper. Gateway availability and client
capabilities were measured in August 2026 and can change independently:
[route/catalog investigation](archive/opencode-go-catalog.md),
[effort measurements](archive/opencode-go-effort.md), and the
[unshipped Codex experiment](archive/opencode-go-codex.md). Those records are
not a live compatibility guarantee.

## Authentication and model routing

The launcher prefers `OPENCODE_API_KEY`; otherwise it reads `opencode-go.key`
from `${OCC_OPENCODE_AUTH_FILE:-~/.local/share/opencode/auth.json}`.
That fallback follows the account selected by `rotate-opencode`.
`occ` needs `jq`, Claude Code, and an OpenCode models cache or explicit limits.

In a launch subshell it clears `ANTHROPIC_AUTH_TOKEN` and
`CLAUDE_CODE_OAUTH_TOKEN`, then sets `ANTHROPIC_API_KEY` and
`ANTHROPIC_BASE_URL`. This uses `x-api-key` rather than a cached Anthropic
OAuth credential. Every Claude model slot is remapped to a gateway model:
main, opus/fable/sonnet/haiku aliases, small-fast, subagent, and background
classifier. An unmapped slot was observed to return an unsupported-model 401.

## Defaults and overrides

| Variable | Default / use |
| --- | --- |
| `OCC_MODEL` | `deepseek-v4-pro`; main, opus/fable/haiku, and subagent slots |
| `OCC_SONNET_MODEL` | `deepseek-v4-pro`; sonnet slot |
| `OCC_SMALL_MODEL` | `deepseek-v4-pro`; small-fast and background classifier |
| `OCC_EFFORT` | `max` |
| `OPENCODE_GO_BASE_URL` | `https://opencode.ai/zen/go` |
| `OCC_CLAUDE_CONFIG_DIR` | `~/.config/clusterfork/occ-claude` |
| `OCC_MODELS_CACHE` | `~/.cache/opencode/models.json` |
| `OCC_MAX_CONTEXT_TOKENS`, `OCC_MAX_OUTPUT_TOKENS` | Override the main model's cached context/output limits |
| `OCC_GATEWAY_MODELS` | Curated picker IDs from `shell/opencode-claude.sh` |
| `OCC_MODEL_DISCOVERY` | `1`; `0` skips writing/enabling picker discovery |

The three model variables default independently to pro. Flash is in the
picker list, not a default slot. `occ` passes `--effort "$OCC_EFFORT"` unless
caller arguments already contain `--effort`/`--effort=…` or
`CLAUDE_CODE_EFFORT_LEVEL` is nonempty. It always adds
`--dangerously-skip-permissions` and forwards the caller's arguments through
[the tmux wrapper](shell-modules.md#tmux-sessions-and-account-labels).

Context and output limits come from the `context` and `output` fields under
`.["opencode-go"].models[OCC_MODEL].limit` in the cache.
The corresponding `CLAUDE_CODE_MAX_*_TOKENS` variables are passed at launch.
Missing cached values leave those exports unset; client defaults then apply.
Changing `/model` during a session does not recompute the launch environment.
`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` is also set.

## Isolated settings and picker

`occ` sets `CLAUDE_CONFIG_DIR` to its private directory, symlinking `skills`
and `plugins` from `~/.claude`. Each launch refreshes private settings from
Claude's installed settings while retaining an already-saved private `model`.
Its settings and gateway cache therefore have separate paths from `cl`.

With discovery enabled, it seeds `cache/gateway-models.json` from
`OCC_GATEWAY_MODELS` and exports
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`. The cache has one gateway;
rows use raw model IDs. The alias rows may all display pro, while gateway
rows add the other curated models. In the investigated client, choosing a
raw-ID row persisted that ID in the private settings, while choosing an
alias persisted the alias name.

The curated list is a static launcher setting, not a fresh gateway scan.
Before relying on a new model, use an intentional tool-result probe as
explained in [Scripts](scripts.md#gateway-tool-loop-probe). The dated
[catalog record](archive/opencode-go-catalog.md) explains unsupported-model
errors, transient failures, and why a first tool call alone is insufficient.

Claude may warn that claude.ai connectors are disabled when the API key takes
precedence. Its shared [statusline](statusline.md#opencode-go) switches to
OpenCode account/dashboard data when the base URL contains `opencode.ai`;
custom gateway hostnames do not match that detection.
