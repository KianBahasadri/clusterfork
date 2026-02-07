# Proposal 001: Clusterfork Core Architecture

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-06

---

## 1. Motivation

Single-agent coding assistants have hit a ceiling. They can handle small, well-scoped tasks, but they collapse under the weight of long-horizon projects — losing context, making contradictory decisions, and failing to coordinate across files. The research is clear: the path forward is **multi-agent orchestration** with hierarchical planning, structured communication, and persistent state.

Clusterfork's ambition is to turn software development into a **high-throughput manufacturing process**: high-level intent in, production-grade code out. This proposal defines the foundational architecture to make that possible.

## 2. Design Principles

These principles are distilled from across the literature review corpus.

| #   | Principle                                                                                                                                            | Source                                           |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| P1  | **Deterministic backbone, non-deterministic intelligence.** Use plain code for orchestration flow; inject LLM agents only where reasoning is needed. | CrewAI Flows, LangGraph                          |
| P2  | **Structure over dialogue.** Agents exchange structured artifacts (specs, schemas, diffs), not free-form chat.                                       | MetaGPT, TalkHier                                |
| P3  | **Role specialization.** Narrow, well-defined agent roles outperform generalists.                                                                    | MetaGPT, Gas Town, Claude Code Teams             |
| P4  | **Separate planning from execution.** The agent that decides _what_ to do should not be the same one that _does_ it.                                 | EPO, MetaGPT, Gas Town                           |
| P5  | **Memory is structured, not flat.** Use scored retrieval and graph structures, not conversation logs.                                                | Generative Agents, Decentralized Agents, Voyager |
| P6  | **Self-correction is mandatory.** Every execution must include a verification step before the result is accepted.                                    | Voyager, EPO, TalkHier                           |
| P7  | **Persistence enables durability.** Agent state and workflow progress survive crashes and restarts.                                                  | LangGraph, Gas Town (Beads)                      |
| P8  | **Human-in-the-loop is a first-class citizen.** The user is an agent in the system, not an afterthought.                                             | AutoGen (UserProxy), LangGraph                   |

## 3. Architecture Overview

Clusterfork is organized into four layers:

```
┌──────────────────────────────────────────────────┐
│                   USER / OVERSEER                │
│          (Intent, Approval, Oversight)           │
├──────────────────────────────────────────────────┤
│               ORCHESTRATION LAYER                │
│     Flow Engine · Task Queue · Merge Queue       │
├──────────────────────────────────────────────────┤
│                  AGENT LAYER                     │
│   Architect · Coder · Reviewer · Scout · QA      │
├──────────────────────────────────────────────────┤
│               INFRASTRUCTURE LAYER               │
│   Memory Store · State Manager · Git Backend     │
└──────────────────────────────────────────────────┘
```

**User/Overseer** — The human. Defines intent ("build a REST API for user management"), approves critical decisions, and overrides when needed. Modeled as an agent with a `UserProxy` interface (AutoGen pattern).

**Orchestration Layer** — The deterministic backbone (P1). This is plain code — no LLM calls. It owns the execution flow: decomposing work into tasks, routing them to agents, managing the merge queue, and enforcing SOPs. Analogous to CrewAI's Flows and Gas Town's Mayor.

**Agent Layer** — The intelligence. Specialized LLM-powered agents that receive structured inputs, reason, and produce structured outputs. Each has a narrow role (P3) and follows defined protocols.

