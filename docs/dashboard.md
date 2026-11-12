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
| Overview | Four headline totals, linked summaries for every tab, and top four directory/language line counts |
| History | Cumulative LOC, commit graph, directory/recent changes over `--max-commits` (default 1000) |
| Churn | Additions/deletions over that window; directories and up to 200 files, including deleted history |
| Files | Tracked index, source composition, symbols/imports/estimated complexity, content and ownership/history |
| Tree | Expandable folder hierarchy of the Files index, path filtering with ancestor context, and file profiles |
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
browser print/PDF. `Alt+1`–`Alt+0` switch to the first ten tabs in display order,
including modules; `Alt+Shift+1`–`Alt+Shift+6` switch to tabs 11–16. Hashes track
`#history`, `#m:skills`.
`Alt+Left` and `Alt+Right` cycle through all tabs in display order, wrapping at
either end, when focus is outside an editable field. Alt shortcuts change the
visible view without leaving a tab button focused.
Touch drags on the tab strip or panel slide between adjacent tabs; vertical
scrolling, nested controls, and horizontally scrolling data keep their gestures.
Details and reference dialogs keep keyboard focus and page shortcuts stay
inactive until closed; Spotlight continues to handle its command shortcuts.

History's chart opens a native explorer with commit-range presets, sliders and
exact numeric range entry, zoom/pan, line/area and axis controls, sample pinning,
and a sortable exact-data table. CSV exports the selected range in displayed
table order, or the full scanned dataset in original commit order. Cumulative
totals are net line changes within the scanned window; they are not absolute
repository size when earlier commits fall outside that window. Cached history
retains full commit hashes, timestamps with offsets, and subjects; the explorer
and CSV normalize timestamps to UTC.

Files and Churn rows and Tree files open the file profile in a dialog. Closing
it restores the originating row and preserves filters, sorting, and expanded
folders. Tree supports expand/collapse all, arrow-key navigation, Home/End,
type-to-find, and Enter/Space to toggle a folder or open a file. Its indexed
paths follow the same exclusions as Files. Overview summaries use scanned totals
and each custom module's description; broken modules link to their error details.

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
