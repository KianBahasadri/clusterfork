# Turn-completion notifications

Claude, Grok, Command Code, Codex and Antigravity Stop hooks call
`~/.config/clusterfork/bin/clusterfork-notify <agent>`. Bell (`mpv`, then terminal
fallback) and optional ntfy run concurrently; phone timeout is three seconds.
Hook errors are silent, exit zero. Messages contain agent/project basename,
never transcripts/payloads. See [ntfy](notifications-ntfy.md) and [relay](notifications-relay.md).

## Event gates

- Antigravity requires `fullyIdle: true`, excluding tool/subagent yields; project
  comes from `workspacePaths[0]` because hooks run in the config directory.
- Grok rejects explicit reasons other than `end_turn`, including shutdown.
  Empty/malformed payloads or a missing reason remain accepted.
- Codex uses asynchronous root `Stop`, without `SubagentStop` or a usage-limit
  watcher; see the [coverage investigation](archive/codex-usage-limit-bell.md).

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

Agents: `claude`, `codex`, `command-code`, `grok`, `antigravity`. Tests bypass local
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
