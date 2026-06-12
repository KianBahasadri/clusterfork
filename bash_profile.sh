#!/usr/bin/env bash

CLUSTERFORK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTERFORK_ENV_FILE="$CLUSTERFORK_DIR/.env"
if [ -f "$CLUSTERFORK_ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  . "$CLUSTERFORK_ENV_FILE"
  set +a
fi
unset CLUSTERFORK_ENV_FILE

for script in "$CLUSTERFORK_DIR"/shell/*.sh; do
  [ -f "$script" ] || continue
  # shellcheck source=/dev/null
  source "$script"
done
unset CLUSTERFORK_DIR
