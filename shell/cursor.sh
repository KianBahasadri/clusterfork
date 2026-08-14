alias ca='cursor-agent --yolo'

rotate-cursor-cli() {
  python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/rotate_auth.py" cursor "$@"
}
