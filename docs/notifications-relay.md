# Devbox notification relay

The repo's `notify/relay-settings.sh` selects a Unix socket on `node-dev`:
`$HOME/clusterfork/.notify-relay.sock`. The host creates the socket at the
matching home-relative path inside the existing read-only, UID-shifted
Clusterfork mount. This route needs no extra Incus device or TCP listener.
The [notification command](notifications.md) remains the same on each side.

Guest preferences choose which channels to request. Host preferences can
further silence channels or the agent; host `notify volume` controls playback.
Antigravity and Grok event gates run before forwarding. Hook delivery failures
remain silent, with a one-second client socket timeout.

## Host setup

Install clusterfork on both sides. On the host:

```bash
systemctl --user enable --now \
  "$HOME/.config/clusterfork/notify/clusterfork-notify-relay.service"
```

The unit starts on subsequent logins and restarts on failure. It assumes the
host checkout is `~/clusterfork`. Keep its socket directory read-only to the
guest and install the receiver outside guest-writable mounts.
After updating an enabled service on the host:

```bash
systemctl --user daemon-reload
systemctl --user restart clusterfork-notify-relay.service
```

`CLUSTERFORK_NOTIFY_RELAY_SOCKET` in the installed `.env` overrides the route;
an empty value disables forwarding. In the devbox, `notify` prints the socket
path and identifies a missing socket. `notify test bell` uses the bridge and
reports host acceptance; it does not confirm audible playback. Tests bypass
guest switches while host switches still apply.

Accepted requests appear in guest history. Host history records channels
dispatched under host preferences. Phone service health is independent of
local bell playback.

## Receiver contract

`bin/clusterfork-notify-relay` uses a mode-0600 Unix socket. It reads at most
512 bytes within a 250 ms request deadline and accepts only known agent/channel
names plus a sanitized project label of at most 80 characters. It invokes one
fixed host notifier without evaluating guest text as shell code.

Anyone with socket access can request an alert and supply an agent/project
label; an alert does not authenticate task completion. Requests are limited to
one per ten seconds across all agents, and a still-running notifier also blocks
the next request. The systemd unit limits memory, process count, and CPU.

## Diagnosis and removal

```bash
systemctl --user status clusterfork-notify-relay.service
journalctl --user -u clusterfork-notify-relay.service -n 20
systemctl --user disable --now clusterfork-notify-relay.service
```

Clean shutdown removes the socket. Disable the guest route as well to return
to direct local delivery. Phone server setup belongs to
[Private ntfy](notifications-ntfy.md).
