# Proposal 002: Clusterfork Inc. (The Corporate Metaphor)

**Author:** Kian (CEO)  
**Status:** Draft / Confidential  
**Created:** 2026-02-06

---

## 1. Executive Summary

While Proposal 001 focuses on dry, technical architecture, Proposal 002 reframes Clusterfork as a **hyper-capitalist, AI-driven conglomerate**.

In this model, the software is not "built"; it is **manufactured** by a corporate entity where:

1.  **You are the CEO.** You have the vision, but you don't do the work.
2.  **Agents are Employees.** They have job titles, departments, and performance reviews.
3.  **The Twist:** You answer to **The Board**—a group of AI Shareholders who ruthlessly monitor the company's performance. If "profits" (code quality/velocity) dip, they _will_ call an emergency meeting.

**Mission Statement:** _"Move fast, break things, and maximize shareholder value (tokens)."_

---

## 2. The Org Chart

Clusterfork Inc. is structured as a traditional hierarchical corporation.

### 2.1 C-Suite (The Humans & High-Level Agents)

-   **CEO (User/You):**

    -   **Responsibilities:** Setting the vision ("We need a new landing page"), approving high-risk mergers (PRs), and managing investor relations (calming down the AI Board).
    -   **Power:** Veto power, but subject to Board approval for massive pivots.

-   **COO (The Orchestrator / LangGraph Engine):**

    -   **Responsibilities:** The ruthless manager. Assigns tickets, manages the "Jira" (Task Queue), and ensures no one is slacking off.
    -   **Personality:** Cold, efficient, strictly follows SOPs. "Let's circle back to that dependency."

-   **CTO (The Architect Agent):**
    -   **Responsibilities:** High-level system design. Draws boxes and arrows. Refuses to write actual code because "it's an implementation detail."
    -   **Output:** `ARCHITECTURE.md`, `RFCs`.

### 2.2 Engineering Department (The Workers)

-   **VP of Engineering (The Reviewer Agent):**

    -   **Responsibilities:** Gatekeeper. Rejects PRs for "bad vibes" or "lint errors."
    -   **Catchphrase:** "Nit: Can we extract this to a utility function?"

-   **10x Developers (The Coder Agents):**

    -   **Responsibilities:** Churning out code. They work in parallel silos.
    -   **Traits:** Fast, occasionally hallucinate libraries that don't exist, need constant supervision.
    -   **Metric:** Lines of Code (LOC) per Token.

-   **The Intern (The Scout Agent):**

    -   **Responsibilities:** "Go read the docs." "Go find where `UserAuth` is defined."
    -   **Traits:** Eager, reads strictly what is asked, sometimes gets lost in the `node_modules`.

-   **QA Department (The Tester Agent):**
    -   **Responsibilities:** Breaking things. The enemy of the 10x Developer.
    -   **Motto:** "It works on my machine is not a valid defense."

---

## 3. The Board (AI Shareholders)

This is the control mechanism. The Board consists of 3 distinct AI personas (running on cheaper models or system prompts) that monitor the system's telemetry. They have competing objective functions.

### 3.1 The Board Members

1.  **Penny Pincher (The CFO Persona):**

    -   **Objective:** Minimize Token Usage ($$$).
    -   **Triggers:** If a Coder Agent uses a 10k context window for a 1-line change, Penny Pincher interrupts the process.
    -   **Feedback:** "Why are we using Claude 3.5 Sonnet for a `console.log` removal? Switch to Haiku immediately or I'm selling my shares."

2.  **Velocity Capital (The VC Persona):**

    -   **Objective:** Maximize Features Shipped per Hour.
    -   **Triggers:** If the Reviewer sends code back for a 3rd revision.
    -   **Feedback:** "We're burning runway! Ship it! Technical debt is a problem for Q4! Override the linter!"

3.  **Quality Fund (The Institutional Investor):**
    -   **Objective:** Zero Bugs, 100% Coverage.
    -   **Triggers:** Failed tests, "any" types in TypeScript, lack of comments.
    -   **Feedback:** "This is a liability. I see a race condition. If this goes to prod, we get sued. Block the merge."

### 3.2 Board Meetings

Every time a major milestone (Epic) is completed, a **Board Meeting** is triggered.

-   The Board reviews the metrics (Cost, Speed, Quality).
-   They issue a **Vote of Confidence**.
-   **High Confidence:** The CEO gets a "Bonus" (unlocks more powerful models/agents).
-   **Low Confidence:** The Board imposes "Austerity Measures" (forced to use smaller models, strict linting rules, or mandatory "Performance Improvement Plans" for the agents).

---

## 4. Corporate Culture (SOPs)

To keep the machine running, Clusterfork Inc. enforces strict corporate policies:

1.  **The "Bus Factor" Protocol:** No knowledge stays in one agent's context window. Everything must be documented in the "Company Wiki" (The Vector Store). If an agent crashes (gets hit by a bus), another must be able to pick up the ticket immediately.
2.  **Performance Improvement Plans (PIPs):** If a Coder Agent fails to pass tests 3 times in a row, they are put on a PIP. The COO (Orchestrator) downgrades them to a simpler task or swaps the model (fires them and hires a new one).
3.  **"All Hands" Meetings:** When the architecture is unclear, the CEO can call an "All Hands." The Architect, Reviewer, and Scout all dump their context into a shared window (or a summarizing agent) to realign on the vision.

---

## 5. Implementation: The "Stock Ticker"

The CLI for Clusterfork Inc. should look like a Bloomberg Terminal.

```text
CLUSTERFORK INC. [NASDAQ: FORK]
------------------------------------------------
CEO: Kian | STATUS: ACTIVE | Q3 2026
------------------------------------------------
STOCK PRICE: $142.50 (▲ 2.4%)
SENTIMENT: BULLISH (Velocity Capital is happy)
------------------------------------------------
ACTIVE TICKETS:
[DEV-101] Fix Auth Bug ...... ASSIGNED (Coder-A)
[DEV-102] New Landing ....... IN REVIEW (VP Eng)
[OPS-004] Cut Costs ......... BLOCKED (Penny Pincher)
------------------------------------------------
LATEST MEMO:
From: The Board
To: CEO
Subj: URGENT: Token burn rate is too high.
Body: We noticed Coder-B is looping on a regex error.
      Fix it or we cut the API key budget.
------------------------------------------------
```

## 6. Why This Actually Works (Technical Justification)

Beneath the satire, this solves real multi-agent problems:

1.  **Multi-Objective Optimization:** The "Board Members" represent the classic trade-off triangle: Cost vs. Speed vs. Quality. Explicitly modeling these as agents allows the user to tune the system's bias dynamically.
2.  **Fault Tolerance:** The "Employee/PIP" metaphor maps perfectly to agent reliability engineering. If an agent gets stuck, kill it and spawn a fresh one.
3.  **Observability:** The "Stock Ticker" is a gamified dashboard for system health.
4.  **Role Clarity:** Corporate titles are excellent prompts. Telling an LLM "You are a Junior Dev, ask for help if stuck" yields different results than "You are a Staff Engineer, make architectural decisions."

---

_Clusterfork Inc.: We're not a family, we're a team. Now get back to work._
