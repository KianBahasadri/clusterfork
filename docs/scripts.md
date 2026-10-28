# Scripts

Utilities under `scripts/` use Python's standard library. The installed rotation
script and codeview package are listed in [Installation](installation.md);
the converter and probes below run from the checkout. Cloud automation lives
under `automation/` and is documented in [Remote development](remote-development.md).

## Account rotation

`scripts/rotate_auth.py` implements every `rotate-*` shell command. Do not
create another copy of its account-selection logic. Flags, storage, and
container sharing belong to [Auth Rotation](auth-rotation.md).

```bash
python3 scripts/rotate_auth.py claude --list
python3 scripts/rotate_auth.py codex --help
```

## Auth conversion

`scripts/auth_convert.py` reads an OpenCode or Codex auth file and writes
converted credential JSON to stdout. It does not update the input file or
install the output.

```bash
python3 scripts/auth_convert.py --from opencode path/to/auth.json
python3 scripts/auth_convert.py --from codex path/to/auth.json
python3 scripts/auth_convert.py --from auto path/to/auth.json
```

OpenCode's `openai.{access,refresh,accountId}` maps to Codex's
`tokens.{access_token,refresh_token,account_id}` with `auth_mode: "chatgpt"`.
The OpenCode access JWT also fills Codex's `id_token`; the reverse conversion
emits only the `openai` token object.

## Gateway tool-loop probe

`scripts/opencode_go_probe.py` tests a streaming tool call plus a tool-result
follow-up on OpenCode Go's Anthropic route. A nonempty answer must echo the
injected tool result; emitting a tool call alone is insufficient.

```bash
python3 scripts/opencode_go_probe.py deepseek-v4-flash
python3 scripts/opencode_go_probe.py deepseek-v4-flash -n 3
```

With no model IDs it sweeps the cached catalog. `-j` controls parallelism
(default 12); `-n` repeats each target. Any failure or missing echo exits
nonzero. It reads `OPENCODE_API_KEY` or OpenCode's auth file and uses the
OpenCode models cache. See [OpenCode Go](opencode-go.md) for interpretation.

## Gateway effort probe

`scripts/opencode_go_effort_probe.py` samples effort levels on `messages`,
`chat`, or `responses`. It measures reasoning volume rather than answer quality
or price. Its controls, output-cap limitations, and existing measurements live
in the [dated effort investigation](archive/opencode-go-effort.md), including
why the large historical runs should not be repeated casually.

For an intentional small recheck:

```bash
python3 scripts/opencode_go_effort_probe.py --route messages \
  --prompt absproof -n 3 deepseek-v4-flash
```

Defaults are 12 samples per level, 12 jobs, the `hotel` prompt, an 8192-token
output cap, and a 240-second request timeout. `--max-tokens` sets `max_tokens`
on messages/chat and `max_output_tokens` on responses. Exit zero means a
comparison was measurable, even when no significant effort effect was found;
a failed control or entirely absent reasoning signal exits nonzero.

## Installer hook trust

`scripts/codex_hook_trust.py` is imported by the installer, not installed as a
user command. It computes the Codex Stop-hook identity hash and formats
`hooks.state` entries. The hash covers normalized command/type/async/timeout
fields, with the upstream 600-second timeout default. See
[Installation](installation.md#replacement-and-preservation) for what is written.
