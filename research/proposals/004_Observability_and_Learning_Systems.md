# Proposal 004: Observability & Learning Systems

**Author:** Kian  
**Status:** Draft  
**Created:** 2026-02-07

---

## 1. Motivation

Proposals 001 and 002 define what Clusterfork _is_ — a hierarchical orchestration of specialized agents executing SOPs with corporate structure and governance. But neither addresses the critical question: **How do we know if it's working? How does it improve?**

A system that churns through tasks but never learns is just expensive busywork. A system that's invisible to the user is flying blind. Clusterfork needs two critical capabilities:

1. **Observability** — Real-time visibility into agent decisions, task flow, resource consumption, and bottlenecks.
2. **Learning** — Post-execution analysis that captures lessons, refines SOPs, and improves future performance.

This proposal defines the observability architecture and the reflection/learning loop that turns Clusterfork from a one-shot executor into a self-improving system.

---

## 2. The Observability Stack

### 2.1 Event Stream

Every meaningful action in Clusterfork emits a structured event to a central event log:

```python
class Event:
    """A discrete, observable action in the system."""
    timestamp: datetime
    event_type: str                    # "task_created", "agent_claimed", "reviewer_approved", etc.
    agent_id: str                      # who triggered this
    task_id: str | None                # which task (if applicable)
    metadata: dict                     # context-specific data
    metrics: dict                       # cost (tokens, time), quality signals
```

**Event types include:**

| Category           | Examples                                                                            |
| ------------------ | ----------------------------------------------------------------------------------- |
| **Lifecycle**      | task_created, task_claimed, task_completed, task_failed                             |
| **Agent Actions**  | agent_initialized, agent_reasoning_started, agent_submitted, agent_escalated        |
| **Feedback Loops** | reviewer_approved, reviewer_changes_requested, reviewer_rejected, coder_resubmitted |
| **Resource**       | tokens_used, context_window_filled, file_locked, file_unlocked                      |
| **Quality**        | test_passed, test_failed, lint_error_found, coverage_increased                      |
| **Escalation**     | escalated_to_user, escalated_to_board, retry_budget_exhausted                       |

Every event is immutable and appended to a distributed log (inspired by Event Sourcing patterns).

### 2.2 The Dashboard: "The Clusterfork Network"

A real-time web dashboard displays system health in multiple views:

#### 2.2.1 Executive Overview (CEO View)

-   **Current Sprint:** Feature list with ETA, risk level
-   **Health Metrics:** Cost/hour, features/hour, error rate
-   **Board Sentiment:** Penny Pincher/Velocity Capital/Quality Fund confidence levels
-   **Critical Alerts:** Tasks blocked, agents in failure state, budget warnings

#### 2.2.2 Task Flow View (COO View)

A live dependency graph showing:

-   All active tasks with status (pending, in-progress, review, done, failed)
-   Task-to-task dependencies with estimated completion times
-   File locks and conflict warnings
-   Agent assignments and capacity utilization

```
[TASK-001: Auth] ──DONE──┐
                         ├─→ [TASK-004: Integration Tests] ──IN_REVIEW── (Reviewer)
[TASK-002: API]  ──REVIEW─┘
                                         ↓
[TASK-003: UI]   ──IN_PROGRESS── (Coder-B, 45% done)
```

#### 2.2.3 Agent Performance View (HR View)

For each agent role:

-   **Throughput:** Tasks completed per hour
-   **Quality:** Pass rate through reviewer, test coverage, error-to-fix ratio
-   **Efficiency:** Average tokens used per task, re-submissions required
-   **Reliability:** Uptime, escalation rate, retry budget consumption

Agents performing below SLA appear in red ("Performance Improvement Plan Required").

#### 2.2.4 Memory & Decision View (Architect View)

-   Active memory entries with recency/importance/relevance scores
-   Decision log: what architecture choices were made and why
-   Skill library: reusable patterns discovered and their effectiveness

### 2.3 Metrics Taxonomy

#### 2.3.1 Velocity Metrics

| Metric              | Definition                      | Target                       |
| ------------------- | ------------------------------- | ---------------------------- |
| **Features/Hour**   | Completed features per hour     | > 1.5 (varies by complexity) |
| **Time-to-Review**  | Task → Reviewer submission, avg | < 5 min                      |
| **Time-to-Merge**   | Task → Merged to main, avg      | < 15 min                     |
| **Reviewer Cycles** | Avg cycles before approval      | ≤ 2                          |

#### 2.3.2 Quality Metrics

| Metric                   | Definition                      | Target |
| ------------------------ | ------------------------------- | ------ |
| **Test Coverage**        | % of code covered by tests      | > 80%  |
| **Lint Pass Rate**       | % of submissions passing lint   | > 98%  |
| **Integration Failures** | Failed QA tests post-merge      | = 0    |
| **Bug Escape Rate**      | Bugs found post-merge in 7 days | < 2%   |

