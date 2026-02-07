# Proposal 005: Autonomous Divisional Architecture

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-07

---

## 1. Motivation

Single-agent coding assistants have reached their limit. Multi-agent systems like those proposed in 001-004 offer a path forward, but they often lack the structural rigor required for true, unsupervised autonomy. Without clear boundaries, work becomes a "soup" of context where agents talk past each other, duplicate effort, or accidentally exceed their authority.

Furthermore, LLMs have been trained on the sum of human organizational knowledge. They "know" how a company works. By aligning Clusterfork's architecture with honest organizational concepts—**Managers**, **Inboxes**, **Memos**, **Requisitions**—we leverage the latent reasoning capabilities of the models. We don't need to teach an agent how to be a "Manager"; it already has a deep bias toward that behavior.

This proposal defines a **Divisional Architecture** that treats the system as a lean, high-throughput organization. It enforces strict work flow, hard capability boundaries, and universal observability, enabling a single human to operate a parallel agent swarm with zero oversight and total forensic visibility.

---

## 2. Design Principles

| #      | Principle                          | Description                                                                                                   |
| :----- | :--------------------------------- | :------------------------------------------------------------------------------------------------------------ |
| **P1** | **Work Flows Through the Org**     | No agent acts in isolation. All cross-division changes must use formal organizational protocols.              |
| **P2** | **Strict Divisional Boundaries**   | Only Ops can deploy. Only Dev can code. Only Finance can spend. Boundaries are enforced in code, not prompts. |
| **P3** | **Honest Mapping**                 | Use real-world names (Manager, Ticket, Memo) only when they honestly map to system behavior.                  |
| **P4** | **Zero Oversight, Total Auditing** | The human does not supervise; they audit. Every action is logged in full fidelity for forensic review.        |
| **P5** | **Provider Agnosticism**           | Intelligence is a commodity. The system must be able to swap LLM providers without core logic changes.        |

---

## 3. The Organization (Divisions)

Clusterfork is organized into four primary divisions. Each division has a **Manager** agent responsible for coordination and several specialist agents.

```
                          ┌─────────────┐
                          │    Human    │
                          │   (Owner)   │
                          └──────┬──────┘
                                 │ briefs, pages
                                 v
                    ┌────────────────────────┐
                    │    Comms Division       │
                    │  Manager + Liaison(s)   │
                    └───┬──────────┬─────────┘
               memos    │          │    memos
          ┌─────────────┘          └──────────────┐
          v                                       v
┌──────────────────┐  requisitions  ┌──────────────────┐
│  Dev Division    │ ─────────────> │  Ops Division    │
│ Manager + Devs   │ <───────────── │ Manager + SREs   │
│ + Reviewers + QA │ incident rpts  │ + Monitors       │
└────────┬─────────┘                └────────┬─────────┘
         │        expense reports             │
         │         requisitions               │
         v                                    v
         └──────> ┌──────────────────┐ <──────┘
                  │ Finance Division │
                  │ Manager + Acct   │
                  └──────────────────┘
```

### 3.1 Division Responsibilities

-   **Comms (Communications):** The sole interface for the human. Translates intent into **Briefs** for Dev. Delivers results and **Expense Reports**. Sends **Pages** for emergencies.
-   **Dev (Development):** Architecture, implementation, and quality. Breaks Briefs into **Tickets**. Requisitions deployment from Ops and budget from Finance.
-   **Ops (Operations):** Deployment, monitoring, and agent health. Owns production. Executes **Requisitions** from Dev. Files **Incident Reports** when production fails.
-   **Finance (Finance):** Cost accounting and provider routing. Enforces budget policies. Issues **Expense Reports**. Manages the **LLM Provider Bus**.

---

## 4. Organizational Protocols (The "Org Tools")

All cross-division work flows through **Inboxes** using typed artifacts.

