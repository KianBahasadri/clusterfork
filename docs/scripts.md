# Scripts

Standalone Python utilities under `scripts/`.

## scripts/auth_convert.py

Converts `auth.json` files between OpenCode and Codex formats.

```bash
python scripts/auth_convert.py --from opencode path/to/auth.json   # → Codex format (stdout)
python scripts/auth_convert.py --from codex path/to/auth.json      # → OpenCode format (stdout)
python scripts/auth_convert.py --from auto path/to/auth.json       # auto-detect format
```

OpenCode stores tokens under `openai.{access,refresh,accountId}`. Codex stores them under `tokens.{access_token,refresh_token,account_id}` with `auth_mode: "chatgpt"`.

## scripts/opencode_go_probe.py

Checks which OpenCode Go models can drive a Claude Code agent loop, by running a streaming tool-result round trip against the Anthropic `/v1/messages` route. Catches the failure mode that a plain "does it emit a tool call" probe misses: a model that returns an **empty reply** to the tool output, stalling the loop with no error.

```bash
python scripts/opencode_go_probe.py                          # sweep whole catalog
python scripts/opencode_go_probe.py deepseek-v4-flash        # one model
python scripts/opencode_go_probe.py deepseek-v4-flash -n 10  # repeat, for flaky models
```

Reads the key from `OPENCODE_API_KEY` or `~/.local/share/opencode/auth.json`, and the catalog from `~/.cache/opencode/models.json`. Exits non-zero if any probed model fails, so it doubles as a regression check before changing `OCC_MODEL`. See [OpenCode Go endpoint](opencode-go.md) for how to read the results.

## scripts/opencode_go_effort_probe.py

Measures whether OpenCode Go honours the reasoning effort a client sends. Status codes cannot answer this — an accepted enum value says nothing about effect — so the script samples each level and compares how much thinking comes back, with a rank test on `low` vs `max`. Two routes: `--route messages` (default) replays what Claude Code sends (`output_config.effort` on `/v1/messages`); `--route chat` replays what OpenCode itself sends (`reasoning_effort` on `/v1/chat/completions`).

```bash
python scripts/opencode_go_effort_probe.py                       # Claude Code route
python scripts/opencode_go_effort_probe.py --route chat          # OpenCode route
python scripts/opencode_go_effort_probe.py qwen3.8-max -n 20     # more samples
```

Every run includes a positive control (`thinking: {"type": "disabled"}` on messages, `reasoning_effort: "none"` on chat — both must return zero thinking), and samples are interleaved across levels so mid-run upstream drift cannot masquerade as an effort effect. The script exits non-zero if the control fails, since a null result from a harness that cannot detect change is worthless. See [OpenCode Go endpoint](opencode-go.md#reasoning-effort-is-accepted-and-ignored--mostly) for the measured result.
