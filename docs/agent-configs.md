# Agent Configs

JSON/TOML files under `agents/` configure individual agents. The installer copies each to its destination.

## agents/qwen.json → ~/.qwen/settings.json

Qwen Code settings:

- **Model / auth:** not pinned by clusterfork — use Qwen Code's built-in login or add providers locally
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), ElevenLabs (local via `~/.config/clusterfork/bin/elevenlabs-mcp`, requires `ELEVENLABS_API_KEY`), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), pixellab (remote, disabled — requires `PIXELLAB_API_KEY`)
- **Privacy:** usage statistics disabled
- **Memory:** managed auto-memory, auto-dream, and auto-skill all disabled

## agents/opencode.json → ~/.config/opencode/opencode.json

OpenCode settings:

- **Permissions:** tools are allowed by default; the `plan` agent explicitly
  denies shell commands and file edits, and OpenCode itself cannot load the
  self-delegating `ask-opencode` skill
- **Default agent:** `build`
- **MCP servers:** context7 (remote), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), pixellab (remote, disabled — requires `PIXELLAB_API_KEY`), ElevenLabs (local via clusterfork launcher, disabled)

## agents/antigravity.json → ~/.gemini/antigravity-cli/settings.json

Antigravity CLI settings:

- **Model:** not pinned by clusterfork
- **Theme:** dark
- **Telemetry:** disabled
- **Trusted workspaces:** `~/.config/clusterfork`, `~/steam_cart_evaluator`

## agents/antigravity-mcp.json → ~/.gemini/config/mcp_config.json

Antigravity MCP servers. The installer expands `${ENV}` placeholders from the clusterfork `.env` when writing the destination (full replace, `disabled: true` means off):

- **context7:** remote `https://mcp.context7.com/mcp` with `CONTEXT7_API_KEY`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher (`ELEVENLABS_API_KEY` from `.env`)
- **linear:** remote `https://mcp.linear.app/mcp`, disabled
- **chrome-devtools:** local `pnpm dlx chrome-devtools-mcp@latest`, disabled — uses Chromium on port 9222
- **pixellab:** remote `https://api.pixellab.ai/mcp`, disabled, with its bearer token from `PIXELLAB_API_KEY` in `.env`

## agents/antigravity-hooks.json → ~/.gemini/config/hooks.json

Antigravity lifecycle hooks:

- **turn-bell:** `Stop` command hook invoking the shared completion notifier
  (`clusterfork-notify antigravity`). The helper notifies only when stdin has
  `fullyIdle: true`, so intermediate tool yields and subagent cycles stay
  quiet. See [Notifications](notifications.md).

## agents/grok.toml → ~/.grok/config.toml

Grok CLI settings:

- **Model:** not pinned by clusterfork — Grok CLI uses its own default; fork secondary: `grok-4.5`
- **UI:** default theme `tokyonight` (installer preserves an existing theme in `~/.grok/config.toml`), `permission_mode = always-approve`, `yolo = false`
- **Privacy:** telemetry disabled (`[features] telemetry = false`); the initial
  "Help improve Grok" banner is acknowledged (`[privacy].privacy_banner_acked`)
  so it is not shown on fresh installations
- **Marketplace:** xAI Official plugin marketplace source; default skills installs are not purged (`default_skills_installs_purged = false`)
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), pixellab (remote, disabled — requires `PIXELLAB_API_KEY`), ElevenLabs (local via clusterfork launcher)
- **Hooks:** `[[hooks.Stop]]` invokes the shared completion notifier; see [Notifications](notifications.md). `[compat.claude] hooks = false` so Grok does not also import `~/.claude/settings.json`'s Stop notifier (that pair is what produced `hooks: 2` and a double bell). Other Claude compatibility cells stay at Grok's defaults.
- **Plugins:** chrome-devtools-mcp disabled
- **Updates:** auto-update enabled; `installer = internal`


## agents/claude.json → ~/.claude/settings.json

Claude Code settings:

