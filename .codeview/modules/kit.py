"""Codeview tab: live inventory of this repo's launchers, skills, scripts, bins."""
from __future__ import annotations

import ast
import html
import re
from pathlib import Path

NAME = "kit"
DESCRIPTION = "Launchers, skills, scripts, and bins this repo installs"

REPO = Path(__file__).resolve().parents[2]
FUNC_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_-]*)\(\)\s*\{(.*)$", re.M)
FLAG_RE = re.compile(r"--[A-Za-z0-9-]+(?:=[^\s\"']+)?")
SKIP_FLAGS = {"--arg", "--slurpfile", "--argjson"}
ROTATE_FLAGS = "--list --save NAME --unhook --kickoff [names]"


def register(reg):
    def page(_req):
        return (200, render(scan(REPO)), "text/html; charset=utf-8")
    reg.add_route("GET", "", page)


def scan(repo: Path) -> dict:
    launchers = scan_launchers(repo / "shell")
    bins = scan_bins(repo / "bin")
    skills = scan_skills(repo / "skills")
    scripts = scan_scripts(repo / "scripts")
    configs = scan_configs(repo)
    return {
        "launchers": launchers,
        "bins": bins,
        "skills": skills,
        "scripts": scripts,
        "configs": configs,
        "counts": {
            "Launch": sum(1 for r in launchers if r["kind"] == "launch"),
            "Rotate": sum(1 for r in launchers if r["kind"] == "rotate"),
            "PATH bins": len(bins),
            "Skills": len(skills),
            "Scripts": len(scripts),
            "Configs": len(configs),
        },
    }


def scan_launchers(shell_dir: Path) -> list[dict]:
    rows = []
    if not shell_dir.is_dir():
        return rows
    for path in sorted(shell_dir.glob("*.sh")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in FUNC_RE.finditer(text):
            name, rest = match.group(1), match.group(2)
            if name.startswith("_"):
                continue
            body = rest
            if "}" not in rest:
                start = match.end()
                depth = 1
                i = start
                while i < len(text) and depth:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                body = text[start:i - 1]
            else:
                body = rest[:rest.find("}")]
            kind = "rotate" if name.startswith("rotate-") else "launch"
            rows.append({
                "name": name,
                "source": f"shell/{path.name}",
                "runs": summarize_fn(body),
                "kind": kind,
                "flags": extract_flags(body, kind),
            })
    return rows


def extract_flags(body: str, kind: str) -> str:
    if kind == "rotate":
        return ROTATE_FLAGS
    chunks = re.findall(
        r"(?:_cf_tmux|_cl_cmd\+?=|_occ_cmd\+?=|nohup)\s*\(?([^)\n]*)",
        body)
    text = " ".join(chunks) if chunks else body
    flags: list[str] = []
    seen: set[str] = set()

    def add(flag: str) -> None:
        flag = flag.replace('"', "").replace("'", "")
        key = flag.split("=", 1)[0].split()[0]
        if (key in SKIP_FLAGS or key in seen or key == "--"
                or "*" in flag or ")" in flag):
            return
        seen.add(key)
        flags.append(flag)

    tokens = re.findall(r"\"[^\"]*\"|'[^']*'|\S+", text)
    i = 0
    while i < len(tokens):
        tok = tokens[i].strip("\"'")
        if tok.startswith("--") and tok != "--":
            if "=" not in tok and i + 1 < len(tokens):
                nxt = tokens[i + 1].strip("\"'")
                m = re.search(r":-([^}]+)\}", nxt)
                val = (m.group(1) if m else nxt).rstrip(");")
                if (not val.startswith("-") and val not in ("$@", ";;")
                        and re.match(r"^[A-Za-z0-9._${}/:-]+$", val)):
                    add(f"{tok} {val}")
                    i += 2
                    continue
            add(tok)
        i += 1
    for alt in re.findall(
            r"--[A-Za-z0-9-]+(?:\|--[A-Za-z0-9-]+)+", body):
        for part in alt.split("|"):
            add(part)
    return " ".join(flags)


def summarize_fn(body: str) -> str:
    hits = re.findall(r"_cf_tmux\s+(.+?)(?:\s+\"\$@\"|;|\n|$)", body)
    if hits:
        picked = next((h for h in hits if "--resume" in h or "--yolo" in h),
                      hits[-1])
        picked = picked.replace('"$bin"', "cmd").replace("$bin", "cmd")
        return collapse(picked)
    m = re.search(r"rotate_auth\.py\"?\s+(\S+)", body)
    if m:
        return f"rotate_auth.py {m.group(1).strip('\"')}"
    m = re.search(r"_cl_cmd=\(([^)]+)\)", body)
    if m:
        return collapse(m.group(1))
    if "OPENCODE_GO_BASE_URL" in body or "opencode.ai/zen/go" in body:
        return "claude via OpenCode Go"
    m = re.search(r"nohup\s+(\S+)", body)
    if m:
        return collapse(m.group(0) + " …")
    lines = [ln.strip() for ln in body.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    return collapse(lines[0]) if lines else ""


def scan_bins(bin_dir: Path) -> list[dict]:
    rows = []
    if not bin_dir.is_dir():
        return rows
    for path in sorted(bin_dir.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        rows.append({
            "name": path.name,
            "source": f"bin/{path.name}",
            "runs": first_doc(path),
            "flags": flags_from_argparse(path),
        })
    return rows


def scan_skills(skills_dir: Path) -> list[dict]:
    rows = []
    if not skills_dir.is_dir():
        return rows
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name, desc = skill_frontmatter(skill_md)
        rows.append({
            "name": name,
            "source": f"skills/{skill_md.parent.name}/",
            "runs": desc,
        })
    return rows


def skill_frontmatter(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = path.parent.name
    desc = ""
    if not text.startswith("---"):
        return name, first_heading(text)
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
    return name, first_sentence(desc)


def scan_scripts(scripts_dir: Path) -> list[dict]:
    rows = []
    if not scripts_dir.is_dir():
        return rows
    installer = scripts_dir.parent / "install-clusterfork.sh"
    if installer.is_file():
        rows.append({
            "name": installer.name,
            "source": installer.name,
            "runs": first_doc(installer) or "install clusterfork onto this machine",
            "flags": flags_from_argparse(installer),
        })
    for path in sorted(scripts_dir.glob("*.py")):
        rows.append({
            "name": path.name,
            "source": f"scripts/{path.name}",
            "runs": first_doc(path),
            "flags": flags_from_argparse(path),
        })
    codeview = scripts_dir / "codeview"
    if codeview.is_dir():
        rows.append({
            "name": "codeview/",
            "source": "scripts/codeview/",
            "runs": first_doc(codeview / "server.py") or "repo dashboard server",
        })
    return rows


def scan_configs(repo: Path) -> list[dict]:
    rows = []
    agents = repo / "agents"
    if agents.is_dir():
        for path in sorted(agents.iterdir()):
            if path.name.startswith("."):
                continue
            if path.is_file():
                rows.append({
                    "name": path.name,
                    "source": f"agents/{path.name}",
                    "runs": "agent settings",
                })
            elif path.is_dir():
                n = sum(1 for p in path.rglob("*") if p.is_file())
                rows.append({
                    "name": path.name + "/",
                    "source": f"agents/{path.name}/",
                    "runs": f"{n} plugin files",
                })
    for rel in ("statusline/claude/statusline.sh",
                "statusline/cursor/statusline.sh",
                "notify/compose.yaml",
                "tmux.conf",
                "bash_profile.sh"):
        path = repo / rel
        if path.is_file():
            rows.append({
                "name": path.name,
                "source": rel,
                "runs": first_doc(path) or rel,
            })
    return rows


def first_doc(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if path.suffix == ".py" or text.startswith("#!/usr/bin/env python"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        if tree and ast.get_docstring(tree):
            return collapse(ast.get_docstring(tree).splitlines()[0])
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#!") or s.startswith("set ") or s.startswith("\"\"\""):
            continue
        if s.startswith("#"):
            return collapse(s.lstrip("#").strip())
        break
    return ""


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


def flags_from_argparse(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    flags: list[str] = []
    seen: set[str] = set()
    for flag in re.findall(r'add_argument\(\s*["\'](--[A-Za-z0-9-]+)', text):
        if flag not in seen:
            seen.add(flag)
            flags.append(flag)
    return " ".join(flags)


def render(data: dict) -> str:
    counts = data["counts"]
    metrics = "".join(
        f'<div class="metric-card"><span class="metric-label">{esc(k)}</span>'
        f'<span class="metric-num">{n:,}</span></div>'
        for k, n in counts.items())
    launch = [r for r in data["launchers"] if r["kind"] == "launch"]
    rotate = [r for r in data["launchers"] if r["kind"] == "rotate"]
    sections = [
        section("Launch", "agent wrappers sourced on startup",
                launch, "Command"),
        section("Rotate", "switch saved accounts",
                rotate, "Command"),
        section("PATH bins", "bin/ helpers on PATH after install",
                data["bins"], "Command"),
        section("Scripts", "install + scripts/*.py",
                data["scripts"], "Script"),
        section("Skills", "skills/*/SKILL.md — slash commands",
                data["skills"], "Skill", wide=True),
        section("Configs", "agent settings, statusline, notify, tmux",
                data["configs"], "File", wide=True),
    ]
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark light">
<title>kit</title>
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
  .kit-toolbar {{
    display: flex; flex-wrap: wrap; align-items: flex-end;
    justify-content: space-between; gap: 12px 24px;
  }}
  .kit-search {{ max-width: 320px; margin: 0; }}
  .kit-board {{
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px 24px;
    align-items: start;
  }}
  .kit-board [data-kit-wide] {{ grid-column: 1 / -1; }}
  .kit-board .table-container {{ width: 100%; }}
  .kit-board .data-table {{ width: 100%; }}
  .kit-board .data-table td.wrap {{ white-space: normal; }}
  @media (max-width: 900px) {{
    .kit-board {{ grid-template-columns: minmax(0, 1fr); }}
  }}
</style>
</head>
<body>
<div class="dashboard-layout">
  <header class="dashboard-heading">
    <div class="dashboard-title">
      <h1>Kit</h1>
    </div>
    <div class="dashboard-context">Scanned from this repo on each load</div>
  </header>
  <div class="kit-toolbar">
    <div class="metric-grid overview-metrics">{metrics}</div>
    <div class="form-group kit-search">
      <label class="form-label" for="kit-filter">Filter kit</label>
      <div class="input-with-action">
        <svg class="icon input-leading-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="m21 21-4.34-4.34" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"></path>
          <circle cx="11" cy="11" r="8" fill="none" stroke="currentColor" stroke-width="2"></circle>
        </svg>
        <input class="input-field" type="search" id="kit-filter" autocomplete="off">
      </div>
    </div>
  </div>
  <div class="kit-board">{"".join(sections)}</div>
</div>
<div class="tooltip-bubble" id="kit-tip" role="tooltip" hidden></div>
<script>
(function () {{
  var input = document.getElementById("kit-filter");
  input.addEventListener("input", function () {{
    var q = input.value.toLowerCase();
    document.querySelectorAll("[data-kit-section]").forEach(function (sec) {{
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
    navigator.clipboard.writeText(btn.dataset.copy);
  }});

  var tip = document.getElementById("kit-tip");
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
    var flags = el.getAttribute("data-flags");
    if (!flags) return;
    if (tipOwner === el) {{
      if (!tip.hidden) placeTip(el, clientX);
      return;
    }}
    hideTip();
    tip.textContent = flags;
    tipOwner = el;
    tipTimer = window.setTimeout(function () {{
      tip.hidden = false;
      el.setAttribute("aria-describedby", "kit-tip");
      placeTip(el, clientX);
    }}, 150);
  }}
  document.addEventListener("pointerover", function (e) {{
    var row = e.target.closest("tr[data-flags]");
    if (row) showTip(row, e.clientX);
  }});
  document.addEventListener("pointerout", function (e) {{
    var row = e.target.closest("tr[data-flags]");
    if (!row) return;
    if (e.relatedTarget && row.contains(e.relatedTarget)) return;
    hideTip();
  }});
  document.addEventListener("focusin", function (e) {{
    var row = e.target.closest("tr[data-flags]");
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
        f'<tr{flag_attr(r.get("flags", ""))}><td class="wrap">{copy_btn(r["name"])}</td>'
        f'<td class="wrap"><span class="meta-tag">{esc(r["source"])}</span></td>'
        f'<td class="wrap">{esc(r["runs"])}</td></tr>'
        for r in rows)
    wide_attr = " data-kit-wide" if wide else ""
    return f"""
<section data-kit-section{wide_attr}>
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


def flag_attr(flags: str) -> str:
    return f' data-flags="{esc(flags)}"' if flags else ""


def copy_btn(name: str) -> str:
    return (f'<button type="button" class="meta-tag meta-tag-copy" '
            f'data-copy="{esc(name)}" aria-label="Copy {esc(name)}">'
            f'{esc(name)}</button>')


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)
