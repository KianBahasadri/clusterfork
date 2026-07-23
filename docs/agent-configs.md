# Agent Configs

JSON/TOML files under `agents/` configure individual agents. The installer copies each to its destination.

## agents/qwen.json → ~/.qwen/settings.json

Qwen Code settings:

- **Model:** `zai-org/GLM-5.2`
- **Auth:** OpenAI type (reads `PIONEER_API_KEY`)
- **Providers:** six models on `https://api.pioneer.ai/v1` — Pioneer Auto, Qwen 3.7 Max, DeepSeek V4 Pro, Kimi K2.7 Code, GLM 5.2, MiniMax M3. All use reasoning effort `xhigh`.
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`)
- **Privacy:** usage statistics disabled
- **Memory:** managed auto-memory, auto-dream, and auto-skill all disabled

## agents/opencode.json → ~/.config/opencode/opencode.json

OpenCode settings:

- **Permission:** `allow`
- **Default agent:** `build`
- **Provider:** Pioneer (OpenAI-compatible), same six models as Qwen config
- **MCP servers:** context7 (remote), linear (remote, disabled), chrome-devtools (local, disabled — uses Chromium on port 9222)

## agents/antigravity.json → ~/.gemini/antigravity-cli/settings.json

Antigravity CLI settings:

- **Model:** Gemini 3.5 Flash (High)
- **Theme:** dark
- **Telemetry:** disabled
- **Trusted workspaces:** `~/.config/clusterfork`, `~/steam_cart_evaluator`

## agents/grok.toml → ~/.grok/config.toml

Grok CLI settings:

- **Model:** `grok-composer-2.5-fast` (fork secondary: `grok-build`)
- **UI:** oscura-midnight theme, `permission_mode = bypassPermissions` (always-approve)
- **Marketplace:** xAI Official plugin marketplace source
- **MCP servers:** context7 (remote, requires `CONTEXT7_API_KEY`), chrome-devtools (local, disabled — uses Chromium on port 9222)
- **Plugins:** chrome-devtools-mcp disabled
- **Updates:** auto-update enabled

## agents/claude.json → ~/.claude/settings.json

Claude Code settings:

- **Model:** `opus`, effort `xhigh`
- **Status line:** command `bash ~/.claude/statusline-command.sh`, refresh every 60s (see [Statusline](statusline.md))
- **Plugins:** context7 enabled
- **UI:** dark theme, fullscreen TUI, prompt suggestions off
- **Voice:** enabled, hold mode
- **Other:** auto-memory off, skip dangerous-mode permission prompt, agent push notifications on
