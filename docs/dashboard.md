# Dashboard (codeview)

`codeview` runs a localhost dashboard for the current git repository as a
background daemon. It uses Python's standard library, git, and a browser.
GitHub CI reporting additionally uses an authenticated `gh` CLI.

## Running

```text
codeview                     # same as codeview start
codeview start [--port N] [--max-commits N] [--reindex] [--no-watch] [--no-open]
codeview stop
codeview restart [start flags]
codeview reload               # full git-data rescan in place
codeview status
codeview open
```

Commands resolve the repo root through git, including from nested directories
and worktrees. Each repo tracks its daemon in `.codeview/daemon.json`.
The default port is a stable path hash in 46000–49999; `--port` overrides it.
The control CLI requests that port strictly rather than drifting to another.

`start` detaches a process logging to `.codeview/daemon.log`, waits up to 30
seconds for the port, then opens a browser unless `--no-open`. Repeated starts
open the existing dashboard. A responding chosen port is treated as an
already-running server even without tracked bookkeeping. `stop` refuses to
kill an untracked server or a tracked process whose available command line
does not resemble codeview. Stale daemon files are cleaned up.

`status` exits 1 when no daemon is running; a live API also supplies branch,
dirty state, watch mode, module list, and last-scan time. `reload` rescans and
persists git data without restarting the server, then bumps the generation so
browsers refresh. New or changed Python modules need a watcher restart or
`codeview restart`, rather than a data reload.

The launcher uses the installed package. Reinstall after editing
`scripts/codeview/`, or set `CLUSTERFORK_DIR` to the checkout to run that copy
without installing it. [Installation](installation.md) owns the file mapping.

## Views

| Tab | Contents |
| --- | --- |
| Overview | File/line/commit counts and lines by directory/language |
| History | Cumulative LOC over at most `--max-commits` (default 1000), inspectable commit graph, directory changes, and recent commits |
| Churn | Additions plus deletions over the same window, ranked directories and up to 200 files; deleted files remain historical entries |
| Files | Tracked-file index with source composition, symbols, imports, and estimated complexity; file content and git ownership/history |
| Deps | Root Cargo/npm/Python/Go manifest summaries and supported lockfile inventories; no dependency-resolution graph |
| Server logs | In-memory ring of 5000 request, scan, watch, CI, and boot log lines; always the last tab |

The Files pane caps content at 5000 lines/1 MB and identifies binaries. Its
`/api/file?path=…` endpoint accepts only paths in the cached tracked-file index.
Python metrics use the standard-library AST when parsing succeeds; other
language metrics are labeled lexical estimates. Vendor/build directories and
minified, lock, map, sum, and SVG files are excluded from the index.
Lockfile entry inventories are capped at 500 and are not available for every
recognized lockfile format.

The header shows branch/dirty state, last-scan age, and a GitHub Actions verdict
for HEAD when available. CI is fetched on boot, on HEAD changes, and roughly
once a minute while watching. Missing `gh`, auth, GitHub origin, or check runs
hides the badge. Dates use three-letter months and 12-hour times.

The command palette (`Ctrl+K`, `⌘K`, or `Ctrl+Space`) changes views/theme and
exports the active view as Markdown or via browser print/PDF. `Alt+1`–`Alt+6`
select core tabs. URL hashes track the view, such as `#history` or `#m:skills`.

## Caches and watching

The tool writes `.codeview/.gitignore` for itself, `cache/`, `daemon.json`, and
`daemon.log`. Drop-in `modules/` remain trackable. Cache JSON is written
atomically and reused on boot; the scanner schema and scan options participate
in fingerprints so scanner changes can invalidate old results.

Every three seconds the watcher checks HEAD and the hash of git's porcelain
status. A changed fingerprint rescans stale data and updates browsers in place.
Further edits to an already-dirty file can leave porcelain status unchanged;
use `codeview reload` to force a fresh scan in that case. `--reindex` does the
same on startup; `--no-watch` disables background updates.

## Clusterfork tabs and extensions

This repository tracks three modules under `.codeview/modules/`:

- `scripts.py`: launchers, rotate wrappers, PATH helpers, scripts, and configs.
- `skills.py`: shared skills and slash-command inventory.
- `statusline.py`: Claude/Cursor scripts, usage fetchers, and previews.

For the drop-in API, sandboxed frames, theme handoff, and module reload behavior,
read [Codeview modules](codeview-modules.md). The
[add-codeview-module skill](../skills/add-codeview-module/SKILL.md) supplies the
page template and implementation workflow.
