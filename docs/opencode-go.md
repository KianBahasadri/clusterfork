# OpenCode Go endpoint

Record of what the **OpenCode Go** subscription endpoint actually serves, and
which third-party CLIs can be pointed at it. The `occ` launcher that came out of
this work is documented in [Shell Modules](shell-modules.md); this file is the
research behind it, including the parts that did not ship.

Everything below was measured against the live endpoint on 2026-08-06 with
`opencode-cli` 0.16.x, Claude Code 2.1.223, and Codex CLI 0.146.0. The catalog
and the upstreams behind it change without notice — re-probe before trusting it.
The `deepseek-v4-flash` reversal recorded below happened within hours on that
same day, so treat every table here as perishable.

## Why not just use OpenCode

`oc` reaches the whole catalog over Chat Completions with no remapping, and it
is the better path whenever it is available. The one thing it lacks is a
`/goal` command, which is the entire reason `occ` exists. **If OpenCode ever
ships `/goal`, drop `occ` and go back to `oc`** — nothing else about this
detour is worth keeping.

## Endpoint and auth

Base URL is `https://opencode.ai/zen/go`. It is not just an OpenAI-compatible
gateway — it serves **three** wire formats off the same base and the same key:

| Route | Format | Auth |
| --- | --- | --- |
| `/v1/messages` | Anthropic Messages | `x-api-key` **only** — `Authorization: Bearer` returns 401 |
| `/v1/chat/completions` | OpenAI Chat Completions | `Authorization: Bearer` |
| `/v1/responses` | OpenAI Responses | `Authorization: Bearer` |
| `/v1/models` | OpenAI list shape (`{"object":"list","data":[…]}`) | `Authorization: Bearer` |

That the Anthropic route exists at all is the reason `occ` needs no proxy. The
models.dev registry lists the provider as `@ai-sdk/openai-compatible`, which
undersells it.

The key lives at `.["opencode-go"].key` in `~/.local/share/opencode/auth.json`.
Per-model context windows are in the models.dev cache OpenCode maintains at
`~/.cache/opencode/models.json` (`.["opencode-go"].models[ID].limit.context`).

The gateway exposes **no usage or quota data**. `/usage`, `/v1/usage`, `/account`,
`/v1/account`, `/billing`, and `/quota` all 404 into the marketing SPA, and a
successful `/v1/messages` call returns no rate-limit headers. The only source of
Go plan usage is the authenticated web dashboard, which is why the `occ`
statusline reads a scrape of it rather than asking the gateway — see
[Statusline](statusline.md).

Error codes are not what you would expect. An unknown or unsupported model id on
a given route comes back as **401 `Model … is not supported for format …`**, not
404. Upstream failures surface as 400/422/500/503 with the generic message
`Error from provider (Console Go): Upstream request failed` — the real reason is
usually absent, so isolating a bad field means bisecting the request body.

## Catalog liveness

25 ids are listed. Four are dead on every route and have been for the duration of
this testing (three consecutive retries each, all formats):

| Model | Failure |
| --- | --- |
| `kimi-k3` | 503 `Endpoint is unavailable` |
| `hy3-preview` | 400 `Model is unavailable` |
| `mimo-v2-omni` | 404 `This model has been deprecated` |
| `mimo-v2-pro` | 404 `This model has been deprecated` |

`grok-4.5` is a fifth special case: **dead on `/v1/chat/completions`** (503,
consistently) but healthy on `/v1/responses`. Route availability is per-model,
not global.

## Which route serves which models

The three routes do **not** expose the same catalog, and the two that matter for
agent CLIs — Anthropic and Responses — are almost perfectly **disjoint**.

**`/v1/chat/completions` — 20 of 25.** Every live model works. All 20 emit
`tool_calls`, answer a `role: "tool"` follow-up, and stream. Only the four dead
models plus `grok-4.5` fail. This is the route OpenCode itself uses, which is why
`oc` reaches models the other launchers cannot.

