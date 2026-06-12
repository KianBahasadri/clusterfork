chrome() {
  nohup chromium --remote-debugging-port=9222 --user-data-dir="$HOME/.config/chromium" "$@" >/dev/null 2>&1 &
  disown
}
