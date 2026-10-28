# Private ntfy service over Tailscale

`notify/compose.yaml` pins ntfy server v2.28.0 with a persistent 24-hour
message cache. [Installation](installation.md) copies the service files to
`~/.config/clusterfork/notify/`. The repo-owned, non-secret routes are:

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

The container binds port 2586 on localhost and the laptop's stable Tailscale
address (`100.123.102.71`). Binding that address alone does not enforce
Tailscale ingress: Docker can forward specifically routed LAN packets to it.
On this laptop, the root-managed `laptop-guard.service` loads
`/etc/nftables/laptop-guard.nft`; its pre-DNAT rule drops connections to that
publication arriving outside `lo` or `tailscale0`. This host configuration is
separate from the Compose installer and must be retained when deploying or
moving the service. Reload it with `sudo systemctl reload laptop-guard.service`.

The hook publishes through localhost; the phone's persistent instant-delivery
connection uses Tailscale's encrypted WireGuard path with its existing URL.
HTTP is intentional here: there are no application credentials, and access is
restricted by the Tailscale policy plus the host ingress guard. Recorded checks
on September 10, 2026 verified both health URLs and blocked a connection routed
through an isolated test interface, with the guard's drop counter advancing.

On the Pixel, install the native ntfy Android app, add
`http://100.123.102.71:2586` as an HTTP server (or open
`ntfy://100.123.102.71:2586/clusterfork?secure=false`) and subscribe to topic
`clusterfork`. Allow ntfy's instant-delivery foreground service, exempt ntfy
from battery optimization, and keep Tailscale enabled (always-on VPN is the
most reliable setting). The app then reconnects across Wi-Fi, cellular, and
VPN changes and replays anything still in the server cache. See ntfy's
[Android setup and `ntfy://` link formats](https://docs.ntfy.sh/subscribe/phone/).

In this configuration, the Tailnet is the access boundary: ntfy application auth
is not enabled, so any device permitted by the Tailnet access policy can reach
the server. If the Tailnet gains untrusted members, enable ntfy access control
and put a dedicated publisher token in the gitignored clusterfork `.env` as
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
