# Clusterfork documentation

Use the topic that owns the behavior you are changing. Installation describes
where files go; skill-specific instructions stay in each `skills/<name>/SKILL.md`.

Complete documentation before this prune: parent commit [`f5642f9103c7772775231612b0e0a8cceff08fc4`](https://github.com/KianBahasadri/clusterfork/tree/f5642f9103c7772775231612b0e0a8cceff08fc4/docs).

## Setup and daily use

- [Installation](installation.md) — requirements, file mapping, and preservation rules
- [Shell modules](shell-modules.md) — agent launch commands, tmux, and browser helper
- [Agent configs](agent-configs.md) — shipped settings, MCP roster, and enablement
- [Auth rotation](auth-rotation.md) — saved profiles, kickoff, repair, and container sharing
- [Statusline](statusline.md) — Claude/Cursor usage caches and shared native Codex layout
- [Notifications](notifications.md) — Stop events, switches, tests, and trigger history
- [Notification relay](notifications-relay.md) — devbox-to-host socket and user service
- [Private ntfy](notifications-ntfy.md) — Tailnet phone service setup and troubleshooting
- [OpenCode Go](opencode-go.md) — shipped `occ` profile, overrides, and model discovery
- [Hex dictation](hex.md) — concise rewriting, conversation context, vocabulary, and optional microphone profile

## Development

- [Conventions and validation](conventions.md) — repository ownership and local checks
- [Scripts](scripts.md) — rotation, auth conversion, gateway probes, and hook trust
- [Skills](skills.md) — installation, discovery, invocation, and skill catalog
- [Dashboard](dashboard.md) — codeview commands, views, caches, and shipped tabs
- [Codeview modules](codeview-modules.md) — drop-in API, frames, theme, and reload behavior
- [Symphony](symphony.md) — Antigravity coordinator, 2-tier Flash-to-Hydrogen queue, and Guaranine deployment
- [Symphony follow-ups](symphony-follow-ups.md) — work deferred from the MVP

## Historical investigations


Dated records establish neither current health nor compatibility; recheck upstream behavior.

- [OpenCode Go catalog](archive/opencode-go-catalog.md) — August 2026 route/tool-loop results
- [OpenCode Go effort](archive/opencode-go-effort.md) — measured controls and findings; [raw high-cap log](archive/opencode-go-effort-highcap-2026-08-07.log)
- [Codex on OpenCode Go](archive/opencode-go-codex.md) — unshipped Responses-provider experiment
- [MCP disable probes](archive/mcp-disable-probes.md) — version-scoped reasons for the shipped controls
- [Codex rewind, 0.147.0](archive/codex-rewind-bug-0147.md) — upstream prompt-ID regression
- [Codex context management, 0.153.x](archive/codex-context-management-0153.md) — deferred experiment and measurement criteria
- [Codex account-scoped history, 0.169.0](archive/codex-account-scoped-history.md) — disappearing-session diagnosis, custom-build attempt and why it was abandoned
- [Codex usage-limit bell](archive/codex-usage-limit-bell.md) — completion-hook gap and rejected watcher
- [Command Code resume](archive/cmd-resume-bug.md) — empty-directory failure and reverted fallback
- [Hex rewrite effort, Nov 2026](archive/hex-rewrite-effort-2026-11.md) — DeepSeek Flash vs. Haiku latency and adaptive `low`-vs-`medium` prompt experiments
- [Retired integrations](archive/retired-integrations.md) — removed Aerolink and Pioneer gateways

Current topics describe implemented behavior, not a proposed specification.
Keep each fact in one owning topic, each file under about 400 lines, and dated
investigations accessible here. Detailed skill rules stay in their `SKILL.md`.