**`/v1/messages` — 9 of 25.** See the matrix below.

**`/v1/responses` — 3 of 25 usable by a real agent CLI.** See the Codex section.

| Route | Models that can drive a full agent loop |
| --- | --- |
| Chat Completions | all 20 live models |
| Anthropic Messages | `deepseek-v4-flash`, `qwen3.5-plus`, `qwen3.6-plus`, `qwen3.7-plus`, `qwen3.7-max`, `qwen3.8-max`, `minimax-m2.5`, `minimax-m2.7`, `minimax-m3` |
| Responses | `gpt-5.6-luna`, `grok-4.5`, `deepseek-v4-flash` |

`deepseek-v4-flash` is the **only** model both agent routes serve. Otherwise the
two sets are disjoint: Claude Code gets Qwen and MiniMax, Codex gets GPT and Grok.

## Claude Code over `/v1/messages`

Most of the catalog cannot drive a Claude Code agent loop. Only these 9 both emit
`tool_use` and answer a follow-up `tool_result`:

`deepseek-v4-flash`, `qwen3.5-plus`, `qwen3.6-plus`, `qwen3.7-plus`,
`qwen3.7-max`, `qwen3.8-max`, `minimax-m2.5`, `minimax-m2.7`, `minimax-m3`

The rest fail, and every one of them fails **loudly**, at the first request:

| Model(s) | Failure |
| --- | --- |
| `deepseek-v4-pro` | 400, upstream cannot deserialize the tool schema |
| `glm-5`, `glm-5.1`, `glm-5.2` | 422 on the tool schema |
| `kimi-k2.5`, `kimi-k2.6`, `kimi-k2.7-code`, `mimo-*`, `hy3` | 400, upstream rejects the request shape |
| `kimi-k3`, `hy3-preview` | 503 / model unavailable |
| `grok-4.5` | 401 `not supported for format anthropic` |
| `gpt-5.6-luna` | 400, never emits `tool_use` |

Transient 503s (`Endpoint is unavailable`) do occur on otherwise-healthy models
including `qwen3.8-max`. Retry before concluding a model is broken; three clean
retries was the bar used here.

### `deepseek-v4-flash` — corrected

An earlier sweep on the same day recorded this model as emitting `tool_use` and
then returning an **empty response** to the `tool_result`, stalling the agent
loop silently. **That is no longer true, and it is now the `occ` default.**

Re-measured on 2026-08-06, after the original sweep:

- Non-streaming round trip: 4/4 across tool_result shapes (bare string and
  `[{type:"text"}]`), with the assistant turn replayed both with and without its
  `thinking` block.
- Streaming round trip: **10/10**, full event sequence, non-empty answer that
  echoes the injected tool output.
- Real Claude Code, `--print`, multi-step loops chaining Grep → Read → Bash →
  Edit → Write: correct results, files actually written.

In the same re-sweep every *other* model reproduced its documented failure
byte-for-byte, so the original methodology was sound and this one model changed
underneath it — most likely a mid-incident upstream during the first pass, or a
shim fix shipped between the two runs.

Two things this cost, worth not repeating:

