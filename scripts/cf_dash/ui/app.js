/* cf-dash frontend: fetches /api/summary + /api/section/*, renders core tabs.
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

function svgLineChart(series, labels, opts = {}) {
  // series: [{values:[...], color}], all same length. Simple SVG polyline
  // chart with area fill; no external chart lib needed.
  const w = 1000, h = 240, pad = { l: 46, r: 8, t: 8, b: 20 };
  const vals = series.flatMap(s => s.values);
  const vmax = Math.max(1, ...vals), vmin = Math.min(0, ...vals);
  const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
  const n = (series[0]?.values.length) || 0;
  const x = i => pad.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const y = v => pad.t + ih - ((v - vmin) / (vmax - vmin || 1)) * ih;
  let out = `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`;
  for (let g = 0; g <= 4; g++) {
    const gy = pad.t + (ih * g) / 4;
    const val = Math.round(vmax - (ih * g) / 4 * (vmax - vmin) / ih);
    out += `<line x1="${pad.l}" y1="${gy}" x2="${w - pad.r}" y2="${gy}"
      stroke="rgba(148,163,184,.14)" stroke-width="1"></line>`;
    out += `<text x="${pad.l - 6}" y="${gy + 4}" fill="#8b949e"
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
    out += `<text x="${x(i)}" y="${h - 4}" fill="#8b949e" font-size="10"
      text-anchor="middle">${short}</text>`;
  });
  return out + "</svg>";
}

function barRows(entries, colorFn = () => "") {
  // entries: [[label, value], ...]
  const max = Math.max(1, ...entries.map(e => Math.abs(e[1])));
  return entries.map(([label, value]) => `
    <div class="bar-row">
      <span class="bar-label" title="${label}">${label}</span>
      <div class="bar-track"><div class="bar-fill ${colorFn(label)}"
        style="width:${Math.max(1, (Math.abs(value) / max) * 100)}%"></div></div>
      <span class="bar-value">${fmt(value)}</span>
    </div>`).join("");
}

function metric(label, value, sub = "") {
  return `<div class="metric"><div class="label">${label}</div>
    <div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
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
  const langs = Object.entries(s.langs || {})
    .sort((a, b) => b[1].lines - a[1].lines).slice(0, 12);
  const tops = Object.entries(s.tops || {})
    .sort((a, b) => b[1].lines - a[1].lines)
    .map(([k, v]) => [k === "." ? "(root)" : k, v.lines]);
  panel.innerHTML = `
    <div class="metrics">
      ${metric("files", fmt(s.total_files))}
      ${metric("lines of code", fmt(s.total_lines))}
      ${metric("commits scanned", fmt(s.commits_count))}
      ${metric("top-level dirs", fmt((s.dirs || []).length))}
      ${metric("modules", s.modules.length)}
      ${metric("scanned at", fmtDate(s.meta.scanned_at_iso))}
    </div>
    <h2>lines by top-level dir</h2>
    <div id="ov-tops">${barRows(tops)}</div>
    <h2>lines by language</h2>
    <div id="ov-langs">${barRows(langs.map(([k, v]) => [k, v.lines]))}</div>`;
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
    ${svgLineChart([{ values: totalSeries, color: "#58a6ff" }],
                   commits.map(c => c.date))}
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
}

async function renderFiles() {
  const filesData = await api("/api/section/files");
  const panel = $('.panel[data-panel="files"]');
  const files = filesData.files || [];
  const byLines = files.filter(f => f.lines != null)
    .sort((a, b) => b.lines - a.lines).slice(0, 30);
  panel.innerHTML = `
    <div class="metrics">
      ${metric("tracked files", fmt(filesData.total_files),
               "after exclusions")}
      ${metric("total lines", fmt(filesData.total_lines))}
    </div>
    <input type="search" placeholder="filter paths…" id="file-filter">
    <table id="file-table"><thead><tr>
      <th>path</th><th>lang</th><th class="num">lines</th>
      <th class="num">bytes</th>
    </tr></thead><tbody>
      ${byLines.map(f => `
        <tr class="file-row" data-path="${esc(f.path)}">
          <td>${esc(f.path)}</td><td>${esc(f.lang)}</td>
          <td class="num">${f.lines == null ? "—" : fmt(f.lines)}</td>
          <td class="num">${fmt(f.bytes)}</td></tr>`).join("")}
    </tbody></table>`;
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
  const lc = s.last_commit;
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
        <div class="metrics">
          ${metric("lang", esc(d.lang || "—"))}
          ${metric("lines", fmt(d.total_lines))}
          ${metric("bytes", fmt(d.bytes))}
          ${metric("commits", fmt(s.commits))}
        </div>
        <div class="metrics">
          ${metric("added", fmt(s.added), "all history")}
          ${metric("deleted", fmt(s.deleted), "all history")}
        </div>
        <h2>last commit</h2>
        ${lc ? `<div class="last-commit">
            <div class="commit-subject">${esc(lc.subject)}</div>
            <div class="commit-sha">${esc(lc.sha)} · ${esc(lc.author)}
              · ${esc(fmtDate(lc.date))}</div>
          </div>` : "<p>—</p>"}
        <h2>first commit</h2>
        <p>${s.first_commit_date ? esc(fmtDate(s.first_commit_date)) : "—"}</p>
        ${s.authors && s.authors.length ? `
          <h2>top authors (commits)</h2>
          ${barRows(s.authors)}` : ""}
        ${s.blame && s.blame.length ? `
          <h2>lines by author (blame)</h2>
          ${barRows(s.blame)}` : ""}
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
                  : '<span style="color:#e3b341">not in lock</span>'}</td>
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
