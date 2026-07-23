# Clusterfork Documentation

- [Installation](installation.md) — running the installer and what it writes
- [Shell Modules](shell-modules.md) — launch wrappers and aliases per agent
- [Agent Configs](agent-configs.md) — per-agent settings files
- [Statusline](statusline.md) — Claude Code and Cursor Agent status lines
- [Auth Rotation](auth-rotation.md) — switching between saved accounts
- [Scripts](scripts.md) — standalone Python utilities
- [Skills](skills.md) — shared skills for Qwen, Grok, Claude, and Codex
- [Conventions](conventions.md) — style, safety, idempotency, and repo-as-source-of-truth
- [aerolink.sh (removed)](aerolink-removed.md) — historical record of the deleted Aerolink proxy launcher

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
