"""Codeview tab: live inventory and inspection of Claude, Cursor, and Codex statuslines."""
from __future__ import annotations

import ast
import html
import json
import os
import re
import time
import tomllib
from pathlib import Path

NAME = "statusline"
DESCRIPTION = "Claude Code, Cursor Agent, and Codex status line configs and scripts"

REPO = Path(__file__).resolve().parents[2]

DEST_MAP = {
    "statusline/claude/statusline.sh": Path.home() / ".claude" / "statusline-command.sh",
    "statusline/claude/usage-fetch.py": Path.home() / ".claude" / "claude-usage-fetch.py",
    "statusline/cursor/statusline.sh": Path.home() / ".cursor" / "statusline.sh",
    "statusline/cursor/usage-fetch.py": Path.home() / ".cursor" / "cursor-usage-fetch.py",
}


def register(reg):
    def page(_req):
        return (200, render(scan(REPO)), "text/html; charset=utf-8")
    reg.add_route("GET", "", page)


def scan(repo: Path) -> dict:
    files = scan_files(repo / "statusline")
    lines = get_line_specs(repo)
    caches = scan_caches()
    installed_count = sum(1 for f in files if f["installed"])
    return {
        "files": files,
        "lines": lines,
        "caches": caches,
        "counts": {
            "Statuslines": 3,
            "Modes": 4,
            "Scripts": len(files),
            "Installed": f"{installed_count}/{len(files)}",
        },
    }


def scan_files(statusline_dir: Path) -> list[dict]:
    rows = []
    if not statusline_dir.is_dir():
        return rows
    for path in sorted(statusline_dir.rglob("*")):
        if not path.is_file() or path.suffix not in (".sh", ".py"):
            continue
        rel = f"statusline/{path.relative_to(statusline_dir)}"
        dest = DEST_MAP.get(rel)
        installed = dest.is_file() if dest else False
        dest_str = str(dest).replace(str(Path.home()), "~") if dest else "—"
        rows.append({
            "name": path.name,
            "rel": rel,
            "source": rel,
            "dest": dest_str,
            "installed": installed,
            "runs": first_doc(path),
        })
    return rows


def get_line_specs(_repo: Path) -> list[dict]:
    # Active account check
    claude_acct = detect_claude_acct()
    cursor_acct = detect_cursor_acct()

    # Active caches for preview
    claude_cache = read_json(Path.home() / ".claude" / ".usage-cache.json") or {}
    h5 = int(claude_cache.get("five_hour", {}).get("used_percentage") or 15)
    wk = int(claude_cache.get("seven_day", {}).get("used_percentage") or 18)

    cursor_cache = read_json(Path.home() / ".cursor" / ".usage-cache.json") or {}
    auto_p = int(cursor_cache.get("auto", {}).get("used_percentage") or 0)
    api_p = int(cursor_cache.get("api", {}).get("used_percentage") or 0)

    # Codex config detection
    codex_info = detect_codex_info()

    return [
        {
            "name": "Claude Code",
            "mode": "Standard (Anthropic)",
            "format": "model · effort · account · ctx% · 5h% · wk%",
            "source": "statusline/claude/statusline.sh",
            "dest": "~/.claude/statusline-command.sh",
            "wiring": "agents/claude.json → ~/.claude/settings.json (statusLine)",
            "summary": "Renders model name, reasoning effort, active profile, context used %, and 5h/weekly rate limit percentages.",
            "preview_html": render_preview_claude("Claude 3.7 Sonnet", "high", claude_acct or "default", 14, h5, wk),
        },
        {
            "name": "Claude Code (occ)",
            "mode": "OpenCode Go (occ)",
            "format": "model · go · account · ctx% · 5h% · wk% · mo%",
            "source": "statusline/claude/statusline.sh",
            "dest": "~/.claude/statusline-command.sh",
            "wiring": "Inherited from occ launcher via ANTHROPIC_BASE_URL",
            "summary": "Renders model name, 'go' tag, selected OpenCode profile, context %, and 5h ($12), weekly ($30), monthly ($60) spend percentages from dashboard cache.",
            "preview_html": render_preview_occ("Claude 3.7 Sonnet", "go", claude_acct or "sepehr", 18, 22, 41, 65),
        },
        {
            "name": "Cursor Agent",
            "mode": "Cursor CLI",
            "format": "model · params/max · account · ctx% · auto% · api%",
            "source": "statusline/cursor/statusline.sh",
            "dest": "~/.cursor/statusline.sh",
            "wiring": "~/.cursor/cli-config.json (statusLine)",
            "summary": "Renders model display name, parameter summary or max flag, resolved auth profile, context %, and auto / api monthly usage percentages.",
            "preview_html": render_preview_cursor("cursor-grok-4.5", "max", cursor_acct or "default", 25, auto_p, api_p),
        },
        {
            "name": "Codex CLI",
            "mode": "Native TUI",
            "format": "model-with-reasoning · context-used · task-progress · five-hour-limit · weekly-limit",
            "source": "agents/codex.toml / ~/.codex/config.toml",
            "dest": "~/.codex/config.toml",
            "wiring": "~/.codex/config.toml ([tui].status_line)",
            "summary": "Built-in TUI statusline showing active model, reasoning effort, context percentage, task progress, 5h rate limit, and weekly rate limit.",
            "preview_html": render_preview_codex(
                codex_info.get("model", "gpt-6-astra"),
                codex_info.get("effort", "max"),
                12,
                15,
                18,
            ),
        },
    ]