**Infrastructure Layer** — Persistence, memory, and state. Git is the single source of truth for code and workflow state (Gas Town's Beads pattern). Memory is stored as a scored, retrievable structure (Generative Agents pattern).

## 4. Core Abstractions

### 4.1 Agent

Every participant in the system is an `Agent`. This is the universal interface (AutoGen's `ConversableAgent` pattern).

```python
class Agent:
    """Base abstraction for all system participants."""
    role: str               # e.g. "architect", "coder", "reviewer", "user"
    capabilities: list[str] # e.g. ["read_files", "write_files", "run_tests"]
    constraints: list[str]  # e.g. ["no_file_writes", "read_only"]

    def receive(self, message: Message) -> Message:
        """Process an incoming message and produce a response."""
        ...
```

Agents are **stateless between tasks** — all persistent state lives in the Infrastructure Layer. This makes agents ephemeral and replaceable (Gas Town's NDI principle).

### 4.2 Message

Agents communicate via structured `Message` objects, not raw strings (P2, TalkHier pattern).

```python
class Message:
    """Structured communication between agents."""
    sender: str             # agent role/id
    receiver: str           # agent role/id or "broadcast"
    context: str            # background — why this message exists
    content: str            # the actual payload (instruction, artifact, feedback)
    artifacts: list[Artifact]  # structured outputs (specs, diffs, test results)
    metadata: dict          # routing info, priority, timestamps
```

The `context` field is critical — it explicitly carries the "why" so downstream agents don't hallucinate context or lose track of the goal (TalkHier's Background field).

### 4.3 Task

A `Task` is the atomic unit of work in the system.

```python
class Task:
    """A discrete unit of work assigned to an agent."""
    id: str
    description: str        # natural language description
    assignee: str           # agent role
    dependencies: list[str] # task IDs that must complete first
    status: TaskStatus      # pending | claimed | in_progress | review | done | failed
    artifacts: list[Artifact]  # inputs and outputs
    file_locks: list[str]   # files this task will modify (conflict avoidance)
```

Tasks support **dependency tracking** and **file locking** (Claude Code Teams pattern). The orchestrator will not assign a task until its dependencies are satisfied, and no two tasks can claim the same files concurrently.

### 4.4 Artifact

An `Artifact` is a structured output produced by an agent. This replaces free-form "here's the code" responses with typed, versioned deliverables (MetaGPT pattern).

```python
class Artifact:
    """A typed, versioned deliverable."""
    type: ArtifactType      # spec | architecture | code_diff | test_result | review
    content: str            # the actual content
    version: int            # monotonically increasing
    source_task: str        # which task produced this
```

Examples of artifacts:

-   **Spec**: A PRD or feature specification (Product Manager output)
-   **Architecture**: File structure, interfaces, dependency graph (Architect output)
-   **CodeDiff**: A git-format diff (Coder output)
-   **TestResult**: Pass/fail results with coverage (QA output)
-   **Review**: Structured feedback with severity ratings (Reviewer output)

## 5. Agent Roles

Clusterfork defines five core agent roles, inspired by MetaGPT's SOP approach and Gas Town's role specialization.

### 5.1 Architect

**Purpose:** Translates high-level intent into a structured technical plan.

-   **Input:** User intent, codebase context
-   **Output:** `Architecture` artifact — file structure, interfaces, dependency graph, task breakdown
-   **Key behavior:** Never writes implementation code. Produces a plan that the Coder can execute without ambiguity.
-   **Inspiration:** MetaGPT's Architect, Voyager's Automatic Curriculum

### 5.2 Coder

**Purpose:** Writes code to fulfill a specific, well-scoped task.

-   **Input:** A single `Task` with an `Architecture` artifact for context
-   **Output:** `CodeDiff` artifact
-   **Key behavior:** Operates on a narrow scope (one task, specific files). Follows the iterative Write → Execute → Verify → Refine loop (Voyager pattern). Must pass self-verification before submitting.
-   **Inspiration:** Voyager's Iterative Prompting, Gas Town's Crew

### 5.3 Reviewer

**Purpose:** Evaluates code quality against the architecture and requirements.

-   **Input:** `CodeDiff` artifact + original `Architecture` + `Task` description
-   **Output:** `Review` artifact — approve, request changes, or reject
-   **Key behavior:** Dedicated evaluation agent (P6). Does not fix code — sends it back to the Coder with structured feedback if changes are needed.
-   **Inspiration:** TalkHier's Evaluation Team, EPO's Reward Model, MetaGPT's QA Engineer

### 5.4 Scout

**Purpose:** Explores and understands the existing codebase.

-   **Input:** A question or exploration goal (e.g., "How does authentication work?")
-   **Output:** Structured context summary — relevant files, patterns, dependencies
-   **Key behavior:** Read-only. Builds the contextual foundation that the Architect and Coder need. When an agent joins a new codebase, the Scout runs a "curriculum" to map the territory (Voyager's curriculum concept).
-   **Inspiration:** Voyager's Automatic Curriculum, Generative Agents' memory retrieval

### 5.5 QA

**Purpose:** Validates that the integrated result meets the original requirements.

-   **Input:** The full set of changes for a feature/task group
-   **Output:** `TestResult` artifact — pass/fail with details
-   **Key behavior:** Runs tests, checks for regressions, validates against the original spec. This is the final gate before merge.
-   **Inspiration:** MetaGPT's QA Engineer, Gas Town's Witness

## 6. Orchestration Model

The Orchestration Layer is the deterministic backbone (P1). It uses a **Flow-based execution model** inspired by CrewAI's Flows and LangGraph's graph execution.

### 6.1 The Standard Flow

Every feature request follows this SOP:

```
                    ┌─────────┐
                    │  USER   │
                    │ Intent  │
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │  SCOUT  │  ← Explore codebase, gather context
                    └────┬────┘
                         │
                  ┌──────▼──────┐
                  │  ARCHITECT  │  ← Produce plan + task breakdown
                  └──────┬──────┘
                         │
              ┌──────────▼──────────┐
              │   TASK SCHEDULER    │  ← Resolve dependencies, assign
              └──┬──────┬──────┬───┘
                 │      │      │
            ┌────▼─┐ ┌──▼──┐ ┌▼────┐
            │CODER │ │CODER│ │CODER│  ← Parallel execution
            │  #1  │ │ #2  │ │ #3  │
            └──┬───┘ └──┬──┘ └──┬──┘
               │        │       │
            ┌──▼────────▼───────▼──┐
            │      REVIEWER        │  ← Review each diff
            └──────────┬───────────┘
                       │
                  ┌────▼────┐
                  │   QA    │  ← Integration testing
                  └────┬────┘
                       │
                ┌──────▼──────┐
                │ MERGE QUEUE │  ← Auto-merge on approval
                └──────┬──────┘
                       │
                  ┌────▼────┐
                  │  USER   │  ← Notification + approval if needed
                  └─────────┘
```

### 6.2 Feedback Loops

The flow is not a one-way pipeline. Agents can escalate backward:

-   **Reviewer → Coder:** "Changes requested" — Coder revises and resubmits.
-   **QA → Architect:** "Integration failure" — Architect revises the plan.
-   **Any agent → User:** "Ambiguity detected" — Requests clarification (P8).

Each feedback loop has a **retry budget** (e.g., max 3 Reviewer → Coder cycles). If exceeded, the task escalates to the User.

### 6.3 Task Scheduling

The scheduler implements:

1. **Dependency resolution** — Tasks only enter the queue when all dependencies are `done`.
2. **File lock management** — No two active tasks may modify the same file (Claude Code Teams pattern).
3. **Parallel dispatch** — Independent tasks are dispatched to multiple Coder agents simultaneously.
4. **Priority ordering** — Critical-path tasks are scheduled first.

## 7. Memory & State

### 7.1 Working Memory (Per-Task)

Each agent receives a scoped context window for its current task:

-   The `Task` object with its artifacts
-   Relevant codebase files (identified by the Scout)
-   The `Architecture` artifact for structural context

This is **not** the full conversation history. It's curated, relevant context (P5).

### 7.2 Long-Term Memory (Persistent)

Inspired by Generative Agents and Voyager's Skill Library, Clusterfork maintains a persistent memory store with three retrieval signals:

| Signal         | Description                                      | Source            |
| -------------- | ------------------------------------------------ | ----------------- |
| **Recency**    | Exponential decay — recent memories score higher | Generative Agents |
| **Importance** | LLM-scored 1-10 significance rating              | Generative Agents |
| **Relevance**  | Embedding similarity to the current query        | Generative Agents |

Memory entries include:

-   **Decisions:** "We chose PostgreSQL over SQLite because..." (Architectural context)
-   **Lessons:** "The React hydration bug was caused by..." (Voyager's Skill Library concept)
-   **Patterns:** "This codebase uses the repository pattern for data access." (Scout discoveries)

### 7.3 Workflow State (Git-Backed)

All workflow state is persisted to Git (Gas Town's Beads pattern):

-   Task status and assignments
-   Agent outputs and artifacts
-   Merge queue state

This ensures **durability** (P7) — if the system crashes, it restarts from the last committed state.

## 8. Communication Protocol

Building on TalkHier and AutoGen, all agent-to-agent communication follows a strict protocol:

### 8.1 Message Schema

Every message must include:

| Field        | Required | Purpose                                            |
| ------------ | -------- | -------------------------------------------------- |
| `context`    | Yes      | Why this message exists — the goal, the background |
| `content`    | Yes      | The actual instruction, question, or deliverable   |
| `artifacts`  | No       | Structured outputs (diffs, specs, reviews)         |
| `escalation` | No       | If present, routes to a higher authority           |

### 8.2 No Free-Form Chat

Agents do **not** engage in open-ended conversation with each other. Every message is either:

1. A **task assignment** (Orchestrator → Agent)
2. A **deliverable** (Agent → Orchestrator)
3. A **feedback request** (Reviewer → Coder, with structured critique)
4. An **escalation** (Any → User, with context)

This eliminates the "telephone game" problem where context degrades across agent hops.

## 9. Technology Choices

### 9.1 Recommended Stack

| Component             | Technology                | Rationale                                                     |
| --------------------- | ------------------------- | ------------------------------------------------------------- |
| **Orchestration**     | Python + LangGraph        | Graph-based execution with checkpointing and cycling (P1, P7) |
| **Agent Runtime**     | Claude (Anthropic)        | Strong code generation, large context, tool use               |
| **State Persistence** | Git + SQLite              | Git for code/workflow state, SQLite for memory store          |
| **Memory Retrieval**  | Embedding store (local)   | For relevance-based memory retrieval                          |
| **Task Queue**        | In-process (initial)      | Start simple; migrate to Redis/Temporal if needed             |
| **Communication**     | Structured Python objects | Type-safe message passing within the process                  |

### 9.2 Rationale for LangGraph

LangGraph is selected as the orchestration engine because it natively supports:

-   **Cyclic graphs** — essential for feedback loops (Reviewer → Coder → Reviewer)
-   **Checkpointing** — workflow state survives restarts (P7)
-   **Human-in-the-loop** — first-class interrupt/resume for user approval (P8)
-   **Streaming** — real-time visibility into agent progress

The CrewAI Flows pattern is an alternative, but LangGraph's graph model maps more naturally to Clusterfork's feedback-loop-heavy architecture.

## 10. Implementation Roadmap

### Phase 1: Foundation (Milestone 1)

Build the minimal viable orchestration:

-   [ ] `Agent` base class with `receive()` interface
-   [ ] `Message` and `Task` data structures
-   [ ] Single-threaded Flow Engine (Scout → Architect → Coder → Reviewer)
-   [ ] Git-backed task state
-   [ ] CLI interface for user intent input

**Goal:** A single feature request flows through all four agents and produces a reviewed diff.

### Phase 2: Parallelism (Milestone 2)

Scale to multiple concurrent agents:

-   [ ] Task Scheduler with dependency resolution and file locking
-   [ ] Parallel Coder dispatch (multiple tasks simultaneously)
-   [ ] Merge Queue with conflict detection
-   [ ] Retry budgets and escalation logic

**Goal:** A multi-file feature is decomposed into parallel tasks, coded concurrently, and merged.

### Phase 3: Memory & Learning (Milestone 3)

Add persistent intelligence:

-   [ ] Long-term memory store with Recency/Importance/Relevance scoring
-   [ ] Reflection mechanism — post-task "lessons learned" generation
-   [ ] Skill Library — reusable solution patterns indexed by embedding
-   [ ] Scout curriculum — automated codebase exploration on first run

**Goal:** The system remembers past decisions and improves over time.

### Phase 4: Production Hardening (Milestone 4)

Make it reliable at scale:

-   [ ] LangGraph checkpointing for crash recovery
-   [ ] Cost tracking and budget enforcement
-   [ ] Observability dashboard (agent activity, token usage, task throughput)
-   [ ] Configurable human-in-the-loop gates

**Goal:** Clusterfork can run unsupervised on long-horizon projects.

## 11. Open Questions

1. **Agent model diversity:** Should all agents use the same LLM, or should we use cheaper/faster models for simpler roles (Scout, QA) and more capable models for complex roles (Architect, Coder)?

2. **Conflict resolution:** When the Reviewer and Coder disagree after exhausting retries, what's the escalation policy beyond "ask the user"? Could a senior Architect agent arbitrate?

3. **Context window management:** For large codebases, how do we ensure agents receive enough context without blowing token limits? The Scout role helps, but we may need more sophisticated chunking strategies.

4. **Testing the orchestrator:** How do we test the orchestration layer itself? We need a suite of synthetic "feature requests" with known-good outputs to regression-test the flow.

5. **Cost model:** At scale (10-30 agents), token costs could be significant. Do we need a "cost-aware scheduler" that balances parallelism against budget?

6. **Decentralization:** The current design is centralized (single orchestrator). Should we explore decentralized coordination (Decentralized Agents' graph-sharing pattern) for resilience, or is centralized simpler and sufficient?

## 12. References

| Paper/Framework      | Key Contribution to This Proposal                                    |
| -------------------- | -------------------------------------------------------------------- |
| Voyager              | Skill Library, self-correction loops, curriculum-based exploration   |
| AutoGen              | ConversableAgent interface, UserProxy pattern, dynamic orchestration |
| EPO                  | Hierarchical decomposition, reward-model-as-reviewer                 |
| MetaGPT              | Role specialization, SOPs, structured artifacts                      |
| Decentralized Agents | Knowledge graph memory, sub-graph communication                      |
| Generative Agents    | Memory stream with scored retrieval, reflection mechanism            |
| TalkHier             | Structured message protocol, dedicated evaluation team               |
| Gas Town             | Git-backed state, role-based orchestration at scale, NDI             |
| Claude Code Teams    | Task dependency tracking, file locking, lead/teammate hierarchy      |
| CrewAI               | Deterministic Flows backbone, scoped agent intelligence              |
| LangGraph            | Graph execution, checkpointing, human-in-the-loop, cycling           |

---

_This is a living document. It will evolve as we prototype, learn, and iterate._
