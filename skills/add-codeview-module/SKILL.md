---
name: add-codeview-module
description: >
  Add a drop-in codeview dashboard tab: a Python file in
  `.codeview/modules/` that becomes a namespaced `/m/<name>/` page.
  Follows the repo's existing tabs and the codeview module contract.
  Use when asked to "add a codeview module", "create a codeview tab",
  "custom codeview module", "drop-in dashboard tab", or
  "/add-codeview-module".
---

# Add Codeview Module

Add a project-specific tab to the codeview dashboard for the current git
repo. Write only `<repo>/.codeview/modules/<slug>.py`. Do not edit
codeview's server, scanners, or UI assets (`scripts/codeview/` in
clusterfork, or `~/.config/clusterfork/scripts/codeview/` on a machine).

If this repo already has `.codeview/modules/*.py`, list them and copy the
closest one's page shell, `scan`/`render` split, class-prefixing, and tests
rather than starting from the template below.

## Instructions

1. **Pick the tab.** One slug, one concern. The slug — `NAME`, or the
   filename stem when `NAME` is absent — must match `^[a-z0-9][a-z0-9-]*$`.
   Tab order is filename sort. Confirm no existing module already claims the
   slug: a collision silently drops one of the two tabs.

2. **Collect, then render.** `scan(repo)` returns a dict. `render(data)`
   returns a complete HTML document. The GET `""` handler is
   `(200, render(scan(repo)), "text/html; charset=utf-8")`. Keep I/O out of
   `render`. Extra routes (`POST`, JSON `dict` bodies) stay under the
   module namespace.

3. **Write the module.** Create `.codeview/modules/` if needed. Use only
   the standard library. Resolve the repo as
   `Path(__file__).resolve().parents[2]`. Escape every interpolated value
   with `html.escape(..., quote=True)`.

4. **Match the host page.** The tab is framed with
   `sandbox="allow-scripts allow-forms"`, so the response is a full document
   and the frame has an *opaque* origin: `localStorage`, cookies,
   `window.parent`, and top-level navigation are all unavailable inside a
   tab. Keep the theme bootstrap wrapped in `try`/`catch` and let it fall
   back to `prefers-color-scheme` — the stored `codeview-theme` only applies
   when `/m/<slug>/` is opened directly in a browser tab, and the
   dashboard's own theme toggle does not reach the frame. Treat
   `navigator.clipboard` the same way: give it a `.catch()` so a blocked
   write cannot become an unhandled rejection. Link `/assets/app.css` and
   use the host classes below. Do not paste design-guide catalog markup —
   those class names are not what `/assets/app.css` defines. Extra CSS is
   for layout only; prefix it with the slug. Colors use host variables
   (`--ink`, `--ink-strong`, `--muted`, `--line`, `--surface`,
   `--surface-raised`, `--good`, `--danger`, `--accent`, `--radius`).

5. **Test.** If this repo already tests codeview modules, extend that file
   and update every assertion that pins the module set or its count — those
   break the moment a tab is added. Otherwise add a test in the repo's
   existing style, or skip a new file if there is no suite. Cover: module
   loads `ok`, GET `/m/<slug>` is registered, `scan()` returns the expected
   keys/rows, the page handler returns 200 HTML containing the heading and a
   stable marker (board class, filter id). Drive the handler with
   `types.SimpleNamespace()`.

6. **Verify.** `python3 -m py_compile .codeview/modules/<slug>.py`, then
   run the tests you touched. `codeview reload` only rescans git data — it
   does not pick up a new or edited module; the watcher restarts the daemon
   after a ~5 s quiet period, or use `codeview restart`. The daemon imports
   the module, so running it writes `.codeview/modules/__pycache__/`;
   confirm the repo ignores that. The tool's own `.codeview/.gitignore`
   covers `cache/`, `daemon.json`, and `daemon.log`, but not the module
   bytecode.

7. **Docs.** Only if this repo lists the tabs it ships (clusterfork:
   `docs/dashboard.md`), add the new tab to that list. Do not copy the
   module contract into other files.

## Contract

```python
NAME = "my-tab"          # optional; defaults to filename stem
DESCRIPTION = "tooltip"  # optional; tab tooltip

def register(reg):
    def page(req):
        return (200, "<h1>…</h1>", "text/html; charset=utf-8")
    reg.add_route("GET", "", page)        # /m/my-tab/
    reg.add_route("POST", "/do", handler) # /m/my-tab/do
```

- `reg.add_route(method, path, handler)` — methods `GET|POST|PUT|DELETE|PATCH`.
  `path` is `""`/`"/"` for the main page, or `/segment`s. The registry
  prefixes `/m/<NAME>/`, rejects escapes, and rejects a duplicate
  `(method, path)` within one module.
- Handler `req`: `.method`, `.path`, `.query` (dict), `.headers`,
  `.json()`, `.text()`.
- Return `(status: int, body, content_type: str)`. `body` is `str`,
  `bytes`, or `dict` (JSON).
- Failures are contained, never fatal: an import or `register` error becomes
  a "broken" tab showing its traceback, and a handler that raises at request
  time renders a 500 traceback page. Neither takes down the server or the
  other tabs — so a traceback in the tab is the normal way you find out
  `scan()` hit a missing path.

## Host markup

Reuse these classes from `/assets/app.css`:

- Shell: `dashboard-layout`, `dashboard-heading`, `dashboard-title`,
  `dashboard-context`
- Metrics: `metric-grid overview-metrics`, `metric-card`, `metric-label`,
  `metric-num`
- Sections: `section-heading`, `section-note`
- Tables: `table-container`, `data-table`, `td.wrap`, `caption.sr-only`
- Filter: `form-group`, `form-label`, `input-with-action`, `input-field`,
  `input-leading-icon`
