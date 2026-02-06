# Generative Agents: Interactive Simulacra of Human Behavior

**Authors:** Joon Sung Park, Joseph C. O'Brien, Carrie J. Cai, Meredith Ringel Morris, Percy Liang, Michael S. Bernstein
**Venue:** UIST 2023 (arXiv:2304.03442)
**Link:** https://arxiv.org/abs/2304.03442
**Code:** https://github.com/joonspk-research/generative_agents

## Summary

This paper introduces "Generative Agents," computational agents that simulate believable human behavior. The authors propose an architecture that extends Large Language Models (LLMs) with a mechanism to store, synthesize, and apply memories to generate consistent and complex behavior over time. They demonstrate this in "Smallville," a sandbox environment resembling *The Sims*, where 25 agents live, interact, and form emergent social relationships (e.g., planning a Valentine's Day party) without explicit scripting.

## Problem Addressed

LLMs are excellent at generating text but struggle with long-term coherence and maintaining a consistent persona over extended interactions. Existing agent architectures often fail to:
1.  **Retrieve relevant past experiences** effectively over long periods.
2.  **Reflect** on these experiences to form higher-level inferences or personality traits.
3.  **Plan** consistent long-term behaviors based on these reflections.

## Approach

The core contribution is an agent architecture composed of three main components:

### 1. Memory Stream
A comprehensive database (list) of all the agent's experiences, recorded in natural language. It is not just a vector database but a chronological log.

**Retrieval Function:** To decide what to pass to the LLM context, the system retrieves memories based on a combined score of:
*   **Recency:** Exponential decay function giving higher weight to recent memories.
*   **Importance:** An LLM-generated score (1-10) distinguishing mundane events (eating breakfast) from significant ones (breaking up).
*   **Relevance:** Cosine similarity between the memory and the current query/situation.

### 2. Reflection
A mechanism to synthesize low-level observations into higher-level abstract thoughts.
*   **Trigger:** Executed periodically when the sum of importance scores of recent observations exceeds a threshold.
*   **Process:** The agent asks itself, "Given these observations, what can I infer?" The LLM generates insights, which are then stored back into the Memory Stream as new "thought" memories. This allows the agent to generalize (e.g., "I am shy") from specific events.

### 3. Planning
A top-down approach to action generation.
*   **High-level:** The agent creates a broad plan for the day.
*   **Refinement:** This is recursively broken down into hourly and then minute-by-minute schedules.
*   **Reaction:** The plan can be modified dynamically if the agent perceives an event that conflicts with the current plan (e.g., a fire, or a friend asking to hang out).

## Key Contributions

*   **Agent Architecture:** A novel framework combining Memory Stream, Reflection, and Planning to enable long-term coherence.
*   **Emergent Behavior:** Demonstration of complex social behaviors (information diffusion, relationship formation, coordination) emerging from simple agent definitions.
*   **Evaluation:** Ablation studies showing that removing Reflection or Planning significantly degrades the believability of the agents.

## Relevance to Clusterfork

This paper is foundational for any advanced multi-agent system. For Clusterfork, the specific relevance lies in:

1.  **Memory Management:** We should move beyond simple conversation history. Implementing a **Memory Stream** with **Retrieval (Recency, Importance, Relevance)** would allow our coding agents to remember architectural decisions made days ago without filling the context window with irrelevant chatter.
2.  **Reflection for Code:** We can adapt the "Reflection" mechanism. After completing a task, an agent could "reflect" on what went wrong or right, storing that as a "lesson learned" in its memory stream. This would prevent repeating the same bugs.
3.  **Planning:** The top-down planning approach is analogous to how we should handle complex coding tasks: Break down the feature -> Plan the files -> Plan the functions -> Write the code.

## Strengths

*   **Believability:** The agents feel genuinely "alive" and consistent compared to standard chatbots.
*   **Interpretability:** Because memories and reflections are stored in natural language, it is easy to debug *why* an agent made a specific decision.
*   **Robustness:** The architecture handles the "context window" problem elegantly by retrieving only what is necessary.

## Limitations

*   **Latency & Cost:** The architecture requires multiple LLM calls for every action (retrieval, reflection, planning), making it slow and expensive for real-time applications.
*   **Dependency on LLM Quality:** The system relies heavily on the underlying LLM's ability to follow instructions and score importance correctly.
*   **Hallucination:** Agents can still hallucinate false memories or details if the retrieval fails to provide the correct context.

## Key Takeaways

1.  **Context is not just History:** Effective agents need a way to score and retrieve *relevant* context, not just the last N messages.
2.  **Reflection is Crucial:** Agents need a "quiet time" to process their experiences and update their internal state/beliefs.
3.  **Natural Language Storage:** Storing state in natural language (vs. JSON or vectors alone) allows the LLM to reason about its own history more effectively.

## Technologies Used

*   **LLM:** ChatGPT (gpt-3.5-turbo)
*   **Environment:** Phaser (for the 2D visualization)
*   **Backend:** Python (Django)
