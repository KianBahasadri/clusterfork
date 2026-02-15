# Clusterfork

Orchestrated agent workflow for AI-assisted software development in [OpenCode](https://opencode.ai).

## How it works

An **orchestrator** agent (high-capability model) handles planning and coordination with the user. It delegates execution to four specialized **mini workers** (cost-efficient models) through OpenCode's Task tool.

| Agent | Model | Role |
|---|---|---|
| `orchestrator` | `openai/gpt-5.3-codex` | Plans, delegates, reviews results |
| `mini-implementer` | `azure/gpt-5-mini` | Writes and edits code |
| `mini-tester` | `azure/gpt-5-mini` | Runs tests, builds, lints |
| `mini-ui-builder` | `azure/gpt-5-mini` | Frontend components and styling |
| `mini-researcher` | `azure/gpt-5-mini` | Codebase and web research |

The orchestrator has its own tools disabled (no write, edit, bash, webfetch) so it's forced to delegate. Workers have tool access scoped to their role.

## Repo structure

```
opencode.json              # Sets orchestrator as default agent, Context7 MCP
.opencode/
  agents/
    orchestrator.md        # Primary agent - plans and delegates
    mini-implementer.md    # Subagent - code changes
    mini-tester.md         # Subagent - verification
    mini-ui-builder.md     # Subagent - frontend work
    mini-researcher.md     # Subagent - research
tools/
  browser-tools/           # Chromium automation scripts (source)
    browser-start.js       # Launch Chromium with remote debugging
    browser-nav.js         # Navigate tabs to URLs
    browser-eval.js        # Execute JS in page context
    browser-screenshot.js  # Capture viewport screenshots
    SKILL.md               # OpenCode skill definition
    package.json           # Dependencies (puppeteer-core)
scripts/
  install-opencode-tools.sh   # Install tools globally
  uninstall-opencode-tools.sh # Remove global install
research/
  literature review/       # Paper and framework analyses
  proposals/               # Original design documents (001-007)
```

## Usage

Clone this repo and run `opencode` from the root. OpenCode picks up `opencode.json` and `.opencode/agents/` automatically.

To use these agents in a different project, copy `opencode.json` and `.opencode/` into that project's root.

## Global tools setup

The browser tools are installed globally so OpenCode agents can use them from any project. The install script copies scripts to `~/.local/share/opencode-tools/browser-tools/`, symlinks them into `~/.local/bin/`, and registers a SKILL.md so agents discover them automatically.

```bash
# Install
./scripts/install-opencode-tools.sh

# Verify
browser-start && browser-nav https://example.com

# Uninstall
./scripts/uninstall-opencode-tools.sh
```

**Requirements:** Chromium (or Chrome) installed, `~/.local/bin` on your PATH, Node.js.

### What lives where

| Location | What | Why |
|---|---|---|
| `tools/browser-tools/` (repo) | Source scripts and SKILL.md | Versioned, portable |
| `~/.local/share/opencode-tools/browser-tools/` | Installed scripts + node_modules | Available system-wide |
| `~/.local/bin/browser-*` | Symlinks to installed scripts | On PATH for bash calls |
| `~/.config/opencode/skills/browser-tools/SKILL.md` | Skill definition | OpenCode agent discovery |

## Background

This started as a research project exploring multi-agent orchestration frameworks. After 6 design proposals and 11 literature reviews, [Proposal 007](research/proposals/007_Reality_Check.md) killed the standalone framework idea. The useful patterns (hierarchy, role specialization, cost-aware model routing, phased execution) are now encoded directly as OpenCode agent configuration.