- The original probes were throwaway and uncommitted, so the two runs could not
  be diffed and the discrepancy can only be inferred. The probe scripts are now
  kept — see [Reproducing](#reproducing).
- One bad measurement propagated into a shipped default, a launcher comment, and
  a "do not trust `oc` results" warning that was itself wrong. A single silent
  failure is worth re-running before it becomes a design constraint.

One real quirk survives: on this route the model returns a `thinking` block whose
`signature` is just the message id echoed back, not a real signature. Claude Code
does not validate it, and blanking it changes nothing.

### Reasoning effort is accepted and ignored — mostly

Claude Code sends the effort level as `output_config: {"effort": "xhigh"}`, under
the `effort-2025-11-24` beta header. Captured off a log-and-forward proxy, that
string is the **only** difference between `--effort low` and `--effort xhigh` —
same `thinking: {"type": "adaptive", "display": "omitted"}`, same `max_tokens`,
nothing else changes client-side.

Enum validation is not evidence of implementation, and where it happens at all
is per-upstream and unstable. A bogus value 400s against `deepseek-v4-flash` on
both OpenAI routes (a serde "unknown variant" error listing
`none/minimal/low/medium/high/xhigh/max`), but is silently accepted by
`gpt-5.6-luna` on chat, and by everything tested on `/v1/messages` — which
400ed a bogus value on the morning of 2026-08-06 and accepted it by evening.
Since parsing says nothing about effect, this has to be settled by measurement:
`scripts/opencode_go_effort_probe.py`, same prompt, n = 12 samples per level
interleaved, rank test on `low` vs `max`, reasoning volume as the signal:

| Model | `/v1/messages` (Claude Code) | `/v1/chat/completions` (OpenCode) | `/v1/responses` (Codex) |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | ignored, p = 0.61 | ignored, p = 0.84 | ignored, p = 0.42 |
| `qwen3.8-max` | ignored, p = 0.64 | **inverted**, p ≈ 0.00 | not served |
| `minimax-m3` | ignored | — | not served |
| `gpt-5.6-luna` | never emits `tool_use` | unmeasurable — no signal | **works**, p ≈ 0.00 |
| `grok-4.5` | 401 | dead (503) | nearly flat; no control possible |

**The one working ladder in the catalog is `gpt-5.6-luna` over
`/v1/responses`**: median reasoning tokens step monotonically 82 → 94 → 116 →
216 → 379 → 474 from `minimal` to `max`, with `none` pinned at zero. The token
counter is the only signal on that route — reasoning text is never returned, so
the upstream's own `output_tokens_details.reasoning_tokens` is the measurement.

Inverted means what it says: `qwen3.8-max` on chat reasons *less* at higher
effort — medians step down from ~1300 thinking chars at minimal/low/medium to
~650 at xhigh/max, with the upstream's own `reasoning_tokens` counter dropping
in step (~430 → ~180). Reproduced across two runs, so the field does reach that
upstream — it just does the opposite of what the ladder promises.

The two "unmeasurable" cells fail differently. `gpt-5.6-luna` on chat exposes
no reasoning signal at all — no `reasoning_content`, no token count — so every
level, control included, reads zero (the probe refuses to call that "ignored").
`grok-4.5`'s upstream 400s `none` *and* `max`, accepting only the middle five
rungs, so the off-switch control cannot run; the five that do run creep from
262 to 305 reasoning tokens, which without a control is recorded as nearly
flat, not certified.

The controls are what make the null results mean anything: `thinking: {"type":
"disabled"}` on `/v1/messages`, `reasoning_effort: "none"` on chat, and
`reasoning: {"effort": "none"}` on responses each return zero thinking,
deterministically, wherever the upstream accepts them. `thinking.display` is
ignored (`"omitted"` still returns the thinking text), and `enable_thinking:
false` on chat is silently dropped — the enum's off-switch is the only thinking
control that reaches upstream on those routes.

Samples must be submitted **interleaved across levels** — submitting all of one
level before the next turns any mid-run upstream drift into a spurious effort
effect. The qwen inversion survives interleaving, so it is not drift; a first
un-interleaved run of it looked identical, which is how the confound was found.

So `/effort` and `--effort` are inert under `occ`. Nothing is malfunctioning —
the request is well-formed and accepted — there is simply no implementation
behind the graded budget on any model `occ` can reach. Claude Code offers the
full ladder anyway because its capability check falls through to "first-party
⇒ supported" for any model id it
does not recognise; a custom `ANTHROPIC_BASE_URL` does not make it think
otherwise, since its `gateway` provider mode keys off a gateway *auth session*,
not off `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY`.

### Client fingerprinting

Requests are behind Cloudflare, and a default `Python-urllib/3.x` user agent gets
**403 with body `error code: 1010`** before reaching the gateway — no JSON error
envelope. Any probe script must send a realistic `user-agent`; the CLIs already
do. A 1010 is a blocked client, never a model or auth problem.

## Codex over `/v1/responses` (experiment — not shipped)

Codex CLI 0.146.0 **removed** `wire_api = "chat"`; it errors at config load with
"no longer supported" and points at `wire_api = "responses"`. So Codex can only
use the Responses route, which is the most poorly served of the three.

### Working invocation

Verified end-to-end (model reads a file via its shell tool and reports a token
the prompt never contained):

```bash
OPENCODE_API_KEY=$(jq -r '.["opencode-go"].key' ~/.local/share/opencode/auth.json) \
codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  -c model_provider=opencodego \
  -c 'model_providers.opencodego.name="OpenCode Go"' \
  -c 'model_providers.opencodego.base_url="https://opencode.ai/zen/go/v1"' \
  -c 'model_providers.opencodego.env_key="OPENCODE_API_KEY"' \
  -c 'model_providers.opencodego.wire_api="responses"' \
  -c 'model="gpt-5.6-luna"' \
  -c 'web_search="disabled"' \
  -c 'mcp_servers.context7.enabled=false' \
  -c 'mcp_servers.chrome-devtools.enabled=false' \
  --disable multi_agent \
  "your prompt"
```

Codex does not need `codex login` for this — a custom provider with `env_key`
authenticates from the environment, and no request touches an OpenAI account.

### Why each override is required

Codex sends a much wider tool surface than Claude Code, and the gateway's
upstreams reject the exotic parts. Captured by pointing `base_url` at a
log-and-forward proxy on localhost, then replaying the captured body against the
real endpoint with fields deleted one at a time:

- **`web_search="disabled"`** — Codex always includes a `{"type":"web_search"}`
  tool. `glm-*` reject it (`tools.0.function-after[_domain_filters(),
  WebSearchTool].type`). The valid values for this key are `disabled`, `cached`,
  `indexed`, `live`; `tools.web_search=false` and `--disable web_search_request`
  are both silently ignored.
- **`--disable multi_agent` + disabling every MCP server** — these contribute
  `{"type":"namespace"}` tools (`multi_agent_v1`, `mcp__context7`,
  `mcp__chrome_devtools`). `grok-4.5` 422s on any namespace tool. Note
  `-c mcp_servers={}` does **not** clear them — the override merges rather than
  replaces, so each server needs `enabled=false` by name.
- **`model_reasoning_effort` ≤ `high` for `grok-4.5`** — Codex sends
  `reasoning: {effort, summary}` from config, and `~/.codex/config.toml` sets
  `max`. `grok-4.5` 400s on `effort: "max"` and succeeds on `"high"`. Codex does
  not surface this as a config problem; it prints five `Reconnecting…` lines and
  a bare 400.
- An **empty** `tools` array is also rejected (400) — at least one function tool
  must be present.

### Why only three models work

The gateway's Responses-format shim is complete for some upstreams and degraded
for the rest. Streaming `/v1/responses` and collecting distinct SSE event types
splits the catalog cleanly:

| Stream | Event types emitted | Models |
| --- | --- | --- |
| Full | `response.created`, `response.in_progress`, `response.output_item.added/done`, `response.content_part.added/done`, `response.output_text.delta/done`, `response.completed` | `gpt-5.6-luna`, `grok-4.5`, `deepseek-v4-flash` |
| Degraded | `response.output_text.delta`, `response.completed`, `ping` — nothing else | `glm-5`, `glm-5.1`, `glm-5.2`, `deepseek-v4-pro`, `hy3`, `kimi-k2.5`, `kimi-k2.6`, `kimi-k2.7-code`, `mimo-v2.5`, `mimo-v2.5-pro` |
| Not served | 401 `not supported for format openai` (`qwen*`, `minimax-m2.5`, `minimax-m3`) or 500 (`minimax-m2.7`) | — |

Without `response.created` and the `output_item` events, Codex cannot assemble
output items, so it can never see a tool call. The visible symptom is a session
that prints the banner and then **ends with no output and no error**. This is now
the only silent failure mode on the endpoint, and it is Codex-specific.

The degraded models are not simply broken: a non-streaming `/v1/responses` call
does return a `function_call` for most of them. They fail on the `function_call_output`
follow-up (`invalid_request` from upstream) or on the stream shape. Codex always
streams, so that path is unavailable.

Intermittent 500s on `kimi-*` and `hy3` muddy this — an early hypothesis that
these models required an explicit `max_output_tokens` (which Codex never sends)
did not survive retesting. The stream shape is the real cause.

### Verdict

Codex is **not** the smoother experience.

| | Claude Code (`occ`) | Codex |
| --- | --- | --- |
| Setup | env vars only, no flags | 8 config overrides, or a checked-in provider profile |
| Usable models | 9 | 3 |
| Reasoning effort | inert on every reachable model | **works on `gpt-5.6-luna`** — the only graded ladder on the gateway ([measured](#reasoning-effort-is-accepted-and-ignored--mostly)) |
| Startup noise | one cosmetic connectors warning | `failed to refresh available models` error every run (Codex expects `{"models":[…]}`, gateway returns OpenAI's `{"data":[…]}`), plus a `Model metadata … not found` warning per model |
| Failure mode | loud | silent — sessions end with no output |

Codex's advantages are reach and, it turns out, effort: it is the only way to
get at `gpt-5.6-luna` and `grok-4.5`, and `gpt-5.6-luna` is the only model on
the gateway with a working graded effort ladder — though Codex's own
`model_reasoning_effort` must stay at `high` or below on `grok-4.5`, whose
upstream 400s `max`. The invocation above is known-good and would drop into
`shell/` the same way `occ` did if either of those two models is ever
specifically wanted.

Codex also has no config key for per-model output limits — `model_max_output_tokens`
and `models.<id>.*` are both rejected as unknown fields. `model_context_window` is
recognized, and is worth setting per model (`deepseek-v4-flash` 1M,
`gpt-5.6-luna` 1.05M, `grok-4.5` 500k) since Codex's fallback metadata otherwise
guesses.

## Reproducing

The Anthropic-route sweep is committed as
[`scripts/opencode_go_probe.py`](scripts.md#scriptsopencode_go_probepy) — run it
before trusting the tables above, and after any `OCC_MODEL` change. The effort
measurement is
[`scripts/opencode_go_effort_probe.py`](scripts.md#scriptsopencode_go_effort_probepy),
which covers all three agent routes (`--route messages` for Claude Code,
`--route chat` for OpenCode, `--route responses` for Codex) with an on/off
control attached to each.

The other probes were throwaway scripts, which is precisely how the
`deepseek-v4-flash` error above went unexplained. To rebuild them, the shapes
that mattered were:

1. **Per-route tool probe** — one request with a system prompt and 2–3 function
   tools, asserting a tool call comes back. Necessary but far from sufficient.
2. **Tool-result round trip** — feed the tool call back as `tool_result` /
   `role: "tool"` / `function_call_output` and assert a non-empty answer. This is
   the test that catches silent stalls; it is what the committed script does for
   the Anthropic route, and it still needs writing for the other two.
3. **Stream event census** — `stream: true`, collect `event:` lines, sort unique.
   This is what separates the Codex-usable models from the rest.
4. **Body capture and replay** — point the CLI's `base_url` at a local
   log-and-forward proxy to capture the exact request, then replay it with `curl`
   and delete fields one at a time. Guessing at what a CLI sends wastes more time
   than writing the twenty-line proxy.

Run all 25 models in parallel; a full sweep takes under a minute.
