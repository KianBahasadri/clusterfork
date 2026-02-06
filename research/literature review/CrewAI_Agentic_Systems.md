# How to build Agentic Systems: The Missing Architecture for Production AI Agents

**Authors:** João (Joe) Moura  
**Venue:** CrewAI Blog, Dec 2025  
**Link:** https://blog.crewai.com/agentic-systems-with-crewai/

## Summary

CrewAI's take on production multi-agent systems. Core idea: use a deterministic backbone ("Flows") for structure and control, then plug in LLM agents only where intelligence is actually needed. Based on patterns from 1.7 billion agentic workflows.

## Problem Addressed

Most agent implementations fail in production. They're either too rigid (DAGs, graphs that become unmaintainable) or too loose (autonomous agents with no constraints). The gap isn't agent intelligence—it's system architecture.

## Approach

"Agentic Systems" = two components:

1. **Flows** — deterministic backbone. Regular code with decorators, state management, conditional branching. Owns execution order and guardrails.
2. **Crews/Agents** — intelligence injected at specific steps within the Flow. Ranges from a single LLM call to multi-agent crews depending on complexity.

Control always returns to the backbone after agents finish.

## Key Contributions

-   Framework for deciding when to use code vs single LLM call vs single agent vs multi-agent crew
-   "Flows" abstraction for deterministic orchestration around non-deterministic agents
-   Production validation at scale (DocuSign case study, Fortune 500 deployments)

## Relevance to Our Work

Directly relevant. Clusterfork is about large autonomous agent networks. CrewAI's Flows pattern is one approach to keeping those networks controllable—deterministic structure with intelligence at the edges. Worth considering whether our architecture needs a similar backbone or if we want something more decentralized.

## Strengths

-   Battle-tested at scale (1.7B workflows)
-   Practical guidance on when to use agents vs plain code
-   DocuSign case study with real metrics

## Limitations

-   Heavy on marketing, light on technical detail
-   Doesn't share much about failure modes or debugging specifics
-   "Flows" is thin on implementation details in this post

## Key Takeaways

-   Not everything needs an agent. Use plain code where possible.
-   Deterministic backbone + scoped intelligence is a proven production pattern.
-   Multi-agent works best when each agent has clear boundaries and a defined role.

## Public Code

-   https://github.com/crewAIInc/crewAI

## Datasets

None mentioned.

## Benchmarks

None mentioned. DocuSign AB test results referenced but not published.

## Technologies Used

### LLM Provider

-   Model-agnostic (encourages swapping models)

### LLM Tools

-   CrewAI
-   CrewAI Flows
-   CrewAI Enterprise Platform

### General Technologies

-   Python
-   Salesforce, Snowflake (in DocuSign case study)

## Notes

-   The "Flows + Crews" pattern is essentially the same insight as microservices: deterministic orchestration calling specialized services. Not new, but applied well to agents.
-   Would be useful to see how Flows handle failures mid-execution vs LangGraph's checkpointing approach.