```python
@dataclass
class Memo:
    id: str
    sender_division: str
    recipient_division: str
    subject: str
    body: str
    attachments: List[ArtifactRef]
    correlation_id: str  # Links to the original Brief/Task

@dataclass
class Requisition:
    id: str
    type: Literal["deploy", "budget", "resource"]
    priority: int
    requirements: Dict[str, Any]
    justification: str
```

### 4.1 The Artifact Lifecycle

1.  **Brief:** User input → Comms Manager → Dev Inbox.
2.  **Ticket:** Dev Manager → Dev Specialists (Internal work).
3.  **Requisition:** Dev Manager → Ops Inbox (e.g., "Deploy this branch to staging").
4.  **Memo:** Ops Manager → Dev Manager ("Deploy successful at URL X").
5.  **Expense Report:** Finance Manager → Comms Manager ("This task cost $4.52").

---

## 5. Capability Boundaries (The Org Chart)

Capabilities are enforced by the **Orchestrator** using a deterministic RBAC (Role-Based Access Control) matrix. Agents cannot "hallucinate" their way into other divisions' tools.

| Capability             | Comms |  Dev  |  Ops  | Finance |
| :--------------------- | :---: | :---: | :---: | :-----: |
| `user_comms`           | **Y** |   -   |   -   |    -    |
| `source_control_write` |   -   | **Y** |   -   |    -    |
| `production_deploy`    |   -   |   -   | **Y** |    -    |
| `budget_enforcement`   |   -   |   -   |   -   |  **Y**  |
| `provider_config`      |   -   |   -   |   -   |  **Y**  |
| `emergency_page`       | **Y** |   -   |   -   |    -    |

---

## 6. Universal Full-Fidelity Log

The "Universal Log" is an append-only, immutable stream of every event in the system.

### 6.1 Event Schema

```json
{
    "timestamp": "2026-02-07T14:00:00Z",
    "trace_id": "a0892f35...",
    "span_id": "f03067aa...",
    "division": "Dev",
    "agent_id": "Coder-Alpha",
    "event_type": "tool_call",
    "detail": "git_commit",
    "cost_usd": 0.0012,
    "payload_ref": "sha256:e3b0c442..."
}
```

### 6.2 Full-Fidelity Storage

Raw payloads (prompts, completions, tool results) are stored in a content-addressed, encrypted blob store. The event log contains only the reference. This allows for total forensic reconstruction of any agent decision.

---

## 7. LLM Provider Bus & Smart Routing

Finance manages the **Provider Bus**, ensuring the system is never locked into a single vendor.

-   **Adapters:** Lightweight wrappers for OpenAI, Anthropic, Azure, OpenRouter, etc.
-   **Smart Router:** A deterministic scoring engine that selects providers based on:
    1.  **Cost:** Current token pricing.
    2.  **Capability:** Model class (Reasoning vs. Fast).
    3.  **Health:** Recent latency and error rates.
-   **Policy:** Finance can set a "Max Cost per Ticket" policy that forces the Router to use cheaper models as the budget depletes.

---

## 8. Self-Monitoring & Emergency Escalation

The system is designed to be self-healing.

-   **Ops Monitoring:** Detects stuck agents or infinite loops. Restarts agents or cancels stalled Tickets.
-   **Finance Monitoring:** Detects budget anomalies or runaway spend.
-   **The "Page":** Comms sends a notification to the human _only_ when:
    -   A hard budget limit is reached.
    -   A division Manager reports an unrecoverable "Blocker."
    -   System-wide health (e.g., Provider Bus failure) is critical.

---

## 9. Implementation Roadmap

### Phase 1: Infrastructure (The "Black Box")

-   Implement the Universal Event Log and Encrypted Payload Store.
-   Build the Provider Bus with a single adapter (e.g., OpenRouter).

### Phase 2: The Organization

-   Define the four Division Managers.
-   Implement the Inbox and Memo/Requisition protocols.
-   Enforce the Capability Matrix in the Orchestrator.

### Phase 3: Governance & Ops

-   Implement the Finance Policy Engine (budget limits).
-   Build the Ops Deployment Pipeline (canary/rollback).
-   Add the "Emergency Page" escalation path.
