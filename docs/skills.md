# Skills

The `skills/` directory contains skills in the shared `SKILL.md` format. The installer copies them to `~/.qwen/skills/` (Qwen Code), `~/.grok/skills/` (Grok CLI), `~/.claude/skills/` (Claude Code), and `~/.codex/skills/` (Codex). It also installs a normalized copy to `~/.gemini/antigravity-cli/skills/` for Antigravity CLI and compatibility aliases to `~/.config/opencode/skills/` for OpenCode.

For Codex, only non-dot skill directories under `~/.codex/skills/` are replaced. `~/.codex/skills/.system` is left alone (Codex-managed system skills).

OpenCode additionally discovers global Claude-compatible skills from `~/.claude/skills/`, so the native OpenCode directory only contains aliases for source names that OpenCode rejects. OpenCode requires lowercase hyphenated names matching the skill directory; for example, `commit_and_push` and `generate_docs` are installed there as `commit-and-push` and `generate-docs`. OpenCode loads skills through its native `skill` tool. See the [OpenCode skill documentation](https://opencode.ai/docs/skills).

Antigravity CLI discovers global skills from `~/.gemini/antigravity-cli/skills/`. Its normalized copy uses the same hyphenated names and is available to all workspaces. See the [Antigravity CLI skill documentation](https://antigravity.google/docs/cli/plugins).

## commit_and_push

Commits and pushes staged changes to the current branch. Invoked via the `/commit_and_push` slash command in Qwen Code, Grok, Claude Code, or Codex. OpenCode and Antigravity receive the compatible `commit-and-push` skill name.

## generate_docs

Regenerates `AGENTS.md` and the `docs/` directory from the current state of the repo. Reads all source files, creates one doc per topic with no repeated information, and writes a slim `AGENTS.md` with process rules for reading and maintaining `docs/` (not topic detail). Invoked via the `/generate_docs` slash command in Qwen Code, Grok, Claude Code, or Codex. OpenCode and Antigravity receive the compatible `generate-docs` skill name.

Creates a `CLAUDE.md → AGENTS.md` symlink so Claude Code picks up the same instructions. **Do not** add a matching `GEMINI.md` symlink for Google Antigravity: current Antigravity (and Antigravity CLI) reads root `AGENTS.md` natively, in addition to `GEMINI.md` and `.agents/rules/`. Official sources: [changelog](https://antigravity.google/changelog) (“reading rules from AGENTS.md in addition to GEMINI.md”) and [CLI best practices](https://antigravity.google/docs/cli/best-practices) (`GEMINI.md` or `AGENTS.md` at workspace root). Global Antigravity rules still live in `~/.gemini/GEMINI.md`; workspace rule files live under `.agents/rules/`.

## ask-claude

Invokes the Claude Code CLI so the agent can get help from Claude on difficult work. The agent chooses how to use the CLI from task context; the user only runs `/ask-claude`. Scope is general — not code-only.

## ask-codex

Invokes Codex (`codex exec`; currently model `gpt-5.6-sol`, effort `xhigh` — see the skill file for pins) so the agent can get help from Codex on difficult work. The agent chooses new vs resume, sandbox (`read-only` / `workspace-write` / `danger-full-access`), approval (`-c approval_policy=never` for unattended runs; do not put `-a never` after `exec` on current CLI), and prompt shape from task context; the user only runs `/ask-codex`. Scope is general — not code-only.

## ask-grok

Invokes Grok headlessly (`grok --prompt-file` / `-p`, with session, sandbox, and permission flags chosen from context) so the agent can get help from Grok on difficult work, including image generation via `image_gen` / `image_edit`. The agent decides how to use the CLI; the user only runs `/ask-grok`. Scope is general — not code-only.

## ask-cursor

Invokes the Cursor Agent CLI headlessly (`cursor-agent --print`; default model `cursor-grok-4.5-high` — Grok 4.5 High — see the skill file for the pin) so the agent can get help from Cursor on difficult work. The agent chooses new vs continue/resume, mode (`--mode ask` / `--mode plan` read-only vs default action mode), approval (`--yolo` for unattended write/shell work; `--trust` to skip the workspace-trust prompt), and model from task context; the user only runs `/ask-cursor`. Models are listed via `cursor-agent --list-models` (or `cursor-agent models`) and switched with `--model <id>` / bracket overrides. Scope is general — not code-only.

## ask-opencode

Invokes OpenCode headlessly (`opencode run`; default `-m opencode-go/deepseek-v4-flash --variant max` — DeepSeek V4 Flash at `max` effort — see the skill file for the pin) so the agent can get help from OpenCode on difficult work. The agent chooses new vs continue/resume (`-c` / `-s`), permissions (`--auto` auto-approves asks for tool-heavy handoffs but cannot override explicit denies), agent (`plan` for enforced no-shell/no-edit judgment, default `build` for implementation), and model/variant from task context; the user only runs `/ask-opencode`. Prompts describe the underlying task directly, and the skill has a runtime guard against delegating recursively from OpenCode itself. `opencode run` reads the prompt from stdin or positional args. Models and variants are listed via `opencode models [provider] [--verbose]` and switched with `-m <provider/model>` plus `--variant <effort>`. Scope is general — not code-only.

## ask-antigravity

Invokes Antigravity CLI headlessly (`agy --print`; default model `gemini-3.6-flash-high` with `--effort high` — see the skill file for the pin) so the agent can get help from Antigravity on difficult work, including image generation via the built-in `generate_image` tool (Nano Banana 2; args `Prompt`, `ImageName`, optional `ImagePaths` for edits). The agent chooses new vs continue/resume (`--continue` / `--conversation`), mode (`--mode plan` for judgment, `--mode accept-edits` for auto-applied file edits), permissions (`--dangerously-skip-permissions` for unattended tools/images; always detach stdin with `< /dev/null` in print mode), and model from task context; the user only runs `/ask-antigravity`. Workspace is the process cwd (no `--cwd` flag); use `--add-dir` for extras. Models are listed via `agy models`. Scope is general — not code-only.
