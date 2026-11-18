# Devbox notification relay

`notify/relay-settings.sh` routes `node-dev` through
`$HOME/clusterfork/.notify-relay.sock`, created by the host in the existing
read-only UID-shifted mount at the same relative path. No extra Incus device/TCP
listener is needed. [Notify](notifications.md) commands stay the same.
Guest switches choose requested channels; host switches may silence them/agents,
and host volume controls playback. Event gates run before forwarding. Client
socket timeout is one second; hook failures remain silent.

## Host setup

Install on the host first and restart the relay before installing in the devbox.
On the host, enable the service at login.

```bash
systemctl --user enable --now \
  "$HOME/.config/clusterfork/notify/clusterfork-notify-relay.service"
```

The unit assumes `~/clusterfork`, starts on login and restarts on failure. Keep
its socket directory guest-read-only and receiver outside guest-writable mounts.
After service updates:

```bash
systemctl --user daemon-reload
systemctl --user restart clusterfork-notify-relay.service
```

Installed `.env` variable `CLUSTERFORK_NOTIFY_RELAY_SOCKET` overrides the route;
empty disables it. Guest `notify` shows path/missing socket; `notify test bell`
bypasses guest switches and reports host acceptance, not audibility. Guest history
records accepted requests; host history records channels it dispatches. Phone
service health is independent of bell playback.

## Receiver contract

`bin/clusterfork-notify-relay` uses mode `0600`, at most 1,024 bytes and a 250 ms
request deadline. The guest resolves the spoken name before sending the request.
The receiver accepts known agents and channels, sanitized project labels up to
80 characters, and spoken names as printable text. It invokes one fixed notifier
without shell evaluation.
Socket access permits alerts/labels, not authenticated task-completion proof.
Global rate limit is one per ten seconds; a running notifier blocks the next.
Systemd also limits memory/processes/CPU.

## Diagnosis and removal

```bash
systemctl --user status clusterfork-notify-relay.service
journalctl --user -u clusterfork-notify-relay.service -n 20
systemctl --user disable --now clusterfork-notify-relay.service
```

Clean shutdown removes the socket; also disable the guest route for direct
local delivery. Phone setup belongs to [ntfy](notifications-ntfy.md).