def scan_caches() -> list[dict]:
    rows = []
    claude_p = Path.home() / ".claude" / ".usage-cache.json"
    if claude_p.is_file():
        c = read_json(claude_p)
        if isinstance(c, dict):
            h5 = c.get("five_hour", {}).get("used_percentage")
            wk = c.get("seven_day", {}).get("used_percentage")
            fetched = format_time(c.get("fetched_at"))
            rows.append({
                "target": "Claude Code",
                "file": "~/.claude/.usage-cache.json",
                "source": c.get("source", "statusline"),
                "data": f"5h: {h5}% · wk: {wk}%",
                "updated": fetched,
            })
    cursor_p = Path.home() / ".cursor" / ".usage-cache.json"
    if cursor_p.is_file():
        c = read_json(cursor_p)
        if isinstance(c, dict):
            auto_p = c.get("auto", {}).get("used_percentage")
            api_p = c.get("api", {}).get("used_percentage")
            fetched = format_time(c.get("fetched_at"))
            rows.append({
                "target": "Cursor Agent",
                "file": "~/.cursor/.usage-cache.json",
                "source": c.get("source", "api"),
                "data": f"auto: {auto_p}% · api: {api_p}%",
                "updated": fetched,
            })
    codex_p = Path.home() / ".codex" / "config.toml"
    if codex_p.is_file():
        info = detect_codex_info()
        items = info.get("status_line", [])
        mtime = codex_p.stat().st_mtime
        rows.append({
            "target": "Codex CLI",
            "file": "~/.codex/config.toml",
            "source": "[tui] status_line",
            "data": f"model: {info.get('model')} ({info.get('effort')}) · {len(items)} segments",
            "updated": format_time(mtime),
        })
    return rows


def detect_claude_acct() -> str:
    creds = Path.home() / ".claude" / ".credentials.json"
    if not creds.is_file():
        return ""
    try:
        cur = creds.read_text(encoding="utf-8", errors="replace")
        for sib in creds.parent.glob(".credentials.json.*"):
            if sib.name.endswith((".bak", ".tmp")):
                continue
            if sib.read_text(encoding="utf-8", errors="replace") == cur:
                return sib.name.replace(".credentials.json.", "")
    except OSError:
        pass
    return ""


def detect_cursor_acct() -> str:
    auth = Path.home() / ".config" / "cursor" / "auth.json"
    try:
        if auth.is_symlink():
            target = os.readlink(auth)
            stem = Path(target).name
            if stem.startswith("auth.json."):
                return stem.replace("auth.json.", "")
            return stem
    except OSError:
        pass
    return ""


