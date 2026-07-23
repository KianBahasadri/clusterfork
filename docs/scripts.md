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

## scripts/enumerate_pioneer.py

Lists all models available on the Pioneer API with pricing, context windows, and probed reasoning effort tiers.

```bash
PIONEER_API_KEY=... python scripts/enumerate_pioneer.py [--json] [--include-deprecated] [--skip-probe] [--workers N] [--probe-workers N]
```

The script fetches `/v1/models` and `/base-models`, then probes each model via `POST /inference` to determine which reasoning effort levels are accepted. Requires `PIONEER_API_KEY`.