- **Model:** `claude-opus-4-8` (Opus 4.8; not the `opus` alias, which resolves to Opus 5)
- **Status line:** command `bash ~/.claude/statusline-command.sh`, refresh every 60s (see [Statusline](statusline.md))
- **Plugins:** context7 enabled; linear, chrome-devtools, and pixellab shipped disabled — see [agents/claude-plugins/](#agentsclaude-plugins--claudeskills)
- **Hooks:** `Stop` invokes the shared completion notifier; see [Notifications](notifications.md)
- **UI:** dark theme, fullscreen TUI, prompt suggestions off
- **Voice:** enabled, hold mode
- **Other:** auto-memory off, skip dangerous-mode permission prompt, agent push notifications on, drafted feedback off (`"feedbackDrafts": "off"`)

Claude user-scope MCP (ElevenLabs) is upserted into `~/.claude.json` by the installer — see [Installation](installation.md).

## agents/claude-plugins/ → ~/.claude/skills/

Claude Code MCP servers that have to default to off. Each directory is a plugin
(`.claude-plugin/plugin.json` + `.mcp.json`) and auto-loads as
`<name>@skills-dir` once it sits under `~/.claude/skills/` — no marketplace
registration involved. This is the only route that survives into projects
clusterfork has never seen; see [the disable mechanism](#claude-code-disable-mechanism-verified-on-21235-2026-08) below for why a
plain `mcpServers` entry cannot do it.

- **linear:** remote `https://mcp.linear.app/mcp`, disabled
- **chrome-devtools:** local `pnpm dlx chrome-devtools-mcp@latest`, disabled — uses Chromium on port 9222
- **pixellab:** remote `https://api.pixellab.ai/mcp`, disabled

`agents/claude.json` ships each one `false` in `enabledPlugins`; the installer
refuses to install a plugin directory missing either file, or one that
`agents/claude.json` does not explicitly turn off, since plugins are on unless
told otherwise.

As with Codex, no `${ENV}` expansion happens at install time — Claude Code
expands `${PIXELLAB_API_KEY}` in the plugin's `.mcp.json` from its own
environment and `bash_profile.sh` exports all of `.env`, so no key is written to
disk. Verified against a header-logging server: expansion works in `args`, `env`,
and `headers`.

To enable one, flip its `enabledPlugins` value in `~/.claude/settings.json` to
`true` (or use `/plugin`); `claude plugin list` reports the current state under
"Skills-directory plugins". A reinstall resets it to disabled, matching the
other CLIs. Two consequences of the plugin route: the tools arrive named
`mcp__plugin_<plugin>_<server>__<tool>` rather than `mcp__<server>__<tool>`, and
`/mcp` lists an enabled one under "Built-in MCPs (always available)" instead of
"User MCPs", so it is toggled through `/plugin`, not `/mcp`.

## agents/cursor-mcp.json → ~/.cursor/mcp.json

Cursor IDE MCP servers. The installer expands `${ENV}` placeholders from the clusterfork `.env` when writing the destination:

- **context7:** `pnpx @upstash/context7-mcp` with `CONTEXT7_API_KEY`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher (`ELEVENLABS_API_KEY` from `.env`)

## agents/command-code.json → ~/.commandcode/config.json

Command Code settings. The installer merges keys from the repo template into the existing config so user settings (provider, model, etc.) are preserved:

- **Privacy:** telemetry disabled (`"telemetry": false`)

If `~/.commandcode/config.json` does not exist, it is created with `{"telemetry": false}`.

## agents/command-code-settings.json → ~/.commandcode/settings.json

Command Code user-scope settings. The installer ensures the shared `Stop`
notifier, migrates the exact legacy clusterfork bell, and preserves every
other key and hook in the file:

- **Hooks:** `Stop` invokes the shared completion notifier; see [Notifications](notifications.md)

## agents/command-code-mcp.json → ~/.commandcode/mcp.json

Command Code MCP servers. The installer expands `${ENV}` placeholders from the clusterfork `.env` when writing the destination (full replace):

- **context7:** remote `https://mcp.context7.com/mcp` with `CONTEXT7_API_KEY`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher (`ELEVENLABS_API_KEY` from `.env`)
- **linear:** remote `https://mcp.linear.app/mcp`, disabled
- **chrome-devtools:** local `pnpm dlx chrome-devtools-mcp@latest`, disabled — uses Chromium on port 9222
- **pixellab:** remote `https://api.pixellab.ai/mcp`, disabled, with its bearer token from `PIXELLAB_API_KEY` in `.env`

## agents/codex.toml → ~/.codex/config.toml

Codex settings and MCP servers. Top-level settings defined in `agents/codex.toml`
and all `[mcp_servers…]` / hook event tables are updated/overwritten into
`~/.codex/config.toml`. The installer also strips retired clusterfork keys
(`notify`) and stamps `trusted_hash` for the clusterfork Stop notifier only
(`[hooks.state."…/config.toml:stop:0:0"]`; other `hooks.state` entries stay).
Codex owns the rest of `~/.codex/config.toml` — `model`,
`model_reasoning_effort`, `approvals_reviewer`, `service_tier`, and the
`[projects]` trust levels it writes as you accept directories — so other keys
and tables stay untouched. `${HOME}` placeholders in values are expanded at
install time.

- **Hooks:** root-only asynchronous `[[hooks.Stop]]` invokes the shared completion notifier; see [Notifications](notifications.md). The installer writes its matching `trusted_hash` so it does not wait on `/hooks`.
- **context7:** remote `https://mcp.context7.com/mcp`
- **ElevenLabs:** clusterfork `bin/elevenlabs-mcp` launcher
- **linear:** remote `https://mcp.linear.app/mcp`, disabled
- **chrome-devtools:** local `pnpm dlx chrome-devtools-mcp@latest`, disabled — uses Chromium on port 9222
- **pixellab:** remote `https://api.pixellab.ai/mcp`, disabled

No `${ENV}` expansion is needed for the MCP secrets themselves, unlike Cursor
and Command Code. Codex resolves `env_http_headers` (context7's `CONTEXT7_API_KEY`
header) and `bearer_token_env_var` (pixellab's `PIXELLAB_API_KEY`) from its own
environment at launch, and `bash_profile.sh` exports all of `.env` with `set -a`,
so no key is written to disk. Verified against a header-logging server: both
arrive on the wire, and an unset variable drops the header instead of failing
the server.

## ElevenLabs MCP launcher

`bin/elevenlabs-mcp` → `~/.config/clusterfork/bin/elevenlabs-mcp`. Loads `ELEVENLABS_API_KEY` from the clusterfork `.env` and runs `uvx elevenlabs-mcp`. Agent MCP configs invoke it via `bash -c` so GUI clients do not need clusterfork on `PATH`.

## Disabled-by-default MCP servers

linear, chrome-devtools, and pixellab ship disabled in OpenCode (`"enabled": false`), Grok (`enabled = false`), Qwen (`mcp.excluded`), Codex (`enabled = false`), Command Code (`"enabled": false` on each `mcpServers` entry), Antigravity (`"disabled": true` in `mcp_config.json`), and Claude Code (`enabledPlugins` set to `false`, one plugin per server) — present but inactive; flip the flag (or remove the name from Qwen's exclude list, or run `agy mcp enable <name>` for Antigravity) in the live config to use one, and a reinstall resets it to disabled.

They are deliberately omitted from Cursor, which has no shippable off switch at all: `mcp.json` has no per-server disabled field, and on/off is IDE state toggled in Customize → MCPs, tracked per project (the CLI has `cursor-agent mcp enable/disable`, also local state).

### Claude Code disable mechanism (verified on 2.1.235, 2026-08)

Probe method: an isolated `CLAUDE_CONFIG_DIR` holding a minimal stdio MCP server
that touches a marker file the moment it is spawned and answers
`initialize`/`tools/list` with one tool, so "was it started" and "was its tool
offered to the model" are both directly observable.

There is no **global** off switch for an `mcpServers` entry in `~/.claude.json`.
Each of these was ignored — the server spawned and its tool was offered:

- `"disabled": true` (or `"enabled": false`) on the `mcpServers` entry
- `disabledMcpServers` in `~/.claude/settings.json`
- `enabledMcpServers` in `~/.claude/settings.json` used as an allowlist that omits the name
- top-level `disabledMcpServers` in `~/.claude.json`

`mcpServers` in `~/.claude/settings.json` is not read at all — a server defined
only there never spawns, so that file cannot host the roster either.
`disabledMcpjsonServers` is a separate settings key that rejects only servers
defined in a project `.mcp.json`.

What does work — and what `/mcp`'s toggle writes — is `disabledMcpServers` in
the **project entry** of `~/.claude.json`. With
`projects["<cwd>"].disabledMcpServers = ["probe"]` the server did not spawn;
without it, it did. But it is per-project state keyed by absolute path: a
directory with no project entry yet gets every user-scope server **enabled**, so
it cannot express a shipped default.

The route that does survive into unseen projects: **ship the server inside a
plugin.** A directory under `~/.claude/skills/<name>/` containing
`.claude-plugin/plugin.json` and `.mcp.json` auto-loads as the plugin
`<name>@skills-dir` (the layout `claude plugin init` scaffolds; no marketplace
registration needed). Plugins are on by default, so the off switch has to be
written explicitly in `~/.claude/settings.json`:

```json
{ "enabledPlugins": { "chrome-devtools@skills-dir": false } }
```

Verified: with `false` the server never spawned and no tool was exposed; with
`true` — and with the key absent — it spawned and its tool appeared. This is
what the installer ships — see
[agents/claude-plugins/](#agentsclaude-plugins--claudeskills) above, which also
covers the tool-naming and `/plugin`-vs-`/mcp` consequences.

### Codex disable mechanism (verified on `codex-cli` 0.147.0, 2026-08)

Codex honors a per-server `enabled` flag at user scope in `~/.codex/config.toml`
— a real global default-off:

```toml
[mcp_servers.linear]
url = "https://mcp.linear.app/mcp"
enabled = false
```

Verified with the marker-file probe under an isolated `CODEX_HOME`: on a headless
`codex exec` run the `enabled = false` server was never spawned while a sibling
entry without the flag was. `codex mcp list` reports a `Status` column of
`disabled`/`enabled` (`--json` adds `"enabled"` and `"disabled_reason"`) and,
unlike `qwen mcp list`, connection-tests nothing — no spawn there either.

This is what the installer ships — see
[agents/codex.toml](#agentscodextoml--codexconfigtoml)
above. Re-running it resets a locally flipped `enabled` back to the repo value,
same as the other CLIs.

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
