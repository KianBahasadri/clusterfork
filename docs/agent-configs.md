# Agent configs

`agents/` holds shipped settings. `agents/mcp-servers.json` is the only MCP
server list. [Installation](installation.md) owns destinations and merge rules.
Models are pinned only where specified below.

## Claude Code

`claude.json` pins `claude-opus-4-8`. Install fills `enabledPlugins` from the
MCP roster and does not download context7. Settings: dark fullscreen TUI,
hold-to-talk voice, prompt suggestions/auto-memory/drafted feedback off,
dangerous-mode prompt skipped, agent push notifications on, shared
[Stop hook](notifications.md) and [statusline](statusline.md#claude).

### Skills-directory MCP plugins

A disabled roster server other than context7 is written to
`~/.claude/skills/<name>/` with `.claude-plugin/plugin.json` and `.mcp.json`.
The skills copy clears that directory first; plugin generation runs after it.
Installed names are `<name>@skills-dir`. `enabledPlugins` sets each one to
false, which is what keeps the plugin from starting. Tools look like
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
workspaces without relocating paths. `antigravity-hooks.json` supplies Stop. The MCP roster supplies MCP entries.

## Command Code

`command-code.json` disables telemetry; `command-code-settings.json` supplies Stop.
The MCP roster supplies MCP. Model/provider remain unpinned.

## Codex

`codex.toml` supplies asynchronous root Stop, without model, effort, approval
or project-trust pins. The MCP roster supplies MCP. Installation merges settings
and stamps hook trust; launchers add the [native statusline](statusline.md#codex).

## Cursor

`cursor-hooks.json` supplies Stop. The MCP roster supplies Cursor's enabled
servers. CLI [statusline](statusline.md#cursor) wiring is separate.

## MCP roster

`agents/mcp-servers.json` lists every server once: a name, one `enabled` flag,
and either a remote `url` or a `command`. Install reads that file and writes
every agent. Add a server there and reinstall.

`on` still requires credentials, dependencies and any OAuth login.

| Server | Enabled | Reach |
| --- | --- | --- |
| context7 | on | `https://mcp.context7.com/mcp` |
| ElevenLabs | on | `bin/elevenlabs-mcp` |
| Linear | on | `https://mcp.linear.app/mcp` |
| Chrome DevTools | off | `pnpm dlx chrome-devtools-mcp@latest` on port 9222 |
| PixelLab | off | `https://api.pixellab.ai/mcp` |

Chrome DevTools uses the port-9222 [browser helper](shell-modules.md#browser-helper).
`bin/elevenlabs-mcp` loads `ELEVENLABS_API_KEY` from installed `.env` (override:
`CLUSTERFORK_ENV_FILE`), rejects an empty key and executes `uvx elevenlabs-mcp`.
Configs use `bash -c` so GUI clients need no clusterfork `PATH`. Grok and Qwen
also send `Accept: application/json, text/event-stream` on context7; that header
is on the context7 entry for those two agents.

### How each agent records the flag

The flag is the same value for every agent. The file shape differs because each
CLI looks in a different place.

| Agent | Installed control |
| --- | --- |
| Claude Code | Context7 is its marketplace plugin. Other enabled servers are user MCP entries in `~/.claude.json`. A disabled server is a skills-directory plugin with `enabledPlugins["<name>@skills-dir"]` false |
| OpenCode, Grok, Codex, Command Code | Per-server `enabled` |
| Antigravity | Per-server `disabled`, or `agy mcp enable <name>` until the next install |
| Qwen Code | Disabled names in `mcp.excluded` |
| Cursor | Disabled servers are omitted. Cursor has no off switch in the config file |

Reinstalling rewrites these from the roster. A local toggle lasts until the
next install. Qwen 0.19.2's list command tested excluded servers while sessions
skipped them; see [probe notes](archive/mcp-disable-probes.md).

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

The roster contains references only; installed `.env` and expanded MCP files are
`0600`. Codex never receives the key in config. Output excludes credentials;
keep expanded files out of version control/logs. See [Linear bearer auth](https://linear.app/docs/mcp#faq).

After installing or rotating, run `source ~/.config/clusterfork/bash_profile.sh`
and start new sessions. GUI clients using references need the refreshed environment;
Antigravity/Command Code can read expanded headers after MCP reload or restart.
Reinstalling restores the roster flag. Authentication mode does not change it.

Absent/empty/whitespace keys omit bearer configuration and preserve OAuth caches
and login flows (`codex mcp login linear`, Antigravity MCP authentication). Adding,
removing or rotating a key requires editing checkout `.env` and reinstalling;
exporting it alone or editing installed `.env` leaves rendered headers unchanged.
To return to OAuth, remove/empty the checkout value, reinstall, and
`unset LINEAR_API_KEY` before reloading the profile.

For an authorized smoke check, read the same known issue in Codex and Antigravity
and confirm the intended workspace/team. Repeat after rotation before retiring
the old key. Installer tests use fake credentials; they do not prove live access.
