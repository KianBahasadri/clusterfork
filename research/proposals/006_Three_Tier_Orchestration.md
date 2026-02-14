# Proposal 006: Three-Tier Orchestration

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-07

---

## 1. Architecture

The Three-Tier Orchestration model consists of:

1. **You + the Orchestrator (co-equal)** — you provide intent + budget; the Orchestrator produces the plan and spawns/budgets Managers.
2. **Managers** — one per plan section; each owns an objective and a budget.
3. **Workers** — ephemeral agents spawned by Managers to execute tasks; charged to the Manager's budget.

- **Three-tier hierarchy**: Human + Orchestrator -> Managers -> Workers.
- **Budget-driven control**: Budget is the hard execution constraint.
- **Dynamic structure**: Managers are spawned from plan sections at runtime.
- **Universal logging**: LLM calls, tool calls, and memos are persisted.
- **Model seniority**: Managers choose model tier per task.

---

## 2. The Three Tiers

At startup, only **Tier 1** exists (you + the Orchestrator). Tiers 2 and 3 are spawned dynamically from the plan.

```
┌───────────────────────────────┐   ┌──────────────────────────────────┐
│        TIER 1: HUMAN           │   │     TIER 1: ORCHESTRATOR          │
│                               │   │                                  │
│  - intent                     │<->│  - plan from intent               │
│  - total budget               │   │  - spawn Managers                 │
│  - kill switch                │   │  - allocate Manager budgets       │
│                               │   │  - collect + report results       │
└───────────────┬───────────────┘   └───────────────┬──────────────────┘
                │ intent + budget                   │ sections + budgets
                └───────────────┬───────────────────┘
                                v
┌──────────────────────────────────────────────────────────────────┐
│              TIER 2: MANAGERS (dynamically spawned)               │
│         one per plan section, each with its own budget            │
└───┬──────────────────────┬──────────────────────┬────────────────┘
    │ $12 budget            │ $8 budget            │ $5 budget
    v                       v                      v
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Auth Manager  │     │ API Manager   │     │ Frontend Mgr  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │ spawn workers       │ spawn workers       │ spawn workers
       v                     v                     v
┌──────────────────────────────────────────────────────────────────┐
│                    TIER 3: WORKERS (ephemeral)                    │
│     short-lived agents picked by seniority for each task           │
└──────────────────────────────────────────────────────────────────┘
```

### 2.1 Tier 1: Human + Orchestrator

At the start of a run, there are only two actors: you and the Orchestrator. In the org hierarchy, you're peers: you're not a subsystem under the Orchestrator, and the Orchestrator doesn't outrank you.

-   **You (Owner) do**: provide intent, set the total budget, (optionally) override/abort.
-   **The Orchestrator does**: plan from intent, spawn Managers, allocate Manager budgets, collect results + cost reports.

The Orchestrator is the only permanent agent in the system. It is a fully capable agent (ReAct loop) that can use tools to research and refine the plan before committing to it.

**What it does:**

```python
class Orchestrator:
    """Tier-1 control plane. A full agent loop that plans and coordinates."""

    def run(self, user_intent: str, total_budget_usd: float):
        # Step 1: Research & Planning Loop
        # The Orchestrator can use tools (search, read docs) to understand the task
        # before generating the final plan.
        plan = self.develop_plan_with_tools(user_intent)

        # plan = {
        #   "sections": [
        #     { "name": "Authentication System", "description": "...", "budget_pct": 0.40 },
        #     { "name": "REST API",              "description": "...", "budget_pct": 0.30 },
        #     { "name": "Frontend",              "description": "...", "budget_pct": 0.20 },
        #     { "name": "Deployment & CI/CD",    "description": "...", "budget_pct": 0.10 },
        #   ]
        # }

        # Step 2: Spawn a Manager for each section, with a budget slice
        managers = []
        for section in plan["sections"]:
            budget = total_budget_usd * section["budget_pct"]
            manager = self.spawn_manager(section, budget)
            managers.append(manager)

        # Step 3: Let them run (in parallel). Collect results.
        results = self.run_managers_parallel(managers)

        # Step 4: Report to human
        self.report(results)

    def handle_budget_request(self, manager_id: str, amount_usd: float, justification: str) -> bool:
        """Decide whether to grant extra funds.

        The Orchestrator evaluates the request against:
        1. Remaining total budget (reserves)
        2. Progress of the requesting Manager
        3. Potential to reallocate from under-budget Managers
        """
        # ... logic ...
        return approved
```

