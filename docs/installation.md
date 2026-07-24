# Installation

## Quick start

```bash
./install-clusterfork.sh
```

## What the installer writes

`install-clusterfork.sh` copies config from the repo into your home directory:

| Source (repo)                     | Destination                              | Contents                          |
|-----------------------------------|------------------------------------------|-----------------------------------|
| `.env`                            | `~/.config/clusterfork/.env`             | API keys (gitignored)             |
| `bash_profile.sh`                 | `~/.config/clusterfork/bash_profile.sh`  | Sourced on shell startup          |
| `shell/*.sh`                      | `~/.config/clusterfork/shell/*.sh`       | One module per agent              |
| `bin/*`                           | `~/.config/clusterfork/bin/*`            | Helper launchers (on `PATH`)      |
| `agents/opencode.json`            | `~/.config/opencode/opencode.json`       | OpenCode settings                 |
| `agents/qwen.json`                | `~/.qwen/settings.json`                  | Qwen Code settings                |
| `agents/antigravity.json`         | `~/.gemini/antigravity-cli/settings.json`| Antigravity settings              |
| `agents/grok.toml`                | `~/.grok/config.toml`                    | Grok CLI settings                 |
| `agents/claude.json`              | `~/.claude/settings.json`                | Claude Code settings              |
| `agents/cursor-mcp.json`          | `~/.cursor/mcp.json`                     | Cursor MCP servers (`${ENV}` expanded from `.env`) |
| `statusline/claude/statusline.sh` | `~/.claude/statusline-command.sh`        | Claude status line script         |
| `statusline/claude/usage-fetch.py`| `~/.claude/claude-usage-fetch.py`        | Claude usage cache helper         |
| `statusline/cursor/statusline.sh` | `~/.cursor/statusline.sh`                | Cursor status line script         |
| `statusline/cursor/usage-fetch.py`| `~/.cursor/cursor-usage-fetch.py`        | Cursor usage cache helper         |
| `skills/`                         | `~/.qwen/skills/`, `~/.grok/skills/`, `~/.claude/skills/`, and `~/.codex/skills/` | Shared skills (Codex keeps `.system`) |
| `skills/`                         | `~/.gemini/antigravity-cli/skills/` | Antigravity CLI skills (normalized for its global skill directory) |
| `skills/`                         | `~/.config/opencode/skills/`     | OpenCode compatibility aliases; other shared skills are discovered through `~/.claude/skills/` |

The installer also:

- Appends a `source` line to `~/.bashrc` so `bash_profile.sh` is loaded in every new shell
- Ensures the `statusLine` key in `~/.cursor/cli-config.json` (without replacing that whole file). See [Statusline](statusline.md)
- Ensures the ElevenLabs entry in `~/.claude.json` `mcpServers` (without replacing that whole file)

## Requirements

- A `.env` file in the repo root containing your API keys. The installer aborts if it's missing.

## Re-running

The installer is idempotent — running it again **overwrites** every mapped destination from the repo (full replace, not merge). It will not add a duplicate `source` line to `~/.bashrc`. See [Conventions](conventions.md) for the source-of-truth rule.
