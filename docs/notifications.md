# Turn-completion notifications

Claude Code, Grok, Command Code, Codex, and Antigravity use one shared
Stop-hook command:

```text
~/.config/clusterfork/bin/clusterfork-notify <agent>
```

The helper plays `~/.config/clusterfork/bell.mp3` locally and, when
`CLUSTERFORK_NTFY_URL` is set, publishes a phone notification through ntfy.
Both jobs run together, and both are on by default. Network delivery has a
three-second ceiling, errors are silent, and the helper always exits zero, so
a phone or server outage cannot fail or delay an agent beyond the existing
4.5-second bell. `notify` can silence the bell, the phone path, or a particular
agent without removing the Stop hook.

The notification is deliberately small: its title identifies the agent and
its body contains only the current directory's basename (Antigravity uses the
basename of `workspacePaths[0]`, because its hook cwd is the `hooks.json`
directory). Hook stdin is never forwarded. Other agents do not read it.
Antigravity Stop also fires on tool yields and subagent cycles, so that path
reads stdin locally and notifies only when `fullyIdle` is true.

Codex keeps the notifier on root `Stop` with `async = true`; it does not
register `SubagentStop`, so thread-spawned subagents stay quiet. Its upstream
usage-limit gap is unchanged; see [Codex bell vs usage-limit deaths](codex-usage-limit-bell.md).

## `notify` command

`notify` is on PATH via clusterfork `bin/`. With no arguments (or `status`) it
prints the current switches followed by the five most recent notification
triggers. Each history row shows a friendly local date and time with the time
zone, the agent that invoked the hook, and the channels triggered. A target
with no value toggles; `on`/`off` sets it. Interactive output is colorized when
connected to a terminal (or forced via `CLICOLOR_FORCE`/`FORCE_COLOR`), and stays
plain text when redirected or when `NO_COLOR`/`CLICOLOR=0`/`TERM=dumb` is set.

```bash
notify                 # status
notify all off         # silence everything
notify all on          # restore every switch
notify volume 40       # local bell loudness, 0-100 (mpv --volume)
notify bell            # toggle the local bell
notify phone           # toggle ntfy phone push
notify grok            # toggle one agent
notify bell off        # set instead of toggle
notify test            # play the bell and post a phone test
notify test bell       # play the local bell now
notify test phone      # post a phone test now
notify log              # show the 50 most recent triggers
notify log 100          # choose a history length, up to 200
```

Targets: `bell`, `phone`, `claude`, `codex`, `command-code`, `grok`,
`antigravity`. `all` sets every switch at once and requires `on` or `off`.
`volume` is 0–100 (default 100) and is not changed by `all`; setting it
reports the previous and new volume (e.g. `100% → 40%`). Those five
agents are the Stop hooks that call `clusterfork-notify`. Disabling an agent
skips both the bell and the phone path for that source. Disabling both
channels leaves the hook silent for every agent. Missing keys default to on,
matching the previous always-on behavior. Volume is passed to `mpv` as
`--volume`; the tty `\a` fallback has no volume control.

`notify test` fires the real channels now, without changing prefs and without
waiting for a Stop hook. No argument tests both; `bell` or `phone` tests one.
A test still runs when that switch is off, so a silenced channel can be
checked before turning it back on. Successful test channels appear in history
with `test` as their source. The bell test starts playback and returns; it does
not wait for the clip to finish. The phone test waits for the post (title
`Clusterfork test`, body `Phone path works`) to `CLUSTERFORK_NTFY_URL`. Missing
URL, missing `curl`/`mpv`, or a failed post prints an error and exits non-zero.

Prefs live in `~/.config/clusterfork/notify-prefs`. That file is not a mapped
installer destination, so reinstalling clusterfork does not reset it.

Trigger history lives in `~/.config/clusterfork/notify-history`, which is also
not overwritten on reinstall. The helper records enabled channels when it
dispatches a notification; the history is not a delivery receipt. Disabled
agents and Antigravity events rejected by the `fullyIdle` gate do not create a
row. Writes are serialized when `flock` is available, logging failures remain
silent, and only the newest 200 rows are retained. `notify log` displays 50 by
default, accepts a count from 1 through 200, and—like the five-row status
summary—prints newest first.

## Private ntfy service over Tailscale

`notify/compose.yaml` runs ntfy server v2.28.0 on localhost port 2586 with a
persistent 24-hour message cache. The installer copies it and
`notify/settings.env` to `~/.config/clusterfork/notify/`. The settings file is
the repo-owned, non-secret route configuration:

```text
CLUSTERFORK_NTFY_URL=http://127.0.0.1:2586/clusterfork
CLUSTERFORK_NTFY_PUBLIC_URL=http://100.123.102.71:2586
```

Start or update the server after installation:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml up -d
```

The container binds port 2586 only on localhost and the laptop's stable
Tailscale address (`100.123.102.71`), not on its LAN address. The hook
publishes through localhost; the phone's persistent instant-delivery
connection travels directly over Tailscale's encrypted WireGuard path. HTTP
is intentional here: there are no application credentials, and the service
is not reachable outside the Tailnet.

On the Pixel, install the native ntfy Android app, add
`http://100.123.102.71:2586` as an HTTP server (or open
`ntfy://100.123.102.71:2586/clusterfork?secure=false`) and subscribe to topic
`clusterfork`. Allow ntfy's instant-delivery foreground service, exempt ntfy
from battery optimization, and keep Tailscale enabled (always-on VPN is the
most reliable setting). The app then reconnects across Wi-Fi, cellular, and
VPN changes and replays anything still in the server cache. See ntfy's
[Android setup and `ntfy://` link formats](https://docs.ntfy.sh/subscribe/phone/).

The Tailnet is currently the access boundary: ntfy application auth is not
enabled, so any device admitted to this Tailnet can reach the server. If the
Tailnet gains untrusted members, enable ntfy access control and put a dedicated
publisher token in the gitignored clusterfork `.env` as
`CLUSTERFORK_NTFY_TOKEN`; the helper already sends that token when present.

Useful checks:

```bash
notify test bell
notify test phone
curl -fsS http://127.0.0.1:2586/v1/health
curl -d 'phone path works' \
  -H 'X-Title: Clusterfork test' \
  http://100.123.102.71:2586/clusterfork
```

If the phone reports `ECONNREFUSED` on port 2586, check both host addresses
with `/v1/health`. A container can appear `Up` while its network attachment
and published ports are missing; this failure was observed with empty
`NetworkSettings.Networks` and `NetworkSettings.Ports` in `docker inspect`.
Recreate just ntfy from its saved configuration to restore them:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml \
  up -d --no-deps --force-recreate ntfy
```

[Compose recreation preserves mounted volumes](https://docs.docker.com/reference/cli/docker/compose/up/),
including the notification cache. Verify health on both `127.0.0.1:2586`
and `100.123.102.71:2586`, then run `notify test phone`. A successful post
confirms server acceptance; reopen ntfy on the phone to check receipt if its
subscription has not reconnected yet.

The server is reversible:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml down
```

The Docker named volume is retained by `down`; add `--volumes` only when the
cached notifications should also be deleted.
