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
| Overview | Four linked totals, directory/language rankings, current hotspots, attention markers, recent commits, and a collapsible tab index |
| History | Cumulative LOC explorer beside searchable commits and directory changes over `--max-commits` (default 1000) |
| Churn | Additions/deletions over that window; directories and up to 200 files, including deleted history |
| Files | Tracked index with language/directory/attention filters and Summary, Structure, Composition, or All metrics columns |
| Tree | Expandable Files hierarchy, path filtering with ancestor context, name/line-count ordering, and file profiles |
| Deps | Searchable root Cargo/npm/Python/Go declared and supported lockfile inventories with ecosystem/scope filters and package details |
| Server logs | Searchable 5000-line request/scan/watch/CI/boot ring with event/window filters, pause/resume, inspection, copy, and download; always last |

Files caps content at 5000 lines/1 MB and identifies binaries; `/api/file?path=…`
requires cached tracked paths. Python uses AST when parsable; other metrics are
lexical estimates. Exclude vendor/build and minified/lock/map/sum/SVG files.
Lock inventories cap at 500 and do not cover every recognized format.
Header shows branch/dirty/scan age and HEAD Actions verdict when available. CI
refreshes on boot, HEAD changes and roughly each watch minute; missing gh/auth/
GitHub origin/checks hides it. Dates use short months and 12-hour times.
Ranked directory, language, and author bar lists stay at most 560px wide and
shrink to the available width on smaller screens. Overview rankings open filtered
Files views; Churn rankings filter hotspots. Related charts and tables sit beside
each other on desktop and stack on smaller screens. Truncated ranking, tree, and
overview labels reveal their full text on pointer hover or keyboard focus.

History, Churn, Files, and Deps use 25-row pages, searchable editable dropdowns,
filter reset, and CSV export of all matching rows in current sort order and visible
columns. File column presets leave the full metrics in the file profile. Package
details preserve all scanned versions of a declared package; lock-only entries
do not establish dependency relationships. Commit subjects open scanned details.

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
File profiles expose source search with highlighted matching lines, previous/next
navigation (`Enter`/`Shift+Enter`), path/source copy, and source download. Truncated
content actions explicitly export the preview. Source composition starts expanded;
other metric groups open on demand. Historical Churn rows open their scanned
change counts without requiring a current file.

## Caches and watching

Generated `.codeview/.gitignore` excludes itself, `cache/`, daemon files/logs;
`modules/` stays trackable. Atomic JSON caches reuse scanner-schema/options
fingerprints. Every three seconds, watch checks HEAD and hashed porcelain status;
changes rescan stale data/update browsers. Edits to already-dirty files may leave
status unchanged: force `reload` or startup `--reindex`. `--no-watch` disables updates.

## Clusterfork tabs and extensions

Tracked `.codeview/modules/` tabs: `deployments.py` for Terraform resources and
retail cost estimates, `scripts.py` for launchers/rotation/PATH/scripts/configs,
`skills.py` for skills/slash commands, `statusline.py` for scripts/fetchers/previews.
[Codeview modules](codeview-modules.md) owns API, frames, theme and reload;
the [add-codeview-module skill](../skills/add-codeview-module/SKILL.md) owns templates/workflow.

Scripts, Skills, and Statusline use compact tables with 15-row pages, section
dropdowns, summary/all-field columns, sorting, CSV export, and row details with copy actions. Statusline
previews follow the shared theme. Deployments keeps snapshot/pricing explanations
in disclosures and adds dropdown filters, resource ordering, and bulk expansion.

Deployments discovers local `.tf`/`.tf.json` directories outside dependency and
cache trees and runs only `terraform state pull` against their existing backends.
It distinguishes state records, state-only/deposed instances, declarations missing
from readable state, and unverified declarations when state access fails. This is
the last Terraform snapshot, not a live cloud refresh. Resource details expose
allowlisted identifiers, sizing, networking, retention, access relationships, and
common tags; raw state, outputs, credentials, and marked-sensitive attributes are
never rendered. Missing Terraform/backend access leaves configuration visible.

Azure estimates use paginated public USD Consumption rates from the
[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).
The fixed subtotal includes supported regular Linux compute, its managed OS disk,
managed data disks, and Standard IPv4 allocations. VM hours are editable; disks
and IPs assume a full 730-hour month. Source queries and rate dates are inspectable.
Blob capacity, standard vault operations, and Analytics Logs ingestion show unit
rates, with usage unknown; dependent resources point to their parent cost rather
than counting it again. Unsupported SKUs and failed/ambiguous rate lookups remain
unpriced. The subtotal excludes usage charges, free allowances, discounts, taxes,
and other unmodeled meters. In-memory inventory caches for 60 seconds and successful
prices for 24 hours (failures five minutes); Refresh bypasses both. No cloud writes,
backend initialization, refresh, plan, or apply runs from the tab.

## Browser validation

The optional browser check uses disposable repository data, offline module
snapshots, and a locally installed Chromium. It exercises filtering, dropdown
keyboard behavior, pagination, exports, dialogs, and all core views in both themes
at desktop, zoom-equivalent, and phone widths, including opaque module frames:

```bash
env -u NO_COLOR uv run --no-project \
  --with-requirements automation/controller/requirements.txt --with playwright \
  python tests/codeview_browser.py
```
