# Proposal 005: Autonomous Divisional Architecture

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-07

---

## 1. Motivation

Multi-agent systems fail in two predictable ways. The first is structural: flat agent pools devolve into a "soup" where context degrades across hops, work is duplicated, and no one owns anything. MetaGPT showed that imposing **Standard Operating Procedures** (SOPs) and role specialization eliminates cascading hallucinations and produces coherent output. Gas Town showed that role-based orchestration scales to 20-30 concurrent agents when backed by durable state.

The second failure is operational: systems that require human oversight for every decision cannot scale. A single human cannot review every PR from a 10-agent swarm. The system must self-manage, self-correct, and self-heal — and the human must still be able to reconstruct _exactly_ what happened after the fact.

This proposal defines a **Divisional Architecture** that solves both problems simultaneously. It borrows from MetaGPT's SOP-driven role specialization, TalkHier's structured communication protocol, CrewAI's deterministic backbone pattern, Voyager's self-correction loops, and Stanford's Generative Agents memory model — but organizes them into a lean corporate structure with hard-enforced capability boundaries and total forensic observability.

### 1.1 Why Corporate Structure?

LLMs were trained on the sum of human organizational knowledge. They have deep, implicit models of how managers delegate, how memos are written, how tickets flow through a pipeline, and how escalation chains work. By aligning our architecture with these real-world concepts, we get better agent behavior _for free_ — the model already knows the norms.

**The rule:** Use a real-world organizational term only when the system concept _genuinely is_ that thing. A Manager that delegates and reviews is a manager. An ordered queue of incoming requests is an inbox. A formal internal message with sender, recipient, subject, and body is a memo. We never distort the system to fit a metaphor.

**Concepts adopted (honest mappings):**

| Concept         | System Equivalent                      | Why It's Honest                                            |
| :-------------- | :------------------------------------- | :--------------------------------------------------------- |
| Manager         | Division lead agent                    | Delegates, coordinates, reviews — that's a manager         |
| Inbox           | Per-division message queue             | Ordered queue of incoming work — that's an inbox           |
| Memo            | Cross-division structured message      | Formal internal message with sender/recipient/subject/body |
| Ticket          | Unit of work with status lifecycle     | Open → Assigned → In Progress → Review → Done              |
| Brief           | User intent translated into spec       | Creative agencies call this a brief; so do we              |
| Requisition     | Cross-division resource/action request | Formal request for deploy, budget, or resources            |
| Incident Report | Production failure record              | Standard SRE term, maps directly                           |
| Standup         | Periodic Manager status exchange       | Short, structured sync — not a meeting                     |
| Org Chart       | Capability matrix                      | Who can do what — that's an org chart                      |

**Concepts rejected:**

| Concept            | Why Not                                                                     |
| :----------------- | :-------------------------------------------------------------------------- |
| Email              | Our memos are synchronous-ish, no reply-all, no spam. "Memo" is more honest |
| Slack / Chat       | We enforce structure over dialogue (MetaGPT, TalkHier)                      |
| HR                 | No hiring/firing/benefits. Agent lifecycle is an Ops concern                |
| Board Meeting      | Implies deliberation. Our policies are deterministic rules, not debates     |
| Performance Review | Subjective process. We have performance _reports_ (metrics)                 |

---

## 2. Design Principles

| #      | Principle                                                                                                                                                                                  | Source                                                                        |
| :----- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :---------------------------------------------------------------------------- |
| **P1** | **Deterministic backbone, non-deterministic intelligence.** The orchestrator is plain code. LLMs are injected only where reasoning is needed.                                              | CrewAI Flows, LangGraph                                                       |
| **P2** | **Structure over dialogue.** Agents exchange typed artifacts (Memos, Tickets, Requisitions), not free-form chat. Every message carries explicit Background and Intermediate Output fields. | MetaGPT, TalkHier                                                             |
| **P3** | **Work flows through the org.** No agent acts alone. Cross-division changes require formal organizational protocols.                                                                       | MetaGPT (SOPs), Gas Town (GUPP)                                               |
| **P4** | **Strict divisional boundaries.** Only Ops deploys. Only Dev codes. Only Finance spends. Enforced in code via capability tokens, not prompts.                                              | Gas Town (role specialization), Claude Code Teams (file locking)              |
| **P5** | **Honest mapping.** Use real-world names only when the concept genuinely maps.                                                                                                             | Original to this proposal                                                     |
| **P6** | **Zero oversight, total auditing.** The human does not supervise; they audit. Every action is logged in full fidelity.                                                                     | Original to this proposal                                                     |
| **P7** | **Self-correction is mandatory.** Every execution includes a verification step (Reviewer, QA, or self-check) before results are accepted.                                                  | Voyager (iterative prompting), EPO (reward model), TalkHier (evaluation team) |
| **P8** | **Provider agnosticism.** Intelligence is a commodity sourced from a pluggable bus.                                                                                                        | CrewAI (model-agnostic), LiteLLM (routing)                                    |

---

## 3. The Organization

### 3.1 Division Structure

Clusterfork is organized into four logical divisions within a single orchestrator process. Each division has a **Manager** agent and zero or more **Specialist** agents. All divisions share the same infrastructure (event log, provider bus, state store) but are isolated by capability boundaries.

