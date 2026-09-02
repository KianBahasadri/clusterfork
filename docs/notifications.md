# Turn-completion notifications

Claude Code, Grok, Command Code, and Codex use one shared Stop-hook command:

```text
~/.config/clusterfork/bin/clusterfork-notify <agent>
```

The helper plays `~/.config/clusterfork/bell.mp3` locally and, when
`CLUSTERFORK_NTFY_URL` is set, publishes a phone notification through ntfy.
Both jobs run together. Network delivery has a three-second ceiling, errors
are silent, and the helper always exits zero, so a phone or server outage
cannot fail or delay an agent beyond the existing 4.5-second bell.

The notification is deliberately small: its title identifies the agent and
its body contains only the current directory's basename. Hook stdin is never
read or forwarded, so prompts, transcripts, and final assistant messages do
not leave the machine.

Codex keeps the notifier on root `Stop` with `async = true`; it does not
register `SubagentStop`, so thread-spawned subagents stay quiet. Its upstream
usage-limit gap is unchanged; see [Codex bell vs usage-limit deaths](codex-usage-limit-bell.md).

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
curl -fsS http://127.0.0.1:2586/v1/health
curl -d 'phone path works' \
  -H 'X-Title: Clusterfork test' \
  http://100.123.102.71:2586/clusterfork
clusterfork-notify codex
```

The server is reversible:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml down
```

The Docker named volume is retained by `down`; add `--volumes` only when the
cached notifications should also be deleted.
