chrome() {
  nohup prime-run google-chrome --ozone-platform=x11 --remote-debugging-port=9222 --user-data-dir="$HOME/.config/chromium" "$@" >/dev/null 2>&1 &
  disown
}
