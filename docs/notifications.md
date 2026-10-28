# Turn-completion notifications

Claude Code, Grok, Command Code, Codex, and Antigravity call one Stop-hook
helper: `~/.config/clusterfork/bin/clusterfork-notify <agent>`. It runs local
`bell.mp3` playback and optional ntfy delivery concurrently. Phone requests have
a three-second ceiling; hook errors are silent and the helper exits zero.
Bell playback uses `mpv`, falling back to a terminal bell when available.

Phone messages identify the agent and the project directory's basename.
Transcripts and hook payloads are never forwarded. For service deployment,
read [Private ntfy](notifications-ntfy.md); for the container-to-host path,
read [Notification relay](notifications-relay.md).

## Event gates

- Antigravity also emits Stop during tool yields and subagent cycles. Its
  notifier requires `fullyIdle: true` and reads the project basename from
  `workspacePaths[0]`, because the hook runs from the config directory.
- Grok also emits Stop at session shutdown. An explicit reason other than
  `end_turn` is rejected. Empty/malformed payloads or a missing reason are
  accepted for compatibility.
- Codex registers asynchronous root `Stop`, without `SubagentStop`. No
  usage-limit error watcher is installed; the
  [dated upstream investigation](archive/codex-usage-limit-bell.md) explains
  the normal-completion hook's coverage gap.

## `notify` command

`notify` is installed on `PATH`. Channels and agents default to on; phone
posting also needs `CLUSTERFORK_NTFY_URL`.

```bash
notify                  # status and five most recent triggers
notify all off          # set every switch; requires on or off
notify all on
notify volume 40        # local mpv volume, 0–100 (default 100)
notify bell             # toggle a channel
notify phone on         # set a channel
notify grok off         # silence one agent's bell and phone
notify test             # test both channels
notify test bell
notify test phone
notify log              # newest 50 triggers
notify log 100          # accepts 1–200
```

Agent targets are `claude`, `codex`, `command-code`, `grok`, and `antigravity`.
`all` changes switches but leaves volume alone; the tty fallback has no volume
control. Interactive output is colored, or can be forced with
`CLICOLOR_FORCE`/`FORCE_COLOR`. `NO_COLOR` takes precedence; otherwise
`CLICOLOR=0`, `TERM=dumb`, or redirected output disables automatic coloring.

A test bypasses channel switches without changing preferences. A bell test
returns after starting playback; a phone test waits for server acceptance and
returns nonzero on missing configuration or posting failure. A server acceptance
is not proof of phone receipt. The relay path additionally applies host
preferences, as documented in [Notification relay](notifications-relay.md).

## Preferences, history, and configuration

`~/.config/clusterfork/notify-prefs` and `notify-history` are runtime files
outside the installer's replacement trees. Reinstallation retains them.
The history retains 200 rows, newest first in command output, with local
date/time/timezone, source agent, and requested channels. It records dispatches,
not delivery receipts. Disabled agents and rejected Stop events produce no row.
Logging errors are silent; `flock` serializes writes when available.

The helper loads installed `notify/settings.env`, `notify/relay-settings.sh`,
then `.env` (or `CLUSTERFORK_ENV_FILE`), so the last file can override routes.
`CLUSTERFORK_NTFY_TOKEN` adds a bearer header when set.
`CLUSTERFORK_NOTIFY_SOUND` selects another local sound file.
