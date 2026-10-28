# MCP disable probes

Historical checks: Qwen Code 0.19.2 in July 2026, Claude Code 2.1.235 and
Codex CLI 0.147.0 in August 2026. These explain the shipped configuration
choices; they are not a claim about every later CLI version. The maintained
roster and controls are in [Agent Configs](../agent-configs.md#mcp-roster).

## Method

An isolated config directory held a minimal stdio MCP server that touched a
marker file on spawn, answered initialization, and advertised one tool. The
checks distinguished whether a process started from whether a tool reached the
model. No production credentials were involved.

## Claude Code

None of these disabled a user-scope `mcpServers` entry globally: per-entry
`disabled`/`enabled`, settings-level `disabledMcpServers`, an omitting
`enabledMcpServers` allowlist, or top-level `disabledMcpServers` in
`~/.claude.json`. Defining the roster in `settings.json` did not load it.
`disabledMcpjsonServers` applied only to project `.mcp.json` servers.

The `/mcp` toggle wrote `projects["<cwd>"].disabledMcpServers` in
`~/.claude.json`. That worked for that directory, but unseen projects still
started user-scope servers. Skills-directory plugins provided a global default:
`enabledPlugins["<name>@skills-dir"] = false` prevented both spawn and tool
exposure; `true` or an absent key enabled them. This is why optional Claude
MCPs ship as plugins.

## Codex

Per-server `enabled = false` in `~/.codex/config.toml` prevented spawning under
`codex exec`; a sibling without the flag started. `codex mcp list` reported
status without connecting to either server. Its JSON form included `enabled`
and `disabled_reason`.

## Qwen Code

Per-entry `disabled: true` was ignored. Session discovery instead checked
exact names in `mcp.excluded`; the installed implementation used
`.includes(serverName)` rather than expanding globs. An excluded probe was
skipped while a probe with only a per-entry disabled flag still started.

`qwen mcp list` connection-tested every `mcpServers` entry, including excluded
ones. Headless sessions honored the exclusion. Recheck these version-specific
behaviors before changing the configuration mechanism.
