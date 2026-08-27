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
| `scripts/rotate_auth.py`          | `~/.config/clusterfork/scripts/rotate_auth.py` | Shared `rotate-*` implementation |
| `scripts/cf_dash/`                | `~/.config/clusterfork/scripts/cf_dash/` | cf-dash dashboard server + UI (see [Dashboard](dashboard.md)) |
| `tmux.conf`                       | `~/.tmux.conf`                           | tmux settings (`mouse on`)        |
| `agents/opencode.json`            | `~/.config/opencode/opencode.json`       | OpenCode settings                 |
| `agents/qwen.json`                | `~/.qwen/settings.json`                  | Qwen Code settings                |
| `agents/antigravity.json`         | `~/.gemini/antigravity-cli/settings.json`| Antigravity settings              |
| `agents/grok.toml`                | `~/.grok/config.toml`                    | Grok CLI settings                 |
| `agents/claude.json`              | `~/.claude/settings.json`                | Claude Code settings              |
| `agents/claude-plugins/`          | `~/.claude/skills/<name>/`               | Claude Code MCP servers, shipped disabled (one skills-dir plugin each) |
| `agents/cursor-mcp.json`          | `~/.cursor/mcp.json`                     | Cursor MCP servers (`${ENV}` expanded from `.env`) |
| `agents/command-code-mcp.json`    | `~/.commandcode/mcp.json`                | Command Code MCP servers (`${ENV}` expanded from `.env`) |
| `agents/command-code.json`        | `~/.commandcode/config.json`             | Command Code settings (`telemetry` only; other keys preserved) |
| `agents/codex-mcp.toml`           | `~/.codex/config.toml`                   | Codex MCP servers (`mcp_servers` tables only; secrets referenced by env var name) |
| `statusline/claude/statusline.sh` | `~/.claude/statusline-command.sh`        | Claude status line script         |
| `statusline/claude/usage-fetch.py`| `~/.claude/claude-usage-fetch.py`        | Claude usage cache helper         |
| `statusline/cursor/statusline.sh` | `~/.cursor/statusline.sh`                | Cursor status line script         |
| `statusline/cursor/usage-fetch.py`| `~/.cursor/cursor-usage-fetch.py`        | Cursor usage cache helper         |
| `skills/`                         | `~/.qwen/skills/`, `~/.grok/skills/`, `~/.claude/skills/`, and `~/.codex/skills/` | Shared skills (Codex keeps `.system`) |
| `skills/`                         | `~/.commandcode/skills/`                 | Shared skills, normalized to Command Code's hyphenated IDs |
| `skills/`                         | `~/.gemini/antigravity-cli/skills/` | Antigravity CLI skills (normalized for its global skill directory) |
| `skills/`                         | `~/.config/opencode/skills/`     | OpenCode compatibility aliases; other shared skills are discovered through `~/.claude/skills/` |

The installer also:

- Appends a `source` line to `~/.bashrc` so `bash_profile.sh` is loaded in every new shell
- Ensures the `statusLine` key in `~/.cursor/cli-config.json` (without replacing that whole file). See [Statusline](statusline.md)
- Ensures the ElevenLabs entry in `~/.claude.json` `mcpServers` (without replacing that whole file)
- Replaces only the `mcp_servers` tables in `~/.codex/config.toml` from `agents/codex-mcp.toml`; Codex's model, approval settings, and `[projects]` trust levels are left alone
- Installs each `agents/claude-plugins/<name>/` into `~/.claude/skills/` as the plugin `<name>@skills-dir`, after the skills copy that wipes that directory. It aborts if `agents/claude.json` does not set `"<name>@skills-dir": false` in `enabledPlugins`, since plugins are on unless told otherwise
- Overwrites `~/.commandcode/mcp.json` from `agents/command-code-mcp.json`, expanding `${ENV}` placeholders from `.env` (full replace)
- Ensures `telemetry: false` in `~/.commandcode/config.json` from `agents/command-code.json` (key only; other keys preserved)
- Best-effort repair of Codex/Cursor/OpenCode shared auth links under `~/.local/share/clusterfork-auth/`. See [Auth Rotation](auth-rotation.md)

## Requirements

- A `.env` file in the repo root containing your API keys. The installer aborts if it's missing.
- `python3`, used by `rotate-*` (and the other utilities under `scripts/`). The installer aborts if it is missing.

## Re-running

The installer is idempotent — running it again **overwrites** every mapped destination from the repo (full replace, not merge). It will not add a duplicate `source` line to `~/.bashrc`. Exceptions: `~/.grok/config.toml` is replaced from the repo, but an existing `theme` value is kept; `~/.codex/config.toml` has only its `mcp_servers` tables replaced; `~/.commandcode/config.json` and `~/.claude.json`/`~/.cursor/cli-config.json` have only specific keys merged. See [Conventions](conventions.md) for the source-of-truth rule.