- Copy: `meta-tag meta-tag-copy` with `data-copy` and a document click
  listener

Put the theme bootstrap from the template `<head>` before first paint.

## Template

Replace `NAME`, `DESCRIPTION`, `<title>`, headings, and every `my-tab`
class, id, and `data-*` attribute. Point `scan()` at the paths this tab
is for.

```python
"""Codeview tab: <one-line inventory>. """
from __future__ import annotations

import html
from pathlib import Path

NAME = "my-tab"
DESCRIPTION = "tooltip shown on the tab"

REPO = Path(__file__).resolve().parents[2]


def register(reg):
    def page(_req):
        return (200, render(scan(REPO)), "text/html; charset=utf-8")
    reg.add_route("GET", "", page)


def scan(repo: Path) -> dict:
    rows = []
    root = repo / "path-to-scan"
    if root.is_dir():
        for path in sorted(root.glob("*")):
            if path.name.startswith("."):
                continue
            rows.append({
                "name": path.name,
                "source": str(path.relative_to(repo)),
                "runs": "",
            })
    return {
        "rows": rows,
        "counts": {"Items": len(rows)},
    }


def render(data: dict) -> str:
    metrics = "".join(
        f'<div class="metric-card"><span class="metric-label">{esc(k)}</span>'
        f'<span class="metric-num">{num(n)}</span></div>'
        for k, n in data["counts"].items())
    body = "".join(
        f'<tr><td class="wrap">{copy_btn(r["name"])}</td>'
        f'<td class="wrap"><span class="meta-tag">{esc(r["source"])}</span></td>'
        f'<td class="wrap">{esc(r["runs"])}</td></tr>'
        for r in data["rows"])
    table = ""
    if data["rows"]:
        table = f"""
<section data-my-tab-section>
  <div class="section-heading">
    <h2>Items</h2>
    <span class="section-note">scanned from this repo on each load</span>
  </div>
  <div class="table-container" role="region" aria-label="Items">
    <table class="data-table">
      <caption class="sr-only">Items</caption>
      <thead><tr>
        <th scope="col">Name</th>
        <th scope="col">Source</th>
        <th scope="col">What it does</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</section>
"""
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>my-tab</title>
<link rel="stylesheet" href="/assets/app.css">
<script>
(function () {{
  // localStorage throws in the dashboard's sandboxed frame (opaque origin);
  // this only takes effect when /m/my-tab/ is opened directly.
  try {{
    var stored = localStorage.getItem("codeview-theme");
    if (stored === "light" || stored === "dark")
      document.documentElement.dataset.theme = stored;
    else if (window.matchMedia("(prefers-color-scheme: light)").matches)
      document.documentElement.dataset.theme = "light";
  }} catch (err) {{}}
}})();
</script>
<style>
  .my-tab-toolbar {{
    display: flex; flex-wrap: wrap; align-items: flex-end;
    justify-content: space-between; gap: 12px 24px;
  }}
  .my-tab-search {{ max-width: 320px; margin: 0; }}
  .my-tab-board {{
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px 24px;
    align-items: start;
  }}
  .my-tab-board .table-container {{ width: 100%; }}
  .my-tab-board .data-table {{ width: 100%; }}
  .my-tab-board .data-table td.wrap {{ white-space: normal; }}
</style>
</head>
<body>
<div class="dashboard-layout">
  <header class="dashboard-heading">
    <div class="dashboard-title">
      <h1>My tab</h1>
    </div>
    <div class="dashboard-context">one-line scope</div>
  </header>
  <div class="my-tab-toolbar">
    <div class="metric-grid overview-metrics">{metrics}</div>
    <div class="form-group my-tab-search">
      <label class="form-label" for="my-tab-filter">Filter</label>
      <div class="input-with-action">
        <svg class="icon input-leading-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="m21 21-4.34-4.34" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"></path>
          <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" stroke-width="2"></circle>
        </svg>
        <input class="input-field" type="search" id="my-tab-filter" autocomplete="off">
      </div>
    </div>
  </div>
  <div class="my-tab-board">{table}</div>
</div>
<script>
(function () {{
  var input = document.getElementById("my-tab-filter");
  if (input) input.addEventListener("input", function () {{
    var q = input.value.toLowerCase();
    document.querySelectorAll("[data-my-tab-section]").forEach(function (sec) {{
      var shown = 0;
      sec.querySelectorAll("tbody tr").forEach(function (tr) {{
        var hit = !q || tr.textContent.toLowerCase().indexOf(q) !== -1;
        tr.style.display = hit ? "" : "none";
        if (hit) shown++;
      }});
      sec.hidden = shown === 0;
    }});
  }});
  document.addEventListener("click", function (e) {{
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    navigator.clipboard.writeText(btn.dataset.copy).then(function () {{
      btn.classList.add("is-copied");
      window.setTimeout(function () {{ btn.classList.remove("is-copied"); }}, 1600);
    }}).catch(function () {{ /* clipboard can be blocked in the frame */ }});
  }});
}})();
</script>
</body>
</html>
"""


def copy_btn(name: str) -> str:
    return (f'<button type="button" class="meta-tag meta-tag-copy" '
            f'data-copy="{esc(name)}" aria-label="Copy {esc(name)}">'
            f'{esc(name)}</button>')


def num(n: object) -> str:
    """Thousands-separate real numbers; escape anything else."""
    if isinstance(n, int) and not isinstance(n, bool):
        return f"{n:,}"
    return esc(n)


def esc(s: object) -> str:
    """Escape for HTML. Only None is empty — 0 and False must still render."""
    return "" if s is None else html.escape(str(s), quote=True)
```
