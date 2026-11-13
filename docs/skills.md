# Skills

Detailed instructions and examples are canonical in each repository
`skills/<name>/SKILL.md`. This page covers discovery and purpose; model pins,
CLI flags, component rules, and templates stay in the skill that uses them.

## Installation and discovery

| Agent | Shared skills location | Source naming |
| --- | --- | --- |
| Claude Code | `~/.claude/skills/` | Unchanged |
| Codex | `~/.codex/skills/` | Unchanged; installer preserves `.system` and other dot entries |
| Qwen Code | `~/.qwen/skills/` | Unchanged |
| Grok | `~/.grok/skills/` | Unchanged |
| Command Code | `~/.commandcode/skills/` | Underscores normalized to hyphens |
| Antigravity CLI | `~/.gemini/antigravity-cli/skills/` | Underscores normalized to hyphens |
| OpenCode | Discovers `~/.claude/skills/`; compatibility aliases in `~/.config/opencode/skills/` | Aliases only for incompatible underscore names |

Thus `commit_and_push` and `generate_docs` retain their source names in the
first four agents and become `commit-and-push` and `generate-docs` in the
normalized copies. OpenCode requires a lowercase hyphenated skill name matching
its directory and loads skills with its native `skill` tool. See the
[OpenCode discovery reference](https://opencode.ai/docs/skills).

Work on this repository's `skills/`, then reinstall to distribute edits.
Disabled definitions are kept in `disabled-skills/`, outside the installer's
source tree and the dashboard's active inventory. Move a definition's directory
back into `skills/` and reinstall to enable it again.
`agents/claude-plugins/` also installs directories into Claude's skills tree;
those are [MCP plugins](agent-configs.md#skills-directory-mcp-plugins), not
shared `SKILL.md` skills.

## Upstream skills

`code-simplifier` follows Anthropic's official
[`claude-plugins-official` prompt](https://github.com/anthropics/claude-plugins-official/blob/main/plugins/code-simplifier/agents/code-simplifier.md).
`deslop` follows Cursor's
[`cursor-team-kit` skill](https://github.com/cursor/plugins/blob/main/cursor-team-kit/skills/deslop/SKILL.md).
Sources and their tracked branches are declared in `scripts/upstream_skills.json`.

The **Sync upstream skills** GitHub Actions workflow runs daily at 08:23 UTC and
can be dispatched manually from the default branch. It fetches each source's
latest `main` commit, then downloads the prompt and license at that immutable
revision. Each skill carries its license and `UPSTREAM.json` provenance/hashes.
Cursor's `SKILL.md` is copied byte-for-byte. Anthropic's agent becomes a shared
skill with its Claude-only `model` metadata removed and a generated-file notice;
its body is unchanged, and `UPSTREAM.md` retains the original file.

The workflow updates one `automation/sync-upstream-skills` PR and enables squash
auto-merge after the existing required checks pass. It uses `GITHUB_TOKEN` with
Contents, Pull requests and Actions write permissions; the repository must allow
GitHub Actions to create pull requests and allow auto-merge. Since token-authored
pushes do not trigger push workflows, it explicitly dispatches `test.yml` on the
update branch. Only the two generated skill directories are included in the PR.
The workflow must reach the default branch before its schedule can run.

Unchanged prompt/license content produces no update, even if upstream `main`
advanced for other reasons. Fetch or validation failures leave the existing
snapshots intact. Skills moved to `disabled-skills/` remain disabled. Installers
use the checked-in snapshots without network access; pull and reinstall to
distribute merged updates to local agents.

For a manual refresh, run `scripts/sync_upstream_skills.py` using the local Python
prefix in [Conventions](conventions.md#local-validation). Add `--check` to verify
the snapshots, hashes and adaptation offline; CI runs that check and the offline
sync tests. Edit the adapter or manifest rather than the generated prompts.

## Catalog

Invoke an explicit skill with `/<name>` where the CLI supports slash-command
skill invocation, using the installed name above. Natural-language requests
can also select skills according to their descriptions and client support.
The links below open the complete rules, including any approval requirements.

| Skill | Purpose and invocation |
| --- | --- |
| [commit_and_push](../skills/commit_and_push/SKILL.md) | Commits and pushes staged changes to the current branch; `/commit_and_push` |
| [generate_docs](../skills/generate_docs/SKILL.md) | Regenerates repo documentation and slim agent instructions from source; `/generate_docs` |
| [create-github-action-tests](../skills/create-github-action-tests/SKILL.md) | Backfills characterization tests and hosted-runner CI after checking its billing scope; `/create-github-action-tests` |
| [add-codeview-module](../skills/add-codeview-module/SKILL.md) | Adds a project-specific dashboard tab under `.codeview/modules/`; `/add-codeview-module` |
| [setup-clusterfork-symphony-remote-development](../skills/setup-clusterfork-symphony-remote-development/SKILL.md) | Enrolls a GitHub repository and an explicitly selected Linear project in Clusterfork Symphony's shared remote development system; `/setup-clusterfork-symphony-remote-development` |
| [design-guide](../skills/design-guide/SKILL.md) | Exact interface specifications and runnable component/dashboard references for building or reviewing UI |
| [conservative-academic-prose-sweep](../skills/conservative-academic-prose-sweep/SKILL.md) | Removes embellishment and unsupported claims from technical or academic prose while preserving meaning and limitations |
| [ask-codex](../skills/ask-codex/SKILL.md) | Gets headless help from Codex on difficult work; `/ask-codex` |
| [ask-cursor](../skills/ask-cursor/SKILL.md) | Gets headless help from Cursor Agent on difficult work; `/ask-cursor` |
| [ask-antigravity](../skills/ask-antigravity/SKILL.md) | Gets headless help from Antigravity, including image work; `/ask-antigravity` |
| [decide](../skills/decide/SKILL.md) | Forms and explains an independent recommendation when asked to decide |
| [code-simplifier](../skills/code-simplifier/SKILL.md) | Anthropic's upstream prompt for refining recently changed code while preserving behavior; `/code-simplifier` |
| [deslop](../skills/deslop/SKILL.md) | Cursor's upstream skill for removing generated code clutter from the branch diff; `/deslop` |

Disabled skills:

| Skill | Purpose |
| --- | --- |
| [ask-claude](../disabled-skills/ask-claude/SKILL.md) | Gets headless help from Claude on difficult work |
| [ask-grok](../disabled-skills/ask-grok/SKILL.md) | Gets headless help from Grok, including image work |
| [ask-opencode](../disabled-skills/ask-opencode/SKILL.md) | Gets headless help from OpenCode with a self-delegation guard |
| [codebase-design](../disabled-skills/codebase-design/SKILL.md) | Vocabulary and principles for deep modules, interfaces, seams, and testability |
| [domain-modeling](../disabled-skills/domain-modeling/SKILL.md) | Sharpens terminology and maintains `CONTEXT.md` and ADRs during design |
| [grilling](../disabled-skills/grilling/SKILL.md) | Reusable interview loop for stress-testing plans and decisions |
| [improve-codebase-architecture](../disabled-skills/improve-codebase-architecture/SKILL.md) | Presents module-deepening candidates in an HTML report, then interviews through a choice |

The disabled improve-codebase-architecture, codebase-design, domain-modeling,
and grilling skills retain provenance links to [mattpocock/skills](https://github.com/mattpocock/skills) in
their own files. `generate_docs` maintains the `CLAUDE.md → AGENTS.md` symlink;
Antigravity reads root `AGENTS.md`, so no matching `GEMINI.md` link is installed.
