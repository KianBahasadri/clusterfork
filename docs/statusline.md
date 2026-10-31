# Statusline

Claude/Cursor shell statuslines read stdin JSON and need `jq`/Python 3, with
background usage refresh. Codex renders natively. [Installation](installation.md#installed-files)
owns destinations/wiring.

## Claude

Layout: `model · effort · account · ctx% · 5h% · wk%`; missing effort/account
segments disappear. Labels match active `~/.claude/.credentials.json` access tokens
to saved copies, so refresh can temporarily remove the label.
`claude-usage-fetch.py` harvests payload `rate_limits` into `~/.claude/.usage-cache.json`;
when absent (including fast mode), it probes quota headers. API refresh uses a
nonblocking lock and `CLAUDE_USAGE_TTL` (120 seconds); failures retain old cache.

### OpenCode Go

Base URLs containing `opencode.ai` add `go`/monthly usage, retain effort and replace
Anthropic limits with dashboard usage. Label follows the OpenCode `current` suffix,
with `ROTATE_OPENCODE_AUTH_STORE_DIR` override; it does not match an overriding
billing key. Usage takes the external cache's first account, also independent of key:

| Variable | Default |
| --- | --- |
| `OCC_USAGE_CACHE` | `~/conky-linear-HUP/cache/opencode-usage.json` |
| `OCC_USAGE_FETCHER` | `~/conky-linear-HUP/scripts/fetch_opencode_usage.py` |
| `OCC_USAGE_TTL` | 300 seconds |

Clusterfork does not install this scraper. If available it refreshes old caches;
`staleCache` adds `~`, missing/failed data shows `—`. See [launcher](opencode-go.md).

## Cursor

Layout: `model · params/max · account · ctx% · auto% · api%`. Label uses resolved
`auth.json` suffix under `${CURSOR_HOME:-~/.config/cursor}`, then selected label in
`~/live-wallpaper/conky-linear-HUP/cache/cursor-usage.json`. `cursor-usage-fetch.py`
updates `~/.cursor/.usage-cache.json` from conky or dashboard API; `CURSOR_USAGE_TTL`
is 120 seconds, `CURSOR_AUTH_PATH` overrides credentials, failures retain cache.

## Codex

Shared `statusline/codex/statusline.config.toml`:

```toml
[tui]
status_line = ["model-with-reasoning", "context-used", "task-progress", "five-hour-limit", "weekly-limit"]
status_line_use_colors = true
```

Install on the writable auth-store host before [read-only containers](auth-rotation.md#sharing-with-a-container).
The installer writes the shared file and links `~/.codex/clusterfork-statusline.toml`.
`codex`/`cc` read that link under `${CODEX_HOME:-~/.codex}` each launch, passing only
these two keys as `--config` before caller arguments. Malformed TOML/invalid types
stop launch; caller overrides win, direct binary calls bypass the wrapper.
Edit the template/reinstall to distribute layouts; direct shared edits affect new
launches until overwritten. Existing sessions need restart. `/statusline` changes
local config/current session, not shared config. A shared native `--profile` is
avoided because Codex 0.162.1 also redirected unrelated preference writes.
Other settings stay local; context percentage and quota consumption are distinct.
