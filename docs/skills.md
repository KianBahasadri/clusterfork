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
`agents/claude-plugins/` also installs directories into Claude's skills tree;
those are [MCP plugins](agent-configs.md#skills-directory-mcp-plugins), not
shared `SKILL.md` skills.

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
| [setup-remote-development](../skills/setup-remote-development/SKILL.md) | Enrolls a GitHub repository and an explicitly selected Linear project in the shared remote development system; `/setup-remote-development` |
| [design-guide](../skills/design-guide/SKILL.md) | Exact interface specifications and runnable component/dashboard references for building or reviewing UI |
| [conservative-academic-prose-sweep](../skills/conservative-academic-prose-sweep/SKILL.md) | Removes embellishment and unsupported claims from technical or academic prose while preserving meaning and limitations |
| [ask-claude](../skills/ask-claude/SKILL.md) | Gets headless help from Claude on difficult work; `/ask-claude` |
| [ask-codex](../skills/ask-codex/SKILL.md) | Gets headless help from Codex on difficult work; `/ask-codex` |
| [ask-cursor](../skills/ask-cursor/SKILL.md) | Gets headless help from Cursor Agent on difficult work; `/ask-cursor` |
| [ask-grok](../skills/ask-grok/SKILL.md) | Gets headless help from Grok, including image work; `/ask-grok` |
| [ask-opencode](../skills/ask-opencode/SKILL.md) | Gets headless help from OpenCode with a self-delegation guard; `/ask-opencode` |
| [ask-antigravity](../skills/ask-antigravity/SKILL.md) | Gets headless help from Antigravity, including image work; `/ask-antigravity` |
| [improve-codebase-architecture](../skills/improve-codebase-architecture/SKILL.md) | Presents module-deepening candidates in an HTML report, then interviews through a choice; explicit `/improve-codebase-architecture` |
| [codebase-design](../skills/codebase-design/SKILL.md) | Vocabulary and principles for deep modules, interfaces, seams, and testability; selected during design work |
| [domain-modeling](../skills/domain-modeling/SKILL.md) | Sharpens terminology and maintains `CONTEXT.md` and ADRs during design |
| [grilling](../skills/grilling/SKILL.md) | Reusable interview loop for stress-testing plans and decisions |
| [decide](../skills/decide/SKILL.md) | Forms and explains an independent recommendation when asked to decide |

The architecture, codebase-design, domain-modeling, and grilling skills retain
provenance links to [mattpocock/skills](https://github.com/mattpocock/skills) in
their own files. `generate_docs` maintains the `CLAUDE.md → AGENTS.md` symlink;
Antigravity reads root `AGENTS.md`, so no matching `GEMINI.md` link is installed.
