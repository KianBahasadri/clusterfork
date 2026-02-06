# LLM-Powered Decentralized Generative Agents with Adaptive Hierarchical Knowledge Graph for Cooperative Planning

**Authors:** Hanqing Yang, Jingdi Chen, Marie Siew, Tania Lorido-Botran, Carlee Joe-Wong
**Venue:** arXiv, 2025
**Link:** https://arxiv.org/abs/2502.05453
**Project Page:** https://happyeureka.github.io/damcs

## Summary

This paper introduces **DAMCS** (Decentralized Adaptive Knowledge Graph Memory and Structured Communication System), a framework designed to enable LLM-based agents to cooperate effectively in decentralized, open-world environments. Unlike traditional Multi-Agent Reinforcement Learning (MARL) which often relies on centralized training, DAMCS uses a **hierarchical knowledge graph** to structure agent memory and a **structured communication protocol** to share only relevant information. The system demonstrates significant efficiency gains in the "Multi-agent Crafter" environment, outperforming both MARL and standard LLM baselines.

## Problem Addressed

Current multi-agent systems face several challenges:

1.  **Scalability & Flexibility:** Traditional MARL approaches (like CTDE) often struggle to scale and require retraining for new tasks.
2.  **Long-term Planning:** Designing reward functions for complex, long-horizon tasks is difficult.
3.  **Communication Efficiency:** Agents often share too much (full history) or too little information, leading to noise or lack of context.
4.  **Decentralization:** Centralized control is often impractical in dynamic, open-world settings where agents must adapt independently.

## Approach

DAMCS addresses these by implementing two core components:

### 1. Adaptive Hierarchical Knowledge Graph (Memory)

Instead of a flat list of memories, agents maintain a **multi-modal memory system** organized as a hierarchical knowledge graph.
*   **Structure:** Concepts and experiences are linked hierarchically (e.g., "Make Wood Pickaxe" -> requires "Wood" -> found in "Forest").
*   **Reasoning:** Agents traverse this graph to plan actions and reason about dependencies, allowing them to break down complex goals into sub-goals based on past experience.
*   **Adaptivity:** The graph updates dynamically as agents explore and learn, refining the relationships between nodes.

### 2. Structured Communication Protocol

Agents do not broadcast their entire memory. Instead, they use the knowledge graph to identify and share only the **relevant sub-graph** or specific nodes needed for the current context. This minimizes communication overhead while maximizing information utility.

## Key Contributions

*   **DAMCS Framework:** A novel decentralized framework combining LLMs with hierarchical knowledge graphs.
*   **Efficiency Gains:** Demonstrated that 2 agents can complete tasks with **63% fewer steps** than a single agent, and 6 agents with **74% fewer steps**.
*   **Decentralized Coordination:** Proved that structured knowledge sharing allows for effective cooperation without a central coordinator.

## Relevance to Clusterfork

For Clusterfork, the **Adaptive Hierarchical Knowledge Graph** is the critical takeaway:

*   **Shared "Brain":** Instead of just vector databases for memory, we should consider a graph structure that links *tasks*, *files*, and *concepts*.
*   **Contextual Retrieval:** When an agent needs to work on a task, it shouldn't just search for "similar text"; it should traverse the graph to find *dependencies* (e.g., "Editing `auth.ts` requires checking `user_model.ts`").
*   **Decentralized Handoffs:** If we move towards a swarm model, agents can pass "sub-graphs" of context to each other rather than full conversation logs.

## Strengths

*   **High Efficiency:** Drastic reduction in steps required to solve problems compared to single agents.
*   **Structured Reasoning:** The graph structure forces agents to "think" in dependencies rather than just predicting the next token.
*   **Scalability:** The decentralized nature allows adding more agents without retraining a central policy.

## Limitations

*   **Graph Maintenance:** Constructing and updating a knowledge graph in real-time can be computationally expensive and prone to errors (e.g., wrong links).
*   **Environment Specificity:** The "Crafter" environment is well-suited for hierarchical tasks (craft wood -> plank -> stick -> pickaxe). It's unclear how well this translates to less structured tasks like "creative writing" or "debugging vague errors."

## Key Takeaways

1.  **Structure Memory as a Graph:** Flat logs are insufficient for complex planning. Use a graph to link goals, resources, and actions.
2.  **Communicate Sub-graphs:** Don't dump context. Identify the relevant nodes in the graph and share those.
3.  **Decentralization Works with Structure:** You don't need a central boss if everyone shares a common understanding of the dependencies (the graph).

## Benchmarks

*   **Environment:** Multi-agent Crafter (collecting a diamond by crafting tools).
*   **Results:**
    *   **2 Agents:** 63% fewer steps than 1 agent.
    *   **6 Agents:** 74% fewer steps than 1 agent.
    *   **Comparison:** Outperforms MARL (PPO, MAPPO) and standard LLM baselines (ReAct, Reflexion) in success rate and efficiency.

## Technologies Used

*   **LLMs:** Used for reasoning and graph traversal.
*   **Knowledge Graph:** Dynamic, hierarchical structure for memory.
*   **Environment:** Multi-agent Crafter.
