# Agent Configs

JSON/TOML files under `agents/` configure individual agents. The installer copies each to its destination.

## agents/qwen.json → ~/.qwen/settings.json

Qwen Code settings:

- **Model / auth:** not pinned by clusterfork — use Qwen Code's built-in login or add providers locally
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), ElevenLabs (local via `~/.config/clusterfork/bin/elevenlabs-mcp`, requires `ELEVENLABS_API_KEY`), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222)
- **Privacy:** usage statistics disabled
- **Memory:** managed auto-memory, auto-dream, and auto-skill all disabled

## agents/opencode.json → ~/.config/opencode/opencode.json

OpenCode settings:

- **Permissions:** tools are allowed by default; the `plan` agent explicitly
  denies shell commands and file edits, and OpenCode itself cannot load the
  self-delegating `ask-opencode` skill
- **Default agent:** `build`
- **MCP servers:** context7 (remote), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), ElevenLabs (local via clusterfork launcher, disabled)

## agents/antigravity.json → ~/.gemini/antigravity-cli/settings.json

Antigravity CLI settings:

- **Model:** Gemini 3.5 Flash (High)
- **Theme:** dark
- **Telemetry:** disabled
- **Trusted workspaces:** `~/.config/clusterfork`, `~/steam_cart_evaluator`

## agents/grok.toml → ~/.grok/config.toml

Grok CLI settings:

- **Model:** not pinned by clusterfork — Grok CLI uses its own default; fork secondary: `grok-4.5`
- **UI:** default theme `tokyonight` (installer preserves an existing theme in `~/.grok/config.toml`), `permission_mode = always-approve`, `yolo = false`
- **Privacy:** telemetry disabled (`[features] telemetry = false`); the initial
  "Help improve Grok" banner is acknowledged (`[privacy].privacy_banner_acked`)
  so it is not shown on fresh installations
- **Marketplace:** xAI Official plugin marketplace source; default skills installs are not purged (`default_skills_installs_purged = false`)
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), ElevenLabs (local via clusterfork launcher)
- **Plugins:** chrome-devtools-mcp disabled
- **Updates:** auto-update enabled; `installer = internal`


## agents/claude.json → ~/.claude/settings.json

Claude Code settings:

- **Model:** `claude-opus-4-8` (Opus 4.8; not the `opus` alias, which resolves to Opus 5), effort `xhigh`
- **Status line:** command `bash ~/.claude/statusline-command.sh`, refresh every 60s (see [Statusline](statusline.md))
- **Plugins:** context7 enabled
- **UI:** dark theme, fullscreen TUI, prompt suggestions off
- **Voice:** enabled, hold mode
- **Other:** auto-memory off, skip dangerous-mode permission prompt, agent push notifications on

Claude user-scope MCP (ElevenLabs) is upserted into `~/.claude.json` by the installer — see [Installation](installation.md).

## agents/cursor-mcp.json → ~/.cursor/mcp.json

Cursor IDE MCP servers. The installer expands `${ENV}` placeholders from the clusterfork `.env` when writing the destination:

- **context7:** `pnpx @upstash/context7-mcp` with `CONTEXT7_API_KEY`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher (`ELEVENLABS_API_KEY` from `.env`)

## agents/command-code-mcp.json → ~/.commandcode/mcp.json

Command Code MCP servers. The installer expands `${ENV}` placeholders from the clusterfork `.env` when writing the destination (full replace):

- **context7:** remote `https://mcp.context7.com/mcp` with `CONTEXT7_API_KEY`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher (`ELEVENLABS_API_KEY` from `.env`)
- **linear:** remote `https://mcp.linear.app/mcp`, disabled
- **chrome-devtools:** local `pnpm dlx chrome-devtools-mcp@latest`, disabled — uses Chromium on port 9222

## ElevenLabs MCP launcher

`bin/elevenlabs-mcp` → `~/.config/clusterfork/bin/elevenlabs-mcp`. Loads `ELEVENLABS_API_KEY` from the clusterfork `.env` and runs `uvx elevenlabs-mcp`. Agent MCP configs invoke it via `bash -c` so GUI clients do not need clusterfork on `PATH`.

## Disabled-by-default MCP servers

linear and chrome-devtools ship disabled in OpenCode (`"enabled": false`), Grok (`enabled = false`), Qwen (`mcp.excluded: ["linear", "chrome-devtools"]`), and Command Code (`"enabled": false` on each `mcpServers` entry) — present but inactive; flip the flag (or remove the name from Qwen's exclude list) in the live config to use one, and a reinstall resets it to disabled.

They are deliberately omitted from Cursor and Claude Code. Cursor's `mcp.json` has no per-server disabled field — on/off is IDE state toggled in Customize → MCPs, tracked per project (the CLI has `cursor-agent mcp enable/disable`, also local state). Claude Code ignores a user-scope `disabled` field in `~/.claude.json` — verified empirically on v2.1.220 (2026-07): a probe server with `"disabled": true` was still spawned (upstream issues anthropics/claude-code#33958 and #13311 were stale-closed, not fixed). Its per-project `/mcp` toggle (`disabledMcpServers`) is per-project state, not shippable config. Shipping the servers to either agent would enable them by default, the opposite of the intent.

### Qwen disable mechanism (verified on `@qwen-code/qwen-code` 0.19.2, 2026-07)

Qwen Code has **no** per-server `disabled` / `enabled` field on `mcpServers` entries. Official docs list only transport fields plus `timeout`, `trust`, `includeTools`, `excludeTools`, OAuth, etc. A probe with `"disabled": true` on a user-scope stdio server was still spawned on headless start; stderr reported `MCP server(s) failed to start: probe-disabled-field`. Source confirms it: `Config.isMcpServerDisabled(name)` only checks the top-level `mcp.excluded` name list (and extension-level disabled MCP prefs) — it never reads a per-entry `disabled` key.

Correct shippable disable is the top-level blocklist:

```json
{
  "mcp": { "excluded": ["linear", "chrome-devtools"] },
  "mcpServers": { "linear": { "httpUrl": "..." }, "chrome-devtools": { "command": "..." } }
}
```

Empirical check of that path: with both a `"disabled": true` probe and an `mcp.excluded` probe installed, only the `disabled`-flag probe was attempted; the excluded name was skipped (no spawn, not listed among failed MCP starts).

Caveats for operators:

- `qwen mcp list` does **not** honor `mcp.excluded` — it connection-tests every entry in `mcpServers` (so a disabled stdio server still spawns under `list`). Session discovery and headless runs do honor it.
- Matching is exact name equality (`.includes(serverName)` in 0.19.2). Official docs mention globs for `mcp.allowed` / `mcp.excluded`; the installed binary does not expand globs when deciding disabled status.
- Enabling a shipped-disabled server: remove its name from `mcp.excluded` (or clear that list). Do not add `"disabled": false` on the entry — that field is ignored either way.
