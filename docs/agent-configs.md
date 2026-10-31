# Agent configs

`agents/` contains the shipped JSON/TOML settings and Claude MCP plugins.
[Installation](installation.md) owns the destination paths and merge rules.
Agent models are pinned only where the source file specifies one; settings
preserved by the installer are local runtime state.

## Claude Code

`agents/claude.json` pins `claude-opus-4-8`, enables the context7 marketplace
plugin, and disables the three clusterfork skills-directory MCP plugins.
The installer copies the local plugins; it does not download the context7
marketplace plugin.

Other settings: dark fullscreen TUI, hold-to-talk voice, prompt suggestions and
auto-memory off, dangerous-mode permission prompt skipped, agent push
notifications on, and drafted feedback off. It wires the shared
[Stop notifier](notifications.md) and [statusline](statusline.md#claude).

### Skills-directory MCP plugins

Each `agents/claude-plugins/<name>/` has `.claude-plugin/plugin.json` and
`.mcp.json`. Installed under Claude's skills directory, it loads as
`<name>@skills-dir`. The installer rejects missing plugin files or a plugin
not explicitly set to `false` in `agents/claude.json`'s `enabledPlugins`.

Use `/plugin` or change that setting to enable one for the installed profile;
reinstalling restores the repo default. Plugin tools have names such as
`mcp__plugin_<plugin>_<server>__<tool>`, and enabled servers appear under
Built-in MCPs in `/mcp`. The
[disable-mechanism investigation](archive/mcp-disable-probes.md) records why
ordinary user-scope MCP entries were unsuitable for a shipped off-by-default
setting.

## OpenCode

`agents/opencode.json` selects the `build` agent and allows tools by default.
The `plan` agent denies `bash` and `edit`. The `ask-opencode` skill is denied
at the top level to prevent self-delegation. No model/provider is pinned; use
OpenCode's own model and auth flows. The separate
[`occ` launcher](opencode-go.md) configures Claude Code against OpenCode Go.

## Qwen Code

`agents/qwen.json` leaves model/auth selection to Qwen. Usage statistics,
managed auto-memory, auto-dream, auto-skill, and loading phrases are disabled.
MCP servers excluded from session discovery are listed in `mcp.excluded`.

## Grok

`agents/grok.toml` leaves the main model unpinned and sets the fork secondary
model to `grok-4.5`. It ships `tokyonight`, `permission_mode = "always-approve"`,
`yolo = false`, compact mode off, and prompt suggestions off.
Telemetry is off, the privacy banner is acknowledged, internal auto-updates
are enabled, and the xAI Official marketplace source is configured.

The Chrome DevTools marketplace plugin is disabled. Grok's own Stop hook uses
[the shared notifier](notifications.md); `[compat.claude] hooks = false`
prevents a second bell from importing Claude's Stop hook.

## Antigravity

`agents/antigravity.json` sets dark colors and disables telemetry and feedback
surveys without pinning a model. Its initial trusted workspace paths are literal
`/home/kian/...` paths; the installer does not relocate them to another home.
Existing trusted workspaces are preserved as described in
[Installation](installation.md#replacement-and-preservation).

`agents/antigravity-hooks.json` installs the Stop notifier. The event gates and
workspace-name handling belong to [Notifications](notifications.md).
MCP entries live separately in `agents/antigravity-mcp.json`.

## Command Code

`agents/command-code.json` disables telemetry. The separate
`agents/command-code-settings.json` supplies the Stop notifier, and
`agents/command-code-mcp.json` supplies MCP servers.
Model/provider settings are not pinned.

## Codex

`agents/codex.toml` supplies MCP servers and the asynchronous root Stop hook.
It does not pin a model, effort, approval policy, or project trust level.
The installer retains unrelated Codex settings and stamps trust for the
clusterfork Stop hook; see
[Installation](installation.md#replacement-and-preservation).
The shell launcher adds the shared [native statusline](statusline.md#codex).

## Cursor

`agents/cursor-mcp.json` supplies context7 and ElevenLabs. The statusline is
wired separately into Cursor's CLI config; see [Statusline](statusline.md#cursor).

## MCP roster

These are the shipped enablement settings. `on` does not imply the server's
credentials, CLI dependencies, or OAuth login are already available.

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

Context7 uses its remote MCP endpoint except in Cursor, which runs
`pnpx @upstash/context7-mcp`. Chrome DevTools runs
`pnpm dlx chrome-devtools-mcp@latest` against the browser on port 9222;
[the browser helper](shell-modules.md#browser-helper) launches it. Linear and
PixelLab use their remote HTTP MCP endpoints.

`bin/elevenlabs-mcp` loads `ELEVENLABS_API_KEY` from the installed `.env`
(or `CLUSTERFORK_ENV_FILE`) and executes `uvx elevenlabs-mcp`.
It fails if the key is empty. Configs invoke it with `bash -c` so GUI clients
can find it without clusterfork on `PATH`.

### Enabling optional servers

| Agent | Installed configuration control |
| --- | --- |
| Claude Code | `enabledPlugins["<name>@skills-dir"]` or `/plugin` |
| OpenCode, Grok, Codex, Command Code | Per-server `enabled` flag |
| Antigravity | Per-server `disabled` flag, or `agy mcp enable <name>` |
| Qwen Code | Remove the exact name from `mcp.excluded` |
| Cursor | IDE/CLI local state; optional entries are omitted from the template |

Reinstalling restores repo-owned flags. Keep permanent enablement changes in
the templates. For Qwen 0.19.2, `qwen mcp list` connection-tested excluded
servers even though sessions skipped them; see the
[version-scoped probe notes](archive/mcp-disable-probes.md).

### Secret resolution

Cursor, Command Code, and Antigravity MCP placeholders are expanded during
installation. Codex reads `env_http_headers` and `bearer_token_env_var` from
its launch environment. OpenCode uses `{env:NAME}` references. Claude plugins,
Grok, and Qwen keep `${NAME}` placeholders for their clients to resolve.
The shell profile exports the installed `.env`; GUI-launched clients still
need the environment expected by their native expansion mechanism.

### Shared Linear API key

`LINEAR_API_KEY` is optional. Set it in the gitignored checkout-root `.env`
using a plain assignment (optional quotes are supported), then run
`./install-clusterfork.sh`. The installer copies that file to
`~/.config/clusterfork/.env`, which is the central installed secret source.
Use a Linear API key for the workspace and permissions you intend these
clients to access. Linear supports API keys in the
[`Authorization: Bearer` header](https://linear.app/docs/mcp#faq).

At installation, a nonempty, non-whitespace `LINEAR_API_KEY` in that file selects
shared-key authentication for every shipped Linear entry. Ambient environment
variables do not select this mode. Templates contain only secret references;
the installer renders them as follows:

| Client | Shared-key configuration |
| --- | --- |
| Codex | `bearer_token_env_var = "LINEAR_API_KEY"`; [Codex reads the key from its launch environment](https://developers.openai.com/codex/mcp/) |
| Antigravity, Command Code | An installed `headers.Authorization` bearer value, expanded from the installed `.env`; [Antigravity supports custom headers](https://antigravity.google/docs/mcp?tab=cli) |
| OpenCode | `headers.Authorization = "Bearer {env:LINEAR_API_KEY}"`, resolved at launch |
| Claude Linear plugin, Grok, Qwen | `headers.Authorization = "Bearer ${LINEAR_API_KEY}"`, resolved by the client |

The installed `.env` and expanded MCP files have owner-only permissions (`0600`).
Native environment references remain references in installed configuration;
Codex's config never receives the key value. Installer output lists destinations
and server names without printing credentials. Keep expanded configuration out
of version control and shared logs.

Run `source ~/.config/clusterfork/bash_profile.sh` in the launching shell after
installation, and start new client sessions. GUI clients that resolve native
environment references need to inherit the updated environment when restarted.
Antigravity and Command Code can read their expanded headers without inheriting
the key. Reload their MCP configuration or restart them after installation.
Enable Linear where needed using the controls above: Codex still ships on;
Antigravity and the other optional Linear entries still ship off or excluded.
Reinstallation restores those enablement defaults.

If the key is absent, empty, or whitespace-only at installation, the installer
omits the Linear bearer reference/header. Existing OAuth credentials and login
workflows remain usable, including `codex mcp login linear` and Antigravity's
MCP authentication flow. OAuth caches are not changed. Adding or removing the key
requires reinstalling so all clients select the same mode; merely exporting a
key after a keyless install does not enable shared-key authentication.
Controller `LINEAR_OAUTH_CLIENT_ID` / `LINEAR_OAUTH_CLIENT_SECRET` selection and
its app identity remain independent from interactive MCP authentication.

To rotate the key, replace the value in the checkout `.env`, rerun the installer,
reload the shell profile, and restart client sessions or reload MCP configuration
as above. This refreshes Antigravity's and Command Code's expanded headers and
the launch environment for native references. Editing only the installed `.env`
does not refresh expanded headers, and a later installation replaces that file
from the checkout. Rotation needs no separate OAuth login in each application.
To return to OAuth, remove or empty the value in the checkout `.env`, reinstall,
and run `unset LINEAR_API_KEY` before reloading the launching shell profile so
an old exported value does not linger.

For a workspace smoke check, use an already authorized client setup after the
refresh: in both Codex and Antigravity, read the same known Linear issue and
confirm its team/workspace matches the key's intended workspace. Re-enable
Antigravity's Linear entry after reinstalling when necessary. Repeat after
rotation, then retire the old key once the new sessions work. Automated installer
tests use fake credentials to cover both modes, rotation, repeat installs,
preserved client state, and secret-free output; they do not establish live
workspace access.
