# Shell Modules

`bash_profile.sh` sources every `shell/*.sh` on shell startup. Each module defines launch wrappers, aliases, or credential rotation functions for one agent.

## claude.sh

`cl` launches `claude --dangerously-skip-permissions --effort xhigh` and sets `ANTHROPIC_CUSTOM_MODEL_OPTION=claude-opus-4-8` (label `Opus 4.8`) so `/model` keeps a 4.8 row after the `opus` alias moved to Opus 5. The env vars are scoped to that launch. `rotate-claude` switches between multiple saved Claude account credentials.

## codex.sh

`cc` is an alias for `codex resume --yolo`. `rotate-codex` switches between saved Codex accounts via symlinks.

## cursor.sh

`ca` is an alias for `cursor-agent --yolo` (Run Everything / force-allow). `rotate-cursor-cli` switches between saved Cursor accounts via symlinks.

## opencode.sh

`oc` is an alias for `opencode --continue`. `o` is an alias for `opencode`. `rotate-opencode` switches between saved OpenCode accounts via symlinks.

## opencode-claude.sh

`occ` launches Claude Code (`--dangerously-skip-permissions`) against the **OpenCode Go** subscription instead of an Anthropic account. OpenCode Go serves an Anthropic-compatible `/v1/messages` endpoint at `https://opencode.ai/zen/go`, so no proxy is involved — `ANTHROPIC_BASE_URL` points straight at it.

The key is read from `OPENCODE_API_KEY` (clusterfork `.env`) if set, otherwise from `opencode-go.key` in `~/.local/share/opencode/auth.json`, so `occ` follows whichever account `rotate-opencode` selected. It runs in a subshell that unsets `ANTHROPIC_AUTH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` first, since a cached OAuth token would otherwise outrank the gateway key.

Two properties of the endpoint shape the module:

- **Only `x-api-key` authenticates.** `Authorization: Bearer` returns 401, so the key goes in `ANTHROPIC_API_KEY`, never `ANTHROPIC_AUTH_TOKEN`.
- **There are no Claude models in the catalog.** Every model slot Claude Code can route to is remapped to an `opencode-go` id — `ANTHROPIC_MODEL`, the `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` aliases (which settings.json selects by name), `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, and `CLAUDE_CODE_BG_CLASSIFIER_MODEL`. An unmapped id comes back as **401 `Model ... is not supported`**, not a 404, so a missed slot looks like an auth failure.

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS` are both derived per-model from the models.dev cache OpenCode maintains at `~/.cache/opencode/models.json` (`.limit.context` and `.limit.output`). Without the context export, Claude Code assumes a 200k window for models it has no metadata for and auto-compacts far too early — the usable models range from 204k to 1M. Without the output export, Claude Code defaults unrecognised (gateway) model ids to **32000** `max_tokens`, which pins high-effort thinking on flash long before the model's advertised 384000 ceiling — the same confound that made effort look inert until the [2026-08-07 high-cap remeasure](opencode-go.md#deepseek-v4-flash-high-cap-ladders-2026-08-07--do-not-re-run). `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` keeps everything except inference off the third-party gateway.

### Preferences

These are the intended `occ` defaults — change an env var only to override one run:

- **Default model is always `deepseek-v4-pro`.** Every slot — `ANTHROPIC_MODEL`, the four aliases (opus/sonnet/haiku/fable), small-fast, subagent, and the background classifier — resolves to pro. `OCC_SONNET_MODEL` and `OCC_SMALL_MODEL` default to the same id.
- **`deepseek-v4-flash` is picker-only.** It stays in `/model` (the gateway-discovery list) so it can be chosen for a session. It is not a default and is not mapped onto any alias slot.
- **Effort is `max`.** Passed as `--effort max` unless `OCC_EFFORT` or a command-line `--effort` overrides it.
- **`cl` is a separate profile.** `occ` must not write its model (or gateway cache) into `~/.claude/settings.json`.

