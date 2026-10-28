# Codex completion bell and usage-limit errors

Investigated in upstream source on 2026-08-29. This is a dated explanation of
the gap; [Notifications](../notifications.md) owns the installed hook and
[remote development](../remote-development.md) owns headless worker outcomes.

## Source findings

In that source snapshot, `run_turn_stop_hooks` in
`codex-rs/core/src/session/turn.rs` ran only after successful sampling with
no follow-up needed. `UsageLimitReached` emitted an error and returned before
that point; Esc interruption did too. The legacy notifier's
`UserNotification` enum in `hooks/src/legacy_notify.rs` exposed only
`AgentTurnComplete`, so switching notifier mechanisms did not add error events.

The TOML hook schema in `config/src/hook_config.rs` exposed no configurable
`Interrupt` event. `SessionEnd` applied to archive/delete/graceful shutdown,
not each failed turn. A usage-limit error in a still-open TUI therefore did
not trigger a timely completion bell. tmux `pane-died` could not help because
the Codex process remained alive.

`codex exec` returned nonzero on a rate-limit error (covered by upstream
`exec/tests/suite/server_error_exit.rs`). That allowed an exit-code check for
headless runs, but did not cover an interactive session. Rollout JSONL also
persisted structured error events through `history/src/lib.rs`.

## Outcome

A rollout-log watcher could detect interactive usage-limit errors immediately,
but was rejected because it coupled notifications to an undocumented
serialization format. No such watcher was shipped. App-server error handling
was relevant to scripted consumers, not a replacement TUI hook.

The current root Stop notifier was adopted to keep subagent completions quiet;
it does not add an error watcher. Revisit the dated source conclusion if
upstream exposes a supported per-turn error/limit event.
