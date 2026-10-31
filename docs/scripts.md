# Scripts

`scripts/` utilities use stdlib. [Installation](installation.md) copies rotation
and codeview; converters/probes run from checkout. Cloud helpers belong to
[remote development](remote-development.md).

## Account rotation

`scripts/rotate_auth.py` is the sole implementation; [Auth Rotation](auth-rotation.md)
owns flags/storage/container sharing.

```bash
python3 scripts/rotate_auth.py claude --list
python3 scripts/rotate_auth.py codex --help
```

## Auth conversion

`scripts/auth_convert.py` emits converted JSON to stdout without modifying inputs:

```bash
python3 scripts/auth_convert.py --from opencode path/to/auth.json
python3 scripts/auth_convert.py --from codex path/to/auth.json
python3 scripts/auth_convert.py --from auto path/to/auth.json
```

OpenCode `openai.{access,refresh,accountId}` maps to Codex
`tokens.{access_token,refresh_token,account_id}`, `auth_mode: "chatgpt"` and access
JWT as `id_token`; reverse conversion emits only the `openai` object.

## Gateway tool-loop probe

Streaming tool call plus follow-up must echo the injected result, not just call:

```bash
python3 scripts/opencode_go_probe.py deepseek-v4-flash
python3 scripts/opencode_go_probe.py deepseek-v4-flash -n 3
```

No IDs sweeps cached catalog; `-j` defaults 12, `-n` repeats. Missing echo/failure
exits nonzero. Reads `OPENCODE_API_KEY` or auth file plus model cache; see [Go](opencode-go.md).

## Gateway effort probe

Measures reasoning volume on messages/chat/responses, not quality/price. [Dated controls/results](archive/opencode-go-effort.md)
explain output caps and large-run costs. An intentional small recheck:

```bash
python3 scripts/opencode_go_effort_probe.py --route messages \
  --prompt absproof -n 3 deepseek-v4-flash
```

Defaults: 12 samples/level, 12 jobs, `hotel` prompt, 8192 tokens, 240-second timeout.
`--max-tokens` sets messages/chat `max_tokens` or responses `max_output_tokens`.
Zero means measurable, even without significant effect; failed controls/no reasoning
signal exit nonzero.

## Installer hook trust

Installer-only `scripts/codex_hook_trust.py` hashes normalized command/type/async/
timeout (upstream default 600 seconds) and formats `hooks.state`. [Installation](installation.md#replacement-and-preservation)
owns writes; this helper is not installed as a user command.
