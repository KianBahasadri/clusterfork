# Agent configs

`agents/` holds shipped settings and Claude MCP plugins. [Installation](installation.md)
owns destinations/merge rules. Models are pinned only where specified below.

## Claude Code

`claude.json` pins `claude-opus-4-8`, enables context7's marketplace plugin and
ships three local MCP plugins disabled. Installation copies local plugins but
does not download context7. Settings: dark fullscreen TUI, hold-to-talk voice,
prompt suggestions/auto-memory/drafted feedback off, dangerous-mode prompt skipped,
agent push notifications on, shared [Stop hook](notifications.md) and [statusline](statusline.md#claude).

### Skills-directory MCP plugins

Each `agents/claude-plugins/<name>/` requires `.claude-plugin/plugin.json` and
`.mcp.json`; installation rejects missing files or an entry not explicitly `false`
in `enabledPlugins`. Installed names are `<name>@skills-dir`; tools look like
`mcp__plugin_<plugin>_<server>__<tool>` and appear in `/mcp` under Built-in MCPs.
See the [disable-mechanism investigation](archive/mcp-disable-probes.md).

## OpenCode

`opencode.json` selects `build`, allowing tools by default; `plan` denies `bash`
and `edit`. Top-level `ask-opencode` denial prevents self-delegation. Model/auth
remain native choices. [`occ`](opencode-go.md) is the separate Claude Go launcher.

## Qwen Code

`qwen.json` leaves model/auth native. Usage statistics, managed auto-memory,
auto-dream, auto-skill and loading phrases are off; `mcp.excluded` controls discovery.

## Grok

`grok.toml` leaves the main model unpinned, pins fork model `grok-4.5`, and ships
`tokyonight`, `permission_mode = "always-approve"`, `yolo = false`, compact mode
and suggestions off. Telemetry is off, privacy acknowledged, internal updates on,
xAI Official marketplace configured, Chrome DevTools plugin disabled.
`[compat.claude] hooks = false` prevents duplicate bells from Claude's Stop hook.

## Antigravity

`antigravity.json` sets dark colors, telemetry/surveys off and no model pin.
Initial `/home/kian/...` trusted paths are literal; installation preserves existing
workspaces without relocating paths. `antigravity-hooks.json` supplies Stop;
`antigravity-mcp.json` supplies MCP entries.

## Command Code

`command-code.json` disables telemetry; `command-code-settings.json` supplies Stop;
`command-code-mcp.json` supplies MCP. Model/provider remain unpinned.

## Codex

`codex.toml` supplies MCP and asynchronous root Stop, without model, effort,
approval or project-trust pins. Installation merges settings and stamps hook trust;
launchers add the [native statusline](statusline.md#codex).

## Cursor

`cursor-mcp.json` supplies context7 and ElevenLabs; CLI [statusline](statusline.md#cursor)
wiring is separate.

## MCP roster

`on` still requires credentials, dependencies and any OAuth login.

| Agent | context7 | ElevenLabs | Linear | Chrome DevTools | PixelLab |
| --- | --- | --- | --- | --- | --- |
| Claude Code | on, marketplace plugin | on, user MCP | off, local plugin | off, local plugin | off, local plugin |
| OpenCode | on | off | off | off | off |
| Qwen Code | on | on | excluded | excluded | excluded |
| Grok | on | on | off | off | off |
| Antigravity | on | on | off | off | off |
| Command Code | on | on | off | off | off |
| Codex | on | on | on | off | off |
| Cursor | on | on | omitted | omitted | omitted |

Context7 uses remote MCP except Cursor's `pnpx @upstash/context7-mcp`.
Chrome DevTools uses `pnpm dlx chrome-devtools-mcp@latest` and the port-9222
[browser helper](shell-modules.md#browser-helper). Linear/PixelLab use remote HTTP.
`bin/elevenlabs-mcp` loads `ELEVENLABS_API_KEY` from installed `.env` (override:
`CLUSTERFORK_ENV_FILE`), rejects an empty key and executes `uvx elevenlabs-mcp`.
Configs use `bash -c` so GUI clients need no clusterfork `PATH`.

### Enabling optional servers

| Agent | Installed control |
| --- | --- |
| Claude Code | `enabledPlugins["<name>@skills-dir"]` or `/plugin` |
| OpenCode, Grok, Codex, Command Code | Per-server `enabled` |
| Antigravity | Per-server `disabled`, or `agy mcp enable <name>` |
| Qwen Code | Remove exact name from `mcp.excluded` |
| Cursor | IDE/CLI local state; optional entries omitted |

Reinstallation restores repo flags; edit templates for permanent changes.
Qwen 0.19.2's list command tested excluded servers while sessions skipped them;
see [probe notes](archive/mcp-disable-probes.md).

### Secret resolution

Installation expands Cursor/Command Code/Antigravity `${ENV}` from installed `.env`.
Codex uses launch-environment `env_http_headers`/`bearer_token_env_var`; OpenCode uses
`{env:NAME}`; Claude plugins/Grok/Qwen resolve `${NAME}` natively. The shell profile
exports installed `.env`; GUI clients using native references must inherit it.

### Shared Linear API key

Set optional `LINEAR_API_KEY` in checkout-root `.env` as a plain assignment with
optional quotes, then run `./install-clusterfork.sh`. Only a non-whitespace value
in that file selects shared-key mode; ambient variables do not. Installation copies
it to central `~/.config/clusterfork/.env` and renders all shipped Linear entries:

| Client | Shared-key configuration |
| --- | --- |
| Codex | `bearer_token_env_var = "LINEAR_API_KEY"` |
| Antigravity, Command Code | Expanded `headers.Authorization` bearer value |
| OpenCode | `headers.Authorization = "Bearer {env:LINEAR_API_KEY}"` |
| Claude plugin, Grok, Qwen | `headers.Authorization = "Bearer ${LINEAR_API_KEY}"` |

Templates contain references only; installed `.env` and expanded MCP files are
`0600`. Codex never receives the key in config. Output excludes credentials;
keep expanded files out of version control/logs. See [Linear bearer auth](https://linear.app/docs/mcp#faq).

After installing or rotating, run `source ~/.config/clusterfork/bash_profile.sh`
and start new sessions. GUI clients using references need the refreshed environment;
Antigravity/Command Code can read expanded headers after MCP reload or restart.
Use optional-server controls above after reinstalling; authentication mode does
not change enablement defaults.

Absent/empty/whitespace keys omit bearer configuration and preserve OAuth caches
and login flows (`codex mcp login linear`, Antigravity MCP authentication). Adding,
removing or rotating a key requires editing checkout `.env` and reinstalling;
exporting it alone or editing installed `.env` leaves rendered headers unchanged.
To return to OAuth, remove/empty the checkout value, reinstall, and
`unset LINEAR_API_KEY` before reloading the profile. Controller OAuth/app identity
is independent; see [remote setup](remote-development-setup.md#local-configuration).

For an authorized smoke check, read the same known issue in Codex and Antigravity
and confirm the intended workspace/team. Repeat after rotation before retiring
the old key. Installer tests use fake credentials; they do not prove live access.
