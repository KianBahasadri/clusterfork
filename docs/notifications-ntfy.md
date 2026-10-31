# Private ntfy service over Tailscale

`notify/compose.yaml` pins ntfy v2.28.0 with persistent 24-hour cache.
[Installation](installation.md) copies it to `~/.config/clusterfork/notify/`.
Non-secret routes:

```text
CLUSTERFORK_NTFY_URL=http://127.0.0.1:2586/clusterfork
CLUSTERFORK_NTFY_PUBLIC_URL=http://100.123.102.71:2586
```

Start/update after installing:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml up -d
```

Port 2586 binds localhost and laptop Tailscale address `100.123.102.71`. Docker
can forward routed LAN traffic to that address, so retain root-managed
`laptop-guard.service` and `/etc/nftables/laptop-guard.nft`: its pre-DNAT rule drops
publication traffic outside `lo`/`tailscale0`. This host guard is separate from
Compose; reload with `sudo systemctl reload laptop-guard.service` after changes.
September 10, 2026 checks verified both health URLs and blocked an isolated test
interface with advancing drop count.

Hooks publish locally; phones use Tailscale's encrypted WireGuard path. HTTP is
intentional: no application credentials, Tailnet policy plus host guard as access
boundary. Any permitted Tailnet device can reach ntfy. If membership becomes
untrusted, enable ntfy access control and set dedicated publisher token
`CLUSTERFORK_NTFY_TOKEN` in gitignored `.env`.

On Android, add HTTP server `http://100.123.102.71:2586` and topic `clusterfork`,
or open `ntfy://100.123.102.71:2586/clusterfork?secure=false`. Allow instant-delivery
foreground service, exempt battery optimization and keep Tailscale enabled,
preferably always-on. Reconnection replays messages still cached. See [Android setup](https://docs.ntfy.sh/subscribe/phone/).

Checks:

```bash
notify test bell
notify test phone
curl -fsS http://127.0.0.1:2586/v1/health
curl -d 'phone path works' \
  -H 'X-Title: Clusterfork test' \
  http://100.123.102.71:2586/clusterfork
```

For `ECONNREFUSED`, check `/v1/health` on both addresses. A container may be `Up`
with empty `NetworkSettings.Networks`/`Ports` in `docker inspect`. Restore only ntfy:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml \
  up -d --no-deps --force-recreate ntfy
```

[Recreation preserves volumes](https://docs.docker.com/reference/cli/docker/compose/up/).
Recheck both addresses and `notify test phone`; server acceptance is not phone
receipt. Reopen the phone app if its subscription has not reconnected. To stop:

```bash
docker compose \
  --env-file ~/.config/clusterfork/notify/settings.env \
  -f ~/.config/clusterfork/notify/compose.yaml down
```

`down` retains the named volume; add `--volumes` only to delete cached messages.
