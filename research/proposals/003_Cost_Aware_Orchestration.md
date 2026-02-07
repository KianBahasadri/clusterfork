# Proposal 003: Cost-Aware Orchestration & Budget Enforcement

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-07

---

## 1. Motivation

Clusterfork’s tagline is funny because it’s true: a parallel agent swarm can burn tokens (and therefore money) extremely fast.

This becomes a _systems problem_ the moment we introduce:

-   parallel Coder agents
-   feedback loops (Reviewer ↔ Coder retries)
-   large-context Scout explorations
-   long-horizon projects with many epics

If cost is not modeled as a first-class constraint, the orchestrator will optimize for throughput until it hits a cliff: budget exhaustion, runaway retries, or “it technically works but cost is absurd.”

This proposal defines a **deterministic, enforceable cost model** that integrates directly into Clusterfork’s orchestration layer (Proposal 001), and complements the “Board” metaphor (Proposal 002) with real budget controls.

## 2. Goals and Non-Goals

### 2.1 Goals

1. **Make cost measurable.** Every LLM call and tool action should have attributable cost/usage in a ledger.
2. **Make cost enforceable.** The orchestrator must be able to _block_, _degrade_, or _escalate_ based on policy.
3. **Make cost optimizable.** Scheduling decisions (model choice, parallelism, retries, context size) should be cost-aware.
4. **Keep it deterministic.** Enforcement logic is code, not vibes (aligns with Proposal 001 P1).

### 2.2 Non-Goals

-   Building a perfect cost predictor (we’ll start with coarse estimates).
-   Solving provider billing edge cases (tax, rounding, tiering).
-   Shipping a full UI dashboard (CLI-first telemetry is sufficient initially).

## 3. Definitions

-   **Token**: provider-reported tokens for a single LLM call: `prompt_tokens`, `completion_tokens`.
-   **Cost Unit**: the normalized currency used by the policy engine. Typically USD, but can be “tokens” for offline mode.
-   **Scope**: the unit budgets attach to: `run`, `epic`, `task`, `agent`, `flow_step`.
-   **Soft Limit**: threshold that triggers degradation or warnings.
-   **Hard Limit**: threshold that blocks further spend unless explicitly overridden.
-   **Retry Budget**: max allowed evaluation loops per task or step (Reviewer ↔ Coder cycles).

## 4. Budget Hierarchy

Budgets should be hierarchical so that “one task went rogue” doesn’t bankrupt the entire run without visibility.

### 4.1 Budget Scopes

Recommended minimal set:

1. **Run Budget** (global cap for a single “session” or invocation)
2. **Epic Budget** (cap per feature request / user intent)
3. **Task Budget** (cap per atomic task)
4. **Agent Budget** (cap per agent role instance, optional)

### 4.2 Precedence and Enforcement

Enforcement always checks from _narrowest to broadest_:

1. Task
2. Epic
3. Run

The first scope to breach its hard limit blocks the action.

### 4.3 Budget Object (Suggested Schema)

```python
from dataclasses import dataclass
from typing import Literal, Optional

Scope = Literal["run", "epic", "task", "agent"]
Currency = Literal["usd", "tokens"]

@dataclass(frozen=True)
class Budget:
    scope: Scope
    currency: Currency
    soft_limit: float
    hard_limit: float
    warn_at: Optional[float] = None  # e.g. 0.80 meaning 80% of hard_limit
```

## 5. Cost Ledger (Accounting)

### 5.1 Why a Ledger?

If we want deterministic governance, we need append-only accounting:

-   auditing (“why did we spend $7 on this task?”)
-   anomaly detection (“token burn spiked at 02:14”)
-   optimization (“Scout is too expensive, trim context”)

### 5.2 Event Types

Minimum set of ledger events:

-   `llm_call`: prompt/completion tokens, model, provider, latency
-   `tool_call`: which tool, duration (often “free” but can be tracked)
-   `retry`: a counter event that increments “attempt number”
-   `escalation`: user approval required / override granted

### 5.3 Ledger Event Schema (Suggested)

```python
from dataclasses import dataclass
from typing import Any, Literal, Optional

EventType = Literal["llm_call", "tool_call", "retry", "escalation"]

@dataclass(frozen=True)
class CostEvent:
    type: EventType
    ts: float  # unix time

    # Attribution
    run_id: str
    epic_id: Optional[str]
    task_id: Optional[str]
    agent_id: Optional[str]
    agent_role: Optional[str]  # "scout" | "architect" | "coder" | "reviewer" | "qa" | "user"

    # LLM details (for llm_call)
    provider: Optional[str]
    model: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    usd_cost: Optional[float]  # when currency=usd mode is enabled

    # Tool details (for tool_call)
    tool_name: Optional[str]
    tool_duration_ms: Optional[int]

    # Free-form metadata
    meta: dict[str, Any]
```

### 5.4 Storage Format

Two-tier approach:

-   **Tier 1 (Git-backed, human-readable)**: append-only `ndjson` ledger committed per run/epic summary.
-   **Tier 2 (Queryable)**: SQLite database for aggregation and dashboards.

Rationale:

-   Git provides durability (Proposal 001 §7.3).
-   SQLite enables fast queries without adding infrastructure.

