# Orchestrate Teams of Claude Code Sessions

**Authors:** Anthropic  
**Venue:** Claude Code Documentation, 2025  
**Link:** https://code.claude.com/docs/en/agent-teams

## Summary

Documentation for Claude Code's experimental "agent teams" feature, which lets you coordinate multiple Claude Code instances working in parallel. One session acts as a team lead that spawns teammates, assigns tasks, and synthesizes results. Teammates are independent sessions that communicate via a shared task list and messaging system.

## Problem Addressed

Single-agent sessions hit bottlenecks on tasks that benefit from parallel exploration—code reviews across multiple dimensions, debugging with competing hypotheses, or multi-module feature work. Subagents exist but can only report results back to the caller, with no peer-to-peer communication.

## Approach

A lead session spawns teammate sessions, each with its own context window. Coordination happens through:

-   A shared task list with pending/in-progress/completed states and dependency tracking
-   A mailbox system for direct messaging between agents (point-to-point or broadcast)
-   File-lock-based task claiming to prevent race conditions
-   Two display modes: in-process (single terminal) or split panes (tmux/iTerm2)

Teammates load project context (CLAUDE.md, MCP servers, skills) at spawn but don't inherit the lead's conversation history. The lead can operate in delegate mode (coordination only, no code changes) or actively implement alongside teammates.

## Key Contributions

-   Peer-to-peer agent communication, not just hub-and-spoke reporting back to a coordinator
-   Shared task list with dependency tracking and file-lock-based claiming
-   "Competing hypotheses" pattern where agents actively try to disprove each other's theories
-   Plan approval workflow where teammates must get lead sign-off before implementing
-   Direct user-to-teammate interaction without routing through the lead

## Relevance to Our Work

Directly relevant. This is a concrete implementation of multi-agent coordination at the tool level. The architecture—lead/teammate hierarchy, shared task lists, mailbox messaging, dependency tracking—maps closely to what Clusterfork needs for large autonomous systems. The patterns around task decomposition, file conflict avoidance, and the distinction between subagents (lightweight, report-back-only) vs agent teams (independent, peer-communicating) are useful design reference points.

## Strengths

-   Practical, tested implementation rather than theoretical framework
-   Clear comparison between subagents and agent teams with concrete guidance on when to use each
-   Thoughtful coordination primitives (task dependencies, file locking, broadcast vs direct messaging)
-   The competing hypotheses debugging pattern is a strong example of agents doing something genuinely better than a single agent
-   Honest about limitations and failure modes

## Limitations

-   Experimental feature with significant constraints (no session resumption, no nested teams, one team per session)
-   No benchmarks or quantitative evaluation of multi-agent vs single-agent performance
-   Token usage scales linearly with teammates—no discussion of cost optimization
-   Fixed lead role with no failover or leadership transfer
-   Teammates can't spawn their own sub-teams, limiting hierarchical depth
-   No mechanism for shared memory or persistent state beyond the task list

## Key Takeaways

The subagent vs agent team distinction is a useful design axis: lightweight fire-and-forget workers vs independent peers that communicate. For Clusterfork, we likely need both patterns. The task list with dependency tracking and file-lock claiming is a simple but effective coordination mechanism. The "delegate mode" concept (restricting the lead to coordination only) is worth adopting for orchestrator agents that shouldn't be doing implementation work themselves.

## Public Code

Claude Code is a closed-source product by Anthropic. No public implementation of the agent teams feature is available.

## Datasets

None.

## Benchmarks

None provided. No quantitative comparison between single-agent and multi-agent performance.

## Technologies Used

### LLM Provider

-   Claude (Anthropic)

### LLM Tools

-   Claude Code CLI
-   Model Context Protocol (MCP)
-   Subagent framework (built into Claude Code)

### General Technologies

-   tmux (split-pane display mode)
-   iTerm2 (alternative split-pane mode)
-   File-system-based coordination (~/.claude/teams/, ~/.claude/tasks/)
-   File locking for task claiming

## Notes

-   The feature is gated behind an experimental flag (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`), so it's early stage and likely to change
-   The "competing hypotheses" pattern where agents debate and try to disprove each other is the most interesting use case—it's one of the few examples where multi-agent coordination clearly outperforms sequential single-agent work
-   The limitation of no nested teams is significant for Clusterfork's vision of large hierarchical systems. We'd need to solve recursive team spawning.
-   The honest discussion of when NOT to use teams (sequential tasks, same-file edits, high-dependency work) is useful framing for our own system design
