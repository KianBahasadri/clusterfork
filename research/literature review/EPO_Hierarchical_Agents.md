# EPO: Hierarchical LLM Agents with Environment Preference Optimization

**Authors:** Qi Zhao, Haotian Fu, Chen Sun, George Konidaris (Brown University, Cornell University)
**Venue:** EMNLP 2024
**Link:** https://arxiv.org/abs/2408.16090
**Code:** https://github.com/kevinz8866/EPO

## Summary

EPO is a hierarchical agent framework designed for long-horizon tasks (specifically evaluated on ALFRED). It addresses the challenge of planning over long timeframes by decomposing tasks into subgoals and using **Environment Preference Optimization (EPO)** to refine the agent's policy. Instead of relying solely on supervised fine-tuning (SFT) or standard reinforcement learning, EPO uses a preference-based objective (similar to DPO) where the "preferences" are derived from a learned reward model that interprets multimodal environment feedback.

## Problem Addressed

LLM-based agents struggle with **long-horizon decision-making** because:

1.  **Error Accumulation:** Small errors in early steps cascade, leading to failure in long tasks.
2.  **Sparse Rewards:** In many environments, you only know if you succeeded at the very end.
3.  **Lack of Training Signals:** Unannotated datasets (trajectories without explicit rewards) are hard to learn from.

## Approach

The EPO framework consists of three main components:

### 1. Hierarchical Decomposition

The agent operates on two levels:

-   **High-Level Planner:** Decomposes the complex instruction into a sequence of manageable subgoals (e.g., "Go to the fridge," "Pick up the apple").
-   **Low-Level Actor:** Generates specific atomic actions to achieve the current subgoal.
-   _Implementation Note:_ The code suggests this is trained using the PDDL (Planning Domain Definition Language) plans available in the ALFRED dataset as ground truth for subgoals.

### 2. Multimodal Reward Model

To provide denser feedback than just "success/fail," they train a specific **Reward Model**.

-   **Input:** The current state (visual + text) and the agent's action.
-   **Output:** A scalar reward indicating if the action is correct/useful.
-   **Training:** The reward model is trained on a dataset of (instruction, action) pairs where "true" actions (from expert traces) are positive examples and modified "bad" actions (e.g., wrong object) are negative examples.

### 3. Environment Preference Optimization (EPO)

This is the core training innovation. Instead of standard RL (like PPO), they use a DPO-style approach:

-   **Preference Generation:** The Reward Model evaluates different potential actions or trajectories.
-   **Optimization:** The agent is trained to maximize the margin between "preferred" actions (those with higher predicted rewards) and "rejected" actions.
-   **Loss Function:** They use a sigmoid loss (similar to DPO) to align the policy with the environment preferences derived from the reward model.

## Key Contributions

1.  **EPO Algorithm:** A novel method to apply preference optimization (DPO) to agents using environment feedback rather than human labels.
2.  **Automated Reward Modeling:** A strategy to learn a reward function from unannotated data by synthesizing "negative" examples (e.g., swapping objects in the instruction).
3.  **SOTA Performance:** Achieved 1st place on the ALFRED public leaderboard (at the time of publication), significantly outperforming non-hierarchical baselines.

## Relevance to Clusterfork

This paper is highly relevant to our goal of building robust autonomous agents:

-   **Reviewer/Judge Pattern:** The "Reward Model" is essentially an automated "Reviewer" agent. We could implement a similar "Code Reviewer" model that scores agent edits before they are committed.
-   **Preference Learning:** Instead of just cloning expert behavior, we can train agents to prefer actions that pass tests or satisfy the linter. This is "Environment Preference" where the "Environment" is the compiler/test suite.
-   **Hierarchical Structure:** Validates the approach of separating "Planning" (high-level goals) from "Execution" (writing code).

## Strengths

-   **Effective use of Feedback:** Cleverly converts environment signals into a format (preferences) that modern LLMs can learn from efficiently via DPO.
-   **Strong Results:** The empirical performance on ALFRED is impressive, showing that this approach works for complex, multi-step tasks.
-   **No Human-in-the-loop:** The preference signals are generated automatically by the reward model and environment, making it scalable.

## Limitations

-   **Reward Model Dependence:** The entire system relies on the quality of the learned Reward Model. If the reward model is gameable or inaccurate, the agent will learn the wrong behaviors.
-   **Dataset Bias:** The "negative" examples are synthetically generated (e.g., by swapping objects). This might not cover all types of failure modes an agent might encounter in the wild.
-   **Task Specificity:** The implementation is heavily tuned for ALFRED (navigation/manipulation). Adapting the "visual" reward model to "code" tasks requires rethinking the input representation.
