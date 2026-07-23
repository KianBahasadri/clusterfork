# Statusline

Clusterfork installs the Claude Code and Cursor Agent status lines currently in
use on this machine: a colored footer showing model, effort/params, active
account, context usage, and plan/rate-limit usage.

## What gets installed

| Repo file                          | Destination                       |
|------------------------------------|-----------------------------------|
| `statusline/claude/statusline.sh`  | `~/.claude/statusline-command.sh` |
| `statusline/claude/usage-fetch.py` | `~/.claude/claude-usage-fetch.py` |
| `statusline/cursor/statusline.sh`  | `~/.cursor/statusline.sh`         |
| `statusline/cursor/usage-fetch.py` | `~/.cursor/cursor-usage-fetch.py` |

Claude wires the script via `statusLine` in `agents/claude.json` →
`~/.claude/settings.json` (full overwrite, like other agent configs).

Cursor wires via `statusLine` in `~/.cursor/cli-config.json`. That file also
holds session/auth caches, so the installer only sets/updates the `statusLine`
key — it does not replace the whole file. See [Conventions](conventions.md).

## Claude line

Renders: `model · effort · account · ctx% · 5h% · wk%`.

Account comes from matching `~/.claude/.credentials.json` against
`.credentials.json.<label>` copies (same idea as auth rotation). Usage % comes
from the statusline payload's `rate_limits` when present; under fast mode those
fields are omitted, so the installed `claude-usage-fetch.py` refreshes
`~/.claude/.usage-cache.json` in the background (harvest from payload or a
throttled quota API call).

## Cursor line

Renders: `model · params/max · account · ctx% · auto% · api%`.

Account is the suffix of the resolved `~/.config/cursor/auth.json` symlink
(e.g. `ida`), with a fallback to the conky usage cache label.
The installed `cursor-usage-fetch.py` keeps `~/.cursor/.usage-cache.json` fresh
(conky cache harvest or Cursor dashboard API), throttled by TTL.
