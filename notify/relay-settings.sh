# node-dev sees this checkout through its existing read-only, UID-shifted mount.
# Keep this separate from settings.env, which Docker Compose also reads.
if [[ "${HOSTNAME:-}" == node-dev ]]; then
  CLUSTERFORK_NOTIFY_RELAY_SOCKET="$HOME/clusterfork/.notify-relay.sock"
fi