#### 2.3.3 Cost Metrics

| Metric               | Definition                    | Target            |
| -------------------- | ----------------------------- | ----------------- |
| **Tokens/Feature**   | LLM tokens used per feature   | < baseline \* 1.1 |
| **Cost/Hour**        | $ spent (token cost) per hour | < budget cap      |
| **Efficiency Ratio** | Quality score / Tokens used   | > baseline        |

#### 2.3.4 Reliability Metrics

| Metric                      | Definition                       | Target |
| --------------------------- | -------------------------------- | ------ |
| **Agent Uptime**            | % of time agents are operational | > 99%  |
| **Escalation Rate**         | % of tasks escalated to user     | < 5%   |
| **Retry Budget Exhaustion** | Tasks that hit max retries       | = 0    |
| **State Recovery Success**  | % of crashes recovered cleanly   | = 100% |

---

## 3. The Reflection Loop

After a feature is merged, the system enters a **Reflection Phase** where it analyzes what happened and captures lessons for future iterations.

### 3.1 The Reflection Agent

A dedicated `ReflectionAgent` runs post-merge and performs:

```python
class ReflectionAgent(Agent):
    """Post-execution analysis and learning."""

    def reflect_on_task(self, task: Task, artifacts: list[Artifact]) -> Reflection:
        """Analyze what happened and generate insights."""

        # 1. Extract the decision log
        decisions = extract_architectural_decisions(artifacts)

        # 2. Calculate outcome metrics
        metrics = {
            "cost_delta": actual_tokens - estimated_tokens,
            "quality_score": reviewer_approval + test_results,
            "efficiency": quality_score / actual_tokens,
            "cycle_count": reviewer_cycles,
        }

        # 3. Identify patterns and surprises
        patterns = find_surprising_patterns(task, artifacts)

        # 4. Generate lessons
        lessons = generate_lessons(decisions, metrics, patterns)

        return Reflection(
            task_id=task.id,
            decisions=decisions,
            metrics=metrics,
            lessons=lessons,
            recommendations=lessons_to_recommendations(lessons),
        )
```

### 3.2 Reflection Output: The Lesson

A `Lesson` is a structured insight captured for future retrieval:

```python
class Lesson:
    """A reusable insight from execution."""
    id: str
    category: str                          # "architecture", "pattern", "pitfall", "optimization"
    headline: str                          # "Redux state in React SSR must be hydrated"
    explanation: str                       # Why this matters
    context: str                           # When to apply it
    source_task: str                       # Which task discovered this
    importance: float                      # 1-10, user-scored
    tags: list[str]                        # e.g., ["react", "ssr", "state-management"]
    embedding: list[float]                 # For semantic retrieval
    created_at: datetime
```

**Examples:**

-   **Category: Pitfall** — "When using Playwright to test dynamic content, always wait for `networkidle` or specific elements. Headless mode is faster but sometimes races ahead of rendering."
-   **Category: Optimization** — "For large lists, using `virtualization` cuts render time from 2s to 200ms. Rule of thumb: if list > 100 items, virtualize."
-   **Category: Architecture** — "Separating API routes from business logic into distinct files improves testability and makes the Coder's job easier. Pattern: `/routes` for HTTP handlers, `/services` for logic."

### 3.3 Memory Store Integration

Lessons are stored in the long-term Memory Store (introduced in Proposal 001) with three scoring signals:

| Signal         | How It's Updated                                                                             |
| -------------- | -------------------------------------------------------------------------------------------- |
| **Recency**    | Decays over time. Fresh lessons rank higher.                                                 |
| **Importance** | User scores 1-10, or inferred from task metrics (lessons from high-cost tasks score higher). |
| **Relevance**  | Embedding similarity to the current query.                                                   |

When the Scout or Architect retrieves context for a new task, the Memory Store ranks lessons and includes the top-K in the context window.

---

## 4. Closed-Loop Optimization

Observability + Reflection enable **closed-loop optimization**: the system measures, learns, and improves.

### 4.1 SOP Refinement

After N completed features, the system extracts meta-lessons about the SOP itself:

-   **Architect → Coder handoff:** If tasks frequently require clarification, suggest more structured `Architecture` artifacts.
-   **Reviewer turnaround:** If Reviewer cycles exceed target, maybe Coder context is insufficient or requirements were ambiguous.
-   **QA integration failures:** If post-merge bugs cluster around certain file types, add specialized QA checks.

These insights update the SOP prompts and workflows.

### 4.2 Agent Model Assignment

The system dynamically allocates LLM models based on observed performance:

```
Scenario: Coder-A is slowing down the pipeline.
Analysis: Reviewing metrics, Coder-A uses 15% more tokens than peers for similar tasks.
Action: Downgrade from Claude 3.5 Sonnet → Claude 3.5 Haiku for straightforward tasks.
Result: Token savings without quality degradation.

Scenario: Architect is producing ambiguous specs.
Analysis: Coder has to re-request clarification 40% of the time (well above 20% target).
Action: Upgrade from Claude 3.5 Sonnet → Claude 4 (Thinking) for architectural design.
Result: More detailed specs, fewer clarification loops.
```

### 4.3 Dynamic SLA Adjustment

Targets are recalibrated based on feature complexity:

```
Observed: Simple features (< 5 files) average 8 minutes end-to-end.
          Complex features (> 20 files) average 45 minutes end-to-end.

System recalibrates SLA targets:
  - Simple features: SLA = 10 min (95th percentile)
  - Complex features: SLA = 60 min (95th percentile)

Alerts only fire when actual exceeds recalibrated SLA.
```

### 4.4 Board Feedback Loop

The Board reviews observability metrics quarterly and issues directives:

| Scenario                                | Board Decision                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------ |
| Token burn 30% above baseline           | Penny Pincher forces "Cost Reduction Sprint" — downgrade models, increase batch size |
| Feature shipping 50% slower than target | Velocity Capital demands architectural review — maybe we're over-engineering         |
| Test failures spiking                   | Quality Fund blocks merges until root cause is addressed                             |

---

## 5. Implementation Details

### 5.1 Event Log Backend

-   **Local Dev:** SQLite with JSON blobs for simplicity
-   **Production:** PostgreSQL with TimescaleDB (time-series optimized)
-   **Replication:** Immutable append-only log; synced to Git for durability (Beads pattern)

### 5.2 Dashboard Technology

-   **Frontend:** React with D3.js for dependency graphs, Recharts for time-series metrics
-   **Backend:** FastAPI or Go service that queries the event log and computes real-time aggregations
-   **Streaming:** WebSocket for live updates as events arrive

### 5.3 Embedding Store for Lessons

-   **Local:** SQLite + `sentence-transformers` library (e.g., `all-MiniLM-L6-v2`)
-   **Production:** Pinecone, Weaviate, or Milvus for scalability

### 5.4 Integration with LangGraph

LangGraph's checkpoint system can be extended to capture observability events:

```python
graph.add_node("capture_event", lambda state: emit_event(
    event_type="node_executed",
    node_name=state.current_node,
    metrics=state.metrics,
))
```

---

## 6. Observability for End Users

### 6.1 CLI Output

When a user submits a feature request, the CLI streams real-time progress:

```
$ clusterfork build "add user authentication"

[14:23:15] 🔍 Scout exploring codebase...
           Found 42 files, 8 related to auth. (3.2s, 12k tokens)

[14:23:20] 📐 Architect designing plan...
           Tasks: 3 (User model, Auth routes, JWT middleware)
           Estimated time: 18 min, ~4.2k tokens

[14:23:28] 👨‍💻 Coder-A starting Task-1 (User model)
           ⏳ [████░░░░░░] 40%

[14:23:45] 👨‍💻 Coder-B starting Task-2 (Auth routes)
           ⏳ [██░░░░░░░░] 20%

[14:24:00] 🔍 Reviewer checking Task-1
           ✅ Approved! 1 suggestion (extract validator)

[14:24:10] 👨‍💻 Coder-A revising Task-1
           ⏳ [████████░░] 80%

[14:24:22] ✨ All tasks merged
           📊 Summary:
             - 3 files changed, 187 insertions
             - Tests: 28 passed, 0 failed (94% coverage)
             - Cost: $0.34 (4.2k tokens)
             - Time: 9 minutes
             - Quality: A (Reviewer approved, all tests pass)

[14:24:23] 💡 Lessons learned:
             - JWT validation complexity: Consider extracting to middleware library
             - Pattern: Auth middleware should use context injection, not globals
```

### 6.2 Post-Execution Report

After completion, a detailed report is generated:

```markdown
# Feature: Add User Authentication

## Overview

-   **Status:** ✅ Merged
-   **Duration:** 9m 14s
-   **Cost:** $0.34 (4.2k tokens)
-   **Quality:** A

## Task Breakdown

1. **User Model** (Task-1): ✅ Approved

    - Coder: Coder-A
    - Time: 4m 30s
    - Tokens: 1.8k
    - Reviewer cycles: 1

2. **Auth Routes** (Task-2): ✅ Approved

    - Coder: Coder-B
    - Time: 5m 10s
    - Tokens: 1.9k
    - Reviewer cycles: 0

3. **JWT Middleware** (Task-3): ✅ Approved
    - Coder: Coder-A
    - Time: 3m 45s
    - Tokens: 0.5k
    - Reviewer cycles: 0

## Quality Metrics

-   **Test Coverage:** 94% (28/30 passing)
-   **Lint Score:** 100%
-   **Code Review:** 2/3 changes approved on first pass
-   **Estimated Bugs (7-day):** 0.2% (very low risk)

## Insights

-   **Efficiency:** Tasks 2 & 3 had 0 reviewer cycles. Consider Task 1's context.
-   **Token Usage:** 14% above average for "user auth" tasks. Likely due to JWT complexity.
-   **Recommendation:** Template for JWT middleware — could save ~200 tokens next time.
```

---

## 7. Privacy & Cost Considerations

### 7.1 What Gets Logged

-   Event metadata, task descriptions, metrics
-   _Not_ stored: Full code diffs, user secrets, or large artifacts (diffs are hashed, not stored in event log)

### 7.2 Cost Tracking

Every token spent is tracked. Users can set monthly/per-feature budgets. Penny Pincher will escalate if spending exceeds budget.

### 7.3 Transparency

Users can query the observability system at any time:

```bash
$ clusterfork metrics --feature "add-auth" --metric "cost"
Total: $0.34, Architect: $0.08, Coder: $0.22, Reviewer: $0.04

$ clusterfork agents --role "coder" --metric "efficiency"
Coder-A: 94% (high-quality, slightly slower)
Coder-B: 89% (average)
Coder-C: 91% (new agent, ramping up)
```

---

## 8. Connection to Proposals 001 & 002

### 8.1 Proposal 001: Architecture

Observability is the **sensory nervous system** of the deterministic backbone (P1). While Proposal 001 defines the orchestration flow, this proposal ensures every decision and event is visible.

### 8.2 Proposal 002: Corporate Governance

The Board (Section 3.2 of Proposal 002) needs observability data to make decisions. This proposal is the **data pipeline** feeding the Board's AI shareholders. Without it, Penny Pincher, Velocity Capital, and Quality Fund are flying blind.

The metrics taxonomy (Section 2.3) directly maps to the Board's objective functions:

-   **Penny Pincher:** Cost Metrics
-   **Velocity Capital:** Velocity Metrics
-   **Quality Fund:** Quality Metrics

---

## 9. Future Extensions

### 9.1 Predictive Dashboards

Using historical data, predict:

-   "This feature will take ~14 minutes and cost $0.42 based on similar tasks"
-   "Reviewer will likely request changes if Coder-A doesn't add tests"

### 9.2 Agent Coaching

Instead of just marking agents as low-performing, generate personalized coaching:

-   "Coder-B: Your test coverage is below target. Here are 3 similar high-quality submissions to learn from."

### 9.3 Anomaly Detection

-   Unexpected token usage patterns
-   Unusual task durations (early warning of bugs)
-   Agent behavior drift

### 9.4 Competitive Leaderboards

Display agent performance anonymously:

```
🏆 Efficiency Leaderboard (Tokens/Task)
1. Coder-C: 850 avg
2. Coder-A: 920 avg
3. Coder-B: 980 avg

🎯 Quality Leaderboard (Reviewer Approval Rate)
1. Coder-A: 95%
2. Coder-C: 91%
3. Coder-B: 87%
```

(Gamification has been shown to improve performance in multi-agent systems.)

---

## 10. Open Questions

1. **Real-time vs. Batch:** Should events be processed in real-time (higher latency, more infrastructure) or batched every minute (simpler, slightly delayed)?

2. **Privacy at Scale:** If Clusterfork runs on a user's codebase, should metrics be sent to a remote dashboard, or kept local?

3. **Lesson Generalization:** How do we know a lesson from Project A applies to Project B? Do we need project-specific tagging, or semantic similarity?

4. **Board Meeting Frequency:** Should Board Meetings happen after every feature, or daily/weekly? Frequency affects system responsiveness.

5. **Cost Attribution:** When multiple features are in parallel, how do we attribute tokens? Proportional to task count, or based on actual LLM calls?

---

## 11. References

| Paper/Framework             | Key Contribution                                              |
| --------------------------- | ------------------------------------------------------------- |
| Event Sourcing              | Immutable event logs for system replay and audit              |
| Observability (Honeycombs)  | High-cardinality metrics, not just timeseries                 |
| Prometheus / OpenTelemetry  | Standard observability instrumentation                        |
| OODA Loop (Boyd)            | Observe → Orient → Decide → Act — Reflection enables the loop |
| Voyager / Generative Agents | Post-execution reflection and memory formation                |
| Gas Town                    | Instrumentation and telemetry in agent systems                |

---

_Clusterfork learns by watching itself think. Observability is the mirror._