```
                          ┌─────────────┐
                          │    Human    │
                          │   (Owner)   │
                          └──────┬──────┘
                                 │ briefs ↓  pages ↑
                                 v
                    ┌────────────────────────┐
                    │    Comms Division       │
                    │  Manager + Liaison(s)   │
                    └───┬──────────┬─────────┘
               memos ↓↑ │          │ ↓↑ memos
          ┌─────────────┘          └──────────────┐
          v                                       v
┌──────────────────┐  requisitions  ┌──────────────────┐
│  Dev Division    │ ─────────────> │  Ops Division    │
│ Manager          │ <───────────── │ Manager          │
│ + Architect      │ incident rpts  │ + SRE            │
│ + Coder(s)       │                │ + Monitor        │
│ + Reviewer       │                │                  │
│ + QA             │                └────────┬─────────┘
└────────┬─────────┘                         │
         │  expense reports / requisitions   │
         v                                   v
         └──────> ┌──────────────────┐ <─────┘
                  │ Finance Division │
                  │ Manager          │
                  │ + Accountant     │
                  │ + Router         │
                  └──────────────────┘
```

### 3.2 Division Responsibilities

**Comms (Communications)**
Sole human interface. The human talks to Comms; Comms talks to everyone else. Translates raw user input into structured **Briefs** for Dev. Delivers results, summaries, and **Expense Reports** back to the human. Sends **Pages** for emergencies. Owns the `user_comms` and `emergency_page` capabilities.

**Dev (Development)**
Architecture, implementation, code review, and quality assurance. Receives Briefs from Comms, decomposes them into **Tickets** via the Manager, and distributes to Architect → Coder → Reviewer → QA (MetaGPT SOP pipeline). Files **Requisitions** to Ops for deployment and to Finance for budget. Owns `source_control_write`, `run_tests`, and `run_linters` capabilities.

**Ops (Operations)**
Deployment, monitoring, health checks, incident response, and agent lifecycle management. Owns production. Processes **Requisitions** from Dev via canary deploy → health check → promotion pipeline. Files **Incident Reports** to Dev when production fails due to code. Manages agent restarts and stuck-task recovery. Owns `production_deploy`, `rollback`, `infra_monitor`, and `agent_lifecycle` capabilities.

**Finance**
Cost accounting, budget enforcement, provider economics, and spend reporting. Maintains the **Ledger** (a view over the universal event log). Issues **Expense Reports** to Comms for human visibility. Manages the **LLM Provider Bus** and enforces deterministic budget policies. Owns `budget_enforcement`, `provider_config`, and `ledger_write` capabilities.

---

## 4. Agent Architecture

### 4.1 The Agent Base

Every agent in the system — Manager and Specialist alike — inherits from a common `Agent` abstraction (AutoGen's `ConversableAgent` pattern). The key innovation is that agents are **stateless between tasks**: all persistent state lives in the Infrastructure Layer, making agents ephemeral and replaceable (Gas Town's Nondeterministic Idempotence principle).

```python
class Agent:
    """Base abstraction for all division participants."""
    agent_id: str               # Unique identifier (e.g. "dev-manager", "coder-alpha")
    division: Division          # Which division this agent belongs to
    role: str                   # e.g. "manager", "architect", "coder", "reviewer"
    capabilities: set[str]      # Granted by the division's capability token
    system_prompt: str          # Role definition + SOP + constraints (see §4.2)
    tools: list[Tool]           # Tools this agent can invoke (filtered by capabilities)

    def receive(self, message: Message) -> Message:
        """Process an incoming message and produce a structured response.

        The orchestrator calls this method. The agent:
        1. Retrieves relevant context from memory (§4.4)
        2. Constructs an LLM prompt from system_prompt + message + context
        3. Calls the Provider Bus for completion (§8)
        4. Validates the response against its role constraints
        5. Returns a structured Message with typed artifacts
        """
        ...
```

### 4.2 Agent Identity: System Prompts and SOPs

Each agent's personality and behavior is defined by a **system prompt** composed of three layers. This follows MetaGPT's SOP encoding pattern: the role, the rules, and the standard operating procedure are baked into the prompt, not left to improvisation.

```python
@dataclass
class AgentIdentity:
    """Defines who an agent is and how it behaves."""

    # Layer 1: Role Definition
    # WHO you are. One paragraph. Establishes the persona.
    # Example: "You are the Dev Division Manager. You receive Briefs from
    # Comms, decompose them into Tickets, assign them to your team, and
    # review their output before sending results back."
    role_definition: str

    # Layer 2: Constraints
    # What you CANNOT do. Hard rules, not suggestions.
    # Enforced by capability tokens at the orchestrator level,
    # but also stated in the prompt for belt-and-suspenders safety.
    # Example: "You MUST NOT write code directly. You MUST NOT deploy.
    # You MUST NOT communicate with the human — only Comms does that."
    constraints: list[str]

    # Layer 3: Standard Operating Procedure (SOP)
    # HOW you do your job. Step-by-step instructions.
    # This is the MetaGPT insight: encoding SOPs into prompts
    # produces dramatically more consistent output than vague instructions.
    # Example for Dev Manager:
    #   "1. Read the incoming Brief or Memo from your Inbox.
    #    2. If it's a new Brief, create an Architecture Ticket for the Architect.
    #    3. When the Architect returns a plan, decompose it into Coder Tickets.
    #    4. Assign Coder Tickets respecting file locks and dependencies.
    #    5. Route completed code to the Reviewer.
    #    6. Route approved code to QA.
    #    7. When QA passes, send a Requisition to Ops for deployment.
    #    8. Send a Memo to Comms with the results."
    sop: list[str]
```

