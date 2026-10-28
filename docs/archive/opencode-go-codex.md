# Codex on OpenCode Go: unshipped experiment

Investigated with Codex CLI 0.146.0 on 2026-08-06, followed by the
[2026-08-07 effort probes](opencode-go-effort.md). No provider profile or
launcher was shipped. This is a record of that build's behavior, not a tested
invocation for current Codex.

## Provider setup explored

Codex 0.146.0 rejected the retired `wire_api = "chat"`, so the experiment used
a custom provider with `wire_api = "responses"`,
`base_url = "https://opencode.ai/zen/go/v1"`, and
`env_key = "OPENCODE_API_KEY"`. End-to-end file reads worked on the
[three usable streaming models](opencode-go-catalog.md#catalog-and-tool-loops).
The custom provider authenticated from its environment rather than an OpenAI
account login.

A working request also required pruning Codex's tool surface:

- `web_search="disabled"` removed the hosted web-search tool rejected by some
  upstreams. The attempted `tools.web_search=false` and
  `--disable web_search_request` alternatives were ignored.
- `--disable multi_agent` and disabling every configured MCP server removed
  namespace tools that Grok rejected with 422. `-c mcp_servers={}` merged with
  the existing roster rather than clearing it.
- Grok needed reasoning effort at most `high`; `max` returned 400 after
  reconnection attempts. An empty tool array was also rejected, so at least
  one function tool had to remain.

These fields were isolated by capturing the client's actual body and replaying
it while removing one field at a time. Generic gateway errors did not identify
the bad option.

## Why successful direct calls still failed in Codex

Codex streamed Responses. The usable upstream shims emitted lifecycle events
such as `response.created` and `response.output_item.added/done` as well as
text deltas and completion. The degraded shims emitted only text deltas,
completion, and pings, so Codex could not assemble output items or tool calls.
The visible failure was a session ending without output or an error.

The degraded group included GLM, DeepSeek Pro, `hy3`, Kimi, and MiMo. Some
returned a function call nonstreaming but rejected its `function_call_output`
follow-up. Qwen and MiniMax were unsupported or errored on Responses.
An early theory that a missing output cap caused the empty session did not
survive retesting; stream shape explained that failure.

The gateway's OpenAI-shaped model list also caused Codex model-refresh and
missing-metadata warnings. Context windows could be supplied explicitly, but
output caps could not be configured in that Codex version. The later probe's
high-cap flash effort result therefore did not prove equivalent CLI behavior.

## Outcome

The experiment reached models unavailable through Claude's route, but needed
several overrides and had silent streaming failure modes. No Codex wrapper
was adopted. Revisit only with a current client, current model catalog, full
stream-event checks, and a tool-result round trip. The shipped alternative
is [`occ`](../opencode-go.md).
