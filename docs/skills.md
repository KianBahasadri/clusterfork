# Skills

The `skills/` directory contains skills in the shared `SKILL.md` format. The installer copies them to `~/.qwen/skills/` (Qwen Code), `~/.grok/skills/` (Grok CLI), `~/.claude/skills/` (Claude Code), and `~/.codex/skills/` (Codex). All four agents use the same convention.

For Codex, only non-dot skill directories under `~/.codex/skills/` are replaced. `~/.codex/skills/.system` is left alone (Codex-managed system skills).

## commit_and_push

Commits and pushes staged changes to the current branch. Invoked via the `/commit_and_push` slash command in Qwen Code, Grok, Claude Code, or Codex.

## generate_docs

Regenerates `AGENTS.md` and the `docs/` directory from the current state of the repo. Reads all source files, creates one doc per topic with no repeated information, and writes a slim `AGENTS.md` with process rules for reading and maintaining `docs/` (not topic detail). Invoked via the `/generate_docs` slash command in Qwen Code, Grok, Claude Code, or Codex.

Creates a `CLAUDE.md → AGENTS.md` symlink so Claude Code picks up the same instructions. **Do not** add a matching `GEMINI.md` symlink for Google Antigravity: current Antigravity (and Antigravity CLI) reads root `AGENTS.md` natively, in addition to `GEMINI.md` and `.agents/rules/`. Official sources: [changelog](https://antigravity.google/changelog) (“reading rules from AGENTS.md in addition to GEMINI.md”) and [CLI best practices](https://antigravity.google/docs/cli/best-practices) (`GEMINI.md` or `AGENTS.md` at workspace root). Global Antigravity rules still live in `~/.gemini/GEMINI.md`; workspace rule files live under `.agents/rules/`.

## ask-claude

Invokes the Claude Code CLI so the agent can get help from Claude on difficult work. The agent chooses how to use the CLI from task context; the user only runs `/ask-claude`. Scope is general — not code-only.

## ask-codex

Invokes Codex (`codex exec`; currently model `gpt-5.5`, effort `xhigh` — see the skill file for pins) so the agent can get help from Codex on difficult work. The agent chooses new vs resume, sandbox (`read-only` / `workspace-write` / `danger-full-access`), approval (`-a never` for unattended runs), and prompt shape from task context; the user only runs `/ask-codex`. Scope is general — not code-only.

## ask-grok

Invokes Grok headlessly (`grok --prompt-file` / `-p`, with session, sandbox, and permission flags chosen from context) so the agent can get help from Grok on difficult work, including image generation via `image_gen` / `image_edit`. The agent decides how to use the CLI; the user only runs `/ask-grok`. Scope is general — not code-only.