The system prompt is assembled as:

```
{role_definition}

CONSTRAINTS:
{constraints, one per line}

STANDARD OPERATING PROCEDURE:
{sop, numbered steps}

You communicate using structured Messages. Every message you send MUST include:
- background: Why this message exists (context for the recipient)
- content: The actual instruction, deliverable, or question
- artifacts: Any structured outputs (specs, diffs, reviews)
```

This three-layer structure ensures that:

-   **Layer 1** activates the LLM's existing bias for the role (§1.1)
-   **Layer 2** provides hard guardrails (reinforced by capability enforcement at the orchestrator)
-   **Layer 3** gives the agent a deterministic playbook (MetaGPT's core insight)

### 4.3 Manager vs. Specialist

**Managers** are agents whose SOP is about _coordination_: they receive work, decompose it, delegate it, review it, and communicate results. They never do leaf-level work (coding, deploying, accounting). This is EPO's separation of planning from execution.

**Specialists** are agents whose SOP is about _execution_: Coders write code, Reviewers evaluate code, SREs run deploys. They receive a single well-scoped Ticket from their Manager and produce a single deliverable.

| Agent           | Division | Type       | Primary SOP                                                                                                  |
| :-------------- | :------- | :--------- | :----------------------------------------------------------------------------------------------------------- |
| Comms Manager   | Comms    | Manager    | Translate user intent → Brief; deliver results; page on emergency                                            |
| Liaison         | Comms    | Specialist | Draft summaries, format reports                                                                              |
| Dev Manager     | Dev      | Manager    | Brief → Architecture Ticket → Coder Tickets → Review → QA → Requisition                                      |
| Architect       | Dev      | Specialist | Produce Architecture artifact (file structure, interfaces, task breakdown)                                   |
| Coder           | Dev      | Specialist | Write code for one Ticket using iterative Write → Test → Refine loop (Voyager)                               |
| Reviewer        | Dev      | Specialist | Evaluate CodeDiff against Architecture + requirements; approve or request changes (TalkHier evaluation team) |
| QA              | Dev      | Specialist | Integration testing; validate the full changeset against the original Brief                                  |
| Ops Manager     | Ops      | Manager    | Process Requisitions; coordinate deploys; triage incidents                                                   |
| SRE             | Ops      | Specialist | Execute deploy pipeline (canary → health check → promote/rollback)                                           |
| Monitor         | Ops      | Specialist | Watch agent liveness, detect stuck tasks, loop/runaway detection                                             |
| Finance Manager | Finance  | Manager    | Enforce budget policies; route provider decisions; generate reports                                          |
| Accountant      | Finance  | Specialist | Maintain the Ledger; compute expense reports                                                                 |
| Router          | Finance  | Specialist | Score and select LLM providers per-request (§8)                                                              |

### 4.4 Agent Memory

Agents are stateless between tasks (Gas Town's NDI), but they can _retrieve_ relevant context from the shared Memory Store before each action. The retrieval function uses the **Generative Agents** three-signal scoring model:

```python
def retrieve_context(query: str, k: int = 5) -> list[MemoryEntry]:
    """Retrieve the top-k most relevant memory entries.

    Scoring follows Generative Agents (Park et al., 2023):
      score = α * recency + β * importance + γ * relevance

    - recency:    Exponential decay since the memory was created
    - importance: LLM-scored 1-10 significance rating (cached)
    - relevance:  Cosine similarity between query embedding and memory embedding
    """
    ...
```

Memory entries are written by a **Reflection** step that runs after each completed Ticket (Voyager's skill library concept adapted to organizational context):

```python
@dataclass
class MemoryEntry:
    """A discrete piece of organizational knowledge."""
    timestamp: datetime
    division: str               # Which division created this
    category: str               # "decision", "lesson", "pattern", "incident"
    headline: str               # One-line summary
    content: str                # Full natural-language description
    importance: int             # 1-10, scored by LLM at creation time
    embedding: list[float]      # For relevance retrieval
    source_ticket_id: str       # Provenance
```

---

## 5. Structured Communication Protocol

### 5.1 The Message Object

All agent-to-agent communication uses a structured `Message` object. This directly implements TalkHier's protocol design, which proved that adding `background` and `intermediate_output` fields to messages improves task performance by 5-10% over unstructured text.

```python
@dataclass
class Message:
    """Structured communication between agents (TalkHier protocol)."""
    id: str
    sender: str                 # agent_id
    receiver: str               # agent_id or division inbox

    # TalkHier's three fields:
    background: str             # WHY this message exists — goal, context, history
    content: str                # WHAT — the actual instruction, deliverable, or question
    intermediate_output: str    # HOW FAR — partial results, reasoning steps, current state

    # Structured payloads (MetaGPT pattern):
    artifacts: list[Artifact]   # Typed deliverables (specs, diffs, reviews, test results)

    # Routing metadata:
    correlation_id: str         # Links all messages in the same work chain
    priority: int               # 0 (routine) to 3 (emergency)
    timestamp: datetime
```

### 5.2 No Free-Form Chat

Agents do **not** engage in open-ended conversation (P2). Every message is one of:

1. **A delegation** — Manager → Specialist, with a Ticket attached
2. **A deliverable** — Specialist → Manager, with Artifacts attached
3. **A handoff** — Manager → Manager (Memo, Requisition, Incident Report)
4. **Feedback** — Reviewer → Coder, with structured critique and severity ratings
5. **An escalation** — Any → higher authority, with full context

This eliminates the "telephone game" problem where context degrades across agent hops (MetaGPT's core finding).

### 5.3 Organizational Artifact Types

These are the typed artifacts that flow through the system. Each one maps to a real-world organizational document because it _is_ that document.

```python
@dataclass
class Brief:
    """User intent translated into a structured spec (Comms → Dev)."""
    id: str
    user_intent: str            # Raw user input
    requirements: list[str]     # Extracted requirements
    constraints: list[str]      # Budget, timeline, technology constraints
    acceptance_criteria: list[str]  # How to know when it's done
    priority: int

@dataclass
class Ticket:
    """Atomic unit of work within a division (Manager → Specialist)."""
    id: str
    brief_id: str               # Traces back to the originating Brief
    assignee: str               # agent_id of the specialist
    description: str            # What to do
    dependencies: list[str]     # Ticket IDs that must complete first
    file_locks: list[str]       # Files this ticket will modify (conflict avoidance)
    status: Literal["open", "assigned", "in_progress", "in_review", "done", "blocked", "failed"]
    artifacts: list[Artifact]   # Inputs (Architecture) and outputs (CodeDiff)
    retry_budget: int           # Max feedback cycles before escalation (default: 3)

@dataclass
class Requisition:
    """Formal cross-division request for action or resources."""
    id: str
    type: Literal["deploy", "rollback", "budget_increase", "provider_add"]
    from_division: str
    to_division: str
    justification: str          # WHY this is needed (background for the recipient)
    requirements: dict          # Type-specific payload (e.g., branch name, budget amount)
    status: Literal["submitted", "accepted", "in_progress", "completed", "rejected"]

@dataclass
class IncidentReport:
    """Production failure record (Ops → Dev)."""
    id: str
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    root_cause_hypothesis: str
    affected_services: list[str]
    related_ticket_ids: list[str]   # Which recent changes might have caused this
    remediation_status: str

@dataclass
class ExpenseReport:
    """Cost attribution for a completed unit of work (Finance → Comms)."""
    id: str
    scope: str                  # brief_id, ticket_id, or run_id
    total_cost_usd: float
    breakdown: list[CostLineItem]   # Per-agent, per-provider itemization
    budget_remaining_usd: float
    anomalies: list[str]        # Any unexpected cost patterns

@dataclass
class Page:
    """Emergency escalation to the human (Comms → Human)."""
    id: str
    severity: Literal["warning", "critical"]
    reason: str                 # What happened
    context: str                # Full background for the human
    recommended_action: str     # What the system thinks the human should do
    related_ids: list[str]      # Ticket/Incident/Requisition IDs for forensic lookup
```

---

## 6. Capability Boundaries (The Org Chart)

### 6.1 Enforcement Mechanism

Capabilities are **not** enforced by telling agents "please don't do this" in the system prompt. They are enforced by the **Orchestrator** at the tool-call level. Each division receives a **capability token** at initialization, and the orchestrator's tool-dispatch layer checks the token before executing any tool.

```python
@dataclass
class CapabilityToken:
    """Issued to a division at startup. Checked on every tool call."""
    division: str
    granted_capabilities: set[str]  # e.g. {"source_control_write", "run_tests"}

def dispatch_tool(agent: Agent, tool_name: str, args: dict) -> Any:
    """Orchestrator-level tool dispatch with capability enforcement."""
    required_cap = TOOL_CAPABILITY_MAP[tool_name]
    if required_cap not in agent.capabilities:
        # Log the violation, do NOT execute
        emit_event(EventType.CAPABILITY_VIOLATION, agent=agent, tool=tool_name)
        raise CapabilityViolation(
            f"Agent {agent.agent_id} ({agent.division}) attempted {tool_name} "
            f"which requires '{required_cap}' — not in granted capabilities"
        )
    return execute_tool(tool_name, args)
```

### 6.2 The Capability Matrix

| Capability             | Comms |  Dev  |  Ops  | Finance | Description                                       |
| :--------------------- | :---: | :---: | :---: | :-----: | :------------------------------------------------ |
| `user_comms`           | **Y** |   -   |   -   |    -    | Read/write human channel                          |
| `emergency_page`       | **Y** |   -   |   -   |    -    | Send Page to human                                |
| `source_control_read`  |   -   | **Y** | **Y** |    -    | Read source code, git log                         |
| `source_control_write` |   -   | **Y** |   -   |    -    | Commit, branch, merge                             |
| `run_tests`            |   -   | **Y** |   -   |    -    | Execute test suites                               |
| `run_linters`          |   -   | **Y** |   -   |    -    | Execute linters/formatters                        |
| `production_deploy`    |   -   |   -   | **Y** |    -    | Deploy to production                              |
| `rollback`             |   -   |   -   | **Y** |    -    | Rollback production                               |
| `infra_monitor`        |   -   |   -   | **Y** |    -    | Read health checks, metrics                       |
| `agent_lifecycle`      |   -   |   -   | **Y** |    -    | Restart/kill agents                               |
| `budget_enforcement`   |   -   |   -   |   -   |  **Y**  | Set/modify budget policies                        |
| `provider_config`      |   -   |   -   |   -   |  **Y**  | Register/deregister providers                     |
| `ledger_write`         |   -   |   -   |   -   |  **Y**  | Write to cost ledger                              |
| `llm_call`             | **Y** | **Y** | **Y** |  **Y**  | Request LLM completion via Provider Bus           |
| `memory_read`          | **Y** | **Y** | **Y** |  **Y**  | Retrieve from memory store                        |
| `memory_write`         | **Y** | **Y** | **Y** |  **Y**  | Write reflections to memory                       |
| `inbox_send`           | **Y** | **Y** | **Y** |  **Y**  | Send Memo/Requisition to another division's inbox |

### 6.3 Cross-Division Interaction Rules

An agent that believes something outside its capability set is necessary **must** use the organizational protocols to enact that change:

-   Dev Coder finishes code → Dev Manager sends **Requisition** to Ops Inbox → Ops Manager dispatches SRE to deploy.
-   Ops Monitor detects a production bug → Ops Manager sends **Incident Report** to Dev Inbox → Dev Manager creates a hotfix Ticket.
-   Dev Manager needs more budget → sends **Requisition** to Finance Inbox → Finance Manager evaluates against policy.
-   Any Manager detects an unrecoverable failure → sends **Memo** to Comms Manager → Comms Manager sends **Page** to human.

The Orchestrator validates that every cross-division message goes through the inbox system. Direct agent-to-agent calls across division boundaries are blocked.

---

## 7. Universal Full-Fidelity Log

### 7.1 Design Rationale

The universal log is the owner's _only_ window into the system. Since the human provides zero oversight during execution (P6), the log must be comprehensive enough to reconstruct any decision chain after the fact. This is the "black box flight recorder."

### 7.2 Event Schema

Every action in the system — every LLM call, every tool invocation, every message sent, every artifact created — emits an event to an append-only, immutable event stream. The schema follows OpenTelemetry's trace-context correlation model:

```python
@dataclass
class Event:
    """Immutable event in the universal log."""
    timestamp: datetime

    # Trace correlation (OpenTelemetry W3C TraceContext):
    trace_id: str               # 128-bit hex, links all events in a work chain
    span_id: str                # 64-bit hex, unique to this event
    parent_span_id: str | None  # Enables tree reconstruction

    # Organizational context:
    division: str               # "comms", "dev", "ops", "finance"
    agent_id: str               # Which agent emitted this

    # Event classification:
    event_type: str             # See §7.3
    detail: str                 # Human-readable description

    # Artifact correlation:
    ticket_id: str | None       # Which Ticket this relates to
    memo_id: str | None         # Which Memo/Requisition/etc.
    brief_id: str | None        # Traces back to originating Brief

    # Cost attribution:
    cost_tokens_input: int      # Input tokens consumed (0 for non-LLM events)
    cost_tokens_output: int     # Output tokens consumed
    cost_usd: float             # Estimated USD cost
    provider: str | None        # Which LLM provider was used
    model: str | None           # Which model specifically

    # Full-fidelity payload reference:
    payload_ref: str | None     # Content-addressed hash (SHA-256) into blob store
```

### 7.3 Event Types

| Category           | Event Types                                                                                                   |
| :----------------- | :------------------------------------------------------------------------------------------------------------ |
| **Lifecycle**      | `ticket_created`, `ticket_assigned`, `ticket_completed`, `ticket_failed`, `ticket_blocked`                    |
| **Communication**  | `memo_sent`, `memo_received`, `requisition_submitted`, `requisition_completed`, `brief_created`, `page_sent`  |
| **Agent Actions**  | `llm_call_started`, `llm_call_completed`, `tool_call`, `tool_result`, `reflection_generated`                  |
| **Feedback Loops** | `review_approved`, `review_changes_requested`, `review_rejected`, `coder_resubmitted`                         |
| **Quality**        | `test_passed`, `test_failed`, `lint_passed`, `lint_failed`, `qa_approved`, `qa_rejected`                      |
| **Operations**     | `deploy_started`, `deploy_canary`, `deploy_promoted`, `deploy_rolled_back`, `agent_restarted`, `agent_killed` |
| **Finance**        | `budget_threshold_warning`, `budget_hard_limit`, `policy_action_taken`, `expense_report_generated`            |
| **Violations**     | `capability_violation`, `budget_violation`, `loop_detected`, `stall_detected`                                 |

### 7.4 Full-Fidelity Payload Store

Raw payloads (LLM prompts, LLM completions, tool arguments, tool results) are stored in a **content-addressed, encrypted blob store**. The event log contains only the `payload_ref` hash. This separation keeps the event stream lean and queryable while preserving full forensic data.

```python
@dataclass
class PayloadBlob:
    """Encrypted raw payload stored in the blob store."""
    sha256: str                 # Content-addressed key
    encrypted_content: bytes    # AES-256-GCM encrypted payload
    content_type: str           # "llm_prompt", "llm_completion", "tool_args", "tool_result"
    size_bytes: int
    created_at: datetime
    retention_class: str        # "hot" (30d), "warm" (90d), "cold" (365d)
```

### 7.5 Storage Backend

-   **Event stream:** Append-only JSONL file per run, committed to Git for durability (Gas Town's Beads pattern). For high-throughput runs, optionally backed by SQLite WAL-mode append table.
-   **Blob store:** Local directory with content-addressed files. Encrypted at rest. Compressed after `hot` retention window.
-   **Exporters (optional):** The event stream can be tailed by external systems (Prometheus, Langfuse, Helicone, OpenTelemetry Collector) but the JSONL/SQLite log is always the source of truth.

---

## 8. LLM Provider Bus

### 8.1 Design Goals

The Provider Bus ensures the system is never locked to a single vendor. Adding a new provider (OpenRouter, Anthropic, Azure, DigitalOcean GenAI, a local model) requires implementing one adapter and adding a config entry. Zero core logic changes.

### 8.2 Provider Adapter Interface

```python
class ProviderAdapter(Protocol):
    """Interface every LLM provider must implement.

    This is the ONLY contract. Implement this + register = done.
    """

    @property
    def provider_name(self) -> str:
        """Unique provider identifier (e.g. 'openrouter', 'anthropic-direct')."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """What this provider can do (models, max context, features)."""
        ...

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send a completion request and return the response + usage."""
        ...

    async def health_check(self) -> HealthStatus:
        """Return current provider health (latency, error rate, availability)."""
        ...


@dataclass
class ProviderCapabilities:
    """Declared capabilities of a provider."""
    models: list[ModelSpec]             # Available models with their properties
    max_context_tokens: int             # Largest context window available
    supports_streaming: bool
    supports_tool_use: bool
    supports_vision: bool

@dataclass
class ModelSpec:
    """A specific model available from a provider."""
    model_id: str                       # e.g. "claude-sonnet-4-20250514"
    model_class: Literal["reasoning", "standard", "fast"]   # Capability tier
    cost_per_1k_input_tokens: float     # USD
    cost_per_1k_output_tokens: float    # USD
    max_context_tokens: int

@dataclass
class CompletionRequest:
    """Provider-agnostic completion request."""
    messages: list[dict]                # OpenAI-format messages
    model_class: str                    # Requested capability tier (not a specific model)
    max_tokens: int
    temperature: float
    tools: list[dict] | None           # Tool definitions if needed
    requesting_agent: str               # For cost attribution
    requesting_division: str            # For cost attribution
    budget_remaining_usd: float | None  # If set, provider should respect this

@dataclass
class CompletionResponse:
    """Provider-agnostic completion response."""
    content: str
    tool_calls: list[dict] | None
    usage: TokenUsage
    model_used: str                     # Actual model that served the request
    provider: str                       # Which provider served it
    latency_ms: int

@dataclass
class TokenUsage:
    """Token usage for cost accounting."""
    input_tokens: int
    output_tokens: int
    cost_usd: float                     # Calculated from the model's pricing
```

### 8.3 Provider Registry

```python
class ProviderRegistry:
    """Manages registered providers and their live state.

    Providers are registered via config (YAML/TOML) at startup
    and can be hot-added/removed via Finance Requisitions.
    """
    adapters: dict[str, ProviderAdapter]    # name → adapter instance
    health_cache: dict[str, HealthStatus]   # name → last known health

    def register(self, adapter: ProviderAdapter) -> None: ...
    def deregister(self, provider_name: str) -> None: ...
    def get_available(self) -> list[ProviderAdapter]: ...
```

### 8.4 Smart Router

The Router is a **deterministic scoring engine** managed by Finance. It selects the best provider for each request based on multi-factor scoring. No LLM calls — this is pure code (P1).

```python
class SmartRouter:
    """Selects optimal provider per-request. Deterministic, no LLM calls."""

    def route(self, request: CompletionRequest) -> ProviderAdapter:
        """Score all available providers and return the best match.

        Scoring factors (weights configurable by Finance policy):
          1. capability_match:  Does the provider have a model matching
                                the requested model_class? (binary filter)
          2. cost_score:        Inverse of estimated cost for this request.
                                Normalized against cheapest available option.
          3. latency_score:     Inverse of recent p50 latency from health cache.
          4. reliability_score: 1 - recent_error_rate from health cache.
          5. budget_pressure:   As budget_remaining decreases, cost_score weight
                                increases (Finance policy lever).

        Final score = w1*cost + w2*latency + w3*reliability
        (only among providers that pass the capability filter)
        """
        ...
```

---

## 9. Orchestration Model

### 9.1 The Deterministic Backbone

The orchestrator is plain Python code — no LLM calls (P1, CrewAI Flows pattern). It owns:

1. **Inbox routing:** Receives Messages/Artifacts, routes to the correct division inbox.
2. **Capability enforcement:** Validates every tool call against the agent's capability token (§6.1).
3. **Ticket scheduling:** Dependency resolution, file lock management, parallel dispatch (Claude Code Teams pattern).
4. **Feedback loop management:** Tracks retry budgets. Escalates when exceeded.
5. **Event emission:** Every orchestrator action emits to the universal log.

### 9.2 The Standard Flow (End-to-End)

```
Human: "Build a REST API for user management"
  │
  ▼
[Comms Manager]
  │ Receives raw input, produces Brief:
  │   { requirements: ["CRUD endpoints", "JWT auth", ...],
  │     acceptance_criteria: ["All tests pass", "200 OK on /users"] }
  │ Sends Brief → Dev Inbox
  ▼
[Dev Manager]
  │ Receives Brief, creates Architecture Ticket for Architect
  ▼
[Dev Architect]
  │ Produces Architecture artifact:
  │   { files: ["src/routes/users.py", "src/models/user.py", ...],
  │     interfaces: [...], task_breakdown: [...] }
  │ Returns to Dev Manager
  ▼
[Dev Manager]
  │ Decomposes Architecture into Coder Tickets:
  │   Ticket-1: "Implement User model" (file_locks: ["src/models/user.py"])
  │   Ticket-2: "Implement CRUD routes" (depends: [Ticket-1], file_locks: ["src/routes/users.py"])
  │   Ticket-3: "Implement JWT auth middleware" (file_locks: ["src/middleware/auth.py"])
  │ Dispatches Ticket-1 and Ticket-3 in parallel (no dependency conflict)
  ▼
[Dev Coder-A: Ticket-1]  ║  [Dev Coder-B: Ticket-3]
  │ Iterative loop:       ║    │ Iterative loop:
  │ Write → Test → Refine ║    │ Write → Test → Refine
  │ (Voyager pattern)     ║    │ (Voyager pattern)
  │ Produces CodeDiff     ║    │ Produces CodeDiff
  ▼                       ║    ▼
[Dev Reviewer]            ║  [Dev Reviewer]
  │ Evaluates against     ║    │ Evaluates against
  │ Architecture + Brief  ║    │ Architecture + Brief
  │ (TalkHier eval team)  ║    │ Approve or request changes
  ▼                       ║    ▼
[Dev Manager]             ║  [Dev Manager]
  │ Ticket-1 done → unblocks Ticket-2 → dispatches to Coder-A
  ▼
[Dev Coder-A: Ticket-2]
  │ ... same iterative loop ...
  ▼
[Dev QA]
  │ Runs full test suite against all changes
  │ Validates acceptance_criteria from Brief
  │ Produces TestResult artifact
  ▼
[Dev Manager]
  │ QA passed → creates Requisition(type="deploy") → Ops Inbox
  ▼
[Ops Manager]
  │ Receives deploy Requisition, dispatches to SRE
  ▼
[Ops SRE]
  │ Canary deploy → health check → promote (or rollback)
  │ Sends Memo back to Dev Manager: "Deployed at commit abc123"
  ▼
[Dev Manager]
  │ Sends Memo to Comms Manager: "Feature complete, deployed"
  ▼
[Comms Manager]
  │ Sends result summary to Human
  │
  ▼
[Finance Manager]
  │ (Running in parallel throughout)
  │ Generates ExpenseReport for the entire Brief
  │ Sends to Comms Manager → Human
```

### 9.3 Feedback Loops and Retry Budgets

Every feedback loop has a **bounded retry budget** (default: 3 cycles). This prevents infinite Reviewer ↔ Coder loops.

```python
FEEDBACK_POLICIES = {
    "reviewer_to_coder": {
        "max_retries": 3,
        "on_exhaustion": "escalate_to_dev_manager",
        # Dev Manager may reassign to a different Coder,
        # use a more capable model, or escalate further
    },
    "qa_to_dev_manager": {
        "max_retries": 2,
        "on_exhaustion": "escalate_to_comms_manager",
        # Comms may page the human with full context
    },
    "deploy_failure": {
        "max_retries": 1,
        "on_exhaustion": "auto_rollback_then_incident_report",
    },
}
```

### 9.4 Standups (Periodic Manager Coordination)

Division Managers participate in periodic **Standups** — structured status exchanges that prevent work from silently stalling. This is not free-form chat; it's a fixed protocol:

```python
@dataclass
class StandupReport:
    """Structured status from a Division Manager."""
    division: str
    active_tickets: int
    blocked_tickets: int        # Tickets waiting on another division
    completed_since_last: int
    blockers: list[str]         # Natural-language descriptions of what's stuck
    budget_status: str          # "healthy", "warning", "critical"
```

The orchestrator triggers a Standup at configurable intervals (e.g., after every N completed Tickets, or every M minutes). Each Manager produces a `StandupReport`, the orchestrator collates them, and distributes the full picture back to all Managers. This gives each division awareness of the system state without requiring free-form inter-Manager chat.

---

## 10. Self-Monitoring and Emergency Escalation

### 10.1 Ops Monitoring

The Ops Monitor agent continuously watches:

-   **Agent liveness:** Heartbeat checks. If an agent hasn't emitted an event in > T seconds, flag as stalled.
-   **Loop detection:** If a Ticket has cycled through Reviewer → Coder > retry_budget times, kill the loop.
-   **Runaway detection:** If an agent's token burn rate exceeds 3σ above the running mean, flag as runaway.
-   **Production health:** HTTP health checks, error rate monitoring, latency tracking.

Self-correction actions (no human involvement):

1. Restart a stalled agent (Gas Town's NDI — agent is stateless, Ticket state persists).
2. Reassign a Ticket to a different Coder (via Dev Manager).
3. Auto-rollback a failed deploy.
4. Reduce parallelism if system resources are constrained.

### 10.2 Finance Monitoring

The Finance Manager watches:

-   **Burn rate:** USD/minute across all divisions.
-   **Budget cliffs:** Projected time-to-exhaustion at current burn rate.
-   **Anomaly detection:** Sudden cost spikes (e.g., a Coder stuck in a retry loop burning tokens).

Deterministic policy actions (not AI judgment — these are code rules):

| Condition                        | Action                                            |
| :------------------------------- | :------------------------------------------------ |
| Division spend >= 70% soft limit | `degrade_model` — Router shifts to cheaper models |
| Division spend >= 85% soft limit | `reduce_parallelism` — fewer concurrent agents    |
| Division spend >= 95% hard limit | `freeze_division` — pause non-critical Tickets    |
| Run spend >= 100% hard limit     | `emergency_page` — Comms pages human              |

### 10.3 The Escalation Chain

```
Agent self-correction (retry, refine)
  ↓ fails
Specialist → Manager (escalate within division)
  ↓ fails
Manager → Manager (cross-division via Memo/Requisition)
  ↓ fails
Manager → Comms Manager (request human escalation)
  ↓
Comms Manager → Human (Page with full context)
```

The human is the _last_ resort, not the first. Every level of the chain attempts self-resolution before escalating.

---

## 11. Implementation Roadmap

### Phase 1: Infrastructure — The Black Box

-   [ ] Universal event log (JSONL append + SQLite) with OTel trace correlation
-   [ ] Encrypted payload blob store with content-addressed hashing
-   [ ] Provider Bus with `ProviderAdapter` interface + one adapter (e.g., OpenRouter)
-   [ ] Smart Router with cost-weighted scoring

**Goal:** Every LLM call is logged, attributed, and routable.

### Phase 2: The Organization

-   [ ] `Agent` base class with `AgentIdentity` (role + constraints + SOP)
-   [ ] `Message` object with TalkHier fields (background, content, intermediate_output)
-   [ ] Division Inboxes and message routing
-   [ ] Capability token enforcement in the orchestrator
-   [ ] Dev Division: Manager + Architect + Coder + Reviewer + QA
-   [ ] Organizational artifacts: Brief, Ticket, Memo, Requisition

**Goal:** A Brief flows from Comms → Dev → back to Comms with enforced boundaries.

### Phase 3: Feedback and Self-Correction

-   [ ] Reviewer ↔ Coder feedback loops with retry budgets
-   [ ] QA integration testing gate
-   [ ] Memory store with Generative Agents retrieval scoring
-   [ ] Post-ticket Reflection step (write lessons to memory)
-   [ ] Standup protocol between Managers

**Goal:** The system self-corrects within bounded retry budgets.

### Phase 4: Ops and Governance

-   [ ] Ops Division: Manager + SRE + Monitor
-   [ ] Deploy pipeline: canary → health check → promote/rollback
-   [ ] Agent liveness monitoring + auto-restart
-   [ ] Loop/runaway detection
-   [ ] Finance policy engine (degrade_model, reduce_parallelism, freeze, page)
-   [ ] Emergency Page escalation via Comms

**Goal:** The system deploys, monitors, and self-heals autonomously.

---

## 12. References

| Paper / Framework                     | Key Contribution to This Proposal                                                                |
| :------------------------------------ | :----------------------------------------------------------------------------------------------- |
| MetaGPT (Hong et al., 2024)           | SOP-encoded prompts, role specialization, structured artifacts over dialogue                     |
| TalkHier (Wang et al., 2025)          | Structured communication protocol (Background + Content + Intermediate Output), evaluation teams |
| Generative Agents (Park et al., 2023) | Memory retrieval with Recency/Importance/Relevance scoring, reflection mechanism                 |
| Voyager (Wang et al., 2023)           | Iterative Write → Test → Refine loop, skill library as persistent memory                         |
| Gas Town (Yegge, 2026)                | Nondeterministic Idempotence, Git-backed state, role-based orchestration at scale                |
| EPO (Zhao et al., 2024)               | Hierarchical decomposition (planner vs. executor), reward-model-as-reviewer                      |
| CrewAI Flows (Moura, 2025)            | Deterministic backbone + scoped non-deterministic intelligence                                   |
| DAMCS (Yang et al., 2025)             | Structured sub-graph communication, hierarchical knowledge graph memory                          |
| Claude Code Teams                     | Task dependency tracking, file locking, lead/teammate hierarchy                                  |
| LangGraph (Campos, 2025)              | Graph-based execution with checkpointing, cyclic feedback loops                                  |
| AutoGen                               | ConversableAgent interface, UserProxy pattern                                                    |
| OpenTelemetry                         | W3C TraceContext correlation (trace_id, span_id), gen-ai semantic conventions                    |
| LiteLLM                               | Provider-agnostic routing patterns, OpenAI-compatible gateway                                    |

---

_This is a living document. It will evolve as we prototype, learn, and iterate._
