# Clusterfork documentation

Use the topic that owns the behavior you are changing. Installation describes
where files go; skill-specific instructions stay in each `skills/<name>/SKILL.md`.

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

- [Controller implementation](remote-development-controller.md) — live single-owner task database, combined GitHub workflow and acceptance evidence
- [Runtime](remote-development.md) — routing, quota selection, publication, lifecycle, and retries
- [Deployment pauses and recovery](remote-development-deployments.md) — worker draining, startup deferral, resume, and status-reporting recovery
- [Issue description images](remote-development-images.md) — visual inputs, download limits, approval binding, and validation
- [Repository queues](remote-development-queue.md) — dependency waits, priority/FIFO admission, slot release, and queue reporting
- [Agent steering](remote-development-steering.md) — addressed Linear comments, authorization, delivery, and acknowledgements
- [Setup](remote-development-setup.md) — project mappings, GitHub App, Azure, Linear, and local validation
- [Azure infrastructure](azure-infrastructure.md) — Terraform ownership, imports, remote state, credentials, and reconciliation
- [Accounts](remote-development-accounts.md) — enrollment, reclaim, leases, and worker credential boundary
- [Diagnostics](remote-development-observability.md) — correlated logs, failure explanations, and bounded Azure monitoring

## Historical investigations

- [Retired repair binding](archive/remote-development-repair-binding.md) — previous child-issue recovery tool

- Retired Functions coordinator: [runtime](archive/remote-development-functions-runtime.md), [lifecycle](archive/remote-development-functions-lifecycle.md), [queues](archive/remote-development-functions-queue.md), [deployment](archive/remote-development-functions-deployments.md), [diagnostics](archive/remote-development-functions-observability.md)

These records are dated evidence and decisions, not current service or client
compatibility claims. Recheck upstream behavior before adopting an old workaround.

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

_Generated from commit `ebc53a9` on 2026-10-28._

## Notes

These docs are AI-generated as an after-the-fact record of how things actually
work — not a spec for how they should work. They describe what was done and
learned, not what was planned. Treat them as accurate to the implementation,
not necessarily correct in a business or design sense. They may drift from
intent over time, so don't assume something is right just because a doc says
it is.

Information in these docs should not be repeated anywhere else. Each topic
lives in exactly one file.

If something was tried but not shipped — an experiment, a dead-end approach,
a feature that was reverted — it should still get its own doc file here. The
goal is a complete record, not just a catalog of what survived.

Keep each doc file under ~400 lines. Agents open only the topic files they
need, so a file too large to open cheaply gets skipped or eats the context
budget. Past that size, split by sub-topic and list each part in the index
above.
