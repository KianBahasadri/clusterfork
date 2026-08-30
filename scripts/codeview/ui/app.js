/* codeview frontend: fetches /api/summary + /api/section/*, renders core tabs.
   Module tabs are served at /m/<name>/ and framed on demand. */
"use strict";

const $ = (sel, root) => (root || document).querySelector(sel);
const fmt = n => n == null ? "—" : Number(n).toLocaleString("en-US");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-08-28T15:24:…" → "Aug 28 2026 15:24" (3-letter months everywhere)
function fmtDate(iso) {
  const m = String(iso || "").match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(iso || "—");
  return `${MONTHS[+m[2] - 1]} ${+m[3]} ${m[1]}`
    + (m[4] ? ` ${m[4]}:${m[5]}` : "");
}

// ---------------------------------------------------------------- charts --

const CHART_W = 1000, CHART_H = 240;
const CHART_PAD = { l: 46, r: 8, t: 8, b: 20 };

function chartScale(values) {
  const vmax = Math.max(1, ...values), vmin = Math.min(0, ...values);
  const iw = CHART_W - CHART_PAD.l - CHART_PAD.r;
  const ih = CHART_H - CHART_PAD.t - CHART_PAD.b;
  const n = values.length;
  return {
    vmax, vmin, iw, ih, n,
    x: i => CHART_PAD.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw),
    y: v => CHART_PAD.t + ih - ((v - vmin) / (vmax - vmin || 1)) * ih,
  };
}

function svgLineChart(series, labels, opts = {}) {
  // series: [{values:[...], color}], all same length. Simple SVG polyline
  // chart with area fill; no external chart lib needed.
  const vals = series.flatMap(s => s.values);
  const scale = chartScale(vals);
  const { vmax, vmin, ih, n, x, y } = scale;
  const w = CHART_W, h = CHART_H, pad = CHART_PAD;
  let out = `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
    role="img" aria-label="${esc(opts.ariaLabel || "line chart")}">`;
  for (let g = 0; g <= 4; g++) {
    const gy = pad.t + (ih * g) / 4;
    const val = Math.round(vmax - (ih * g) / 4 * (vmax - vmin) / ih);
    out += `<line x1="${pad.l}" y1="${gy}" x2="${w - pad.r}" y2="${gy}"
      stroke="rgba(163,156,147,.14)" stroke-width="1"></line>`;
    out += `<text x="${pad.l - 6}" y="${gy + 4}" fill="#a39c93"
      font-size="10" text-anchor="end">${fmt(val)}</text>`;
  }
  for (const s of series) {
    const pts = s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
    if (s.fill !== false) {
      out += `<polygon points="${x(0)},${y(vmin)} ${pts} ${x(n - 1)},${y(vmin)}"
        fill="${s.color}" opacity=".12"></polygon>`;
    }
    out += `<polyline points="${pts}" fill="none" stroke="${s.color}"
      stroke-width="2" stroke-linejoin="round"></polyline>`;
  }
  const tickEvery = Math.max(1, Math.ceil(n / 6));
  labels.forEach((lab, i) => {
    if (i % tickEvery !== 0) return;
    const s = String(lab);
    const short = MONTHS[+s.slice(5, 7) - 1]
      ? `${MONTHS[+s.slice(5, 7) - 1]} ${+s.slice(8, 10)}` : s;
    out += `<text x="${x(i)}" y="${h - 4}" fill="#a39c93" font-size="10"
      text-anchor="middle">${short}</text>`;
  });
  return out + "</svg>";
}

let chartHoverAbort = null;

