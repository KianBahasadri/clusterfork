# Installation

## Requirements and quick start

Run on Linux with Bash, Python 3.11 or newer (`tomllib` is used during install),
and the usual GNU file utilities, including `realpath`. The installer checks for
`python3` but does not check its version or install agent CLIs.

Create a root `.env` with your local environment values. It must exist, and
`CONTEXT7_API_KEY` and `PIXELLAB_API_KEY` must be present for MCP placeholder
expansion, including entries that ship disabled. Other keys are needed only by
the integrations you use. `.env` is gitignored and is also sourced as Bash by
`bash_profile.sh`. Use plain `NAME=value` assignments with optional quotes;
the installer's placeholder parser does not evaluate shell expansions or
strip `export` prefixes.

```bash
./install-clusterfork.sh
source ~/.bashrc
```

A new shell loads the installed profile automatically. `bash_profile.sh` exports
the installed `.env`, puts the clusterfork `bin/` on `PATH` once, and sources
`shell/*.sh`. Agent commands and their runtime requirements are documented in
[Shell Modules](shell-modules.md) and [Agent Configs](agent-configs.md).

## Installed files

Paths below are relative to the repository unless prefixed with `~`.

| Source | Destination |
| --- | --- |
| `.env`, `bell.mp3`, `bash_profile.sh` | Matching files in `~/.config/clusterfork/` |
| `shell/`, `bin/`, `notify/` | Matching trees in `~/.config/clusterfork/` |
| `scripts/rotate_auth.py`, `scripts/codeview/` | Matching paths in `~/.config/clusterfork/scripts/` |
| `tmux.conf` | `~/.tmux.conf` |
| `agents/opencode.json` | `~/.config/opencode/opencode.json` |
| `agents/qwen.json` | `~/.qwen/settings.json` |
| `agents/grok.toml` | `~/.grok/config.toml` |
| `agents/claude.json` | `~/.claude/settings.json` |
| `agents/claude-plugins/<name>/` | `~/.claude/skills/<name>/` (after the shared skills copy) |
| `agents/antigravity.json` | `~/.gemini/antigravity-cli/settings.json` |
| `agents/antigravity-hooks.json` | `~/.gemini/config/hooks.json` |
| `agents/antigravity-mcp.json` | `~/.gemini/config/mcp_config.json` |
| `agents/cursor-mcp.json` | `~/.cursor/mcp.json` |
| `agents/command-code-mcp.json` | `~/.commandcode/mcp.json` |
| `agents/command-code.json` | `~/.commandcode/config.json` |
| `agents/command-code-settings.json` | `~/.commandcode/settings.json` |
| `agents/codex.toml` | `~/.codex/config.toml` |
| `statusline/claude/statusline.sh`, `statusline/claude/usage-fetch.py` | `~/.claude/statusline-command.sh`, `~/.claude/claude-usage-fetch.py` |
| `statusline/cursor/statusline.sh`, `statusline/cursor/usage-fetch.py` | `~/.cursor/statusline.sh`, `~/.cursor/cursor-usage-fetch.py` |
| `statusline/codex/statusline.config.toml` | `~/.local/share/clusterfork-auth/codex/statusline.config.toml`, linked from `~/.codex/clusterfork-statusline.toml` |

The installer also installs the shared skills and compatibility aliases;
[Skills](skills.md#installation-and-discovery) owns their destinations and names.
It does not install all of `scripts/`: the auth converter and gateway probes run
from the checkout. Copied `bin/` and codeview trees have Python bytecode caches
removed.

## Replacement and preservation

Reinstalling replaces mapped files and trees from the repository. These are the
exceptions:

| Destination | Installer behavior |
| --- | --- |
| `~/.bashrc` | Appends the profile source line only if a clusterfork source line is absent |
| `~/.grok/config.toml` | Replaces the file, then restores an existing `theme` value |
| `~/.gemini/antigravity-cli/settings.json` | Overlays repo keys and unions `trustedWorkspaces`; retains unrelated runtime keys |
| `~/.commandcode/config.json` | Overlays repo keys, currently `telemetry`; retains other settings |
| `~/.commandcode/settings.json` | Ensures the repo's Stop hook and removes the exact legacy clusterfork bell; retains other hooks and settings |
| `~/.claude.json` | Upserts only `mcpServers.ElevenLabs` |
| `~/.cursor/cli-config.json` | Upserts `statusLine`; creates a minimal file when absent |
| `~/.codex/config.toml` | Overlays repo top-level keys and tables, removes retired `notify`, and restores existing `hooks.state` with the notifier's `trusted_hash` updated; retains unrelated settings and project trust |
| `~/.codex/skills/` | Replaces non-dot skill directories, leaving Codex-managed `.system` and other dot entries alone |
| Shared Codex statusline file | Replaces it on a writable host; on a read-only mount requires an existing readable file and creates only the local link |

Codex's merge verifies the parsed result and aborts before writing that file if
unrelated settings would change. Install on the shared auth store's writable
host before installing in a container; see [Statusline](statusline.md#codex).

Cursor, Command Code, and Antigravity MCP files are fully replaced after `${ENV}`
expansion from the installed `.env`. Claude plugin and Codex MCP secrets are
resolved by the CLIs at launch; see [Agent Configs](agent-configs.md).

Codex, Cursor, Grok, and OpenCode shared auth links are repaired as a best-effort
final step. A failure there prints a warning without undoing the rest of the
install. [Auth Rotation](auth-rotation.md#installer-repair) owns the migration
rules. Notification preferences and history are runtime files and are not reset.

The install is repeatable but is not a transaction: a later error can leave
earlier steps installed. Fix the reported problem and rerun it. Output lists
destinations, hooks, MCP names, merged keys, and skills as each step completes.
