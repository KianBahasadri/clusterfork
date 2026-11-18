---
name: dev-box
description: Reference for Kian's node-dev Incus development container. Use when entering the dev box, working in its shared checkouts, troubleshooting local ports, networking, Wayland, or GPU access, or changing its Terraform configuration.
---

# Dev Box Reference

`node-dev` is an Arch Linux container on Kian's laptop. The host checkout at
`/home/kian/dev-box` owns `node-dev.tf`, which declares the instance, devices and
desired running state. The launcher targets the local Incus daemon's `default`
project. Start with the checkout's `docs/README.md` for subsystem details.
[Dev box documentation](https://github.com/KianBahasadri/dev-box/tree/main/docs)

## Entry and administration

- Run `dev` with no arguments from host Bash. In a tool session with a terminal,
  the same entry point is `sudo -n -- /usr/local/sbin/dev-box-enter`. Run commands
  in the resulting interactive Bash shell as `dev` (UID/GID `1000`).
- The launcher maps a host working directory under `/home/kian` to the same
  relative path under `/home/dev` when it can enter that directory. Otherwise it
  starts in `/home/dev`. In pasted output, `[node-dev]` marks the guest prompt.
  Use `hostname`, `id` and `pwd` to confirm the current context.
- Passwordless sudo is scoped to the root-owned launcher with zero arguments.
  `Instance is not running` means the guest is stopped. Starting the guest,
  changing its devices and applying Terraform use host administrator access.
- Guest user creation, shell setup, auth symlinks and timezone are configured
  separately from Terraform. If timezone detection falls back to UTC, check
  the guest's `/etc/localtime`.
- Rclone operations belong to Kian's authenticated root sessions. This covers
  its executable, configuration, credentials, mounts, served endpoints and
  access to the same remotes through other clients or APIs. Give Kian commands
  to run in his own terminal for Rclone tasks.

## Shared checkouts and agent tooling

- Terraform declares individual UID/GID-shifted disk mounts at matching paths
  under `/home/kian` and `/home/dev`. Edits in a writable shared checkout
  appear on both sides, including uncommitted changes. For write errors, check
  the mount mode and compare host file ownership with the guest's numeric UID.
- `node-dev.tf` lists the mounts. The Clusterfork checkout and
  `~/.local/share/clusterfork-auth` are read-only in the guest. Edit Clusterfork
  on the host and run its installer there before installing in the guest. The
  Codex and Cursor auth symlinks follow the shared store's `current` links.
  Rotate accounts and refresh shared credentials on the host.
- For Python projects on a shared checkout, use `UV_PROJECT_ENVIRONMENT=.venv-box`
  in the guest and the default `.venv` on the host. Export the guest setting
  above the interactivity guard in `~/.bashrc` and ignore `.venv-box/` in the
  project. Virtual environments embed absolute paths from the side that built
  them.
- Guest notifications use `~/clusterfork/.notify-relay.sock` and the host's
  `clusterfork-notify-relay.service` when the relay is configured.
  [Relay setup and diagnostics](https://github.com/KianBahasadri/clusterfork/blob/main/docs/notifications-relay.md)

## Network and local app servers

- The host's `laptop-guard.service` manages `inet laptop_guard` separately from
  Terraform and filters traffic on `incusbr0` and `lxdbr1`. Public internet,
  gateway DNS, DHCP, IPv6 discovery, guest loopback and host-initiated
  connections are available. General guest-initiated access to host, LAN and
  Tailscale services and unsolicited routed ingress are blocked. A required
  LAN service needs a narrow destination and port exception in the host policy.
- Terraform declares same-port `127.0.0.1` proxies on `5173` and `4173` for Vite
  development and preview, and on `8000` and `8001` for app servers. Bind the
  server to loopback in the guest and open the matching loopback address on the
  host. For Warframe Toolkit, keep `http://127.0.0.1:5173/` to retain the browser
  inventory stored at that origin.

## Display and GPU

- The Wayland proxy forwards `/run/user/1000/wayland-0` on the host to
  `/mnt/wayland-0` in the guest. The guest shell sets
  `WAYLAND_DISPLAY=/mnt/wayland-0` and `XDG_SESSION_TYPE=wayland`, with
  `/tmp/runtime-dev` as a fallback `XDG_RUNTIME_DIR`. The single-socket proxy
  keeps other host session sockets private. With the host compositor running,
  check the connection with `wayland-info` inside the guest.
- Terraform also declares the shared NVIDIA RTX 4060 Laptop GPU for `dev`, with
  `compute`, `utility` and `video` capabilities. Incus injects the host driver
  libraries at instance startup. Check access with `nvidia-smi` as `dev` and
  check the application's CUDA and cuDNN requirements separately.
