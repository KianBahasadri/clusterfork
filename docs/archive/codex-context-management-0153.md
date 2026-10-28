# Codex context-management experiment: deferred

Investigated 2026-09-05 against the 0.153.x line. No template setting was
introduced. The proposed 2026-09-19 recheck date was a historical reminder,
not a completed evaluation. This document does not establish the feature's
current upstream status or the eligibility of any current account.

## Design being evaluated

The experiment replaced repeated context compaction with active context,
structured notes, and searchable original history, retrieved through
`new_context`. It also enabled token-budget context management. The expected
tradeoff was less dependence on a repeatedly summarized history, with a new
risk of failing to retrieve needed details. Neither quality improvement nor
cost reduction was measured in this repository.

Context-window usage is distinct from rate-limit consumption: moving history
out of the active window lowers the former without proving lower token spend.
Retrieval can add requests and reintroduce context. Any future comparison
needs both correctness checks and quota/spend measurements.

## Status recorded at the time

The [adding PR](https://github.com/openai/codex/pull/42385) described the mode
as under development. The investigation recorded it as off by default in
0.153.0, with no published graduation timeline or substantial usage guidance.
The installed 0.153.4 binary contained `ContextManagementConfigToml`,
`experimental_mode`, and history-note machinery.

Eligibility then required ChatGPT Plus, Pro, or Pro Lite on the Codex backend.
API-key/custom-provider and temporary structured-thread paths were excluded;
ineligible sessions could ignore the flag. A local account was checked at
the time, which says nothing about accounts selected later.

The examined setting was:

```toml
[features]
context_management = { experimental_mode = true }
```

It was left out of `agents/codex.toml`. The current template still ships only
MCP/hook tables; unrelated local feature settings are preserved by the
[installer](../installation.md#replacement-and-preservation).
The former local activation recipe is omitted because its schema and account
gates were version-specific.

## Decision and future evaluation

The decision was to wait for more evidence, informed partly by the earlier
[rewind regression](codex-rewind-bug-0147.md). Before revisiting, check the
current [Codex releases](https://github.com/openai/codex/releases), config
schema, team guidance, and issue reports for retrieval failures. Reconfirm
backend/plan eligibility without exposing credentials. Use low-stakes sessions
to compare retained task details and actual quota consumption; a lower context
percentage alone cannot support a savings claim.