def detect_codex_info() -> dict:
    cfg = Path.home() / ".codex" / "config.toml"
    if not cfg.is_file():
        return {"model": "gpt-6-astra", "effort": "max", "status_line": []}
    try:
        data = tomllib.loads(cfg.read_text(encoding="utf-8", errors="replace"))
        tui = data.get("tui", {})
        return {
            "model": data.get("model", "gpt-6-astra"),
            "effort": data.get("model_reasoning_effort", "max"),
            "status_line": tui.get("status_line", [
                "model-with-reasoning", "context-used", "task-progress", "five-hour-limit", "weekly-limit"
            ]),
            "use_colors": tui.get("status_line_use_colors", True),
        }
    except Exception:
        return {"model": "gpt-6-astra", "effort": "max", "status_line": []}


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def format_time(ts: float | int | None) -> str:
    if not ts:
        return "—"
    try:
        diff = int(time.time() - ts)
        if diff < 60:
            return f"{diff}s ago"
        if diff < 3600:
            return f"{diff // 60}m ago"
        if diff < 86400:
            return f"{diff // 3600}h ago"
        return time.strftime("%b %d %H:%M", time.localtime(ts))
    except Exception:
        return "—"


def first_doc(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
            if tree and ast.get_docstring(tree):
                return collapse(ast.get_docstring(tree).splitlines()[0])
        except SyntaxError:
            pass
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#!") or s.startswith("set "):
            continue
        if s.startswith("#"):
            return collapse(s.lstrip("#").strip())
        break
    return ""


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def pct_span(pct: int, label: str) -> str:
    cls = "term-green" if pct < 50 else ("term-yellow" if pct < 80 else "term-red")
    return f'<span class="term-dim">{label} </span><span class="{cls}">{pct}%</span>'


def render_preview_claude(model: str, effort: str, acct: str, ctx: int, h5: int, wk: int) -> str:
    return (
        f'<span class="term-model">{esc(model)}</span>'
        f'<span class="term-sep"> · </span><span class="term-dim">{esc(effort)}</span>'
        f'<span class="term-sep"> · </span><span class="term-tag">{esc(acct)}</span>'
        f'<span class="term-sep"> · </span>{pct_span(ctx, "ctx")}'
        f'<span class="term-sep"> · </span>{pct_span(h5, "5h")}'
        f'<span class="term-sep"> · </span>{pct_span(wk, "wk")}'
    )


def render_preview_occ(model: str, tag: str, acct: str, ctx: int, h5: int, wk: int, mo: int) -> str:
    return (
        f'<span class="term-model">{esc(model)}</span>'
        f'<span class="term-sep"> · </span><span class="term-dim">{esc(tag)}</span>'
        f'<span class="term-sep"> · </span><span class="term-tag">{esc(acct)}</span>'
        f'<span class="term-sep"> · </span>{pct_span(ctx, "ctx")}'
        f'<span class="term-sep"> · </span>{pct_span(h5, "5h")}'
        f'<span class="term-sep"> · </span>{pct_span(wk, "wk")}'
        f'<span class="term-sep"> · </span>{pct_span(mo, "mo")}'
    )


def render_preview_cursor(model: str, effort: str, acct: str, ctx: int, auto: int, api: int) -> str:
    return (
        f'<span class="term-model">{esc(model)}</span>'
        f'<span class="term-sep"> · </span><span class="term-dim">{esc(effort)}</span>'
        f'<span class="term-sep"> · </span><span class="term-tag">{esc(acct)}</span>'
        f'<span class="term-sep"> · </span>{pct_span(ctx, "ctx")}'
        f'<span class="term-sep"> · </span>{pct_span(auto, "auto")}'
        f'<span class="term-sep"> · </span>{pct_span(api, "api")}'
    )


def render_preview_codex(model: str, effort: str, ctx: int, h5: int, wk: int = 18) -> str:
    effort_part = f" ({esc(effort)})" if effort else ""
    return (
        f'<span class="term-model">{esc(model)}{effort_part}</span>'
        f'<span class="term-sep"> · </span>{pct_span(ctx, "context")}'
        f'<span class="term-sep"> · </span><span class="term-dim">[1/3]</span>'
        f'<span class="term-sep"> · </span>{pct_span(h5, "5h")}'
        f'<span class="term-sep"> · </span>{pct_span(wk, "wk")}'
    )


def render(data: dict) -> str:
    counts = data["counts"]
    metrics = "".join(
        f'<div class="metric-card"><span class="metric-label">{esc(k)}</span>'
        f'<span class="metric-num">{esc(n)}</span></div>'
        for k, n in counts.items())

    previews_html = "".join(
        f"""
        <div class="terminal-box" data-statusline-item>
          <div class="terminal-box-header">
            <span class="terminal-title">{esc(l["name"])} <span class="meta-tag">{esc(l["mode"])}</span></span>
            <span class="meta-tag">{esc(l["dest"])}</span>
          </div>
          <div class="terminal-line">{l["preview_html"]}</div>
        </div>
        """
        for l in data["lines"])

    lines_rows = "".join(
        f"""<tr>
          <td class="wrap"><strong>{esc(l["name"])}</strong></td>
          <td class="wrap"><span class="meta-tag">{esc(l["mode"])}</span></td>
          <td class="wrap"><code>{esc(l["format"])}</code></td>
          <td class="wrap"><span class="meta-tag">{esc(l["wiring"])}</span></td>
          <td class="wrap">{esc(l["summary"])}</td>
        </tr>"""
        for l in data["lines"])

    files_rows = "".join(
        f"""<tr>
          <td class="wrap">{copy_btn(f["name"])}</td>
          <td class="wrap"><span class="meta-tag">{esc(f["source"])}</span></td>
          <td class="wrap"><span class="meta-tag">{esc(f["dest"])}</span></td>
          <td class="wrap">{installed_badge(f["installed"])}</td>
          <td class="wrap">{esc(f["runs"])}</td>
        </tr>"""
        for f in data["files"])

    caches_section = ""
    if data["caches"]:
        cache_rows = "".join(
            f"""<tr>
              <td class="wrap"><strong>{esc(c["target"])}</strong></td>
              <td class="wrap"><span class="meta-tag">{esc(c["file"])}</span></td>
              <td class="wrap"><span class="meta-tag">{esc(c["source"])}</span></td>
              <td class="wrap"><code>{esc(c["data"])}</code></td>
              <td class="wrap">{esc(c["updated"])}</td>
            </tr>"""
            for c in data["caches"])
        caches_section = f"""
        <section data-statusline-section>
          <div class="section-heading">
            <h2>Live Usage Caches &amp; Configs</h2>
            <span class="section-note">Local cache files and active TUI statusline configurations</span>
          </div>
          <div class="table-container" role="region" aria-label="Live Usage Caches & Configs">
            <table class="data-table">
              <caption class="sr-only">Live Usage Caches & Configs</caption>
              <thead><tr>
                <th scope="col">Target</th>
                <th scope="col">Cache / Config File</th>
                <th scope="col">Source</th>
                <th scope="col">Latest Quotas / Segments</th>
                <th scope="col">Updated</th>
              </tr></thead>
              <tbody>{cache_rows}</tbody>
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
<title>statusline</title>
<link rel="stylesheet" href="/assets/app.css">
<script>
(function () {{
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
  .statusline-toolbar {{
    display: flex; flex-wrap: wrap; align-items: flex-end;
    justify-content: space-between; gap: 12px 24px;
  }}
  .statusline-search {{ max-width: 320px; margin: 0; }}
  .statusline-board {{
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 20px 24px;
    align-items: start;
  }}
  .terminal-box {{
    background: var(--color-bg-alt, #0d1117);
    border: 1px solid var(--color-border, #30363d);
    border-radius: var(--radius-md, 6px);
    padding: 14px 16px;
    margin-bottom: 12px;
    font-family: var(--font-mono, monospace);
    font-size: 13px;
    line-height: 1.5;
    overflow-x: auto;
  }}
  .terminal-box-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    font-family: var(--font-sans, system-ui);
    font-size: 12px;
    color: var(--color-text-muted, #8b949e);
  }}
  .terminal-title {{ font-weight: 600; color: var(--color-text, #c9d1d9); }}
  .terminal-line {{
    padding: 8px 12px;
    background: var(--color-bg-elevated, #161b22);
    border: 1px solid var(--color-border, #21262d);
    border-radius: 4px;
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .term-model {{ font-weight: 600; color: #f0f6fc; }}
  .term-sep {{ color: #484f58; margin: 0 3px; }}
  .term-dim {{ color: #8b949e; }}
  .term-tag {{ color: #58a6ff; }}
  .term-green {{ color: #3fb950; font-weight: 500; }}
  .term-yellow {{ color: #d29922; font-weight: 500; }}
  .term-red {{ color: #f85149; font-weight: 500; }}
  .badge-installed {{
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    background: rgba(63, 185, 80, 0.15);
    color: #3fb950;
    border: 1px solid rgba(63, 185, 80, 0.4);
  }}
  .badge-missing {{
    display: inline-block;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    background: rgba(248, 81, 73, 0.15);
    color: #f85149;
    border: 1px solid rgba(248, 81, 73, 0.4);
  }}
  .statusline-board .table-container {{ width: 100%; }}
  .statusline-board .data-table {{ width: 100%; }}
  .statusline-board .data-table td.wrap {{ white-space: normal; }}
</style>
</head>
<body>
<div class="dashboard-layout">
  <header class="dashboard-heading">
    <div class="dashboard-title">
      <h1>Statusline</h1>
    </div>
    <div class="dashboard-context">Terminal status lines and usage fetchers for Claude Code, Cursor Agent, and Codex CLI</div>
  </header>
  <div class="statusline-toolbar">
    <div class="metric-grid overview-metrics">{metrics}</div>
    <div class="form-group statusline-search">
      <label class="form-label" for="statusline-filter">Filter statusline</label>
      <div class="input-with-action">
        <svg class="icon input-leading-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="m21 21-4.34-4.34" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"></path>
          <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" stroke-width="2"></circle>
        </svg>
        <input class="input-field" type="search" id="statusline-filter" autocomplete="off">
      </div>
    </div>
  </div>
  <div class="statusline-board">
    <section data-statusline-section>
      <div class="section-heading">
        <h2>Live Terminal Previews</h2>
        <span class="section-note">Visual mockups of statusline outputs rendered with ANSI 256-color palettes</span>
      </div>
      <div>{previews_html}</div>
    </section>

    <section data-statusline-section>
      <div class="section-heading">
        <h2>Configurations &amp; Formats</h2>
        <span class="section-note">Line formats, segments, wiring, and destination paths</span>
      </div>
      <div class="table-container" role="region" aria-label="Configurations & Formats">
        <table class="data-table">
          <caption class="sr-only">Configurations & Formats</caption>
          <thead><tr>
            <th scope="col">Line</th>
            <th scope="col">Mode</th>
            <th scope="col">Format Segments</th>
            <th scope="col">Wiring</th>
            <th scope="col">Description</th>
          </tr></thead>
          <tbody>{lines_rows}</tbody>
        </table>
      </div>
    </section>

    <section data-statusline-section>
      <div class="section-heading">
        <h2>Files &amp; Scripts</h2>
        <span class="section-note">Source files in repo statusline/ and their installed paths</span>
      </div>
      <div class="table-container" role="region" aria-label="Files & Scripts">
        <table class="data-table">
          <caption class="sr-only">Files & Scripts</caption>
          <thead><tr>
            <th scope="col">File</th>
            <th scope="col">Source</th>
            <th scope="col">Destination</th>
            <th scope="col">Status</th>
            <th scope="col">What it does</th>
          </tr></thead>
          <tbody>{files_rows}</tbody>
        </table>
      </div>
    </section>

    {caches_section}
  </div>
</div>
<script>
(function () {{
  var input = document.getElementById("statusline-filter");
  input.addEventListener("input", function () {{
    var q = input.value.toLowerCase();
    document.querySelectorAll("[data-statusline-section]").forEach(function (sec) {{
      var shown = 0;
      sec.querySelectorAll("tbody tr, [data-statusline-item]").forEach(function (el) {{
        var hit = !q || el.textContent.toLowerCase().indexOf(q) !== -1;
        el.style.display = hit ? "" : "none";
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
    }});
  }});
}})();
</script>
</body>
</html>
"""


def installed_badge(installed: bool) -> str:
    if installed:
        return '<span class="badge-installed">installed</span>'
    return '<span class="badge-missing">not installed</span>'


def copy_btn(name: str) -> str:
    return (f'<button type="button" class="meta-tag meta-tag-copy" '
            f'data-copy="{esc(name)}" aria-label="Copy {esc(name)}">'
            f'{esc(name)}</button>')


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)