function bindHistoryChartHover(wrap, commits) {
  if (!wrap) return;
  const svg = wrap.querySelector("svg.chart");
  const tip = wrap.querySelector(".chart-tip");
  const hair = wrap.querySelector(".chart-crosshair");
  const line = wrap.querySelector(".chart-crosshair-line");
  const dot = wrap.querySelector(".chart-crosshair-dot");
  if (!svg || !tip || !hair || !line || !dot || !commits.length) return;
  const scale = chartScale(commits.map(c => c.total));
  let last = -1;
  let pinned = -1;
  let hovering = false;

  chartHoverAbort?.abort();
  chartHoverAbort = new AbortController();
  const { signal } = chartHoverAbort;

  function cssPos(viewX, viewY) {
    const rect = svg.getBoundingClientRect();
    return {
      x: (viewX / CHART_W) * rect.width,
      y: (viewY / CHART_H) * rect.height,
    };
  }

  function indexFromEvent(e) {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return last < 0 ? 0 : last;
    const viewX = ((e.clientX - rect.left) / rect.width) * CHART_W;
    const n = commits.length;
    const t = n <= 1 ? 0 : (viewX - CHART_PAD.l) / scale.iw;
    return Math.max(0, Math.min(n - 1, Math.round(t * Math.max(n - 1, 1))));
  }

  function hide() {
    if (pinned >= 0) return;
    last = -1;
    hair.hidden = true;
    tip.hidden = true;
    tip.classList.remove("pinned");
    wrap.classList.remove("is-pinned");
  }

  function unpin() {
    const i = pinned;
    pinned = -1;
    tip.classList.remove("pinned");
    wrap.classList.remove("is-pinned");
    if (hovering && i >= 0) show(i);
    else hide();
  }

  function show(i) {
    const c = commits[i];
    const p = cssPos(scale.x(i), scale.y(c.total));
    const top = cssPos(0, CHART_PAD.t).y;
    const bot = cssPos(0, CHART_H - CHART_PAD.b).y;
    hair.hidden = false;
    line.style.left = `${p.x}px`;
    line.style.top = `${top}px`;
    line.style.height = `${bot - top}px`;
    dot.style.left = `${p.x}px`;
    dot.style.top = `${p.y}px`;
    if (i !== last) {
      last = i;
      tip.innerHTML = `
        <div class="chart-tip-row"><span class="k">date</span>
          <span class="v">${esc(fmtDate(c.date))}</span></div>
        <div class="chart-tip-row"><span class="k">sha</span>
          <span class="v">${esc(c.sha)}</span></div>
        <div class="chart-tip-row"><span class="k">loc</span>
          <span class="v">${fmt(c.total)}</span></div>`;
    }
    const isPinned = pinned >= 0;
    tip.classList.toggle("pinned", isPinned);
    wrap.classList.toggle("is-pinned", isPinned);
    tip.hidden = false;
    const bounds = wrap.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let left = p.x + 12;
    let topPx = p.y - th - 8;
    if (left + tw > bounds.width) left = p.x - tw - 12;
    if (left < 0) left = 0;
    if (topPx < 0) topPx = p.y + 12;
    if (topPx + th > bounds.height) topPx = Math.max(0, bounds.height - th);
    tip.style.left = `${left}px`;
    tip.style.top = `${topPx}px`;
  }

  wrap.addEventListener("mousemove", e => {
    hovering = true;
    if (pinned >= 0) return;
    show(indexFromEvent(e));
  }, { signal });
  wrap.addEventListener("mouseleave", () => {
    hovering = false;
    hide();
  }, { signal });
  wrap.addEventListener("click", e => {
    if (e.target.closest(".chart-tip")) return;
    const i = indexFromEvent(e);
    if (pinned === i) {
      unpin();
      return;
    }
    pinned = i;
    show(i);
  }, { signal });
  document.addEventListener("click", e => {
    if (pinned < 0 || wrap.contains(e.target)) return;
    unpin();
  }, { signal });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && pinned >= 0) unpin();
  }, { signal });
}

function barRows(entries, colorFn = () => "") {
  // entries: [[label, value], ...]
  const max = Math.max(1, ...entries.map(e => Math.abs(e[1])));
  return entries.map(([label, value]) => `
    <div class="bar-row">
      <span class="bar-label" title="${esc(label)}">${esc(label)}</span>
      <div class="bar-track"><div class="bar-fill ${colorFn(label)}"
        style="width:${Math.max(1, (Math.abs(value) / max) * 100)}%"></div></div>
      <span class="bar-value">${fmt(value)}</span>
    </div>`).join("");
}

