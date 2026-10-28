# OpenCode Go effort measurements

Measured 2026-08-06 and 2026-08-07. Client versions and route compatibility
belong to the [catalog investigation](opencode-go-catalog.md). These numbers
are historical observations, not present-day model guarantees. The probe
interface is documented in [Scripts](../scripts.md#gateway-effort-probe).

## Client fields

| Route | Effort field | Signal | Off-switch control |
| --- | --- | --- | --- |
| Messages | `output_config.effort` | Thinking-text characters | `thinking: {"type": "disabled"}` |
| Chat Completions | `reasoning_effort` | Thinking-text characters, plus upstream reasoning-token counts when exposed | `reasoning_effort: "none"` |
| Responses | `reasoning.effort` | `output_tokens_details.reasoning_tokens` | `reasoning.effort: "none"` |

A captured Claude low/xhigh request changed only the effort string, keeping
adaptive thinking and the same output cap. OpenCode's `--variant` capture was
family-dependent:

- DeepSeek, GPT, and `hy3` sent a Chat Completions effort field only for levels
  advertised in the registry. An unknown variant could be dropped silently.
- Qwen 3.x used Messages: high enabled a 16000-token thinking budget; low
  omitted thinking. This was a toggle/budget, not a graded enum.
- MiniMax M3 used Messages with adaptive thinking.
- GLM, Kimi, MiMo, and MiniMax M2 variants sent no effort option in that client
  snapshot, even where direct gateway requests showed a measurable ladder.

These captures describe that client/cache combination. Accepted enum values
alone did not prove an effect; validation changed during the investigation.

## Measurement controls and confounds

`scripts/opencode_go_effort_probe.py` interleaves effort levels and compares
the widest surviving pair using a Mann–Whitney normal approximation.
Its printed `p=0.00` is rounding, not a literal zero probability. Reasoning
volume measures neither answer quality nor token savings.

The off-switch must return zero reasoning, and non-control levels must expose
some signal. A route with no reasoning text/counts is unmeasurable, rather than
proven to ignore effort. `thinking.display: "omitted"` and chat's
`enable_thinking: false` were ignored in the tested gateway.

Two confounds invalidated earlier flat-ladder conclusions:

- **Prompt floor:** the default `hotel` riddle elicited memorized fixed-length
  reasoning. `absproof` and `stack` separated levels on some of those models.
- **Output ceiling:** the default 8192-token cap censored flash's thinking.
  Messages and Responses switched from apparently ignored to measurable at
  the advertised 384000-token cap. Responses needed `max_output_tokens` too.

The following matrix uses the corrected flash runs. Other cells retain their
original prompt/cap limitations; a dash means no result is retained here.

| Model | Messages | Chat Completions | Responses |
| --- | --- | --- | --- |
| `deepseek-v4-flash` | works at 384k, p rounded 0.00 | works, p rounded 0.00 | works at 384k, p rounded 0.00 |
| `deepseek-v4-pro` | — | works, p = 0.01 | — |
| `glm-5.1` | — | works, p rounded 0.00 | — |
| `glm-5` | — | works on absproof, p rounded 0.00; stack p = 0.32 | — |
| `qwen3.7-max` | — | consistent with ignored, p = 0.77 | — |
| `qwen3.8-max` | consistent with ignored, p = 0.64 | inverted, p rounded 0.00 | not served |
| `kimi-k2.6` | — | consistent with ignored, p = 0.82 | — |
| `kimi-k2.5` | — | consistent with ignored, p = 0.16 | — |
| `minimax-m3` | no graded effect observed | unmeasurable | not served |
| `gpt-5.6-luna` | no agent tool loop | unmeasurable | works, p rounded 0.00 |
| `grok-4.5` | unsupported | unavailable | nearly flat; off-switch rejected |

Qwen 3.8's inversion survived two interleaved runs and appeared in the upstream
reasoning-token counter too. Kimi 2.7-code rejected the off-switch, so it could
not be certified. GPT on Responses had a clean monotonic median ladder
(82 → 94 → 116 → 216 → 379 → 474 reasoning tokens from minimal through max).
DeepSeek/GLM tended to separate most clearly at the top levels, with noisy
middle rungs. This was not evidence of universal monotonic effort control.

## Flash high-cap record

The full medians, minima, maxima, output-token medians, failed samples,
timestamps, and exit lines are preserved once in the
[raw 2026-08-07 log](opencode-go-effort-highcap-2026-08-07.log).
Each run used 12 samples per level, a 384000-token output cap, a 3600-second
request timeout, and four zero-reasoning controls. Low versus max printed
p=0.00 in all four runs.

| Route / prompt | Low reasoning median | Max reasoning median | Max samples |
| --- | ---: | ---: | ---: |
| Messages / absproof | 1780 characters | 5872 characters | 12/12 |
| Responses / absproof | 517 tokens | 1309 tokens | 11/12 |
| Messages / stack | 46793 characters | 99104 characters | 12/12 |
| Responses / stack | 10101 tokens | 24840 tokens | 7/12 |

The middle levels were ragged. Several Responses stack samples failed at high
and max; comparing only successful medians hides that failure rate. Consult
the raw log before drawing more conclusions from the summary.

Do not rerun the four large ladders just to regenerate documentation. They
cost roughly 70 minutes and substantial Go quota. If a gateway change or live
failure warrants rechecking, start with `absproof`, three samples per level,
one model/route, and an appropriate cap/timeout. The historical expensive-model
list is not a current price list; check the account's plan before a wider run.

## Client implications at investigation time

The tested Claude build defaulted unknown IDs to a 32000-token cap; changing
effort alone did not raise it. That motivated the output-limit export now in
[`occ`](../opencode-go.md#defaults-and-overrides).
The tested Codex build sent no `max_output_tokens` and had no supported output
limit config key, so the probe's high-cap Responses result did not establish
that a stock Codex session achieved the same ladder.
