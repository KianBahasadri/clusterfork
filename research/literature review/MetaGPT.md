# MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework

**Authors:** Sirui Hong, Mingchen Zhuge, Jonathan Chen, Xiawu Zheng, Yuheng Cheng, Ceyao Zhang, Jinlin Wang, Zili Wang, Steven Ka Shing Yau, Zijuan Lin, Liyang Zhou, Chenyu Ran, Lingfeng Xiao, Chenglin Wu, Jürgen Schmidhuber
**Venue:** ICLR 2024 (arXiv:2308.00352)
**Link:** https://arxiv.org/abs/2308.00352
**Code:** https://github.com/geekan/MetaGPT

## Summary

MetaGPT is a meta-programming framework that enhances multi-agent collaboration by incorporating human Standard Operating Procedures (SOPs). Unlike chat-based multi-agent systems that rely on unstructured dialogue, MetaGPT assigns specific roles (Product Manager, Architect, Project Manager, Engineer, QA) to agents and enforces strict workflows. This structured approach allows the system to generate complex software applications—including PRDs, design documents, and code—from a single-line requirement with higher consistency and fewer hallucinations.

## Problem Addressed

Large Language Models (LLMs) often suffer from **cascading hallucinations** when chained together for complex tasks. In unstructured multi-agent conversations:

1.  **Context Loss:** Agents lose track of requirements over long dialogues.
2.  **Lack of Coordination:** Without defined roles, agents may talk past each other or duplicate work.
3.  **Error Propagation:** A small error by one agent amplifies as it passes to the next, leading to non-functional or incoherent final outputs.

## Approach

MetaGPT solves these problems by mimicking a real-world software company. The core innovation is encoding **SOPs (Standard Operating Procedures)** into the agent prompts.

### 1. Role-Based Agents

The framework defines five core roles, each with specific behaviors, goals, and constraints:

-   **Product Manager (PM):** Analyzes requirements and generates a Product Requirement Document (PRD).
-   **Architect:** Translates the PRD into system design, file structures, and API interfaces.
-   **Project Manager:** Allocates tasks and manages dependencies.
-   **Engineer:** Writes the actual code based on the design and tasks.
-   **QA Engineer:** Generates test cases and verifies the code.

### 2. Standard Operating Procedures (SOPs)

Instead of free-form chat, agents follow a strict assembly line.

-   **Structured Outputs:** Agents do not just "talk"; they produce standardized deliverables (documents, diagrams, code files).
-   **Shared Environment:** These deliverables are published to a shared environment (memory) that other agents subscribe to. For example, the Architect subscribes to the PM's PRD.
-   **Phase-Based Execution:** The workflow is divided into phases (Requirement Analysis -> System Design -> Coding -> Testing), ensuring that downstream agents always work from verified upstream outputs.

## Key Contributions

-   **SOP-Based Meta-Programming:** A framework to map human management procedures into agent workflows.
-   **Structured Coordination:** Replacing natural language dialogue with structured artifacts (PRDs, Class Diagrams, Sequence Diagrams) to reduce ambiguity.
-   **State-of-the-Art Performance:** Achieved 87.7% Pass@1 on HumanEval and high scores on MBPP, significantly outperforming existing multi-agent frameworks like ChatDev.
-   **Complex Software Generation:** Demonstrated the ability to generate complete, runnable software projects (e.g., a CLI game, a web scraper) from a single prompt.

## Relevance to Clusterfork

MetaGPT is highly relevant to Clusterfork's goal of building a coding agent team.

1.  **Role Specialization:** We should explicitly define "Architect" and "Coder" personas. The Architect should focus on file structure and interfaces _before_ any code is written.
2.  **Structured Artifacts:** Instead of just putting code in the chat, our agents should generate intermediate artifacts. For example, generating a `PLAN.md` or `ARCHITECTURE.md` before writing source code acts as a "grounding" step.
3.  **SOP Implementation:** We can implement SOPs as **Cursor Rules**. For instance, a rule could state: "Before writing code, always search for existing patterns and propose a file structure."
4.  **Review Process:** The "QA Engineer" role suggests we should have a distinct step (or agent) dedicated solely to reviewing the code against the original requirements before considering the task done.

## Strengths

-   **Reduced Hallucination:** By forcing agents to adhere to structured designs (like class diagrams), the code generation is much more grounded and consistent.
-   **Scalability:** The assembly line approach allows for adding more agents or steps without exponential complexity in communication.
-   **Interpretability:** The intermediate outputs (PRDs, designs) make it easy for human users to understand _why_ the system built the software the way it did.

## Limitations

-   **Rigidity:** The strict SOPs can make the system less flexible for tasks that don't fit the standard "waterfall" software development lifecycle.
-   **Cost & Latency:** Generating full documentation and diagrams for every small task is expensive and slow. It is best suited for generating new projects from scratch rather than small bug fixes.
-   **Error Recovery:** If the initial PRD is flawed, the error cascades down the entire line (though less so than in unstructured chat).

## Key Takeaways

1.  **Structure > Dialogue:** For complex engineering tasks, structured documents are better coordination mechanisms than chat messages.
2.  **Define Roles Clearly:** Agents perform better when they have a narrow, well-defined scope (e.g., "Only write the API interface").
3.  **Mimic Human Workflows:** proven software engineering practices (Waterfall/Agile, PRDs, Code Review) work well for agents too.

## Technologies Used

### LLM Provider

-   OpenAI (GPT-4, GPT-3.5)

### General Technologies

-   **Python:** Core framework language.
-   **Mermaid:** Used for generating diagrams (Class, Sequence, Flowchart).
-   **Git:** For version control of generated code.
