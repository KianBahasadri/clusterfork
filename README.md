# Clusterfork

Orchestrated agent workflow for AI-assisted software development in [OpenCode](https://opencode.ai).

## How it works

An **orchestrator** agent (high-capability model) handles planning and coordination with the user. It delegates execution to four specialized **mini workers** (cost-efficient models) through OpenCode's Task tool.

This repo also defines a **masturbator** agent: a user-facing proxy you talk to directly that relays requests to `orchestrator` on your behalf.
It just plays with itself.


| Agent | Model | Role |
|---|---|---|
| `masturbator` | `openrouter/z-ai/glm-5` | User-facing proxy that confers with `orchestrator` |
| `orchestrator` | `openai/gpt-5.3-codex` | Plans, delegates, reviews results |
| `mini-implementer` | `azure/gpt-5-mini` | Writes and edits code |
| `mini-tester` | `azure/gpt-5-mini` | Runs tests, builds, lints |
| `mini-ui-builder` | `azure/gpt-5-mini` | Frontend components and styling |
| `mini-researcher` | `azure/gpt-5-mini` | Codebase and web research |

## Repo structure

```
opencode.json              # Sets orchestrator as default agent, Context7 MCP
.opencode/
  agents/
    masturbator.md         # User-facing proxy agent
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
install-opencode-tools.sh     # Install tools globally
uninstall-opencode-tools.sh   # Remove global install
research/
  literature review/       # Paper and framework analyses
  proposals/               # Original design documents (001-007)
```

```bash
# Install
./install-opencode-tools.sh

# Verify
browser-start && browser-nav https://example.com

# Uninstall
./uninstall-opencode-tools.sh
```

**Requirements:** Chromium (or Chrome) installed, Node.js.

## Background

This started as a research project exploring multi-agent orchestration frameworks. After a few days I gave up and switched to opencode + subagents.
