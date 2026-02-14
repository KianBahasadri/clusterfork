# Proposal 007: Reality Check

**Author:** Kian  
**Status:** Accepted  
**Created:** 2026-02-14

---

## 1. Purpose

Kill proposal. After six design proposals, I evaluated whether Clusterfork needs to be built at all given the current tool landscape and my actual use case (building webapps without unexpected LLM bills).

Conclusion: no.

---

## 2. Competitive Landscape (February 2026)

### CrewAI
- Hierarchical process mode, manager-to-worker delegation.
- Flows for multi-step orchestration with state, routing, persistence.
- Per-agent LLM model assignment.
- Token usage reported after execution via `usage_metrics`. No pre-emptive budget enforcement.

### AutoGen (Microsoft)
- Event-driven. `AgentChat` for teams, `Core` for distributed agents via gRPC.
- `TokenUsageTermination` — hard stop at a token count. Closest off-the-shelf budget control.
- No per-agent budgets or hierarchical budget allocation.

### OpenAI Agents SDK (successor to Swarm)
- Agents, Handoffs, Guardrails, Sessions, Tracing.
- Provider-agnostic (100+ LLMs via LiteLLM).
- Flat topology (handoffs, not hierarchy).
- `max_turns` only. No cost control.

### MetaGPT
- Role-based SOP pipeline: PM → Architect → Engineer → QA.
- Fixed pipeline, not dynamic. No budget system, no model routing.

### LangGraph
- Execution engine, not a competitor. Proposal 006 correctly uses it as the runtime layer.

### Summary

| Feature                        | CrewAI | AutoGen | OpenAI Agents SDK | MetaGPT |
| :----------------------------- | :----: | :-----: | :---------------: | :-----: |
| Hierarchical orchestration     |   ~    |    ~    |        No         |   Yes   |
| Budget / cost enforcement      |   No   | Tokens  |        No         |   No    |
| Dynamic agent spawning         |   No   |   No    |        No         |   No    |
| Model seniority routing        |   No   |   No    |        No         |   No    |
| Phased execution               |   No   |   No    |        No         |   Yes   |
| Production-ready               |  Yes   |   Yes   |       Yes         |    ~    |

No framework has Proposal 006's specific feature set (budget-as-control, dynamic plan-to-managers, model seniority).

---

## 3. Why Not Build It

**Provider-level caps solve the billing problem.** OpenAI, Anthropic, and GCP/AWS all offer spending limits or hard caps at the account level. Setting a $20/month cap takes 30 seconds and covers the same risk Proposal 006's Budget Enforcement system was designed for.

**Build cost is disproportionate.** Minimum viable Proposal 006 estimate: 5–9 weeks (provider bus 1–2w, orchestrator 2–3w, manager loop 1–2w, log/memo/tests 1–2w). That's time not spent building webapps.

**Existing tools cover the use case.** Claude Code / OpenCode for single-agent coding, Cursor / Windsurf for IDE-integrated AI, CrewAI / AutoGen if multi-agent is ever needed.

---

## 4. Decision

Shelve Clusterfork as a product. Preserve the research.

Next steps at time of writing:
1. Set provider-level spending caps on all LLM API accounts.
2. Use existing tools to build webapps.
3. Revisit only if a concrete project hits a wall existing tools can't handle.

---

## 5. Retained Design Patterns

Useful ideas from the research that apply inside any framework:

- **Structure over dialogue** (MetaGPT, TalkHier) — agents produce artifacts, not chat messages.
- **Budget as control** (Proposals 003, 006) — cost as the natural constraint for autonomous systems.
- **Phased execution** (Proposal 006) — eliminates cross-agent dependency issues.
- **Seniority-based model routing** (Proposals 005, 006) — match model capability to task complexity.