**What it does NOT do:**

-   It does not write code.
-   It does not review code.
-   It does not deploy anything.
-   It does not tell Managers _how_ to do their job. It tells them _what_ needs doing and gives them a budget.

### 2.2 Tier 2: Managers

A Manager is a semi-autonomous agent that owns one section of the plan. It receives a description of what to accomplish and a USD budget. Everything else is up to it.

**What a Manager can do:**

-   Spawn worker agents at any seniority level (Intern through Staff) — each LLM call costs money from the Manager's budget.
-   Use tools (read/write files, run tests, run linters, execute commands) — within whatever scope the Orchestrator granted.
-   Communicate with other Managers via Memos.
-   Request more budget from the Orchestrator (the Orchestrator can approve or deny).

**What a Manager cannot do:**

-   **Spawn sub-Managers.** The hierarchy is strictly three tiers. If a task is too large for one Manager, the Orchestrator failed to scope the plan correctly.
-   Exceed its budget. Hard stop. The Provider Bus rejects calls when budget hits zero.
-   Access tools outside its granted scope.
-   Talk to the human directly. Only the Orchestrator does that.

```python
@dataclass
class Manager:
    """A semi-autonomous agent that owns one section of the plan."""
    manager_id: str
    section_name: str           # e.g. "Authentication System"
    section_description: str    # What needs to be accomplished
    budget_usd: float           # How much this Manager can spend
    budget_spent_usd: float     # Running total
    capabilities: set[str]      # Tools this Manager (and its workers) can use
    seniority_level: str        # The model tier used for the Manager itself

    def execute(self):
        """The Manager's main loop.

        This is where the Manager's own LLM intelligence kicks in.
        The Manager reads its section_description, decides what
        to do, and starts spawning workers and coordinating.

        The Manager's SOP (baked into its system prompt) tells it:
          1. Analyze the section requirements
          2. Break them into tasks
          3. For each task, choose a seniority level and spawn a worker
          4. Review worker output
          5. Iterate until requirements are met or budget runs out
          6. Report results back to the Orchestrator
        """
        ...

    def spawn_worker(self, task: str, seniority: str) -> WorkerResult:
        """Spawn a short-lived worker agent for a specific task.

        The worker is ephemeral — it receives one task, does the work,
        and returns a result. Its LLM calls are charged to this Manager's budget.
        """
        ...

    def request_budget_increase(self, amount_usd: float, justification: str) -> bool:
        """Ask the Orchestrator for more money.

        Calls `orchestrator.handle_budget_request()`.
        If denied, the Manager must degrade quality (cheaper models) or cut scope.
        """
        ...
```

### 2.3 Tier 3: Workers

Workers are short-lived execution agents spawned by Managers. They get one task, do the work, return a result, and terminate.

-   **Ephemeral**: safe to kill/restart without losing progress (the Manager owns the state).
-   **Seniority-based**: the Manager chooses Intern → Staff per task.
-   **Budget-charged**: every Worker LLM call is charged to the spawning Manager's budget.
-   **No human access**: Workers never talk to the human; they report to their Manager.

```python
@dataclass
class Worker:
    """Short-lived agent spawned by a Manager."""
    worker_id: str
    manager_id: str
    seniority_level: str
    task: str

    def execute(self) -> WorkerResult:
        ...
```

### 2.4 Phased Execution

To handle dependencies without complex cross-manager locking, the Orchestrator executes the plan in **Phases**.

1.  **Phase 1 (Design)**: Orchestrator spawns an **Architect Manager**.
    -   _Goal_: Write `api_spec.json` and `db_schema.sql`.
    -   _Budget_: $2.
    -   _Output_: The spec files.
2.  **Phase 2 (Build)**: Orchestrator spawns **Auth Manager**, **API Manager**, and **Frontend Manager** _in parallel_.
    -   _Input_: They all get the `api_spec.json` from Phase 1.
    -   _Goal_: Build their parts.
    -   _Budget_: $15 total.
