# Voyager: An Open-Ended Embodied Agent with Large Language Models

**Authors:** Guanzhi Wang, Yuqi Xie, Yunfan Jiang, Ajay Mandlekar, Chaowei Xiao, Yuke Zhu, Linxi Fan, Anima Anandkumar
**Venue:** arXiv:2305.16291 (2023)
**Link:** https://arxiv.org/abs/2305.16291
**Code:** https://github.com/MineDojo/Voyager
**Project Page:** https://voyager.minedojo.org/

## Summary

Voyager is the first LLM-powered embodied lifelong learning agent capable of playing Minecraft without human intervention. Unlike standard agents that reset after every episode, Voyager continuously explores, writes its own code to master skills, and stores them in a permanent library. It uses GPT-4 to propose its own curriculum (what to learn next), generate code to execute tasks, and refine that code based on execution errors and environmental feedback.

## Problem Addressed

*   **Lack of Lifelong Learning:** Most LLM agents suffer from catastrophic forgetting; they don't accumulate knowledge over time.
*   **Exploration in Open Worlds:** Traditional Reinforcement Learning (RL) struggles with "sparse rewards" in open-ended games like Minecraft where there is no single goal.
*   **Action Space Complexity:** Low-level motor commands are hard to learn. Voyager uses **code** as the action space, which is compositional and interpretable.

## Approach

Voyager consists of three mutually interactive components:

### 1. Automatic Curriculum (The "Manager")
Instead of a human defining tasks, GPT-4 proposes a curriculum based on the agent's current state and inventory.
*   **Goal:** "Discover as many diverse things as possible."
*   **Mechanism:** It looks at what the agent has (e.g., "3 wood planks") and proposes a feasible next step (e.g., "craft a crafting table") rather than an impossible one (e.g., "mine diamond"). This acts as an in-context **novelty search**.

### 2. Skill Library (The "Memory" - **Crucial for Clusterfork**)
Voyager does not store "memories" as just natural language text; it stores **executable code**.
*   **Structure:** A database of Python functions representing skills (e.g., `mine_wood()`, `fight_zombie()`).
*   **Indexing:** Skills are indexed by the embedding of their description (docstring).
*   **Retrieval:** When faced with a new task, the system retrieves the top-5 relevant skills.
*   **Composition:** New skills call older skills. This allows rapid compounding of abilities (e.g., `make_diamond_pickaxe()` calls `mine_iron()` and `craft_sticks()`).

### 3. Iterative Prompting (The "Coder")
The agent writes code to solve the current task from the curriculum.
*   **Self-Verification:** The agent acts as its own critic. It checks the game state to see if the task was actually completed.
*   **Error Correction:** If the code fails (Python error) or the task isn't done (game state unchanged), the error message and feedback are fed back to GPT-4 to refine the code.

## Key Contributions

*   **Code as Policy:** Demonstrates that LLMs are better at writing code to control agents than outputting direct actions.
*   **Skill Library:** A novel mechanism for storing "procedural memory" as executable code, enabling zero-shot generalization to new worlds.
*   **SOTA Performance:** Unlocks the Minecraft tech tree 15.3x faster than previous state-of-the-art methods and discovers 3.3x more unique items.

## Relevance to Clusterfork

Voyager is extremely relevant to our goal of building autonomous coding agents.

1.  **The Skill Library is a Package Manager:** We should treat our agent's "memory" not just as a log of conversations, but as a **library of reusable functions**. If the agent figures out how to "fix a React hydration error," it should save that as a generic script/rule, not just a memory.
2.  **Self-Correction Loop:** Voyager's "Iterative Prompting" (Write -> Execute -> Verify -> Refine) is exactly how our agents should handle coding tasks. We should enforce a "Self-Verification" step where the agent explicitly checks if its code meets the requirements *before* showing it to the user.
3.  **Curriculum for Onboarding:** When an agent joins a new codebase, it shouldn't just try to "fix the bug." It should generate a "curriculum" to explore the repo: "Read package.json" -> "Trace entry point" -> "Understand auth flow" -> "Fix bug".

## Strengths

*   **Generalization:** Skills learned in World A work in World B because they are modular code.
*   **Interpretability:** We can read the code to see exactly *how* the agent solves a problem.
*   **No Training:** Uses frozen GPT-4; no expensive model fine-tuning required.

## Limitations

*   **Cost:** Requires heavy use of GPT-4, which is expensive and slow.
*   **Text-Only Perception:** Relies on an API to convert the 3D world into text descriptions; visual understanding is limited.
*   **Hallucination:** Can still write code that calls non-existent API functions (though the iterative feedback loop catches most of this).
