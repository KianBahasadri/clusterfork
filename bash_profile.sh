#!/usr/bin/env bash

alias oc='opencode --continue'
alias o='opencode'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCODE_ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$OPENCODE_ENV_FILE" ]; then
  set -a
  . "$OPENCODE_ENV_FILE"
  set +a
fi
unset OPENCODE_ENV_FILE
unset SCRIPT_DIR

chrome() {
  nohup chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.config/chromium" "$@" >/dev/null 2>&1 &
  disown
}