3.  **Phase 3 (Integrate)**: Orchestrator spawns **QA Manager**.
    -   _Goal_: Run end-to-end tests.

**Why this wins:**

-   **Simple Managers**: Managers don't need to know about "dependencies" or "blocking." They just start, do their job, and finish.
-   **Simple Orchestrator**: It just loops through a list of phases.
-   **No Deadlocks**: You can't get stuck waiting for a message that never comes.

---

## 3. Budget as Control

Budget is the universal constraint. A Manager that runs out of money simply stops. The Provider Bus enforces the policy: `budget_remaining > 0`.

### 3.1 Budget Flow

```
Human sets total budget: $25
  │
  ▼
Orchestrator reserves 10% for itself: $2.50
Orchestrator allocates the rest to Managers:
  │
  ├── Auth Manager:       $9.00  (40% of remaining)
  ├── API Manager:        $6.75  (30%)
  ├── Frontend Manager:   $4.50  (20%)
  └── Deploy Manager:     $2.25  (10%)
```

### 3.2 Budget Rules

1. **Every LLM call is charged to the requesting Manager's budget.** No exceptions. The Provider Bus tracks this automatically.
2. **Managers choose seniority per-task.** Budget efficiency depends on appropriate model selection for the complexity of the task.
3. **Budget exhaustion = hard stop.** The Manager reports whatever it has back to the Orchestrator with a "budget exhausted" status.
4. **Escalation.** A Manager can request more budget from the Orchestrator. The Orchestrator decides whether to grant it or deny it.

---

## 4. The Universal Log (Simplified)

We capture three core things for every event:

1. **What happened** — every LLM call, tool call, and message.
2. **Who did it** — which Manager, which worker.
3. **What it cost** — tokens, USD, provider.

```python
@dataclass
class Event:
    timestamp: datetime
    trace_id: str               # Links events in the same work chain
    manager_id: str             # Which Manager this belongs to
    agent_id: str               # Which specific agent (Manager or worker)
    event_type: str             # "llm_call", "tool_call", "memo_sent", etc.
    detail: str                 # Human-readable description
    cost_usd: float             # How much this event cost
    provider: str | None        # Which LLM provider
    model: str | None           # Which model
    payload_ref: str | None     # SHA-256 hash into encrypted blob store
```

Full-fidelity payload storage is the same: encrypted blob store, content-addressed, retention policies. The event log is append-only JSONL.

---

## 5. Provider Bus and Seniority Levels

The system uses a `ProviderAdapter` interface and a `SmartRouter` with seniority-based filtering to manage LLM requests. The Orchestrator configures the Provider Bus at startup, and the bus enforces budgets per-Manager automatically.

| Seniority     | Typical Models      | When to Use                            |
| :------------ | :------------------ | :------------------------------------- |
| **Intern**    | Haiku, GPT-4o-mini  | Formatting, summarization, boilerplate |
| **Junior**    | Llama 3 8B, GPT-3.5 | Simple code changes, test writing      |
| **Mid**       | Sonnet 3.5, GPT-4o  | Standard features, debugging           |
| **Senior**    | Opus, GPT-4-Turbo   | Architecture, complex refactoring      |
| **Staff**     | o1, o3-mini         | Deep reasoning, root cause analysis    |
| **Principal** | o1-pro              | System design, the hard stuff          |

---

## 6. How It Actually Plays Out

**You say:** "Build me a CLI tool that converts CSV files to JSON with validation."

**Orchestrator:**

1. Plans: two sections — "Core converter logic" (60% budget) and "CLI interface + tests" (40% budget).
2. Spawns two Managers.

**Manager A ("Core Converter"):**

1. Spawns a Senior worker to design the converter architecture.
2. Spawns two Mid workers in parallel: one for the parser, one for the validator.
3. Spawns a Mid worker to review both outputs.
4. Spawns a Junior worker to write unit tests.
5. Reports back: "Done, $3.20 of $6.00 spent."

**Manager B ("CLI + Tests"):**

1. Spawns a Mid worker to implement the CLI using Click.
2. Spawns a Junior worker for integration tests.
3. Reports back: "Done, $1.80 of $4.00 spent."