Defaults, all overridable: `OCC_MODEL=deepseek-v4-pro`, `OCC_SONNET_MODEL=deepseek-v4-pro`, `OCC_SMALL_MODEL=deepseek-v4-pro`, `OCC_EFFORT=max`. Also honoured: `OPENCODE_GO_BASE_URL`, `OCC_MAX_CONTEXT_TOKENS`, `OCC_MAX_OUTPUT_TOKENS`, `OCC_OPENCODE_AUTH_FILE`, `OCC_MODELS_CACHE`, `OCC_GATEWAY_MODELS`, `OCC_MODEL_DISCOVERY`, `OCC_CLAUDE_CONFIG_DIR`.

Claude Code prints a one-time warning that claude.ai connectors are disabled because `ANTHROPIC_API_KEY` takes precedence over the claude.ai login. That is expected under `occ` and does not affect the session.

`occ` always passes `--effort max` unless you override via `OCC_EFFORT` or `--effort <level>` on the command line. It uses the CLI flag rather than `CLAUDE_CODE_EFFORT_LEVEL` so `/effort` still works mid-session. On the deepseek models, graded effort separates at the top rung — see [Reasoning effort](opencode-go.md#reasoning-effort-works-on-some-models-some-clients).

Only 10 of the 25 catalog models can drive the agent loop over this endpoint, and the defaults above are chosen from that set. Verify with `python scripts/opencode_go_probe.py` before changing `OCC_MODEL` — some models fail this endpoint while working fine under `oc`. See [OpenCode Go endpoint](opencode-go.md) for the matrix and the failure modes.

### All 10 in `/model`

`occ` sets `CLAUDE_CONFIG_DIR` to `~/.config/clusterfork/occ-claude` (overridable via `OCC_CLAUDE_CONFIG_DIR`) so its `settings.json` and `cache/gateway-models.json` cannot change what `cl` reads from `~/.claude`. Skills and plugins are symlinked from `~/.claude` so the isolated profile still has them. On each launch the isolated settings file is refreshed from `~/.claude/settings.json` (theme, statusline, plugins) while keeping `occ`'s own `model` if `/model` already wrote one.

The picker only offers the four alias slots plus a default row, so `occ` also writes that isolated gateway-discovery cache and exports `CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1`, which adds one row per cached model. Rows are labelled with the raw id — Claude Code reuses that label for the session header and the status line — and ones already shown by an alias slot are deduplicated away. `OCC_GATEWAY_MODELS` sets the list; `OCC_MODEL_DISCOVERY=0` skips both steps.

All four alias rows show `deepseek-v4-pro`, because opus, fable, sonnet, and haiku all resolve to `OCC_MODEL` (sonnet via `OCC_SONNET_MODEL`, which defaults the same). Alias slots are not deduplicated against each other the way gateway rows are against them, so the same id appears once per slot. This is expected: every slot has to name a real id or it 401s, and collapsing them onto the strongest model means anything that lands on an alias — `settings.json`, `--model sonnet`, `--model haiku`, subagents — gets pro. `deepseek-v4-flash` appears only as a gateway row. Pointing `OCC_SONNET_MODEL` at another model reclaims a row and gives that up.

Claude Code's own discovery fetcher never maintains that file for this gateway — it keeps only ids matching `/claude|anthropic/i`, which drops the whole catalog — so it neither populates nor overwrites what `occ` writes. The file holds one gateway at a time, so writing it discards another gateway's entry; for any gateway serving `claude-*` ids that fetcher regenerates it.

The two row types persist differently, but only inside the isolated profile. A gateway row carries the raw id, so Enter writes e.g. `deepseek-v4-flash` to `occ`'s `settings.json` as that profile's default — `cl` is unaffected. An alias row carries the alias name (`opus`, `sonnet`, `haiku`, `fable`) and only shows the id as its label, so Enter writes `sonnet`, which re-resolves to pro. `s` keeps the choice for this session only.

The Claude statusline detects `occ` from `ANTHROPIC_BASE_URL` and swaps in the OpenCode account and Go plan usage; see [Statusline](statusline.md).

`occ` exists only because OpenCode has no `/goal` command; `oc` is otherwise the better path, since it reaches the whole catalog with no remapping. If OpenCode ever ships `/goal`, `occ` can be deleted.

## antigravity.sh

`ag` is an alias for `agy --dangerously-skip-permissions`. `rotate-antigravity` switches between saved Antigravity accounts using `secret-tool` (GNOME Keyring).

## chrome.sh

`chrome` launches Chromium with remote debugging on port 9222 for use with browser-automation MCP servers.
