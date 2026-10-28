# Codex rewind regression in 0.147.0

Investigated 2026-08-16. This version-scoped upstream failure is retained for
recognition; it is not a current Codex compatibility report.

## Symptom and cause

Esc-Esc with an empty composer, selecting an earlier prompt, then Enter failed:

```text
Failed to branch before the selected prompt: the selected prompt was not found in the persisted thread
```

The composer restored the prompt, but no branch was created. The paginated
history rework used different IDs for the visible rollout prompt (`msg_…` in
`~/.codex/sessions/`) and the persisted `thread_items.item_id` in
`thread_history_1.sqlite`. Branch lookup used the visible ID and found no
persisted match. Affected threads had `history_mode = 'paginated'`.

The investigation matched
[openai/codex#37421](https://github.com/openai/codex/issues/37421) and
[duplicate #38871](https://github.com/openai/codex/issues/38871). Fresh sessions
failed too; prior compaction and the clusterfork resume wrapper were not the
cause. Clusterfork's installer does not write thread history or compaction data.

## Outcome and diagnostic method

The recorded upstream fix was 0.148.0. At investigation time stable was still
0.147.0 and the fix was available only in alphas; the decision was to wait for
stable rather than change the standalone binary's `current` link.

For that build, app-server `thread/turns/list` and `thread/items/list` exposed
read-only state when initialization declared
`capabilities.experimentalApi = true`. Its experimental JSON schema command
provided method parameters. Those APIs were useful for separating the UI's
visible IDs from persisted items without making a model request.