function metric(label, value, sub = "", className = "") {
  return `<div class="metric ${className}"><div class="label">${label}</div>
    <div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function metricCluster(title, note, cards, className = "") {
  return `<section class="metric-cluster ${className}">
    <div class="cluster-head"><div>
      <h2>${esc(title)}</h2>
      <p>${esc(note)}</p>
    </div></div>
    <div class="metric-grid">${cards.join("")}</div>
  </section>`;
}

function pct(n) {
  return n == null ? "—" : `${Number(n).toFixed(1)}%`;
}

function decimal(n, digits = 1) {
  return n == null ? "—" : Number(n).toFixed(digits);
}

function yesNo(value) {
  return value ? "yes" : "no";
}

function statRows(rows) {
  return `<dl class="stat-list">${rows.map(([label, value, note]) => {
    const display = value == null || value === "" ? "—" : String(value);
    return `<div class="stat-row"><dt>${esc(label)}</dt><dd>${esc(display)}
      ${note ? `<small>${esc(note)}</small>` : ""}</dd></div>`;
  }).join("")}</dl>`;
}

function statGroup(title, body, note = "") {
  return `<details class="stats-group" open>
    <summary><span>${esc(title)}</span>${note ? `<small>${esc(note)}</small>` : ""}</summary>
    <div class="stats-group-body">${body}</div>
  </details>`;
}

function symbolList(items, empty = "none detected") {
  const values = (items || []).map(String);
  if (!values.length) return `<span class="stats-empty">${empty}</span>`;
  return `<div class="symbol-list">${values.map(item =>
    `<code>${esc(item)}</code>`).join("")}</div>`;
}

function complexityRating(metrics) {
  if (metrics.complexity_rating) return metrics.complexity_rating;
  const value = Number(metrics.cyclomatic_complexity || 1);
  return value <= 10 ? "low" : value <= 20 ? "moderate" :
    value <= 40 ? "high" : "very high";
}

function complexityClass(metrics) {
  return `complexity-${complexityRating(metrics).replace(/\s+/g, "-")}`;
}

// ----------------------------------------------------------------- state --

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

const state = { gen: null, tabs: [], active: null };

function showPanel(name) {
  state.active = name;
  document.querySelectorAll(".panel").forEach(p => {
    p.hidden = p.dataset.panel !== name;
  });
  document.querySelectorAll("#tabs button").forEach(b => {
    b.classList.toggle("active", b.dataset.tab === name);
    b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
  });
  if (!document.querySelector(`.panel[data-panel="${name}"] iframe`)
      && name.startsWith("m:")) {
    loadModuleTab(name);
  }
}

function loadModuleTab(name) {
  const slug = name.slice(2);
  const panel = document.querySelector(`.panel[data-panel="${name}"]`);
  panel.innerHTML =
    `<iframe class="module-frame" src="/m/${encodeURIComponent(slug)}/"
       sandbox="allow-scripts allow-forms"></iframe>`;
}

function renderTabs(tabs) {
  const nav = $("#tabs");
  nav.innerHTML = tabs.map(t => {
    const key = t.kind === "core" ? t.name : `m:${t.name}`;
    const label = t.kind === "broken" ? `⚠ ${t.name}` : t.name;
    return `<button role="tab" data-tab="${key}"
      title="${t.description || ""}">${label}</button>`;
  }).join("");
  if (!state.active || !tabs.some(t =>
      (t.kind === "core" ? t.name : `m:${t.name}`) === state.active)) {
    showPanel("overview");
  }
}

// -------------------------------------------------------------- sections --

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));
}

async function refresh() {
  const [summary, tabs] = await Promise.all(
    [api("/api/summary"), api("/api/tabs")]);
  if (JSON.stringify(tabs.tabs) !== JSON.stringify(state.tabs)) {
    state.tabs = tabs.tabs;
    renderTabs(state.tabs);
  }
  const m = summary.meta || {};
  $("#repo-name").textContent = m.repo_name || "?";
  document.title = m.repo_name ? `${m.repo_name} · codeview` : "codeview";
  $("#head-info").textContent =
    (m.branch || "(detached)") + (m.dirty ? " · dirty" : "");
  const ciEl = $("#ci-info");
  if (summary.ci) {
    ciEl.hidden = false;
    ciEl.className = summary.ci;
    ciEl.textContent =
      { passing: "ci ✓", failing: "ci ✗", running: "ci …" }[summary.ci]
      || "ci ?";
  } else {
    ciEl.hidden = true;
  }
  if (state.active === "overview" || !state.active) await renderOverview(summary);
  else if (state.active === "logs") await renderLogs();
}

async function renderOverview(s) {
  const panel = $('.panel[data-panel="overview"]');
  if (s.meta?.empty_repo) {
    panel.innerHTML =
      `<h2>empty repository</h2><p>No commits yet — make a commit and the
        dashboard will pick it up automatically.</p>`;
    return;
  }
  const mt = s.metric_totals || {};
  const langEntries = Object.entries(s.langs || {})
    .sort((a, b) => b[1].lines - a[1].lines);
  const topEntries = Object.entries(s.tops || {})
    .sort((a, b) => b[1].lines - a[1].lines);
  const langs = langEntries.slice(0, 8);
  const tops = topEntries.slice(0, 8).map(([k, v]) => [k === "." ? "(root)" : k, v.lines]);
  const langCount = langEntries.length;
  const dirCount = topEntries.length;
  const ecoCount = (s.ecosystems || []).length;
  const totalLangsLabel = langCount ? `${fmt(langCount)} language${langCount === 1 ? "" : "s"}` : "no languages detected";
  const totalDirsLabel = dirCount ? `${fmt(dirCount)} top-level dir${dirCount === 1 ? "" : "s"}` : "no top-level dirs";
  const hasTops = tops.length > 0;
  const hasLangs = langs.length > 0;
  const maintainability = mt.maintainability_index == null
    ? "—" : `${decimal(mt.maintainability_index)} / 100`;
  const codePct = mt.code_lines != null && s.total_lines
    ? pct((mt.code_lines / s.total_lines) * 100) : "—";
  panel.innerHTML = `
    <div class="metric-layout">
      ${metricCluster("at a glance",
        "Primary scale of the tracked codebase.", [
          metric("source lines", fmt(mt.code_lines ?? s.total_lines),
                 `${codePct} of physical · nonblank, noncomment`, "metric-featured"),
          metric("tracked files", fmt(s.total_files),
                 totalLangsLabel),
          metric("languages", fmt(langCount),
                 hasLangs ? esc(langs[0][0]) + " · most lines" : "—"),
          metric("functions", fmt(mt.functions),
                 fmt(mt.classes) + " classes"),
        ], "cluster-footprint")}
    </div>
    <div class="overview-breakdown">
      ${metricCluster("composition",
        "How physical lines break down.", [
          metric("blank lines", fmt(mt.blank_lines), pct(mt.blank_ratio)),
          metric("comment lines", fmt(mt.comment_lines), pct(mt.comment_ratio)),
          metric("characters", fmt(mt.characters),
                 fmt(mt.words) + " words"),
          metric("attention markers", fmt(mt.todo_count),
                 mt.todo_count ? "TODO / FIXME / HACK" : "no markers"),
        ])}
      ${metricCluster("health & activity",
        "Quality signals and scan scope.", [
          metric("maintainability", maintainability,
                 mt.maintainability_index == null ? "not enough data" : "0–100 · higher is better"),
          metric("complexity", fmt(mt.cyclomatic_complexity),
                 fmt(mt.decision_points) + " decision points"),
          metric("commits scanned", fmt(s.commits_count),
                 fmt((s.dirs || []).length) + " dirs in history"),
          metric("deps · modules", `${fmt(ecoCount)} · ${fmt(s.modules.length)}`,
                 ecoCount ? esc(s.ecosystems.map(e => e.name).join(" · ")) : "no manifests"),
        ])}
    </div>
    <div class="overview-breakdown">
      <section class="breakdown-panel">
        <h2>lines by top-level dir</h2>
        ${hasTops
          ? `<div id="ov-tops">${barRows(tops)}</div>
             <p class="stats-note">${esc(totalDirsLabel)} · top ${tops.length} shown</p>`
          : `<p class="stats-empty">No top-level directories to show.</p>`}
      </section>
      <section class="breakdown-panel">
        <h2>lines by language</h2>
        ${hasLangs
          ? `<div id="ov-langs">${barRows(langs.map(([k, v]) => [k, v.lines]))}</div>
             <p class="stats-note">${esc(totalLangsLabel)} · top ${langs.length} shown</p>`
          : `<p class="stats-empty">No languages detected.</p>`}
      </section>
    </div>
    <p class="scan-stamp">last scanned ${esc(fmtDate(s.meta.scanned_at_iso))} · ${fmt(s.total_lines)} physical lines</p>`;
}

async function renderHistory() {
  const hist = await api("/api/section/history");
  const panel = $('.panel[data-panel="history"]');
  const commits = hist.commits || [];
  if (!commits.length) {
    panel.innerHTML = "<p>No commit history available.</p>";
    return;
  }
  const totalSeries = commits.map(c => c.total);
  const dirSets = {};
  for (const c of commits.slice(-200)) {
    for (const [d, v] of Object.entries(c.dirs || {})) {
      dirSets[d] = (dirSets[d] || 0) + v;
    }
  }
  const hotDirs = Object.entries(dirSets)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6);
  panel.innerHTML = `
    <h2>cumulative lines over history</h2>
    <div class="chart-wrap">
      ${svgLineChart([{ values: totalSeries, color: "#0f4c98" }],
                     commits.map(c => c.date),
                     { ariaLabel: "Cumulative lines over commit history" })}
      <div class="chart-crosshair" hidden>
        <div class="chart-crosshair-line"></div>
        <div class="chart-crosshair-dot"></div>
      </div>
      <div class="chart-tip" hidden></div>
    </div>
    <h2>most-changed dirs (recent commits)</h2>
    ${barRows(hotDirs)}
    <h2>recent commits (${commits.length} scanned)</h2>
    <table><thead><tr>
      <th>date</th><th>sha</th><th>subject</th><th class="num">delta</th>
      <th class="num">total</th>
    </tr></thead><tbody>
      ${commits.slice().reverse().slice(0, 100).map(c => `
        <tr>
          <td>${esc(fmtDate(c.date))}</td>
          <td class="commit-sha">${esc(c.sha)}</td>
          <td class="commit-subject">${esc(c.subject)}</td>
          <td class="num ${c.delta >= 0 ? "delta-pos" : "delta-neg"}">
            ${c.delta >= 0 ? "+" : ""}${fmt(c.delta)}</td>
          <td class="num">${fmt(c.total)}</td>
        </tr>`).join("")}
    </tbody></table>`;
  bindHistoryChartHover(panel.querySelector(".chart-wrap"), commits);
}

async function renderFiles() {
  const filesData = await api("/api/section/files");
  const panel = $('.panel[data-panel="files"]');
  const files = filesData.files || [];
  const byLines = files.slice().sort((a, b) =>
    (b.lines ?? -1) - (a.lines ?? -1));
  const mt = filesData.metric_totals || {};
  panel.innerHTML = `
    <div class="metric-layout files-summary">
      ${metricCluster("file index",
        "Every tracked file currently included in the index.", [
          metric("tracked files", fmt(filesData.total_files),
                 "after exclusions", "metric-featured"),
          metric("physical lines", fmt(filesData.total_lines)),
          metric("source lines", fmt(mt.code_lines)),
          metric("files analyzed", fmt(mt.files_analyzed)),
        ])}
      ${metricCluster("source makeup",
        "Line composition across analyzable files.", [
          metric("blank lines", fmt(mt.blank_lines), pct(mt.blank_ratio)),
          metric("comment lines", fmt(mt.comment_lines), pct(mt.comment_ratio)),
          metric("characters", fmt(mt.characters)),
          metric("attention markers", fmt(mt.todo_count)),
        ])}
      ${metricCluster("structure and quality",
        "Counts and static complexity signals across the index.", [
          metric("functions", fmt(mt.functions)),
          metric("classes", fmt(mt.classes)),
          metric("imports", fmt(mt.imports)),
          metric("decision points", fmt(mt.decision_points)),
        ])}
    </div>
    <div class="file-list-head">
      <input type="search" placeholder="filter paths, languages, metrics…" id="file-filter">
      <span class="file-list-count">${fmt(byLines.length)} files shown</span>
    </div>
    <div class="table-scroll"><table id="file-table"><thead><tr>
      <th>path</th><th>lang</th><th class="num">lines</th>
      <th class="num">code</th><th class="num">blank</th>
      <th class="num">comments</th><th class="num">funcs</th>
      <th class="num">imports</th><th class="num">complexity</th>
      <th class="num">bytes</th>
    </tr></thead><tbody>
      ${byLines.map(f => {
        const m = f.metrics || {};
        return `
          <tr class="file-row" data-path="${esc(f.path)}">
            <td>${esc(f.path)}</td><td>${esc(f.lang)}</td>
            <td class="num">${f.lines == null ? "—" : fmt(f.lines)}</td>
            <td class="num">${fmt(m.code_lines)}</td>
            <td class="num">${fmt(m.blank_lines)}</td>
            <td class="num">${fmt(m.comment_lines)}</td>
            <td class="num">${fmt(m.functions)}</td>
            <td class="num">${fmt(m.imports)}</td>
            <td class="num ${complexityClass(m)}">
              ${fmt(m.cyclomatic_complexity)}</td>
            <td class="num">${fmt(f.bytes)}</td></tr>`;
      }).join("")}
    </tbody></table></div>`;
  const input = $("#file-filter");
  input.addEventListener("input", () => {
    const q = input.value.toLowerCase();
    const rows = $("#file-table tbody").children;
    for (let i = 0; i < rows.length; i++) {
      rows[i].style.display = q && !rows[i].textContent.toLowerCase()
        .includes(q) ? "none" : "";
    }
  });
  panel.querySelectorAll("tr.file-row").forEach(tr => {
    tr.addEventListener("click", () =>
      openFile(tr.dataset.path).catch(console.error));
  });
}

// ------------------------------------------------------------ file view --

async function openFile(path) {
  const panel = $('.panel[data-panel="files"]');
  panel.innerHTML = `<div class="loading">loading ${esc(path)}…</div>`;
  const d = await api(`/api/file?path=${encodeURIComponent(path)}`);
  panel.innerHTML = fileView(d);
  $("#file-back").addEventListener("click", () =>
    renderFiles().catch(console.error));
}

function fileView(d) {
  const s = d.stats || {};
  const m = d.metrics || {};
  const h = m.halstead || {};
  const lc = s.last_commit;
  const rating = complexityRating(m);
  const fileName = String(d.path || "").split("/").pop();
  const markerSummary = Object.entries(m.todo_markers || {})
    .map(([name, count]) => `${name} ${fmt(count)}`).join(" · ") || "none";
  let code;
  if (d.binary) {
    code = `<p style="color:var(--dim)">binary file — content not shown</p>`;
  } else {
    const lines = (d.content ?? "").split("\n");
    code = `<pre>${lines.map((l, i) =>
      `<span class="ln">${i + 1}</span>${esc(l)}`).join("\n")}</pre>`;
  }
  return `
    <div class="file-head">
      <button id="file-back">← files</button>
      <span class="file-path">${esc(d.path)}</span>
      ${d.truncated ? `<span class="file-trunc">truncated</span>` : ""}
    </div>
    <div class="file-grid">
      <div class="file-code">${code}</div>
      <aside class="file-stats">
        <div class="stats-hero">
          <div class="stats-kicker">file profile</div>
          <div class="stats-title">${esc(fileName || "file")}</div>
          <div class="stats-sub">${esc(d.lang || "unknown")} · ${fmt(d.bytes)} bytes</div>
          <div class="stats-analysis">${esc(m.analysis || "metrics unavailable")}</div>
        </div>
        ${statGroup("source composition", statRows([
          ["physical lines", fmt(m.total_lines ?? d.total_lines)],
          ["source lines", fmt(m.code_lines)],
          ["blank lines", fmt(m.blank_lines), pct(m.blank_ratio)],
          ["comment lines", fmt(m.comment_lines), pct(m.comment_ratio)],
          ["comment-only", fmt(m.comment_only_lines)],
          ["inline comments", fmt(m.inline_comment_lines)],
          ["comment blocks", fmt(m.comment_blocks)],
          ["string lines", fmt(m.string_lines)],
        ]), "physical layout")}
        ${statGroup("structure", statRows([
          ["functions", fmt(m.functions)],
          ["classes", fmt(m.classes)],
          ["types", fmt(m.types)],
          ["imports", fmt(m.imports)],
          ["exports", fmt(m.exports)],
          ["declarations", fmt(m.declarations)],
          ["call sites", fmt(m.call_sites)],
          ["parameters", fmt(m.parameters)],
          ["lambdas", fmt(m.lambdas)],
          ["returns", fmt(m.returns)],
          ["raises / throws", fmt(m.raises)],
          ["async / await", `${fmt(m.async_keywords)} / ${fmt(m.await_keywords)}`],
        ]), "symbols and control flow")}
        ${statGroup("complexity", `
          <div class="complexity-summary">
            <span class="complexity-number">${fmt(m.cyclomatic_complexity)}</span>
            <span class="complexity-label">cyclomatic complexity</span>
            <span class="complexity-badge ${complexityClass(m)}">${esc(rating)}</span>
          </div>
          ${statRows([
            ["decision points", fmt(m.decision_points)],
            ["conditionals", fmt(m.conditionals)],
            ["loops", fmt(m.loops)],
            ["exception handlers", fmt(m.exception_handlers)],
            ["max nesting depth", fmt(m.max_nesting_depth)],
            ["max brace depth", fmt(m.max_brace_depth)],
            ["maintainability index", m.maintainability_index == null
              ? "—" : `${decimal(m.maintainability_index)} / 100`],
            ["Halstead vocabulary", fmt(h.vocabulary)],
            ["Halstead length", fmt(h.length)],
            ["Halstead volume", decimal(h.volume, 2)],
            ["Halstead difficulty", decimal(h.difficulty, 2)],
            ["Halstead effort", decimal(h.effort, 2)],
            ["estimated defects", decimal(h.estimated_bugs, 4)],
          ])}
          <p class="stats-note">Cyclomatic and Halstead values are static estimates; maintainability is a 0–100 heuristic.</p>`,
          "static quality signals")}
        ${statGroup("text and formatting", statRows([
          ["bytes", fmt(d.bytes)],
          ["characters", fmt(m.characters)],
          ["unicode characters", fmt(m.unicode_characters)],
          ["words", fmt(m.words)],
          ["tokens", fmt(m.tokens)],
          ["operators", `${fmt(m.operators)} (${fmt(m.unique_operators)} unique)`],
          ["operands", `${fmt(m.operands)} (${fmt(m.unique_operands)} unique)`],
          ["average line length", decimal(m.avg_line_length)],
          ["maximum line length", fmt(m.max_line_length)],
          ["trailing whitespace", fmt(m.trailing_whitespace_lines)],
          ["tab-indented lines", fmt(m.tab_indented_lines)],
          ["space-indented lines", fmt(m.space_indented_lines)],
          ["newline style", m.newline_style],
          ["final newline", yesNo(m.final_newline)],
        ]), "lexical footprint")}
        ${statGroup("attention signals", `
          ${statRows([
            ["TODO / FIXME / HACK", fmt(m.todo_count), markerSummary],
            ["parse status", m.parse_error ? "fallback heuristic" : "ok"],
          ])}
          ${m.parse_error ? `<p class="stats-warning">${esc(m.parse_error)}</p>` : ""}
          <div class="stats-subhead">markers</div>
          <div class="marker-list">${esc(markerSummary)}</div>`, "review prompts")}
        ${statGroup("symbol inventory", `
          <div class="stats-subhead">functions</div>
          ${symbolList(m.function_names)}
          <div class="stats-subhead">classes and types</div>
          ${symbolList(m.class_names)}
          <div class="stats-subhead">imports</div>
          ${symbolList(m.import_names)}
          <div class="stats-subhead">exports</div>
          ${symbolList(m.export_names)}`, "detected names, capped at 100")}
        ${statGroup("git history", `
          ${statRows([
            ["commits", fmt(s.commits)],
            ["lines added", fmt(s.added), "all history"],
            ["lines deleted", fmt(s.deleted), "all history"],
            ["first commit", s.first_commit_date ? fmtDate(s.first_commit_date) : "—"],
          ])}
          <div class="stats-subhead">last commit</div>
          ${lc ? `<div class="last-commit">
              <div class="commit-subject">${esc(lc.subject)}</div>
              <div class="commit-sha">${esc(lc.sha)} · ${esc(lc.author)}
                · ${esc(fmtDate(lc.date))}</div>
            </div>` : "<p class=\"stats-empty\">—</p>"}
          ${s.authors && s.authors.length ? `
            <div class="stats-subhead">top authors by commits</div>
            ${barRows(s.authors)}` : ""}
          ${s.blame && s.blame.length ? `
            <div class="stats-subhead">lines by author (blame)</div>
            ${barRows(s.blame)}` : ""}`, "history and ownership")}
      </aside>
    </div>`;
}

// ------------------------------------------------------------- logs tab --

async function renderLogs() {
  const data = await api("/api/logs");
  const panel = $('.panel[data-panel="logs"]');
  const lines = data.logs || [];
  panel.innerHTML = `
    <h2>server logs — ${fmt(lines.length)} lines (newest last)</h2>
    <pre class="log-view">${lines.length ? lines.map(esc).join("\n") : "—"}</pre>`;
  const view = panel.querySelector(".log-view");
  view.scrollTop = view.scrollHeight;
}

async function renderDeps() {
  const deps = await api("/api/section/deps");
  const panel = $('.panel[data-panel="deps"]');
  if (!(deps.ecosystems || []).length) {
    panel.innerHTML = "<p>No dependency manifests found in this repo.</p>";
    return;
  }
  panel.innerHTML = deps.ecosystems.map(eco => {
    const declared = eco.declared || [], locked = eco.locked || [];
    const lockedByRoot = {};
    for (const l of locked) {
      lockedByRoot[l.name] = lockedByRoot[l.name] || l.version;
    }
    return `
      <h2>${esc(eco.name)} — ${esc(eco.manifest)}${eco.lockfile
        ? ` (lock: ${esc(eco.lockfile)})` : " (no lockfile)"}</h2>
      <table><thead><tr>
        <th>dependency</th><th>kind</th><th>declared</th>
        <th>locked version</th>
      </tr></thead><tbody>
        ${declared.map(d => `
          <tr><td>${esc(d.name)}</td><td>${esc(d.kind)}</td>
            <td>${esc(d.req)}</td>
            <td>${lockedByRoot[d.name] ? esc(lockedByRoot[d.name])
                  : '<span style="color:#c19a56">not in lock</span>'}</td>
          </tr>`).join("") || "<tr><td colspan=4>—</td></tr>"}
      </tbody></table>
      ${locked.length > declared.length
        ? `<p style="color:var(--dim)">…and ${fmt(locked.length - declared.length)
           } more locked packages not directly declared.</p>`
        : ""}`;
  }).join("");
}

// ------------------------------------------------------------ generation --

async function pollGeneration() {
  try {
    const gen = await api("/api/gen");
    if (state.gen != null && gen.generation !== state.gen) {
      location.reload();  // soft reload when data or modules changed server-side
      return;
    }
    state.gen = gen.generation;
    $("#stale-banner").hidden = true;
  } catch {
    $("#stale-banner").hidden = false;  // connection refused mid-restart
  }
}

// ------------------------------------------------------------------ init --

(async function init() {
  try {
    await refresh();
  } catch (err) {
    $(".panel[data-panel='overview']").innerHTML =
      `<h2>backend error</h2><pre>${esc(String(err))}</pre>`;
  }
  showPanel("overview");
  setInterval(refresh, 15000);
  setInterval(pollGeneration, 4000);
  await pollGeneration();

  // Delegated click handling survives renderTabs() replacing nav content.
  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("#tabs button");
    if (!b) return;
    const key = b.dataset.tab;
    showPanel(key);
    if (key === "history") renderHistory().catch(console.error);
    else if (key === "files") renderFiles().catch(console.error);
    else if (key === "deps") renderDeps().catch(console.error);
    else if (key === "logs") renderLogs().catch(console.error);
  });
})();