## 6. Estimation (Preflight Checks)

To enforce budgets proactively, we need a rough estimator _before_ spending.

### 6.1 Estimation Strategy

For each prospective `llm_call`, compute:

-   `estimated_prompt_tokens`: from serialized message + retrieved context + system prompt
-   `estimated_completion_tokens`: configured max (or historical average per role)
-   `estimated_cost`: model pricing function or token-based approximation

Then enforce:

-   if `remaining_budget < estimated_cost`, block or degrade _before calling the model_

### 6.2 Accuracy Requirements

We don’t need perfect estimates. We need _safe_ estimates:

-   slightly conservative estimates are fine
-   gross underestimates are dangerous (lead to budget cliffs)

## 7. Policy Engine (Enforcement and Degradation)

The policy engine is a deterministic function of:

-   budget state (remaining per scope)
-   task state (attempt number, retry count)
-   agent role (scout vs coder)
-   step criticality (planning vs implementation vs review)

### 7.1 Policy Actions

When approaching soft limit:

-   **degrade_model**: switch to cheaper/faster model
-   **shrink_context**: reduce retrieved chunks / summarize context
-   **reduce_parallelism**: limit concurrent coders
-   **tighten_retries**: reduce retry budget, or require escalation earlier
-   **enable_sampling_controls**: lower `max_tokens`, lower temperature, etc.

When hard limit would be exceeded:

-   **block**: stop execution and emit escalation request
-   **escalate_to_user**: request explicit approval with an incremental budget ask

### 7.2 Example Policy Table

| Condition                   | Action                               |
| --------------------------- | ------------------------------------ |
| Task spend \(\ge\) 80% hard | degrade_model + shrink_context       |
| Epic spend \(\ge\) 90% hard | reduce_parallelism + tighten_retries |
| Run spend \(\ge\) hard      | block + escalate_to_user             |

### 7.3 Integration with “The Board” (Proposal 002)

The Board personas are useful as **advisors**, but enforcement must remain deterministic.

Recommended split:

-   **CFO Persona (Penny Pincher)**: proposes optimizations (e.g., “switch Scout to smaller model”)
-   **Policy Engine**: executes rules (hard stop / degrade / escalate)

This prevents “LLM as policy” failure modes while preserving the fun metaphor.

## 8. Cost-Aware Scheduling

Proposal 001 introduces parallel coders and a scheduler. This proposal makes the scheduler cost-aware.

### 8.1 Scheduling Inputs

-   dependency graph (critical path first)
-   file locks
-   estimated task cost
-   remaining epic/run budget
-   expected value heuristic (risk/complexity)

### 8.2 Scheduling Outputs

-   concurrency level (number of coders)
-   model tier per role
-   maximum retries per task

### 8.3 Simple Heuristic (Initial)

Start with:

-   Always schedule critical-path tasks first.
-   Cap concurrency by remaining budget:
    -   more budget → more parallelism
    -   less budget → fewer agents, cheaper models

Later, upgrade to:

-   a knapsack-style optimizer (maximize “expected progress” under budget)

## 9. Metrics (What We Optimize)

To “industrialize vibe coding,” we need KPIs that connect cost, quality, and velocity:

### 9.1 Core KPIs

-   **Cost per merged diff**
-   **Cost per passing test run**
-   **Cost per successful task** (done without escalation)
-   **Rework rate**: Reviewer change requests per task
-   **Token burn rate**: tokens/minute and USD/hour

### 9.2 Guardrail Metrics

-   **Stall detector**: repeated failures without progress
-   **Runaway detector**: high spend with no new artifacts
-   **Loop detector**: repeating similar diffs or messages

## 10. Implementation Roadmap

### Phase 1: Ledger + Basic Limits

-   [ ] Add `CostEvent` ledger write path for each LLM call
-   [ ] Aggregate spend by scope (task/epic/run)
-   [ ] Enforce hard limits (block + escalate)

### Phase 2: Preflight Estimation + Soft Limits

-   [ ] Add token estimation for serialized messages/context
-   [ ] Soft limit actions: shrink context, tighten retries

### Phase 3: Scheduler Integration

-   [ ] Make parallelism dynamic based on remaining budget
-   [ ] Add role-based model routing

### Phase 4: Optimization + Governance UX

-   [ ] CLI “ticker” view of burn rate (ties to Proposal 002 §5)
-   [ ] Cost reports per epic + per agent
-   [ ] Pluggable policy rules (config-driven)

## 11. Open Questions

1. **Pricing source-of-truth:** Hard-code pricing tables, read from config, or query provider APIs?
2. **Currency choice:** Should “tokens-only” mode exist for offline / open-source setups?
3. **Value estimation:** How do we estimate “expected progress” so the scheduler can trade off tasks?
4. **Override policy:** What is the default user experience when a hard limit blocks? (One-click approve, or require explicit budget delta?)
5. **Data retention:** How much raw prompt/context should be stored (privacy + repo bloat)?

## 12. References

-   Proposal 001: Core Architecture (Task scheduling, retries, persistence)
-   Proposal 002: Corporate Metaphor (Board personas for cost/quality/velocity trade-offs)

---

_Tokens are the new electricity: if you can’t meter it, you can’t scale it._
