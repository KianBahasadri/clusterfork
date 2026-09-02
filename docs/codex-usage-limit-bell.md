# Codex bell does not fire on usage-limit deaths (documented behavior — investigated 2026-08-29, no change shipped)

Recorded so this does not get re-derived: hitting the ChatGPT usage limit in
Codex kills the turn mid-flight, and the clusterfork turn-completion bell
never rings — while Claude, Grok, Antigravity, and Command Code all ring on
their own end-of-turn/abort paths. This is upstream by-design behavior, not an
installer bug.

## Symptom

- Codex hits the usage limit → error message in the TUI, no bell.
- Every other fleet CLI rings at turn end; Codex only rings on normal turn
  completion.

## Current setup

`agents/codex.toml` installs the root-only shared notifier on `[[hooks.Stop]]`.
The helper rings locally and publishes to ntfy concurrently; the hook remains
asynchronous in Codex.
The installer also strips the retired top-level `notify` key so it cannot
stack with Stop, and stamps `trusted_hash` for that Stop hook so it does not
wait on `/hooks`. See [Agent Configs](agent-configs.md).

```toml
[[hooks.Stop]]
  [[hooks.Stop.hooks]]
  type = "command"
  command = "${HOME}/.config/clusterfork/bin/clusterfork-notify codex"
  async = true
```

Stop was chosen over `notify` so thread-spawned subagent completions do not
notify (`SubagentStop` is not registered). That does not change the
usage-limit gap below: Stop still only runs on normal turn completion.

## What we verified in the Codex source (upstream `main`, 2026-08-29)

- **The legacy `notify` hook has exactly one event: `AgentTurnComplete`.**
  `codex-rs/hooks/src/legacy_notify.rs` — the `UserNotification` enum has a
  single variant serialized as `"agent-turn-complete"`. There is no error or
  session-limit variant.
- **A usage-limit hit is a mid-turn error, not a turn completion.**
  `codex-rs/core/src/session/turn.rs` — `run_sampling_request` propagates
  `CodexErrorDetails::UsageLimitReached` as an `Err` (after updating the
  rate-limit snapshot); `run_turn`'s error branches emit `EventMsg::Error` and
  return/break **before** `run_turn_stop_hooks` is reached. So the turn never
  "completes" and no notification event is produced.
- **The newer hooks system does not fix it.** `[[Stop]]` hooks (the Claude
  Code-style `[[hooks.<Event>]]` config in `codex-rs/config/src/hook_config.rs`)
  are dispatched from the same `run_turn_stop_hooks`, which only runs on the
  `Ok` + `!needs_follow_up` path — normal completion. Esc-interrupts
  (`TurnAborted`) return before it too.
- **`Interrupt` exists as an internal hook event but is not user-configurable
  via TOML.** `HookEventsToml` exposes 11 events (PreToolUse, PermissionRequest,
  PostToolUse, PreCompact, PostCompact, SessionStart, SessionEnd,
  UserPromptSubmit, SubagentStart, SubagentStop, Stop) — no `Interrupt`.
- **`SessionEnd` fires only on thread archive/delete/graceful shutdown** — not
  per turn. A usage-limit death mid-session stays silent until the whole
  session is closed.
- **`codex exec` exits 1 on `rate_limit_exceeded`** (integration test
  `codex-rs/exec/tests/suite/server_error_exit.rs`), so non-interactive runs
  *could* be covered by an exit-code check.
- **Session rollout JSONL persists `EventMsg` items** including `EventMsg::Error`
  with structured `codex_error_info` categories (`UsageLimitExceeded` et al.;
  `codex-rs/history/src/lib.rs` — `RolloutItem::EventMsg`). A tail-watcher on
  `~/.codex/sessions/**/*.jsonl` would therefore see usage-limit deaths in
  interactive sessions the moment they are written.

## Coverage options evaluated

| Approach | Covers interactive usage-limit? | Verdict |
|---|---|---|
| Legacy `notify` (retired) | No | fires only on `agent-turn-complete`, including subagents |
| `[[Stop]]` hooks (current) | No | same dispatch point; normal root completion only |
| `[[SessionEnd]]` hook | No (delayed only) | rings only when the session is eventually closed |
| `codex exec` + wrapper exit-code bell | No | non-interactive runs only |
| Tail `~/.codex/sessions/**/*.jsonl` for `EventMsg::Error`/`UsageLimitExceeded` | Yes | works, but keys the bell to an undocumented file format rather than a hook contract |
| Drive Codex via app-server / Python SDK | N/A | error events arrive over the wire; only relevant for scripted usage, not the TUI |
| tmux `pane-died` | No | the Codex process does not exit on usage limit |

## Outcome

**No usage-limit coverage shipped.** The rollout-log watcher is the only
approach that rings at the moment the limit hits in an interactive session,
but it was rejected as too much complexity for a workaround keyed to a
serialization format rather than a documented hook contract. The fleet later
moved completion handling from `notify` to `[[hooks.Stop]]` so subagent
completions stay silent; the shared bell/phone notifier now uses that same
path, which still does not run on a usage-limit death.

Upstream has a "broader event taxonomy — including `error`, `session-limit`,
`authentication-required`" on the roadmap; if that lands, revisit.

## References

- `codex-rs/hooks/src/legacy_notify.rs` — single-variant `UserNotification`
- `codex-rs/core/src/session/turn.rs` — `run_turn` / `run_turn_stop_hooks`
  dispatch points, `UsageLimitReached` propagation
- `codex-rs/config/src/hook_config.rs` — `HookEventsToml` (11 events, no Interrupt)
- `codex-rs/history/src/lib.rs` — `RolloutItem::EventMsg` persistence
- `codex-rs/exec/tests/suite/server_error_exit.rs` — exec exit 1 on rate limit
