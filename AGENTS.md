# AGENTS.md

Clusterfork is a dotfile-style config installer for multiple AI coding agents.

## Rules

- **Keep this repo KISS as fuck: stupid easy to use and boring to maintain. Complexity alone is enough to veto an idea.**
  - **Push back before coding:** If a requested feature introduces disproportionate architectural complexity or state, flag it and propose the simplest viable alternative before writing code.
  - **No legacy support:** Replace old behavior cleanly—never keep fallback paths, shims, or backwards-compatibility layers. If compatibility is actually needed, ask me first.
  - **Prefer stdlib and existing dependencies:** Do not introduce a new package for something solvable in under 50 lines of straightforward code.
  - **Flat and explicit over clever:** Use plain functions, direct control flow, and concrete types. Do not create wrappers, factories, or interfaces that only have one implementation.
  - **No speculative extensibility:** Do not add config flags, plugin hooks, or abstraction layers for hypothetical future use cases.
- **Commit and push every repository change** after running the relevant checks. Apply configuration updates with this repository's installer after pushing.
- **Autonomous execution:** Symphony executes submitted issues autonomously. Resolve implementation choices and fix failures without requiring human review or a Needs attention handoff. Follow the automatic execution policy in [docs/remote-development-queue.md](docs/remote-development-queue.md#autonomous-execution).
- **Issue metadata:** Follow the [Symphony issue metadata rule](docs/remote-development-queue.md#issue-metadata) when creating issues, including follow-ups.
- **Skill scope:** When asked to modify a skill, always mean the skills in this repo (`skills/`) — never the installed/global skills under `~/.claude/skills/`, `~/.config/opencode/skills/`, or similar.

## Documentation (read/write)

- **Read first:** For how this repo works, start at `docs/README.md`, then open only the topic files you need. Do not re-derive behavior from filenames alone when a doc covers it.
- **Local Python and validation:** Read [docs/conventions.md](docs/conventions.md) before running Python commands or checks.
- **Before non-trivial changes:** Check the relevant doc so you match existing patterns.
- **After material repository behavior changes:** Update the **one** topic file under `docs/` that owns that fact. Do not copy the same detail into AGENTS.md, README.md, or multiple docs.
- **Skill documentation boundary:** A skill's own `skills/<name>/SKILL.md` is canonical for its detailed rules and examples. Keep `docs/skills.md` to stable summaries of each skill's purpose, scope, discovery, installation, and invocation. Do not update it for routine instruction wording, component rules, example copy, or reference-asset presentation changes.
- **Regenerate vs patch:** Prefer the `generate_docs` skill when docs are broadly stale or many topics shifted; for small, targeted edits, patch the single topic file (and `docs/README.md` if you add/remove a topic).
- `CLAUDE.md` is a symlink to this file.

Do not put implementation detail in this file — it lives in the owning topic under `docs/` or in the relevant skill file.
