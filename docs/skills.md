# Skills

The `skills/` directory contains skills in the shared `SKILL.md` format. The installer copies them to `~/.qwen/skills/` (Qwen Code), `~/.grok/skills/` (Grok CLI), `~/.claude/skills/` (Claude Code), and `~/.codex/skills/` (Codex). It also installs normalized copies to `~/.commandcode/skills/` for Command Code and `~/.gemini/antigravity-cli/skills/` for Antigravity CLI, plus compatibility aliases to `~/.config/opencode/skills/` for OpenCode.

Command Code requires lowercase hyphenated skill IDs, so `commit_and_push` and `generate_docs` are installed there as `commit-and-push` and `generate-docs`.

For Codex, only non-dot skill directories under `~/.codex/skills/` are replaced. `~/.codex/skills/.system` is left alone (Codex-managed system skills).

OpenCode additionally discovers global Claude-compatible skills from `~/.claude/skills/`, so the native OpenCode directory only contains aliases for source names that OpenCode rejects. OpenCode requires lowercase hyphenated names matching the skill directory; for example, `commit_and_push` and `generate_docs` are installed there as `commit-and-push` and `generate-docs`. OpenCode loads skills through its native `skill` tool. See the [OpenCode skill documentation](https://opencode.ai/docs/skills).

Antigravity CLI discovers global skills from `~/.gemini/antigravity-cli/skills/`. Its normalized copy uses the same hyphenated names and is available to all workspaces. See the [Antigravity CLI skill documentation](https://antigravity.google/docs/cli/plugins).

## commit_and_push

Commits and pushes staged changes to the current branch. Invoked via the `/commit_and_push` slash command in Qwen Code, Grok, Claude Code, or Codex. OpenCode and Antigravity receive the compatible `commit-and-push` skill name.

## create-github-action-tests

Backfills characterization tests and a hardened GitHub Actions workflow on a public repo using standard hosted runners (Actions minutes are free there). Auto-rejects billed or unknown CI until the user explicitly approves. Invoked via `/create-github-action-tests`.

## generate_docs

Regenerates `AGENTS.md` and the `docs/` directory from the current state of the repo. Reads all source files, creates one doc per topic with no repeated information, and writes a slim `AGENTS.md` with process rules for reading and maintaining `docs/` (not topic detail). Invoked via the `/generate_docs` slash command in Qwen Code, Grok, Claude Code, or Codex. OpenCode and Antigravity receive the compatible `generate-docs` skill name.

Creates a `CLAUDE.md → AGENTS.md` symlink so Claude Code picks up the same instructions. **Do not** add a matching `GEMINI.md` symlink for Google Antigravity: current Antigravity (and Antigravity CLI) reads root `AGENTS.md` natively, in addition to `GEMINI.md` and `.agents/rules/`. Official sources: [changelog](https://antigravity.google/changelog) (“reading rules from AGENTS.md in addition to GEMINI.md”) and [CLI best practices](https://antigravity.google/docs/cli/best-practices) (`GEMINI.md` or `AGENTS.md` at workspace root). Global Antigravity rules still live in `~/.gemini/GEMINI.md`; workspace rule files live under `.agents/rules/`.

## design-guide

Provides concrete component specifications, design tokens, and core design principles for building clean, accessible, high-craft GUIs across web, desktop, and mobile. The entrypoint pairs a compact philosophy section (truthful state, quiet nominal defaults, platform conventions, and WCAG non-color accessibility) with explicit, actionable rules for buttons, dropdowns, form inputs, selection controls, labels/badges, data tables, SVG charts, modals, tooltips/popovers, navigation tabs, cards, and alerts. Dropdowns use searchable custom comboboxes with synchronized values, explicit list-only or free-entry behavior, accessible keyboard interaction, compact option density, text-weight selection cues without checkmark gutters, and content-fit sizing that avoids excess trailing space while preferring wraps at spaces. Icon-only actions use transparent, borderless, shadowless hit targets with icon-tone feedback and a visible keyboard focus ring. Active side-navigation rows use stronger text and a neutral surface without a redundant leading stripe. Field help appears only for non-obvious constraints, consequences, or recovery—not to restate a label or value—and inline field errors use direct corrective text without a redundant leading icon or trailing period on short one-line messages. Status badges use explicit text and severity-shaped containers without redundant leading indicator dots. Slow and fast flashing variants animate the badge treatment continuously while their live condition remains active, stay below three flashes per second, and become static when reduced motion is requested. Status geometry sharpens with severity as a redundant cue: green is rounded, yellow uses intermediate-radius corners and markers, and red is square or angular. Page sections are distinguished with clear headings and whitespace, never borders or background colors added only to separate them. Component-catalog sections use one compact numbered heading without decorative separators, then render a visually identifiable example without a redundant title, description, heading rule, self-evident taxonomy, or generic label above an already clear navigation list. `skills/design-guide/assets/component-reference.html` provides a self-contained, runnable interactive catalog demonstrating every component in dark and light modes.

## ask-claude

Invokes the Claude Code CLI so the agent can get help from Claude on difficult work. The agent chooses how to use the CLI from task context; the user only runs `/ask-claude`. Scope is general — not code-only.

## ask-codex

Invokes Codex (`codex exec`; currently model `gpt-5.6-sol`, effort `xhigh` — see the skill file for pins) so the agent can get help from Codex on difficult work. The agent chooses new vs resume, sandbox (`read-only` / `workspace-write` / `danger-full-access`), approval (`-c approval_policy=never` for unattended runs; do not put `-a never` after `exec` on current CLI), and prompt shape from task context; the user only runs `/ask-codex`. Scope is general — not code-only.

## ask-grok

Invokes Grok headlessly (`grok --prompt-file` / `-p`, with session, sandbox, and permission flags chosen from context) so the agent can get help from Grok on difficult work, including image generation via `image_gen` / `image_edit`. The agent decides how to use the CLI; the user only runs `/ask-grok`. Scope is general — not code-only.

## ask-cursor

Invokes the Cursor Agent CLI headlessly (`cursor-agent --print`; default model `cursor-grok-4.5-high` — Grok 4.5 High — see the skill file for the pin) so the agent can get help from Cursor on difficult work. The agent chooses new vs continue/resume, mode (`--mode ask` / `--mode plan` read-only vs default action mode), approval (`--yolo` for unattended write/shell work; `--trust` to skip the workspace-trust prompt), and model from task context; the user only runs `/ask-cursor`. Models are listed via `cursor-agent --list-models` (or `cursor-agent models`) and switched with `--model <id>` / bracket overrides. Scope is general — not code-only.

## ask-opencode

Invokes OpenCode headlessly (`opencode run`; default `-m opencode-go/deepseek-v4-pro --variant max` — DeepSeek V4 Pro at `max` effort — see the skill file for the pin) so the agent can get help from OpenCode on difficult work. The agent chooses new vs continue/resume (`-c` / `-s`), permissions (`--auto` auto-approves asks for tool-heavy handoffs but cannot override explicit denies), agent (`plan` for enforced no-shell/no-edit judgment, default `build` for implementation), and model/variant from task context; the user only runs `/ask-opencode`. Prompts describe the underlying task directly, and the skill has a runtime guard against delegating recursively from OpenCode itself. `opencode run` reads the prompt from stdin or positional args. Models and variants are listed via `opencode models [provider] [--verbose]` and switched with `-m <provider/model>` plus `--variant <effort>`. Scope is general — not code-only.

## ask-antigravity

Invokes Antigravity CLI headlessly (`agy --print`; default model `gemini-3.6-flash-high` with `--effort high` — see the skill file for the pin) so the agent can get help from Antigravity on difficult work, including image generation via the built-in `generate_image` tool (Nano Banana 2; args `Prompt`, `ImageName`, optional `ImagePaths` for edits). The agent chooses new vs continue/resume (`--continue` / `--conversation`), mode (`--mode plan` for judgment, `--mode accept-edits` for auto-applied file edits), permissions (`--dangerously-skip-permissions` for unattended tools/images; always detach stdin with `< /dev/null` in print mode), and model from task context; the user only runs `/ask-antigravity`. Workspace is the process cwd (no `--cwd` flag); use `--add-dir` for extras. Models are listed via `agy models`. Scope is general — not code-only.

## improve-codebase-architecture

Scans the codebase for deepening opportunities (shallow modules, leaky seams, poor locality/leverage), presents candidates as a self-contained HTML report (Tailwind + Mermaid) in the OS temp dir, then grills the user on the chosen candidate. Sourced from [mattpocock/skills](https://github.com/mattpocock/skills) (MIT). Invoked via `/improve-codebase-architecture`. Depends on `codebase-design` (vocabulary), `grilling` (interview loop), and `domain-modeling` (CONTEXT.md/ADR updates).

## codebase-design

Shared vocabulary and principles for designing deep modules (module, interface, depth, seam, adapter, leverage, locality) including the deletion test and seam discipline. Model-invoked; also used directly when designing interfaces or deepening modules. Sourced from [mattpocock/skills](https://github.com/mattpocock/skills). Companion docs: `DEEPENING.md` and `DESIGN-IT-TWICE.md`.

## domain-modeling

Actively builds and sharpens the project's domain model during design — challenging terms against `CONTEXT.md`, stress-testing with edge-case scenarios, and updating `CONTEXT.md`/`docs/adr/` inline. Model-invoked when terminology or decisions are being shaped, not just read. Sourced from [mattpocock/skills](https://github.com/mattpocock/skills). Companion docs: `CONTEXT-FORMAT.md` and `ADR-FORMAT.md`.

## decide

Behavioral skill for decision requests: form an independent conclusion before considering the user's implied preference, trust its own capabilities as a genius state-of-the-art model, and own the call with honest reasoning. Model-invoked via "decide", "what should I do", or expressions of trust in the model's judgment.

## grilling

Reusable interview primitive: maps decisions as a design tree, asks the whole frontier each round with a recommended answer, and recomputes after each answer until the frontier is empty. Model-invoked by `improve-codebase-architecture` and other skills. Sourced from [mattpocock/skills](https://github.com/mattpocock/skills) (`skills/productivity/grilling`).
