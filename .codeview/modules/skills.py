"""Codeview tab: live inventory of this repo's skills and slash commands."""
from __future__ import annotations

import html
import re
from pathlib import Path

NAME = "skills"
DESCRIPTION = "Skills and slash commands this repo installs"

REPO = Path(__file__).resolve().parents[2]


def register(reg):
    def page(_req):
        return (200, render(scan(REPO)), "text/html; charset=utf-8")
    reg.add_route("GET", "", page)


def scan(repo: Path) -> dict:
    skills = scan_skills(repo / "skills")
    ask_count = sum(1 for s in skills if s["name"].startswith("ask-"))
    workflow_count = len(skills) - ask_count
    return {
        "skills": skills,
        "counts": {
            "Skills": len(skills),
            "Delegation": ask_count,
            "Workflow & Design": workflow_count,
        },
    }


def scan_skills(skills_dir: Path) -> list[dict]:
    rows = []
    if not skills_dir.is_dir():
        return rows
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name, desc, full_desc = skill_frontmatter(skill_md)
        rows.append({
            "name": name,
            "source": f"skills/{skill_md.parent.name}/",
            "runs": desc,
            "full_desc": full_desc,
        })
    return rows


def skill_frontmatter(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.parent.name
    desc = ""
    if not text.startswith("---"):
        h = first_heading(text)
        return name, h, h
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else ""
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("description:"):
            rest = line.split(":", 1)[1].strip()
            if rest in (">", ">-", "|", "|-"):
                block = []
                i += 1
                while i < len(lines) and (not lines[i].strip()
                                          or lines[i][:1] in " \t"):
                    if lines[i].strip():
                        block.append(lines[i].strip())
                    i += 1
                desc = " ".join(block)
                continue
            desc = rest.strip("\"'")
        i += 1
    if not desc or desc in (">", "|"):
        desc = first_heading(text[end + 4:] if end != -1 else text)
    full = collapse(desc)
    return name, first_sentence(full), full


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return collapse(line[2:])
    return ""


def first_sentence(text: str) -> str:
    text = collapse(text)
    for sep in (". ", ".\n"):
        if sep in text:
            return text.split(sep, 1)[0].rstrip(".") + "."
    return text


def collapse(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def render(data: dict) -> str:
    counts = data["counts"]
    metrics = "".join(
        f'<div class="metric-card"><span class="metric-label">{esc(k)}</span>'
        f'<span class="metric-num">{n:,}</span></div>'
        for k, n in counts.items())
    sections = [
        section("Skills", "skills/*/SKILL.md — slash commands",
                data["skills"], "Skill", wide=True),
    ]
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>skills</title>
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
  .skills-toolbar {{
    display: flex; flex-wrap: wrap; align-items: flex-end;
    justify-content: space-between; gap: 12px 24px;
  }}
  .skills-search {{ max-width: 320px; margin: 0; }}
  .skills-board {{
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 16px 24px;
    align-items: start;
  }}
  .skills-board [data-skills-wide] {{ grid-column: 1 / -1; }}
  .skills-board .table-container {{ width: 100%; }}
  .skills-board .data-table {{ width: 100%; }}
  .skills-board .data-table td.wrap {{ white-space: normal; }}
</style>
</head>
<body>
<div class="dashboard-layout">
  <header class="dashboard-heading">
    <div class="dashboard-title">
      <h1>Skills</h1>
    </div>
    <div class="dashboard-context">skills/*/SKILL.md &mdash; slash commands scanned from this repo on each load</div>
  </header>
  <div class="skills-toolbar">
    <div class="metric-grid overview-metrics">{metrics}</div>
    <div class="form-group skills-search">
      <label class="form-label" for="skills-filter">Filter skills</label>
      <div class="input-with-action">
        <svg class="icon input-leading-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="m21 21-4.34-4.34" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"></path>
          <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" stroke-width="2"></circle>
        </svg>
        <input class="input-field" type="search" id="skills-filter" autocomplete="off">
      </div>
    </div>
  </div>
  <div class="skills-board">{"".join(sections)}</div>
</div>
<div class="tooltip-bubble" id="skills-tip" role="tooltip" hidden></div>
<script>
(function () {{
  var input = document.getElementById("skills-filter");
  input.addEventListener("input", function () {{
    var q = input.value.toLowerCase();
    document.querySelectorAll("[data-skills-section]").forEach(function (sec) {{
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
    }});
  }});

  var tip = document.getElementById("skills-tip");
  var tipTimer = 0;
  var tipOwner = null;
  function hideTip() {{
    window.clearTimeout(tipTimer);
    tip.hidden = true;
    if (tipOwner) tipOwner.removeAttribute("aria-describedby");
    tipOwner = null;
  }}
  function placeTip(el, clientX) {{
    var r = el.getBoundingClientRect();
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    var mid = clientX != null ? clientX : r.left + r.width / 2;
    var left = mid - tw / 2;
    var top = r.top - th - 8;
    left = Math.min(Math.max(12, left), window.innerWidth - tw - 12);
    if (top < 12) top = r.bottom + 8;
    tip.style.left = left + "px";
    tip.style.top = top + "px";
  }}
  function showTip(el, clientX) {{
    var text = el.getAttribute("data-tip");
    if (!text) return;
    if (tipOwner === el) {{
      if (!tip.hidden) placeTip(el, clientX);
      return;
    }}
    hideTip();
    tip.textContent = text;
    tipOwner = el;
    tipTimer = window.setTimeout(function () {{
      tip.hidden = false;
      el.setAttribute("aria-describedby", "skills-tip");
      placeTip(el, clientX);
    }}, 150);
  }}
  document.addEventListener("pointerover", function (e) {{
    var row = e.target.closest("tr[data-tip]");
    if (row) showTip(row, e.clientX);
  }});
  document.addEventListener("pointerout", function (e) {{
    var row = e.target.closest("tr[data-tip]");
    if (!row) return;
    if (e.relatedTarget && row.contains(e.relatedTarget)) return;
    hideTip();
  }});
  document.addEventListener("focusin", function (e) {{
    var row = e.target.closest("tr[data-tip]");
    if (row) showTip(row);
    else hideTip();
  }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape") hideTip();
  }});
  window.addEventListener("scroll", hideTip, true);
  window.addEventListener("resize", hideTip);
}})();
</script>
</body>
</html>
"""


def section(title: str, note: str, rows: list[dict], name_label: str,
            wide: bool = False) -> str:
    if not rows:
        return ""
    body = "".join(
        f'<tr{tip_attr(r)}><td class="wrap">{copy_btn(r["name"])}</td>'
        f'<td class="wrap"><span class="meta-tag">{esc(r["source"])}</span></td>'
        f'<td class="wrap">{esc(r["runs"])}</td></tr>'
        for r in rows)
    wide_attr = " data-skills-wide" if wide else ""
    return f"""
<section data-skills-section{wide_attr}>
  <div class="section-heading">
    <h2>{esc(title)}</h2>
    <span class="section-note">{esc(note)}</span>
  </div>
  <div class="table-container" role="region" aria-label="{esc(title)}">
    <table class="data-table">
      <caption class="sr-only">{esc(title)}</caption>
      <thead><tr>
        <th scope="col">{esc(name_label)}</th>
        <th scope="col">Source</th>
        <th scope="col">What it does</th>
      </tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</section>
"""


def tip_attr(r: dict) -> str:
    full = r.get("full_desc", "")
    runs = r.get("runs", "")
    if full and full != runs:
        return f' data-tip="{esc(full)}"'
    return ""


def copy_btn(name: str) -> str:
    return (f'<button type="button" class="meta-tag meta-tag-copy" '
            f'data-copy="{esc(name)}" aria-label="Copy {esc(name)}">'
            f'{esc(name)}</button>')


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)
