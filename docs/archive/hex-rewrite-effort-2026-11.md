# Hex dictation rewrite model and effort evaluation

Measured 2026-11-24 against local OpenCode Go (`opencode-go/deepseek-v4.1-flash`
and `opencode-go/claude-haiku-5-5`) via `hex/contextual_rewrite.py`. These
results are historical observations for that date and gateway snapshot, not
permanent model guarantees. Current shipped behavior lives in
[Hex dictation](../hex.md#editing-behavior).

Private dictation transcripts remain local to Hex History and are omitted from
this repository; entries below are identified only by local History ID, word
count, latency, and abstracted failure class.

## Background

Commit `e1ceb4d` switched both the Hex Linear assistant (from `gpt-6-luna#max`)
and Hex dictation rewriting (from `claude-haiku-5-5`, base `none`, adaptive
`medium` at 100+ words) to `deepseek-v4.1-flash` (`max` and `low`,
respectively). Dictation rewriting was subsequently reverted to
`claude-haiku-5-5` in commit `2170dc9` after slow rewrites and a 27-second
deadline timeout on long dictations; the Linear assistant remained on
`deepseek-v4.1-flash#max`.

## DeepSeek Flash vs. Haiku latency

### Recorded Hex History (>=100-word dictations)

| Window | Model / variant | Samples | Median | Mean | Max | 27s timeouts |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2026-11-17 to 11-23 | `claude-haiku-5-5` (adaptive `medium`) | 52 | 4.41s | 4.29s | 8.76s (558w) | 0 |
| 2026-11-24 | `deepseek-v4.1-flash` (`low`) | 5 | 11.11s | 14.08s | 27.09s (170w) | 1 |

### Live head-to-head replay (2026-11-24 dictations)

Each entry was replayed sequentially against the local OpenCode service using
its exact prior 2,000-word history excerpt and a 35-second test cap.

| Entry | Raw words | Recorded DeepSeek `low` | Live DeepSeek `low` | Live Haiku `medium` (adaptive) | Live Haiku `none` |
| --- | ---: | ---: | ---: | ---: | ---: |
| `1106` | 270 | 16.97s | 14.52s | 5.80s | 2.09s |
| `1107` | 108 | 5.95s | 6.02s | 1.23s | 1.00s |
| `1110` | 134 | 9.30s | 14.65s | 3.07s | 1.41s |
| `1111` | 191 | 11.11s | 7.85s | 1.34s | 1.42s |
| `1114` | 170 | 27.09s (timeout) | 19.71s | 8.50s | 1.43s |
| `1113` *(sub-100w)* | 98 | 5.44s | 7.86s | 0.96s *(`none`)* | 0.96s |

Across the five 100+ word entries (`1106`, `1107`, `1110`, `1111`, `1114`):

- **Live DeepSeek `low`:** median 14.52s, mean 12.55s, range 6.02s–19.71s.
- **Live Haiku adaptive (`medium`):** median 3.07s, mean 3.99s, range 1.23s–8.50s (~3.1× faster mean, ~4.7× faster median).
- **Live Haiku `none`:** median 1.42s, mean 1.47s, range 1.00s–2.09s.

## Haiku adaptive `low` vs. `medium`

To evaluate dropping the 100+ word adaptive tier from `medium` to `low`,
baseline `low` and `medium` were compared across an 8-entry development set
(134w–713w: `896`, `952`, `1039`, `1103`, `1106`, `1110`, `1111`, `1114`), plus
an initial side-by-side inspection on `1106`, `1111`, and `1114`.

### Baseline failure modes at `low`

- **Silent deletion of unfamiliar or garbled product/model names:** in 2/2
  baseline runs of `1114` (which contained a Whisper-garbled multi-variant
  model reference), `low` silently dropped the specific variant names and
  collapsed the phrase to the generic model family. Baseline `medium` preserved
  the spoken words verbatim in 2/2 runs, matching the prompt's ambiguity rule.
- **History bleed into shorthand clauses:** in 2/2 baseline runs of `1114`,
  `low` appended an unspoken role phrase lifted from an earlier dictation
  (`1106`) in `recent_dictations` to complete a shorthand tool reference.
  Baseline `medium` added no history text in 2/2 runs.
- **Invented framing and voice drift:** baseline `low` fabricated an opening
  claim not present in the transcript (`1111`), dropped continuation cues and
  informal closings (`1103`), and occasionally substituted generic or stricter
  terms (`1106`).
- **Incomplete self-correction folding:** on a 357w multi-step UI redesign
  dictation (`952`), `low` kept both the superseded layout paragraph and the
  final revised design (195w) instead of merging details into the final layout
  as `medium` did (151w).
- **Minor temporal garble:** on a 558w dictation (`1039`), baseline `low`
  attached a wake-up clock time to sleep duration; `medium` kept the timing
  accurate.

### Prompt-engineering experiment (not shipped)

Two incremental revisions were tested in scratch against `claude-haiku-5-5`
(`low`) to check whether explicit guardrails could eliminate `low`'s errors:

- **Candidate v1:** sharpened `INSTRUCTIONS` in `hex/contextual_rewrite.py` to
  forbid importing roles, nouns, or missing clauses from history to complete
  `current_transcript` and forbid deleting unfamiliar names, model/product
  variants, or awkward phrases when uncertain; updated `hex/prompt.md` to keep
  concrete terms, metaphors, informality, continuation cues, and closings, avoid
  inventing framing or swapping generic/stricter words, fold self-correction
  details into the final wording, and preserve uncertain/garbled names as
  spoken.
- **Candidate v2:** kept v1 `INSTRUCTIONS`, changed "continuation cues" to
  "opening transitions" in `hex/prompt.md`, and explicitly forbade filling in
  unspoken words to complete shorthand phrases.

Both revisions were evaluated on repeats of `1106`, `1111`, and `1114` (3 runs
each, `n=9`); v1 was also run on all 8 dev entries, and v2 was compared
head-to-head against baseline `medium` on 6 unseen held-out long dictations
(152w–546w: `868`, `883`, `929`, `1038`, `1040`, `1041`).

| Run set | Variant / prompt | Samples | Median | Mean | Range |
| --- | --- | ---: | ---: | ---: | --- |
| Dev (8 entries, 134w–713w) | Baseline `medium` | 8 | 5.62s | 5.38s | 1.54s–8.37s |
| Dev (8 entries, 134w–713w) | Baseline `low` | 8 | 1.75s | 2.95s | 1.15s–8.14s |
| Dev (8 entries, 134w–713w) | Candidate v1 `low` | 8 | 2.12s | 2.36s | 1.39s–3.69s |
| Repeats (`1106`, `1111`, `1114` × 3) | Candidate v1 `low` | 9 | 1.41s | 1.56s | 1.36s–1.87s |
| Repeats (`1106`, `1111`, `1114` × 3) | Candidate v2 `low` | 9 | 1.59s | 1.63s | 1.35s–1.90s |
| Held-out (6 unseen entries, 152w–546w) | Baseline `medium` | 6 | 4.80s | 4.57s | 1.25s–7.04s |
| Held-out (6 unseen entries, 152w–546w) | Candidate v2 `low` | 6 | 2.57s | 3.06s | 1.38s–6.04s |

### Outcome of prompt edits at `low`

- **Fixed:** voice/tone preservation (`1103`, `896`, `1106` concrete wording in
  7/7 runs), invented opening claims (`1111`, 7/7 runs), and the temporal
  garble (`1039`). On the 6 unseen held-out entries, v2 `low` matched or beat
  baseline `medium` on 5 of 6 (including avoiding a negation inversion on `883`
  and subject/grammar slips on `929`).
- **Not reliably fixed:**
  - **History bleed under strong priming (`1114`):** persisted in 4/7 runs
    across v1 (2/4) and v2 (2/3), still appending an unspoken role phrase from
    `1106`.
  - **Garbled-name replacement (`1114`):** preserved in 4/4 v1 runs and 2/3 v2
    runs, but on v2 run #2 `low` replaced the garbled model-variant clause with
    two role nouns pulled from `1106`.
  - **Multi-step self-correction folding (`952`):** `low` continued to emit
    both the superseded and final layout descriptions (203w vs. `medium`'s
    151w).
  - **Minor regression (`1106`):** both v1 and v2 left a lowercase-transcribed
    imperative opening lowercase in 7/7 runs.

## Decision

- Keep `opencode-go/claude-haiku-5-5` with base variant `none` and adaptive
  `medium` reasoning at `LONG_DICTATION_WORDS = 100`.
- Do not drop the adaptive tier to `low` and do not ship the experimental v1/v2
  prompt edits. On unseen long dictations, `low` saved only ~1.5s mean (~2.2s
  median) while still failing intermittently under strong 2,000-word history
  priming, indicating a reasoning-budget ceiling rather than a prompt gap.
- Add recurring product or model names to `hex/vocabulary.json` rather than
  compensating for speech-recognition misses in rewrite instructions.
- Revisit if future Haiku or OpenCode Go releases widen the `low`-vs-`medium`
  latency gap or improve rule adherence under long history excerpts; retest
  with repeated trials on entries pairing unfamiliar/garbled proper nouns with
  closely related prior dictations.
