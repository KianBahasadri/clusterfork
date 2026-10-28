# OpenCode Go catalog investigation

Measured 2026-08-06 with OpenCode 0.16.x, Claude Code 2.1.223, and Codex
0.146.0; pro was rechecked on 2026-08-13. This is a dated compatibility
record. It does not establish current gateway availability. The shipped
[`occ` wrapper](../opencode-go.md) owns active setup instructions.

## Routes and authentication

All routes were under `https://opencode.ai/zen/go` using the same Go key:

| Route | Format | Authentication observed |
| --- | --- | --- |
| `/v1/messages` | Anthropic Messages | `x-api-key`; Bearer was rejected |
| `/v1/chat/completions` | OpenAI Chat Completions | Bearer |
| `/v1/responses` | OpenAI Responses | Bearer |
| `/v1/models` | OpenAI list shape (`object`/`data`) | Bearer |

Unknown or unsupported IDs returned 401 `Model … is not supported for format …`,
which could be mistaken for bad authentication. Provider failures often hid
request-validation details behind generic 400/422/500/503 errors.
Cloudflare rejected Python urllib's default user agent with 403/body
`error code: 1010` before the gateway; the retained probes send CLI-shaped
user agents.

Usage/account/billing/quota paths returned the marketing SPA rather than quota
data, and successful Messages requests lacked rate-limit headers. That led to
using a dashboard scrape for the [statusline](../statusline.md#opencode-go).

## Catalog and tool loops

The catalog listed 25 IDs. `kimi-k3`, `hy3-preview`, `mimo-v2-omni`, and
`mimo-v2-pro` failed on all tested routes (unavailable/deprecated).
`grok-4.5` failed on Chat Completions but worked on Responses.

| Route | Full agent-loop results |
| --- | --- |
| Chat Completions | 20 of 25; every live model except `grok-4.5` |
| Anthropic Messages | 10: `deepseek-v4-pro`, `deepseek-v4-flash`, `qwen3.5-plus`, `qwen3.6-plus`, `qwen3.7-plus`, `qwen3.7-max`, `qwen3.8-max`, `minimax-m2.5`, `minimax-m2.7`, `minimax-m3` |
| Streaming Responses | 3: `gpt-5.6-luna`, `grok-4.5`, `deepseek-v4-flash` |

These are protocol probes. OpenCode itself selected different SDK routes by
model family; it did not send every model through Chat Completions. See
[effort and variant capture](opencode-go-effort.md#client-fields).
The unshipped [Codex experiment](opencode-go-codex.md) explains why some
otherwise successful Responses requests could not run a Codex session.

On Messages, `glm-*` rejected tool schemas with 422; Kimi, MiMo, and `hy3`
rejected request shapes with 400; Grok was unsupported; GPT did not emit
`tool_use`. Healthy models could still return transient 503s, so three retries
were used before classifying unavailability.

### DeepSeek corrections

An early flash probe emitted a tool call but returned an empty tool-result
reply. Later the same day it passed four nonstreaming shape variants, ten
streaming round trips, and real Claude Grep/Read/Bash/Edit/Write loops.
Other models reproduced their earlier failures. A transient upstream or shim
change was suspected; the original throwaway scripts were unavailable, so the
precise cause was not established.

Flash remained in the working set. Pro's original tool-schema failure was
superseded by a successful tool-result round trip on 2026-08-13 and it became
the launcher default. Flash's thinking signature echoed the message ID rather
than providing a real signature; the tested Claude build did not reject it.

## Rechecking

Use the retained [tool-loop probe](../scripts.md#gateway-tool-loop-probe) for
an intentional model check. Assert the tool-result follow-up produces the
injected result, not just that a first tool call exists. Streaming event types
and real client request capture matter when direct API probes disagree with a
CLI. Preserve a new run's client versions, date, and request shape.
