# Welcome to Gas Town

**Authors:** Steve Yegge
**Venue:** Medium, 2026
**Link:** https://steve-yegge.medium.com/welcome-to-gas-town-4f25ee16dd04

## Summary

The article introduces "Gas Town," an orchestration platform for managing large numbers (10-30+) of AI coding agents (specifically Claude Code instances). It draws analogies to Kubernetes and Temporal, treating agents as workers in a factory that can be "slung" work. The system is built on top of "Beads" (a git-backed issue tracker) and defines a "MEOW" stack (Molecular Expression of Work) for creating complex, durable workflows that survive agent restarts and crashes.

## Problem Addressed

The primary problem is the "tedium of running lots of Claude Code instances" and the difficulty of coordinating them to solve complex tasks. As developers move from using a single agent to "swarms," tracking work, managing context, handling merges, and keeping agents productive becomes unmanageable manually. The paper also references the "MAKER problem" (20-disc Hanoi towers) as a benchmark for long-horizon agent planning that Gas Town aims to solve.

## Approach

Gas Town implements a "town" of specialized agent roles:

-   **Mayor:** Concierge and chief-of-staff.
-   **Polecats:** Ephemeral workers that produce Merge Requests.
-   **Refinery:** Manages the Merge Queue, resolving conflicts and merging changes.
-   **Witness:** Monitors other agents and helps them get unstuck.
-   **Deacon & Dogs:** Daemon processes and helpers that maintain system health.
-   **Crew:** Long-lived, named agents for the user to interact with.

It uses **Beads** (a git-backed JSON store) for all state, including agent identities ("Agent Beads") and work items.
It introduces **GUPP (Gastown Universal Propulsion Principle)**: "If there is work on your hook, YOU MUST RUN IT," ensuring agents automatically pick up tasks.
It relies on **Nondeterministic Idempotence (NDI)**, where persistent workflows ("Molecules") ensure that work eventually completes even if individual agent sessions ("cattle") crash or are restarted.

## Key Contributions

-   **Role-Based Agent Orchestration:** Defining specific roles (Refinery, Witness, etc.) to handle the complexities of multi-agent development.
-   **MEOW Stack:** A hierarchy of work definition: Beads -> Epics -> Molecules (workflows) -> Protomolecules (templates) -> Formulas (TOML source).
-   **Git-Backed Persistence:** Using a git-based issue tracker (Beads) as the single source of truth for agent state, work queues, and messaging.
-   **"Vibe Coding" at Scale:** A framework designed to support a high-throughput, chaotic-but-productive workflow where "work becomes fluid."

## Relevance to Our Work

This is highly relevant to Clusterfork as it demonstrates a working model for orchestrating multiple autonomous agents on a shared codebase. The concepts of the "Refinery" (auto-merging agent) and "Witness" (monitoring agent) are particularly applicable to managing reliability in distributed agent systems. The use of git as a backend for agent state (Beads) is also an architectural pattern worth considering for transparency and reversibility.

## Strengths

-   **Scalability:** Explicitly designed to handle 20-30 concurrent agents.
-   **Resilience:** The NDI model allows the system to progress despite individual agent failures or context window limits.
-   **Practical Focus:** Born out of the author's own need to "industrialize" their coding workflow.
-   **Tooling:** Includes practical CLI tools (`gt`) and a `tmux`-based UI for managing the swarm.

## Limitations

-   **Complexity:** Described as "Kubernetes mated with Temporal and they had a very ugly baby." High learning curve.
-   **Cost:** "Expensive as hell," requiring multiple Claude Code accounts to function effectively.
-   **Maturity:** The project is extremely young (<3 weeks old at time of writing) and described as "100% vibe coded."
-   **Dependencies:** Tightly coupled to "Claude Code" and the "Beads" system.

## Key Takeaways

-   **Orchestration is the bottleneck:** Moving beyond single-agent coding requires a control plane (like Kubernetes) for agents.
-   **Specialization is key:** Generic agents aren't enough; you need specific roles for merging, monitoring, and maintenance.
-   **Persistence enables durability:** Storing workflow state in a persistent medium (Git/Beads) allows agents to be ephemeral while work remains durable.
-   **"Human-in-the-loop" evolves:** The user becomes an "Overseer" or Product Manager, defining work ("slinging" tasks) rather than writing code.

## Public Code

-   **Gas Town:** [https://github.com/steveyegge/gastown](https://github.com/steveyegge/gastown)
-   **Beads:** [https://github.com/steveyegge/beads](https://github.com/steveyegge/beads)

## Datasets

None.

## Benchmarks

-   **MAKER Problem:** 20-disc Towers of Hanoi. The author claims Gas Town can solve the 10-disc version in minutes and theoretically the 20-disc version in ~30 hours.

## Technologies Used

### LLM Provider

-   **Claude Code:** The primary LLM agent used throughout the system.

### LLM Tools

-   **Beads:** A git-backed issue tracker used for state management, agent identity, and work tracking.
-   **MEOW Stack:** (Molecular Expression of Work) The hierarchical workflow definition system.

### General Technologies

-   **Go:** The main programming language for the `gt` binary and system logic.
-   **Git:** Used as the underlying database for Beads and version control.
-   **tmux:** Provides the terminal-based user interface for managing concurrent agents.
-   **TOML:** Used for defining "Formulas" (workflow templates).

## Notes

The article is written in a very informal, humorous style ("Mad Max" theme). While entertaining, it highlights that the system is experimental and "not safe" for casual users. The concept of "Wisps" (ephemeral beads not written to disk) vs. standard Beads is an interesting optimization for high-frequency orchestration events.
