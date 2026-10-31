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

## Development

- [Conventions and validation](conventions.md) — repository ownership and local checks
- [Scripts](scripts.md) — rotation, auth conversion, gateway probes, and hook trust
- [Skills](skills.md) — installation, discovery, invocation, and skill catalog
- [Dashboard](dashboard.md) — codeview commands, views, caches, and shipped tabs
- [Codeview modules](codeview-modules.md) — drop-in API, frames, theme, and reload behavior

## Remote development

- [Controller implementation](remote-development-controller.md) — SQLite ownership, two-job GitHub workflow and signed roles
- [Runtime](remote-development.md) — routing, quota selection, publication, lifecycle, and retries
- [Controller deployment](remote-development-deployments.md) — runtime freshness, draining, holds and direct Linear ingress
- [Issue description images](remote-development-images.md) — visual inputs, download limits, approval binding, and validation
- [Repository queues](remote-development-queue.md) — dependency waits, priority/FIFO admission, slot release, and queue reporting
- [Agent steering](remote-development-steering.md) — addressed Linear comments, authorization, delivery, and acknowledgements
- [Setup](remote-development-setup.md) — project mappings, GitHub App, Azure, Linear and webhook administration
- [Azure infrastructure](azure-infrastructure.md) — Terraform ownership, bootstrap, remote state, credentials and reconciliation
- [Accounts](remote-development-accounts.md) — enrollment, reclaim, leases, and worker credential boundary
- [Diagnostics](remote-development-observability.md) — correlated logs, failure explanations, and bounded Azure monitoring

## Historical investigations

- [Retired repair binding](archive/remote-development-repair-binding.md) — child-issue recovery tool
- Retired Functions coordinator: [runtime](archive/remote-development-functions-runtime.md), [lifecycle](archive/remote-development-functions-lifecycle.md), [queues](archive/remote-development-functions-queue.md), [deployment](archive/remote-development-functions-deployments.md), [diagnostics](archive/remote-development-functions-observability.md)

Dated records establish neither current health nor compatibility; recheck upstream behavior.

- [OpenCode Go catalog](archive/opencode-go-catalog.md) — August 2026 route/tool-loop results
- [OpenCode Go effort](archive/opencode-go-effort.md) — measured controls and findings; [raw high-cap log](archive/opencode-go-effort-highcap-2026-08-07.log)
- [Codex on OpenCode Go](archive/opencode-go-codex.md) — unshipped Responses-provider experiment
- [MCP disable probes](archive/mcp-disable-probes.md) — version-scoped reasons for the shipped controls
- [Codex rewind, 0.147.0](archive/codex-rewind-bug-0147.md) — upstream prompt-ID regression
- [Codex context management, 0.153.x](archive/codex-context-management-0153.md) — deferred experiment and measurement criteria
- [Codex usage-limit bell](archive/codex-usage-limit-bell.md) — completion-hook gap and rejected watcher
- [Command Code resume](archive/cmd-resume-bug.md) — empty-directory failure and reverted fallback
- [Retired integrations](archive/retired-integrations.md) — removed Aerolink and Pioneer gateways
- [Remote live validation](archive/remote-development-validation.md) — 2026-10-28 routing, coding, merge, and lifecycle receipts
- [Controller acceptance, 2026-10-30](archive/controller-acceptance-20261030.md) — sequential checkout fixture
- [Controller cutover receipts](remote-development-controller.md#live-acceptance-2026-10-30) and [resource consolidation](remote-development-controller.md#resource-consolidation-2026-10-31-utc) — runs, PRs and deployment evidence
- [Cursor validation, 2026-10-30](archive/cursor-validation-20261030.md) — retired staged-account probe
- [Webhook administration, 2026-10-31](remote-development-setup.md#url-administration-investigation-kian-938-2026-10-31) — KIAN-938 permission findings

Current topics describe implemented behavior, not a proposed specification.
Keep each fact in one owning topic, each file under about 400 lines, and dated
investigations accessible here. Detailed skill rules stay in their `SKILL.md`.
