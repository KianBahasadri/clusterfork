# Skills

The `skills/` directory contains skills in the shared `SKILL.md` format. The installer copies them to both `~/.qwen/skills/` (Qwen Code) and `~/.grok/skills/` (Grok CLI), since both agents use the same convention.

## commit_and_push

Commits and pushes staged changes to the current branch. Invoked via the `/commit_and_push` slash command in Qwen Code or Grok.

## generate_docs

Regenerates `AGENTS.md` and the `docs/` directory from the current state of the repo. Reads all source files, creates one doc per topic with no repeated information, and writes a slim `AGENTS.md` pointing to `docs/`. Invoked via the `/generate_docs` slash command in Qwen Code or Grok.
