# Dashboard (codeview)

`codeview` serves a localhost web dashboard for the git repository you run it
in. One command, no install-in-repo step; data caches inside the repo and
rescans in the background. Project-specific views are added by dropping
Python "modules" into the repo, which appear as extra tabs.

## Running

```
codeview [--port N] [--max-commits N] [--reindex] [--no-watch]
```

- Must be run inside a git repository (nested subdirs and worktrees resolve
  via `git rev-parse --show-toplevel`).
- Default port is a stable hash of the repo path in the 46000–49999 range, so
  bookmarks survive; `--port` overrides. If the computed port is already
  answering, the wrapper just opens a browser on it instead of failing to bind.
- tmux behavior follows the house launchers: wrapped in a new
  `dash-<basename-pwd>` session (collision suffixes included) with the same
  bypasses (`CF_NO_TMUX`, already inside tmux, non-TTY stdin); the browser
  opens once the server answers. See [Shell Modules](shell-modules.md) for
  `_cf_tmux` semantics — `bin/codeview` reimplements them in Python rather
  than sourcing the bash helper, because the wrapper is a Python script.

## What it shows

Header: `repo · branch · dirty · ci ✓|✗|…` — the CI state is the GitHub
Actions check-run verdict for HEAD, fetched via the `gh` CLI (logged-in
auth) in the background (eager on boot, refreshed ~60 s and on HEAD
changes). It is hidden when `gh` is missing, origin isn't GitHub, or HEAD
has no check runs. The commit hash is not shown. All dates render with
three-letter months ("Aug 28 2026 15:24").

Five core tabs, built from git + manifest scanning (stdlib; no pip
dependencies anywhere in the tool):

- **Overview** — file/line/commit counts, lines by top-level dir, lines by language
- **History** — cumulative LOC over commit history (`git log --numstat`
  aggregates, capped at `--max-commits`, default 1000), most-changed dirs, recent commits
- **Files** — tracked-file index with physical/source/blank/comment lines,
  functions, imports, and estimated cyclomatic complexity in every row;
  filterable and excludes vendor dirs, minified/lockfile/map suffixes by
  default. Clicking a row opens the file: content pane on the left (line
  numbers, capped at 5k lines / 1 MB, binary files show a notice), and a
  verbose, grouped stats pane on the right with source composition, symbols,
  formatting, calls/returns/exceptions, complexity and Halstead estimates,
  maintainability index, attention markers, and git ownership/history. Python
  uses the standard-library AST when it parses; other languages use a
  comment/string-aware lexical heuristic and identify that in the pane. Backed
  by `GET /api/file?path=…` — the path must exact-match an entry in the
  tracked-files cache, so path traversal is impossible by construction
- **Deps** — declared dependencies from `Cargo.toml`, `package.json`,
  `pyproject.toml`, `go.mod`, each with lockfile name/entry counts where a
  lockfile exists (flat summary only — not a resolution graph)
- **Server logs** (always the last tab, pinned to the right edge) — the
  HTTP server's own in-memory log ring (5000 lines), verbose: every request
  line, per-section scan results with timings and counts, watch-loop
  detections (head/dirty change → rescan, module-set change → armed
  restart), CI refreshes, and boot sequence. Auto-scrolls to newest;
  refreshes on the normal polling cycle. Backed by `GET /api/logs`

## Caching

Everything lands in `<repo>/.codeview/`:

```
.codeview/
├── .gitignore      # written by the tool: ignores cache/
├── cache/          # meta.json history.json files.json deps.json fingerprints.json
└── modules/        # your drop-in modules (not ignored)
```

- Cache files are written atomically (tmp + rename) and reloaded on boot, so
  startup is fast on the second run.
- A watch thread (3 s cycle) compares a data fingerprint (HEAD sha + dirty
  status hash + scan options). A real change triggers a background rescan of
  stale sections in place — the generation counter bumps and the browser
  reloads itself via `/api/gen` polling. No restart, no downtime.
- `modules/` is deliberately not gitignored: dropping a module shows up in
  `git status`, and committing it is the repo owner's call.

## Modules (drop-in tabs)

Any `*.py` in `<repo>/.codeview/modules/` becomes a tab. Contract:

```python
NAME = "my-tab"          # optional; defaults to filename stem; ^[a-z0-9][a-z0-9-]*$
DESCRIPTION = "tooltip"  # optional

def register(reg):
    def page(req):
        return (200, "<h1>anything</h1>", "text/html")  # (status, body, content_type)
    reg.add_route("GET", "", page)          # → /m/my-tab/
    reg.add_route("POST", "/do", handler)   # → /m/my-tab/do
```

- Routes are always namespaced under `/m/<NAME>/` — the registry prepends
  the prefix and rejects paths that would escape it. `""` and `"/"` both
  mean the module's main page.
- `body` may be `dict` (served as JSON), `str`, or `bytes`.
- The handler receives a minimal request object: `.method`, `.path`,
  `.query` (dict), `.headers`, `.json()`, `.text()`.
- Modules have full route ownership: any verb/path under their namespace,
  full control of their page HTML (the core UI is plain HTML/JS; no shared
  frontend framework to target).

Failure behavior: a module that fails to import or register becomes a
"⚠ broken" tab showing its traceback — it never breaks the server or the
other tabs. Changes to the module set (files added/removed/modified) are
picked up by the watcher after a ~5 s quiet period and trigger a
self-restart via `os.execve` (the port is held; browsers reload themselves).

Trust model: modules are executed as plain Python from the repo, same trust
level as running the repo's own build scripts.

## Install mapping

| Repo path | Installed to | Role |
|---|---|---|
| `bin/codeview` | `~/.config/clusterfork/bin/codeview` | launcher (on `PATH`) |
| `scripts/codeview/` | `~/.config/clusterfork/scripts/codeview/` | server, scanners, module runtime, UI assets |

The launcher imports the server package from the installed copy — re-run
`./install-clusterfork.sh` after editing `scripts/codeview/` (the repo copy
is not what runs). See [Installation](installation.md).

## Tests

`tests/test_codeview_scan_cache.py`, `test_codeview_modules.py`,
`test_codeview_router.py` (boots the real HTTP server against a throwaway
repo), and `test_codeview_wrapper.py` (mock-tmux on PATH, house pattern).
CI additionally runs `python3 -m py_compile` over the package.
