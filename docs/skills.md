# Skills

The `skills/` directory contains skills in the shared `SKILL.md` format. The installer copies them to both `~/.qwen/skills/` (Qwen Code) and `~/.grok/skills/` (Grok CLI), since both agents use the same convention.

## commit_and_push

Commits and pushes staged changes to the current branch. Invoked via the `/commit_and_push` slash command in Qwen Code or Grok.

## generate_docs

Regenerates `AGENTS.md` and the `docs/` directory from the current state of the repo. Reads all source files, creates one doc per topic with no repeated information, and writes a slim `AGENTS.md` pointing to `docs/`. Invoked via the `/generate_docs` slash command in Qwen Code or Grok.

## ask-claude

Invokes the Claude Code CLI so the agent can get help from Claude on difficult work. The agent chooses how to use the CLI from task context; the user only runs `/ask-claude`. Scope is general — not code-only.

## ask-codex

Invokes Codex (`codex exec`; currently model `gpt-5.5`, effort `xhigh` — see the skill file for pins) so the agent can get help from Codex on difficult work. The agent chooses new vs resume, sandbox (`read-only` / `workspace-write` / `danger-full-access`), approval (`-a never` for unattended runs), and prompt shape from task context; the user only runs `/ask-codex`. Scope is general — not code-only.

## ask-grok

Invokes Grok headlessly (`grok --prompt-file` / `-p`, with session, sandbox, and permission flags chosen from context) so the agent can get help from Grok on difficult work, including image generation via `image_gen` / `image_edit`. The agent decides how to use the CLI; the user only runs `/ask-grok`. Scope is general — not code-only.