**Orchestrator:**

1. Collects results from both Managers.
2. Reports to you: "Feature complete. Total cost: $5.00 of $10.00 budget. Surplus: $5.00."

---

## 7. Implementation Strategy & Tooling

**LangGraph** is the runtime engine for this architecture. Here's why:

-   **Dynamic Spawning**: Its `Send` API (Map-Reduce pattern) lets us spawn parallel Manager subgraphs dynamically at runtime based on the plan. This is exactly what we need when the Orchestrator reads a plan and creates Managers on-the-fly.
-   **Cyclic Loops**: Managers need to loop (Plan → Spawn Worker → Review → Loop). LangGraph's graph-based model naturally supports cycles.
-   **Persistence & Checkpointing**: Built-in SQLite/Postgres checkpointing means every step is persisted. This gives us the "Universal Log" and crash recovery for free.
-   **Control Flow Precision**: Low-level control means we can enforce strict budget policies at the graph edges (rejecting LLM calls when budget = 0).

### 7.1 Stack: LangGraph + Pydantic

-   **Tier 1 (Orchestrator)**: A `StateGraph` that runs the Planning node, then uses LangGraph's `Send` API to spawn parallel **Manager Subgraphs**.
-   **Tier 2 (Managers)**: Each Manager is a reusable `StateGraph` (subgraph) with a standard loop: `Plan` -> `Spawn Worker` -> `Review` -> `Loop`.
-   **Tier 3 (Workers)**: Simple nodes that execute a single LLM call.

---

## 8. Technical Specifications

### 8.1 The Manager Runtime (How they get "Personality")

Managers are not distinct Python classes; they are instances of a generic `ManagerNode` configured with a dynamic system prompt.

**The "Personality" Injection:**

When the Orchestrator spawns a Manager, it injects a `ManagerContext` object. This context is used to hydrate the Manager's system prompt at runtime.

```python
@dataclass
class ManagerContext:
    section_name: str
    section_description: str
    budget_limit: float
    seniority: str  # e.g., "senior"

def build_manager_prompt(ctx: ManagerContext) -> str:
    return f"""
    You are the **{ctx.section_name} Manager**.

    YOUR OBJECTIVE:
    {ctx.section_description}

    YOUR CONSTRAINTS:
    1. Budget: ${ctx.budget_limit} USD.
    2. You cannot write code yourself. You must spawn workers.
    3. You operate at a '{ctx.seniority}' level of rigor.

    AVAILABLE TOOLS:
    - spawn_worker(task, seniority)
    - read_file(path)
    - send_memo(recipient, content)
    - request_budget(amount, reason)
    """
```

### 8.2 Communication Protocol (Memos)

Communication between Managers is handled via a structured `Memo` artifact, stored in the global state (LangGraph `channels`).

```python
class Memo(BaseModel):
    id: str = Field(default_factory=uuid4)
    from_manager: str
    to_manager: str  # or "ALL"
    topic: str
    content: str
    timestamp: datetime

    # Priority helps the receiving Manager decide when to read it
    priority: Literal["FYI", "BLOCKER", "URGENT"]
```

**The Message Bus:**
LangGraph's `Annotated[list[Memo], add_messages]` channel acts as the bus. When Manager A yields a `Memo`, it is appended to the global state. Manager B's loop includes a `check_inbox` step that filters this list for `to_manager == "Manager B"`.

### 8.3 The Universal Log Implementation

We leverage LangGraph's **CheckpointSaver** to implement the Universal Log without writing extra code.

1.  **Storage**: We use a Postgres-backed checkpointer.
2.  **Schema**: Every step (node execution) saves a snapshot of the state.
3.  **Traceability**: The `thread_id` tracks the entire run. `checkpoint_id` tracks individual steps.

To get the "Cost Visibility" view, we simply query the checkpoint history:

```sql
SELECT
    metadata->>'manager_id' as manager,
    key as step,
    channel_values->'budget_spent' as cost
FROM checkpoints
WHERE thread_id = 'run_123'
ORDER BY created_at;
```

---

---

<sup>**Author's note:** The author was lowkey buzzed as fuck at 11 AM on a Saturday when he came up with this.</sup>

