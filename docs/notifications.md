# Agent notifications

Claude, Grok, Command Code, Codex, Antigravity and Cursor Stop hooks call
`~/.config/clusterfork/bin/clusterfork-notify <agent>`. Bell (`mpv`, then terminal
fallback) and optional ntfy run concurrently; phone timeout is three seconds.
Hook errors are silent, exit zero. Messages contain agent/project basename,
never transcripts/payloads. See [ntfy](notifications-ntfy.md) and [relay](notifications-relay.md).

## Event gates

- Antigravity requires `fullyIdle: true`, excluding tool/subagent yields; project
  comes from `workspacePaths[0]` because hooks run in the config directory.
- Grok rejects explicit reasons other than `end_turn`, including shutdown.
  Empty/malformed payloads or a missing reason remain accepted.
- Codex uses asynchronous root `Stop` for completions and `PostModelRequest`
  for failed root requests with Codex's `You've hit your usage limit` message
  (straight or typographic apostrophe).
  `PermissionRequest` also notifies for approvals, and `PreToolUse` matches
  `request_user_input` and `request_user_input_async` for questions. Approvals
  and questions from subagents also need a person; their completions and request
  failures stay quiet. All use the same notifier and channel switches; Codex
  phone alerts say "Codex needs attention" for both completions and waits. These
  [supported hooks](https://learn.chatgpt.com/docs/hooks), verified with Codex
  0.167.0, do not expose a universal pause/terminal-error event. Other request
  failures and cancellations stay quiet, avoiding alerts on automatic retries.
  No transcript watcher is installed. The earlier
  [coverage investigation](archive/codex-usage-limit-bell.md) predates this hook.
- Cursor rejects `status == "aborted"`; project comes from `CURSOR_PROJECT_DIR`
  or `workspace_roots[0]` because user hooks run in the config directory.

## `notify` command

Agents/channels default on; phone also requires `CLUSTERFORK_NTFY_URL`.

```bash
notify                  # status and five recent triggers
notify all off          # requires on/off; does not change volume
notify all on
notify volume 40        # mpv 0–100, default 100; no tty volume control
notify bell             # toggle channel
notify phone on         # set channel
notify grok off         # agent's bell and phone
notify test             # both channels
notify test bell
notify test phone
notify log              # newest 50
notify log 100          # 1–200
```

Agents: `claude`, `codex`, `command-code`, `grok`, `antigravity`, `cursor`. Tests bypass local
switches without changing them; relay host switches still apply. Bell tests return
on playback start; phone tests wait for server acceptance, exiting nonzero on
missing config/failure. Acceptance does not confirm phone receipt. Interactive
color can be forced by `CLICOLOR_FORCE`/`FORCE_COLOR`; `NO_COLOR` wins, otherwise
`CLICOLOR=0`, `TERM=dumb` or redirection disables automatic color.

## Preferences, history, and configuration

Reinstall preserves `~/.config/clusterfork/{notify-prefs,notify-history}`. History
keeps 200 dispatches with local date/time/zone, agent and requested channels,
not receipts; disabled agents/rejected Stops add none. Logging fails silently;
`flock` serializes when available. Helper loads installed `notify/settings.env`,
`notify/relay-settings.sh`, then `.env` (override `CLUSTERFORK_ENV_FILE`), last wins.
`CLUSTERFORK_NTFY_TOKEN` adds bearer auth; `CLUSTERFORK_NOTIFY_SOUND` overrides sound.
