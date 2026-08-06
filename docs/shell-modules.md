# Shell Modules

`bash_profile.sh` sources every `shell/*.sh` on shell startup. Each module defines launch wrappers, aliases, or credential rotation functions for one agent.

## claude.sh

`cl` is an alias for `claude --dangerously-skip-permissions --effort xhigh`. `rotate-claude` switches between multiple saved Claude account credentials.

## codex.sh

`cc` is an alias for `codex resume -c approval_policy=never`. `rotate-codex` switches between saved Codex accounts via symlinks.

## cursor.sh

`ca` is an alias for `cursor-agent --yolo` (Run Everything / force-allow). `rotate-cursor-cli` switches between saved Cursor accounts via symlinks.

## opencode.sh

`oc` is an alias for `opencode --continue`. `o` is an alias for `opencode`. `rotate-opencode` switches between saved OpenCode accounts via symlinks.

## opencode-claude.sh

`occ` launches Claude Code (`--dangerously-skip-permissions`) against the **OpenCode Go** subscription instead of an Anthropic account. OpenCode Go serves an Anthropic-compatible `/v1/messages` endpoint at `https://opencode.ai/zen/go`, so no proxy is involved — `ANTHROPIC_BASE_URL` points straight at it.

The key is read from `OPENCODE_API_KEY` (clusterfork `.env`) if set, otherwise from `opencode-go.key` in `~/.local/share/opencode/auth.json`, so `occ` follows whichever account `rotate-opencode` selected. It runs in a subshell that unsets `ANTHROPIC_AUTH_TOKEN` and `CLAUDE_CODE_OAUTH_TOKEN` first, since a cached OAuth token would otherwise outrank the gateway key.

Two properties of the endpoint shape the module:

- **Only `x-api-key` authenticates.** `Authorization: Bearer` returns 401, so the key goes in `ANTHROPIC_API_KEY`, never `ANTHROPIC_AUTH_TOKEN`.
- **There are no Claude models in the catalog.** Every model slot Claude Code can route to is remapped to an `opencode-go` id — `ANTHROPIC_MODEL`, the `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL` aliases (which `~/.claude/settings.json` selects by name), `ANTHROPIC_SMALL_FAST_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, and `CLAUDE_CODE_BG_CLASSIFIER_MODEL`. An unmapped id comes back as **401 `Model ... is not supported`**, not a 404, so a missed slot looks like an auth failure.

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` is derived per-model from the models.dev cache OpenCode maintains at `~/.cache/opencode/models.json`. Without it Claude Code assumes a 200k window for models it has no metadata for and auto-compacts far too early — the usable models range from 204k to 1M. `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` keeps everything except inference off the third-party gateway.

Defaults, all overridable: `OCC_MODEL=deepseek-v4-flash`, `OCC_SONNET_MODEL=deepseek-v4-flash`, `OCC_SMALL_MODEL=minimax-m3`. The small slot stays on a cheaper model because it drives the background classifier and other trivial calls. Also honoured: `OPENCODE_GO_BASE_URL`, `OCC_MAX_CONTEXT_TOKENS`, `OCC_OPENCODE_AUTH_FILE`, `OCC_MODELS_CACHE`.

Claude Code prints a one-time warning that claude.ai connectors are disabled because `ANTHROPIC_API_KEY` takes precedence over the claude.ai login. That is expected under `occ` and does not affect the session.

Only 9 of the 25 catalog models can drive the agent loop over this endpoint, and the defaults above are chosen from that set. Verify with `python scripts/opencode_go_probe.py` before changing `OCC_MODEL` — some models fail this endpoint while working fine under `oc`. See [OpenCode Go endpoint](opencode-go.md) for the matrix and the failure modes.

The Claude statusline detects `occ` from `ANTHROPIC_BASE_URL` and swaps in the OpenCode account and Go plan usage; see [Statusline](statusline.md).

`occ` exists only because OpenCode has no `/goal` command; `oc` is otherwise the better path, since it reaches the whole catalog with no remapping. If OpenCode ever ships `/goal`, `occ` can be deleted.

## antigravity.sh

`ag` is an alias for `agy --dangerously-skip-permissions`. `rotate-antigravity` switches between saved Antigravity accounts using `secret-tool` (GNOME Keyring).

## chrome.sh

`chrome` launches Chromium with remote debugging on port 9222 for use with browser-automation MCP servers.
