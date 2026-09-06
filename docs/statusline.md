# Statusline

Clusterfork documents and installs the terminal status lines currently in use on
this machine across Claude Code, Cursor Agent, and Codex CLI: colored footers
showing model, effort/params, active account, context usage, and plan/rate-limit
usage.

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

### OpenCode Go mode

Under [`occ`](shell-modules.md) the same script renders
`model · go · account · ctx% · 5h% · wk% · mo%` instead. Claude Code spawns the
statusline as a child process, so it inherits the launcher's environment and
detects the mode from `ANTHROPIC_BASE_URL` — no second script and no
`--settings` override. (CLI `--settings` *does* outrank `~/.claude/settings.json`
for `statusLine` if a fully separate line is ever wanted.)

Every Anthropic-side segment is replaced, because under `occ` each one would
otherwise describe an account the session is not billing against:

- **Account** is the profile `rotate-opencode` selected, read as the suffix of
  the `~/.local/share/clusterfork-auth/opencode/current` symlink. No token
  matching needed — unlike Claude's credentials file, nothing rewrites this link.
- **Usage** is OpenCode Go's three dashboard windows: 5h (`$12`), weekly (`$30`),
  and monthly (`$60`). The payload's `rate_limits` are discarded.

The gateway serves no usage data at all (see
[OpenCode Go endpoint](opencode-go.md)), so the authenticated web dashboard is
the only source. conky-linear-HUP already scrapes it (Firefox `auth` cookie →
the three usage cards) and is the system of record, so the statusline reads its
`cache/opencode-usage.json` rather than duplicating that HTML parser. conky
repolls on its own every 60–300s; only if that cache ages past `OCC_USAGE_TTL`
(default 300s) does the statusline drive conky's fetcher itself, in the
background. A figure served from a stale cache is prefixed `~`; if the cache is
missing or its last fetch failed, the segments show `—` rather than a stale
number. Overridable: `OCC_USAGE_CACHE`, `OCC_USAGE_FETCHER`, `OCC_USAGE_TTL`.

## Cursor line

Renders: `model · params/max · account · ctx% · auto% · api%`.

Account is the suffix of the resolved `~/.config/cursor/auth.json` symlink
(e.g. `ida`), with a fallback to the conky usage cache label.
The installed `cursor-usage-fetch.py` keeps `~/.cursor/.usage-cache.json` fresh
(conky cache harvest or Cursor dashboard API), throttled by TTL.

## Codex line

Codex CLI implements its status line natively in its Rust TUI via `[tui].status_line`
in `~/.codex/config.toml`:

```toml
[tui]
status_line = ["model-with-reasoning", "context-used", "task-progress", "five-hour-limit"]
status_line_use_colors = true
```

Unlike Claude and Cursor — which spawn child-process scripts piped with JSON
payloads on stdin — Codex renders its status line directly in the TUI without
external script hooks.

Renders: `model-with-reasoning · context-used · task-progress · five-hour-limit`.

- **model-with-reasoning**: active model and reasoning effort (e.g. `gpt-6-astra (max)`)
- **context-used**: active context percentage or token budget consumption (e.g. `12% context`)
- **task-progress**: multi-turn or task step counter
- **five-hour-limit**: rolling 5-hour rate limit percentage

