# Dashboard (codeview)

`codeview` runs a localhost daemon for the current git repo using Python stdlib,
git and a browser; GitHub CI badges also need authenticated `gh`.

## Running

```text
codeview                     # same as start
codeview start [--port N] [--max-commits N] [--reindex] [--no-watch] [--no-open]
codeview stop
codeview restart [start flags]
codeview reload               # git-data rescan, no restart
codeview status
codeview open
```

Commands resolve roots from nested directories/worktrees. `.codeview/daemon.json`
tracks each daemon; port defaults to stable path hash 46000–49999, strictly
overridden by `--port`. Start logs to `.codeview/daemon.log`, waits 30 seconds and
opens a browser unless `--no-open`; repeat starts open the existing server. A
responding port counts as running even without bookkeeping. Stop refuses untracked
servers or tracked processes whose available command line does not match codeview;
stale bookkeeping is removed. Status exits 1 when stopped, otherwise reports live
branch/dirty/watch/modules/scan metadata.

Reload persists git data and bumps browser generation. Python module changes need
watcher/server restart. Reinstall after editing `scripts/codeview/`, or set
`CLUSTERFORK_DIR` to run the checkout instead of installed package.

## Views

| Tab | Contents |
| --- | --- |
| Overview | File/line/commit counts; directory/language lines |
| History | Cumulative LOC, commit graph, directory/recent changes over `--max-commits` (default 1000) |
| Churn | Additions/deletions over that window; directories and up to 200 files, including deleted history |
| Files | Tracked index, source composition, symbols/imports/estimated complexity, content and ownership/history |
| Deps | Root Cargo/npm/Python/Go summaries and supported lockfile inventories, not a resolution graph |
| Server logs | In-memory 5000-line request/scan/watch/CI/boot ring; always last |

Files caps content at 5000 lines/1 MB and identifies binaries; `/api/file?path=…`
requires cached tracked paths. Python uses AST when parsable; other metrics are
lexical estimates. Exclude vendor/build and minified/lock/map/sum/SVG files.
Lock inventories cap at 500 and do not cover every recognized format.
Header shows branch/dirty/scan age and HEAD Actions verdict when available. CI
refreshes on boot, HEAD changes and roughly each watch minute; missing gh/auth/
GitHub origin/checks hides it. Dates use short months and 12-hour times.

Palette (`Ctrl+K`, `⌘K`, `Ctrl+Space`) changes view/theme and exports Markdown or
browser print/PDF. `Alt+1`–`Alt+6` select core tabs; hashes track `#history`, `#m:skills`.

## Caches and watching

Generated `.codeview/.gitignore` excludes itself, `cache/`, daemon files/logs;
`modules/` stays trackable. Atomic JSON caches reuse scanner-schema/options
fingerprints. Every three seconds, watch checks HEAD and hashed porcelain status;
changes rescan stale data/update browsers. Edits to already-dirty files may leave
status unchanged: force `reload` or startup `--reindex`. `--no-watch` disables updates.

## Clusterfork tabs and extensions

Tracked `.codeview/modules/` tabs: `scripts.py` for launchers/rotation/PATH/scripts/
configs, `skills.py` for skills/slash commands, `statusline.py` for scripts/fetchers/
previews. [Codeview modules](codeview-modules.md) owns API, frames, theme and reload;
the [add-codeview-module skill](../skills/add-codeview-module/SKILL.md) owns templates/workflow.
