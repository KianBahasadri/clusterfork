# Agent Configs

JSON/TOML files under `agents/` configure individual agents. The installer copies each to its destination.

## agents/qwen.json → ~/.qwen/settings.json

Qwen Code settings:

- **Model / auth:** not pinned by clusterfork — use Qwen Code's built-in login or add providers locally
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), ElevenLabs (local via `~/.config/clusterfork/bin/elevenlabs-mcp`, requires `ELEVENLABS_API_KEY`)
- **Privacy:** usage statistics disabled
- **Memory:** managed auto-memory, auto-dream, and auto-skill all disabled

## agents/opencode.json → ~/.config/opencode/opencode.json

OpenCode settings:

- **Permission:** `allow`
- **Default agent:** `build`
- **MCP servers:** context7 (remote), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222), ElevenLabs (local via clusterfork launcher)

## agents/antigravity.json → ~/.gemini/antigravity-cli/settings.json

Antigravity CLI settings:

- **Model:** Gemini 3.5 Flash (High)
- **Theme:** dark
- **Telemetry:** disabled
- **Trusted workspaces:** `~/.config/clusterfork`, `~/steam_cart_evaluator`

## agents/grok.toml → ~/.grok/config.toml

Grok CLI settings:

- **Model:** `grok-4.5` (high effort is that model’s default; fork secondary: `grok-4.5`)
- **UI:** default theme `tokyonight` (installer preserves an existing theme in `~/.grok/config.toml`), `permission_mode = always-approve`, `yolo = false`
- **Marketplace:** xAI Official plugin marketplace source; default skills installs are not purged (`default_skills_installs_purged = false`)
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), chrome-devtools (local, disabled — uses Chromium on port 9222), ElevenLabs (local via clusterfork launcher)
- **Plugins:** chrome-devtools-mcp disabled
- **Updates:** auto-update enabled; `installer = internal`


## agents/claude.json → ~/.claude/settings.json

Claude Code settings:

- **Model:** `opus`, effort `xhigh`
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

## ElevenLabs MCP launcher

`bin/elevenlabs-mcp` → `~/.config/clusterfork/bin/elevenlabs-mcp`. Loads `ELEVENLABS_API_KEY` from the clusterfork `.env` and runs `uvx elevenlabs-mcp`. Agent MCP configs invoke it via `bash -c` so GUI clients do not need clusterfork on `PATH`.
