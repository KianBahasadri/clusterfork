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
opencode.json              # Sets orchestrator as default agent
.opencode/
  agents/
    orchestrator.md        # Primary agent - plans and delegates
    mini-implementer.md    # Subagent - code changes
    mini-tester.md         # Subagent - verification
    mini-ui-builder.md     # Subagent - frontend work
    mini-researcher.md     # Subagent - research
research/
  literature review/       # Paper and framework analyses
  proposals/               # Original design documents (001-007)
```

## Usage

Clone this repo and run `opencode` from the root. OpenCode picks up `opencode.json` and `.opencode/agents/` automatically.

To use these agents in a different project, copy `opencode.json` and `.opencode/` into that project's root.

## Background

This started as a research project exploring multi-agent orchestration frameworks. After 6 design proposals and 11 literature reviews, [Proposal 007](research/proposals/007_Reality_Check.md) killed the standalone framework idea. The useful patterns (hierarchy, role specialization, cost-aware model routing, phased execution) are now encoded directly as OpenCode agent configuration.
