# Statusline

Claude Code and Cursor use shell scripts that read JSON from stdin. Codex
renders its footer natively. [Installation](installation.md#installed-files)
owns the script destinations and config wiring. The shell scripts need `jq`
and Python 3; usage helpers refresh caches in the background.

## Claude

Renders `model · effort · account · ctx% · 5h% · wk%`, omitting missing effort
and account segments. Account labels come from matching the active
`~/.claude/.credentials.json` access token against saved profile copies.
After a token refresh the label can be absent until a copy matches again.

Usage comes from the statusline payload's `rate_limits` when present.
`claude-usage-fetch.py` harvests those values into
`~/.claude/.usage-cache.json`; when absent (including fast mode), it makes a
small quota-check request and reads rate-limit headers. API fetches use a
nonblocking lock and `CLAUDE_USAGE_TTL` (default 120 seconds). A failed fetch
leaves the previous cache untouched.

### OpenCode Go

When `ANTHROPIC_BASE_URL` contains `opencode.ai`, the script adds `go` and a
monthly usage segment, while retaining any supplied effort. It ignores
Anthropic rate limits and reads OpenCode dashboard usage instead.

The account label follows the suffix of the OpenCode store's `current` link
(`ROTATE_OPENCODE_AUTH_STORE_DIR` overrides its directory). This statusline
lookup does not match an overriding `OPENCODE_API_KEY` to a saved profile;
its label can therefore differ from the account actually billed.

Usage reads the first account's 5h/weekly/monthly windows from the external
conky-linear-HUP cache. Defaults:

| Variable | Default |
| --- | --- |
| `OCC_USAGE_CACHE` | `~/conky-linear-HUP/cache/opencode-usage.json` |
| `OCC_USAGE_FETCHER` | `~/conky-linear-HUP/scripts/fetch_opencode_usage.py` |
| `OCC_USAGE_TTL` | 300 seconds |

Clusterfork does not install that external scraper. If present, the fetcher is
started when the cache file is too old. A cache marked `staleCache` prefixes
values with `~`; missing/failed data displays `—`. The cache's first account
is not selected by the launcher's billing key. Gateway observations and launcher
configuration are in [OpenCode Go](opencode-go.md).

## Cursor

Renders `model · params/max · account · ctx% · auto% · api%`.
The label is the resolved `auth.json` suffix under
`${CURSOR_HOME:-~/.config/cursor}`, falling back to the selected label in
`~/live-wallpaper/conky-linear-HUP/cache/cursor-usage.json`.

`cursor-usage-fetch.py` keeps `~/.cursor/.usage-cache.json` fresh using that
conky cache or Cursor's dashboard API. `CURSOR_USAGE_TTL` defaults to 120
seconds; `CURSOR_AUTH_PATH` overrides the fetcher's credential path. Failed
fetches retain the previous cache.

## Codex

`statusline/codex/statusline.config.toml` owns the shared layout:

```toml
[tui]
status_line = ["model-with-reasoning", "context-used", "task-progress", "five-hour-limit", "weekly-limit"]
status_line_use_colors = true
```

The installer writes it on the shared auth store's writable host and links it
from `~/.codex/clusterfork-statusline.toml`. A container with that store mounted
read-only needs the host installation first. See
[container sharing](auth-rotation.md#sharing-with-a-container).

The `codex` and `cc` shell functions read the link under
`${CODEX_HOME:-~/.codex}` on every launch and pass only those two keys as
`--config` overrides before caller arguments. Invalid statusline types or
malformed TOML stop the launch. A caller override takes precedence; direct
binary invocation bypasses this wrapper.

Edit the repo template and reinstall on the host to distribute a layout.
Direct shared-file edits take effect on new launches but are replaced on the
next writable-host install. Existing sessions must restart. `/statusline`
updates Codex's local config/current session, not the shared file.

The wrapper deliberately avoids selecting a native shared `--profile`: on
Codex 0.162.1 that also redirected unrelated preference writes. Other settings
continue to use each environment's normal config. Native footer fields
include model/effort, context usage, task progress, and both quota windows;
context percentage is distinct from rate-limit consumption.
