# Clusterfork Documentation

- [Installation](installation.md) — running the installer and what it writes
- [Shell Modules](shell-modules.md) — launch wrappers and aliases per agent
- [Agent Configs](agent-configs.md) — per-agent settings files
- [Notifications](notifications.md) — shared completion bell and private ntfy phone delivery over Tailscale
- [Statusline](statusline.md) — Claude Code and Cursor Agent status lines
- [Dashboard](dashboard.md) — codeview repo introspection dashboard and drop-in modules
- [Auth Rotation](auth-rotation.md) — switching between saved accounts
- [Scripts](scripts.md) — standalone Python utilities
- [Skills](skills.md) — shared skills and per-CLI discovery behavior
- [Conventions](conventions.md) — style, safety, idempotency, and repo-as-source-of-truth
- [OpenCode Go endpoint](opencode-go.md) — what the subscription's three wire formats serve, per-model, the Codex experiment that was not shipped, and the 2026-08-07 deepseek-v4-flash high-cap effort ladders (raw log: [opencode-go-effort-highcap-2026-08-07.log](opencode-go-effort-highcap-2026-08-07.log))
- [Codex rewind bug (0.147.0)](codex-rewind-bug-0147.md) — why Esc-Esc branching fails, why it is not clusterfork, and what fixes it
- [Codex bell vs usage-limit deaths](codex-usage-limit-bell.md) — why the turn-completion bell is silent when the usage limit kills a turn (verified in upstream source), coverage options evaluated, and why usage-limit coverage was not shipped
- [cmd "No conversations found to resume." (investigated, fix reverted)](cmd-resume-bug.md) — why `cmd` kicks you out in session-free dirs, the Command Code CLI mechanics, and the validated-but-unshipped fix
- [aerolink.sh (removed)](aerolink-removed.md) — historical record of the deleted Aerolink proxy launcher
- [Pioneer (removed)](pioneer-removed.md) — historical record of the deleted Pioneer gateway integration

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
