/* codeview frontend: fetches /api/summary + /api/section/*, renders core tabs.
   Module tabs are served at /m/<name>/ and framed on demand. */
"use strict";

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
const fmt = n => n == null ? "—" : Number(n).toLocaleString("en-US");

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CORE_TABS = ["overview", "history", "churn", "files", "deps", "logs"];

function fmtDate(iso) {
  const m = String(iso || "").match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (!m) return String(iso || "—");
  const hour24 = Number(m[4]);
  const clock = m[4]
    ? `${hour24 % 12 || 12}:${m[5]} ${hour24 >= 12 ? "PM" : "AM"}`
    : "";
  return `${MONTHS[+m[2] - 1]} ${+m[3]} ${m[1]}`
    + (clock ? ` ${clock}` : "");
}

function fmtAge(isoOrTs) {
  if (!isoOrTs) return "";
  const ts = typeof isoOrTs === "number"
    ? (isoOrTs < 1e11 ? isoOrTs * 1000 : isoOrTs)
    : Date.parse(isoOrTs);
  if (Number.isNaN(ts)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) {
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    return `${h}h ${m}m ago`;
  }
  return `${Math.floor(secs / 86400)}d ago`;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;" }[c]));
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

function iconUse(name, className = "icon") {
  return `<svg class="${className}" aria-hidden="true" focusable="false">
    <use href="#lucide-${name}"></use></svg>`;
}

// ----------------------------------------------------------------- theme --

const htmlEl = document.documentElement;
const themeToggle = $("#themeToggleBtn");
const themeIconUse = $("#themeIconUse");

function applyTheme(theme) {
  htmlEl.dataset.theme = theme;
  const light = theme === "light";
  themeIconUse.setAttribute("href", light ? "#lucide-moon" : "#lucide-sun");
  const label = light ? "Switch to dark theme" : "Switch to light theme";
  themeToggle.setAttribute("aria-label", label);
  themeToggle.setAttribute("title", label);
  try { localStorage.setItem("codeview-theme", theme); } catch { /* */ }
}

function toggleTheme() {
  applyTheme(htmlEl.dataset.theme === "dark" ? "light" : "dark");
}

applyTheme(htmlEl.dataset.theme === "light" ? "light" : "dark");
themeToggle.addEventListener("click", toggleTheme);

// ---------------------------------------------------------------- toasts --

const toastContainer = $("#toastContainer");
const toastHistoryDialog = $("#toastHistoryDialog");
const toastHistoryList = $("#toastHistoryList");
const toastCountdowns = new WeakMap();
const toastHistory = [];
let toastHistoryReturnFocus = null;

function formatToastTimestamp(date) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit",
  }).format(date);
}

function renderToastHistory() {
  toastHistoryList.replaceChildren();
  toastHistory.slice().reverse().forEach(record => {
    const item = document.createElement("li");
    item.className = "toast-history-item";
    item.dataset.tone = record.tone;
    item.innerHTML = `${iconUse(record.tone === "danger" ? "octagon-x" : "check",
      "icon toast-history-icon")}
      <div class="toast-history-copy">
        <span class="toast-history-message">${esc(record.message)}</span>
        <time class="toast-history-time" datetime="${record.createdAt.toISOString()}">
          ${esc(formatToastTimestamp(record.createdAt))}</time>
      </div>`;
    toastHistoryList.appendChild(item);
  });
}

function openToastHistory(trigger) {
  toastHistoryReturnFocus = trigger || document.activeElement;
  renderToastHistory();
  if (!toastHistoryDialog.open) toastHistoryDialog.showModal();
  $("#btnCloseToastHistory").focus();
}

function closeToastHistory() {
  if (toastHistoryDialog.open) toastHistoryDialog.close();
  toastHistoryReturnFocus?.focus?.();
}

function updateToastTrayState() {
  const hasToasts = Boolean(toastContainer.querySelector(".toast"));
  const hasLive = Boolean(toastContainer.querySelector(".toast:not(.is-archived)"));
  toastContainer.classList.toggle("has-history", hasToasts);
  toastContainer.classList.toggle("is-resting", hasToasts && !hasLive);
}

function archiveToast(toast) {
  const countdown = toastCountdowns.get(toast);
  if (!countdown || countdown.archived) return;
  if (countdown.timer) window.clearTimeout(countdown.timer);
  countdown.archived = true;
  toast.classList.add("is-archived");
  updateToastTrayState();
}

function resumeToastCountdown(toast) {
  const countdown = toastCountdowns.get(toast);
  if (!countdown || countdown.archived || countdown.timer) return;
  if (countdown.remaining <= 0) {
    archiveToast(toast);
    return;
  }
  countdown.startedAt = performance.now();
  countdown.timer = window.setTimeout(() => {
    countdown.timer = 0;
    countdown.remaining = 0;
    archiveToast(toast);
  }, countdown.remaining);
}

function pauseToastCountdown(toast) {
  const countdown = toastCountdowns.get(toast);
  if (!countdown || countdown.archived || !countdown.timer) return;
  window.clearTimeout(countdown.timer);
  countdown.timer = 0;
  countdown.remaining = Math.max(0,
    countdown.remaining - (performance.now() - countdown.startedAt));
}

function spawnToast(message, danger = false) {
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.dataset.tone = danger ? "danger" : "good";
  const main = document.createElement("button");
  main.type = "button";
  main.className = "toast-main";
  main.innerHTML = `${iconUse(danger ? "octagon-x" : "check",
    danger ? "icon toast-icon toast-icon-danger" : "icon toast-icon")}
    <span class="toast-msg">${esc(message)}</span>`;
  main.addEventListener("click", () => openToastHistory(main));
  toast.appendChild(main);
  toastContainer.appendChild(toast);
  toastHistory.push({ message, tone: danger ? "danger" : "good", createdAt: new Date() });
  toastCountdowns.set(toast, { remaining: 5000, timer: 0, archived: false, startedAt: 0 });
  toast.addEventListener("mouseenter", () => pauseToastCountdown(toast));
  toast.addEventListener("mouseleave", () => resumeToastCountdown(toast));
  toast.addEventListener("focusin", () => pauseToastCountdown(toast));
  toast.addEventListener("focusout", () => resumeToastCountdown(toast));
  updateToastTrayState();
  resumeToastCountdown(toast);
}

$("#btnCloseToastHistory").addEventListener("click", closeToastHistory);
toastHistoryDialog.addEventListener("click", e => {
  if (e.target === toastHistoryDialog) closeToastHistory();
});
toastHistoryDialog.addEventListener("close", () => {
  toastHistoryReturnFocus?.focus?.();
});

async function copyText(value) {
  const text = String(value || "");
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    spawnToast("Copied to clipboard");
    return true;
  } catch {
    spawnToast("Could not copy to clipboard", true);
    return false;
  }
}

// ----------------------------------------------------------- hash / copy --

function compactSha(sha) {
  const value = String(sha || "");
  return value.length > 7 ? value.slice(0, 7) : value;
}

function shaButton(sha, title = "Copy commit hash") {
  const full = String(sha || "");
  const short = compactSha(full);
  return `<button type="button" class="meta-tag meta-tag-copy" data-copy="${esc(full)}"
    aria-label="Copy commit hash ${esc(full)}" title="${esc(title)}">${esc(short)}</button>`;
}

document.addEventListener("click", e => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  copyText(btn.dataset.copy).then(ok => {
    if (!ok) return;
    btn.classList.add("is-copied");
    window.setTimeout(() => btn.classList.remove("is-copied"), 1600);
  });
});

let hashTooltipTimer = 0;
let hashTooltipEl = null;

function hideHashTooltip() {
  window.clearTimeout(hashTooltipTimer);
  if (!hashTooltipEl) return;
  const owner = document.querySelector(`[aria-describedby="${hashTooltipEl.id}"]`);
  owner?.removeAttribute("aria-describedby");
  hashTooltipEl.remove();
  hashTooltipEl = null;
}

function showHashTooltip(trigger) {
  hideHashTooltip();
  const full = trigger.dataset.copy;
  if (!full || compactSha(full) === full) return;
  const bubble = document.createElement("div");
  bubble.className = "tooltip-bubble";
  bubble.id = "hash-tooltip";
  bubble.setAttribute("role", "tooltip");
  bubble.textContent = full;
  document.body.appendChild(bubble);
  hashTooltipEl = bubble;
  trigger.setAttribute("aria-describedby", bubble.id);
  const rect = trigger.getBoundingClientRect();
  const tw = bubble.offsetWidth, th = bubble.offsetHeight;
  let left = rect.left + (rect.width - tw) / 2;
  let top = rect.top - th - 8;
  left = Math.min(Math.max(12, left), window.innerWidth - tw - 12);
  if (top < 12) top = rect.bottom + 8;
  bubble.style.left = `${left}px`;
  bubble.style.top = `${top}px`;
}

document.addEventListener("pointerenter", e => {
  const btn = e.target.closest?.(".meta-tag-copy");
  if (!btn) return;
  hashTooltipTimer = window.setTimeout(() => showHashTooltip(btn), 150);
}, true);
document.addEventListener("pointerleave", e => {
  if (e.target.closest?.(".meta-tag-copy")) hideHashTooltip();
}, true);
document.addEventListener("focusin", e => {
  const btn = e.target.closest?.(".meta-tag-copy");
  if (btn) hashTooltipTimer = window.setTimeout(() => showHashTooltip(btn), 150);
});
document.addEventListener("focusout", e => {
  if (e.target.closest?.(".meta-tag-copy")) hideHashTooltip();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") hideHashTooltip();
});

// -------------------------------------------------------------- sorting --

function sortHeader(label, type, extraClass = "") {
  return `<th scope="col" class="${extraClass}" data-sort-direction="none">
    <button type="button" class="sort-th-btn" data-sort="${type}">
      <span>${esc(label)}</span>
      <span class="sort-indicator" aria-hidden="true">${iconUse("arrow-up")}
        <span class="sort-priority"></span></span>
    </button>
  </th>`;
}

function makeTableSortable(table) {
  let original = [...table.querySelectorAll("tbody tr")];
  let sorts = [];
  const columns = [...table.querySelectorAll(".sort-th-btn")].map(btn => {
    const header = btn.closest("th");
    const description = document.createElement("span");
    description.id = `${table.id}-sort-${header.cellIndex}`;
    description.className = "sr-only";
    header.appendChild(description);
    btn.setAttribute("aria-describedby", description.id);
    return {
      button: btn, header, index: header.cellIndex,
      type: btn.dataset.sort, direction: null, description,
    };
  });
  table.style.setProperty("--sort-priority-width", `${String(columns.length).length}ch`);

  function apply() {
    const rows = original.slice();
    rows.sort((a, b) => {
      for (const column of sorts) {
        const cellA = a.cells[column.index];
        const cellB = b.cells[column.index];
        const textA = cellA.dataset.sortValue ?? cellA.textContent.trim();
        const textB = cellB.dataset.sortValue ?? cellB.textContent.trim();
        let comparison;
        if (column.type === "number") {
          const missingA = !Number.isFinite(parseFloat(String(textA).replace(/,/g, "")));
          const missingB = !Number.isFinite(parseFloat(String(textB).replace(/,/g, "")));
          if (missingA !== missingB) return missingA ? 1 : -1;
          if (missingA) continue;
          comparison = parseFloat(String(textA).replace(/,/g, ""))
            - parseFloat(String(textB).replace(/,/g, ""));
        } else {
          comparison = String(textA).localeCompare(String(textB), undefined,
            { sensitivity: "base" });
        }
        if (comparison) return column.direction === "ascending" ? comparison : -comparison;
      }
      return 0;
    });
    const tbody = table.querySelector("tbody");
    rows.forEach(row => tbody.appendChild(row));
    columns.forEach(column => {
      const priority = sorts.indexOf(column);
      column.header.dataset.sortDirection = column.direction || "none";
      if (priority === 0) column.header.setAttribute("aria-sort", column.direction);
      else column.header.removeAttribute("aria-sort");
      column.button.querySelector(".sort-priority").textContent =
        priority >= 0 ? String(priority + 1) : "";
      column.button.querySelector("use").setAttribute("href",
        column.direction === "descending" ? "#lucide-arrow-down" : "#lucide-arrow-up");
      column.description.textContent = priority >= 0
        ? `Sort priority ${priority + 1} of ${sorts.length}, ${column.direction}`
        : "Unsorted";
    });
  }

  columns.forEach(column => {
    column.button.addEventListener("click", () => {
      if (!column.direction) {
        column.direction = "ascending";
        sorts.push(column);
      } else if (column.direction === "ascending") {
        column.direction = "descending";
      } else {
        column.direction = null;
        sorts.splice(sorts.indexOf(column), 1);
      }
      apply();
    });
  });
  apply();
  return {
    refresh() {
      original = [...table.querySelectorAll("tbody tr")];
      apply();
    },
  };
}

function bindRowActivation(table, onActivate) {
  table._rowAbort?.abort();
  const ac = new AbortController();
  table._rowAbort = ac;
  const { signal } = ac;
  const rows = () => [...table.querySelectorAll("tbody tr")]
    .filter(r => r.style.display !== "none");
  const buttons = () => rows().map(r => r.querySelector(".table-row-trigger")).filter(Boolean);

  function setTabStops(active) {
    buttons().forEach(btn => { btn.tabIndex = btn === active ? 0 : -1; });
  }
  const first = buttons()[0];
  if (first) setTabStops(first);

  table.querySelectorAll("tbody tr").forEach(tr => {
    if (!tr.querySelector(".table-row-trigger")) return;
    tr.classList.add("is-clickable");
    tr.addEventListener("click", e => {
      if (e.target.closest("button, a, input")) return;
      if (window.getSelection()?.toString()) return;
      const btn = tr.querySelector(".table-row-trigger");
      if (btn) onActivate(btn);
    }, { signal });
  });
  table.querySelectorAll(".table-row-trigger").forEach(btn => {
    btn.addEventListener("click", e => {
      e.stopPropagation();
      onActivate(btn);
    }, { signal });
    btn.addEventListener("focus", () => {
      table.querySelectorAll("tbody tr").forEach(r => r.classList.remove("is-selected"));
      btn.closest("tr").classList.add("is-selected");
      setTabStops(btn);
    }, { signal });
  });
  table.addEventListener("keydown", e => {
    const btn = e.target.closest(".table-row-trigger");
    if (!btn || !["ArrowUp", "ArrowDown"].includes(e.key)) return;
    const list = buttons();
    let i = list.indexOf(btn);
    if (e.key === "ArrowDown") i = Math.min(list.length - 1, i + 1);
    else i = Math.max(0, i - 1);
    e.preventDefault();
    list[i].focus();
    list[i].scrollIntoView({ block: "nearest" });
  }, { signal });
  document.addEventListener("mousedown", e => {
    if (!table.contains(e.target)) {
      table.querySelectorAll("tbody tr").forEach(r => r.classList.remove("is-selected"));
    }
  }, { signal });
}

function bindSearch(input, onFilter) {
  const clear = input.parentElement.querySelector(".input-clear-btn");
  function update() {
    if (clear) clear.hidden = !input.value;
    onFilter(input.value);
  }
  input.addEventListener("input", update);
  clear?.addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
}

// ---------------------------------------------------------------- charts --

const CHART_PAD = { l: 52, r: 12, t: 8, b: 24 };
let chartHoverAbort = null;

function chartScale(values, width, height) {
  const vmax = Math.max(1, ...values), vmin = Math.min(0, ...values);
  const iw = Math.max(1, width - CHART_PAD.l - CHART_PAD.r);
  const ih = Math.max(1, height - CHART_PAD.t - CHART_PAD.b);
  const n = values.length;
  return {
    vmax, vmin, iw, ih, n, width, height,
    x: i => CHART_PAD.l + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw),
    y: v => CHART_PAD.t + ih - ((v - vmin) / (vmax - vmin || 1)) * ih,
  };
}

function renderHistorySvg(commits, width, height) {
  const values = commits.map(c => c.total);
  const scale = chartScale(values, width, height);
  const { vmax, vmin, n, x, y } = scale;
  let out = `<svg class="chart-svg" width="${width}" height="${height}"
    viewBox="0 0 ${width} ${height}" role="presentation">`;
  for (let g = 0; g <= 4; g++) {
    const gy = CHART_PAD.t + (scale.ih * g) / 4;
    const val = Math.round(vmax - (scale.ih * g) / 4 * (vmax - vmin) / scale.ih);
    out += `<line class="chart-grid-line" x1="${CHART_PAD.l}" y1="${gy}"
      x2="${width - CHART_PAD.r}" y2="${gy}"></line>`;
    out += `<text class="chart-axis-text" x="${CHART_PAD.l - 6}" y="${gy + 4}"
      text-anchor="end">${fmt(val)}</text>`;
  }
  const pts = values.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  out += `<polygon class="chart-area"
    points="${x(0)},${y(vmin)} ${pts} ${x(n - 1)},${y(vmin)}"></polygon>`;
  out += `<polyline class="chart-series" points="${pts}"></polyline>`;
  const tickEvery = Math.max(1, Math.ceil(n / 6));
  commits.forEach((c, i) => {
    if (i % tickEvery !== 0) return;
    const s = String(c.date);
    const short = MONTHS[+s.slice(5, 7) - 1]
      ? `${MONTHS[+s.slice(5, 7) - 1]} ${+s.slice(8, 10)}` : s;
    out += `<text class="chart-axis-text" x="${x(i)}" y="${height - 6}"
      text-anchor="middle">${short}</text>`;
  });
  out += `<line class="chart-crosshair" hidden x1="0" x2="0" y1="0" y2="0"></line>
    <circle class="chart-point" r="4" hidden cx="-20" cy="-20"></circle></svg>`;
  return { html: out, scale };
}

function bindHistoryChart(wrap, commits) {
  if (!wrap || !commits.length) return;
  const frame = wrap.querySelector(".chart-frame");
  const surface = wrap.querySelector(".chart-surface");
  const tip = wrap.querySelector(".chart-tooltip");
  const live = wrap.querySelector("[aria-live]");
  if (!frame || !surface || !tip) return;

  chartHoverAbort?.abort();
  chartHoverAbort = new AbortController();
  const { signal } = chartHoverAbort;

  let scale = null;
  let last = -1;
  let pinned = -1;
  let hovering = false;

  function draw() {
    const width = Math.max(320, Math.floor(frame.clientWidth || wrap.clientWidth || 640));
    const height = frame.clientWidth && frame.clientWidth <= 600 ? 180 : 200;
    const drawn = renderHistorySvg(commits, width, height);
    surface.innerHTML = drawn.html;
    scale = drawn.scale;
    if (last >= 0) show(last, true);
  }

  function hide() {
    if (pinned >= 0) return;
    last = -1;
    const svg = surface.querySelector("svg");
    svg?.querySelector(".chart-crosshair")?.setAttribute("hidden", "");
    svg?.querySelector(".chart-point")?.setAttribute("hidden", "");
    tip.hidden = true;
    tip.classList.remove("is-pinned");
  }

  function unpin() {
    const i = pinned;
    pinned = -1;
    tip.classList.remove("is-pinned");
    if (hovering && i >= 0) show(i);
    else hide();
  }

  function show(i, keepTipHtml = false) {
    if (!scale) return;
    const c = commits[i];
    const svg = surface.querySelector("svg");
    const line = svg.querySelector(".chart-crosshair");
    const dot = svg.querySelector(".chart-point");
    const px = scale.x(i), py = scale.y(c.total);
    line.removeAttribute("hidden");
    line.setAttribute("x1", px);
    line.setAttribute("x2", px);
    line.setAttribute("y1", CHART_PAD.t);
    line.setAttribute("y2", scale.height - CHART_PAD.b);
    dot.removeAttribute("hidden");
    dot.setAttribute("cx", px);
    dot.setAttribute("cy", py);
    if (i !== last || !keepTipHtml) {
      last = i;
      tip.innerHTML = `
        <time>${esc(fmtDate(c.date))}</time>
        <dl>
          <dt>sha</dt><dd>${esc(c.sha)}</dd>
          <dt>loc</dt><dd>${fmt(c.total)}</dd>
        </dl>`;
      if (live) live.textContent = `${fmtDate(c.date)} ${c.sha} ${fmt(c.total)} lines`;
    }
    const isPinned = pinned >= 0;
    tip.classList.toggle("is-pinned", isPinned);
    tip.hidden = false;
    const bounds = frame.getBoundingClientRect();
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    const ratioX = px / scale.width, ratioY = py / scale.height;
    let left = ratioX * bounds.width + 12;
    let topPx = ratioY * bounds.height - th - 8;
    if (left + tw > bounds.width) left = ratioX * bounds.width - tw - 12;
    if (left < 0) left = 0;
    if (topPx < 0) topPx = ratioY * bounds.height + 12;
    if (topPx + th > bounds.height) topPx = Math.max(0, bounds.height - th);
    tip.style.left = `${left}px`;
    tip.style.top = `${topPx}px`;
  }

  function indexFromEvent(e) {
    if (!scale) return 0;
    const rect = surface.querySelector("svg").getBoundingClientRect();
    if (!rect.width) return last < 0 ? 0 : last;
    const viewX = ((e.clientX - rect.left) / rect.width) * scale.width;
    const n = commits.length;
    const t = n <= 1 ? 0 : (viewX - CHART_PAD.l) / scale.iw;
    return Math.max(0, Math.min(n - 1, Math.round(t * Math.max(n - 1, 1))));
  }

  draw();
  const ro = new ResizeObserver(draw);
  ro.observe(frame);
  signal.addEventListener("abort", () => ro.disconnect());

  surface.addEventListener("mousemove", e => {
    hovering = true;
    if (pinned >= 0) return;
    show(indexFromEvent(e));
  }, { signal });
  surface.addEventListener("mouseleave", () => {
    hovering = false;
    hide();
  }, { signal });
  surface.addEventListener("click", e => {
    if (e.target.closest(".chart-tooltip")) return;
    const i = indexFromEvent(e);
    if (pinned === i) { unpin(); return; }
    pinned = i;
    show(i);
  }, { signal });
  surface.addEventListener("keydown", e => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    let i = last < 0 ? commits.length - 1 : last;
    if (e.key === "Home") i = 0;
    else if (e.key === "End") i = commits.length - 1;
    else if (e.key === "ArrowLeft") i = Math.max(0, i - 1);
    else i = Math.min(commits.length - 1, i + 1);
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

function barRows(entries) {
  const max = Math.max(1, ...entries.map(e => Math.abs(e[1])));
  return `<div class="rank-list">${entries.map(([label, value]) => `
    <div class="rank-row">
      <span class="rank-label" title="${esc(label)}">${esc(label)}</span>
      <div class="rank-track"><div class="rank-fill"
        style="width:${Math.max(1, (Math.abs(value) / max) * 100)}%"></div></div>
      <span class="rank-value">${fmt(value)}</span>
    </div>`).join("")}</div>`;
}

function metric(label, value) {
  return `<div class="metric-card"><span class="metric-label">${esc(label)}</span>
    <span class="metric-num">${value}</span></div>`;
}

function metricGrid(cards, className = "") {
  const extra = className ? ` ${className}` : "";
  return `<div class="metric-grid${extra}">${cards.join("")}</div>`;
}

function sectionHeading(title, note = "") {
  return `<div class="section-heading"><h2>${esc(title)}</h2>
    ${note ? `<span class="section-note">${esc(note)}</span>` : ""}</div>`;
}

function statRows(rows) {
  return `<dl class="stat-list">${rows.map(([label, value, note]) => {
    const display = value == null || value === "" ? "—" : String(value);
    return `<div class="stat-row"><dt>${esc(label)}</dt><dd>${esc(display)}
      ${note ? `<small>${esc(note)}</small>` : ""}</dd></div>`;
  }).join("")}</dl>`;
}

function statGroup(title, body) {
  return `<details class="stats-group" open>
    <summary>${esc(title)}</summary>
    <div class="stats-group-body">${body}</div>
  </details>`;
}

function symbolList(items, empty = "none detected") {
  const values = (items || []).map(String);
  if (!values.length) return `<span class="stats-note">${empty}</span>`;
  return `<div class="symbol-list">${values.map(item =>
    `<code>${esc(item)}</code>`).join("")}</div>`;
}

function complexityRating(metrics) {
  if (metrics.complexity_rating) return metrics.complexity_rating;
  const value = Number(metrics.cyclomatic_complexity || 1);
  return value <= 10 ? "low" : value <= 20 ? "moderate" :
    value <= 40 ? "high" : "very high";
}

function complexityBadge(metrics) {
  const rating = complexityRating(metrics);
  const tone = rating === "low" ? "good" : rating === "moderate" ? "caution" : "danger";
  return `<span class="badge badge-${tone}">${esc(rating)}</span>`;
}

function searchField(id, label) {
  return `<div class="form-group">
    <label class="form-label" for="${id}">${esc(label)}</label>
    <div class="input-with-action">
      ${iconUse("search", "icon input-leading-icon")}
      <input class="input-field" type="search" id="${id}" autocomplete="off">
      <button type="button" class="input-clear-btn" hidden aria-label="Clear ${esc(label)}">
        ${iconUse("x")}</button>
    </div>
  </div>`;
}

function emptyCallout(title, message) {
  return `<div class="callout callout-info"><strong>${esc(title)}:</strong> ${esc(message)}</div>`;
}

// ----------------------------------------------------------------- state --

async function api(path, opts) {
  const res = await fetch(path, { headers: { Accept: "application/json" }, ...opts });
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return res.json();
}

const state = { gen: null, tabs: [], active: null, scannedAt: null };

function updateScanTime() {
  const scan = $("#scan-time");
  if (!scan) return;
  if (!state.scannedAt) {
    scan.hidden = true;
    return;
  }
  scan.dateTime = state.scannedAt;
  const age = fmtAge(state.scannedAt);
  scan.textContent = `Last scan: ${fmtDate(state.scannedAt)}${age ? ` (${age})` : ""}`;
  scan.setAttribute("aria-label", `Last repository scan${age ? ` ${age}` : ""}`);
  scan.title = `Last repo scan: ${fmtDate(state.scannedAt)} UTC (${state.scannedAt})`;
  scan.hidden = false;
}

function tabKey(tab) {
  return tab.kind === "core" ? tab.name : `m:${tab.name}`;
}

function showPanel(name) {
  state.active = name;
  document.querySelectorAll(".panel").forEach(p => {
    const active = p.dataset.panel === name;
    p.hidden = !active;
    p.classList.toggle("active", active);
  });
  document.querySelectorAll("#tabs button").forEach(b => {
    const active = b.dataset.tab === name;
    b.setAttribute("aria-selected", active ? "true" : "false");
    b.setAttribute("tabindex", active ? "0" : "-1");
  });
  if (location.hash.slice(1) !== name) {
    history.replaceState(null, "", `#${name}`);
  }
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
       sandbox="allow-scripts allow-forms" title="${esc(slug)}"></iframe>`;
}

function activateTab(key) {
  showPanel(key);
  if (key === "history") renderHistory().catch(console.error);
  else if (key === "churn") renderChurn().catch(console.error);
  else if (key === "files") renderFiles().catch(console.error);
  else if (key === "deps") renderDeps().catch(console.error);
  else if (key === "logs") renderLogs().catch(console.error);
  else if (key === "overview") refresh().catch(console.error);
}

function renderTabs(tabs) {
  const nav = $("#tabs");
  const moduleKeys = new Set();
  for (const tab of tabs) {
    if (tab.kind === "core") continue;
    const key = `m:${tab.name}`;
    moduleKeys.add(key);
    if (document.querySelector(`.panel[data-panel="${key}"]`)) continue;
    const panel = document.createElement("section");
    panel.className = "panel tab-panel";
    panel.dataset.panel = key;
    panel.id = `panel-m-${tab.name}`;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", `tab-m-${tab.name}`);
    panel.hidden = true;
    panel.innerHTML = '<div class="loading">Loading…</div>';
    $("#panels").appendChild(panel);
  }
  document.querySelectorAll('.panel[data-panel^="m:"]').forEach(panel => {
    if (!moduleKeys.has(panel.dataset.panel)) panel.remove();
  });
  nav.innerHTML = tabs.map(t => {
    const key = tabKey(t);
    const idPart = t.kind === "core" ? t.name : `m-${t.name}`;
    const label = t.name;
    const active = key === (state.active || "overview");
    const broken = t.kind === "broken" ? " is-broken" : "";
    const desc = t.kind === "broken" ? "Broken module" : (t.description || "");
    return `<button type="button" class="tab-btn${broken}" id="tab-${idPart}" role="tab"
      data-tab="${key}" aria-controls="panel-${idPart}"
      aria-selected="${active ? "true" : "false"}"
      tabindex="${active ? "0" : "-1"}"
      title="${esc(desc)}">${esc(label)}</button>`;
  }).join("");
  rebuildSpotlightDestinations();
  if (!state.active || !tabs.some(t => tabKey(t) === state.active)) {
    showPanel("overview");
  }
}

// -------------------------------------------------------------- sections --

function ciBadge(ci) {
  const el = $("#ci-info");
  if (!ci) {
    el.hidden = true;
    return;
  }
  el.hidden = false;
  el.classList.remove("badge-good", "badge-danger", "badge-caution", "badge-nominal",
    "badge-flash", "badge-flash-slow");
  if (ci === "passing") {
    el.classList.add("badge-good");
    el.textContent = "CI passing";
  } else if (ci === "failing") {
    el.classList.add("badge-danger");
    el.textContent = "CI failing";
  } else if (ci === "running") {
    el.classList.add("badge-caution", "badge-flash", "badge-flash-slow");
    el.textContent = "CI running";
  } else {
    el.classList.add("badge-nominal");
    el.textContent = "CI unknown";
  }
}

async function refresh() {
  const [summary, tabs] = await Promise.all(
    [api("/api/summary"), api("/api/tabs")]);
  if (JSON.stringify(tabs.tabs) !== JSON.stringify(state.tabs)) {
    state.tabs = tabs.tabs;
    renderTabs(state.tabs);
  }
  const m = summary.meta || {};
  $("#repo-name").textContent = m.repo_name || "codeview";
  document.title = m.repo_name ? `${m.repo_name} · codeview` : "codeview";
  $("#branch-label").textContent = m.branch || "(detached)";
  const dirty = $("#dirty-badge");
  dirty.hidden = !m.dirty;
  ciBadge(summary.ci);
  if (m.scanned_at_iso) {
    state.scannedAt = m.scanned_at_iso;
    updateScanTime();
  } else {
    state.scannedAt = null;
    updateScanTime();
  }
  if (state.active === "overview" || !state.active) await renderOverview(summary);
  else if (state.active === "logs") await renderLogs();
}

async function renderOverview(s) {
  const panel = $('.panel[data-panel="overview"]');
  if (s.meta?.empty_repo) {
    panel.innerHTML = emptyCallout("Empty repository",
      "No commits yet — make a commit and the dashboard will pick it up automatically.");
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
  const ecoCount = (s.ecosystems || []).length;
  const maintainability = mt.maintainability_index == null
    ? "—" : decimal(mt.maintainability_index);
  panel.innerHTML = `
    <div class="panel-stack">
      ${metricGrid([
        metric("Source lines", fmt(mt.code_lines ?? s.total_lines)),
        metric("Tracked files", fmt(s.total_files)),
        metric("Languages", fmt(langCount)),
        metric("Functions", fmt(mt.functions)),
        metric("Commits scanned", fmt(s.commits_count)),
        metric("Maintainability", maintainability),
        metric("Blank lines", fmt(mt.blank_lines)),
        metric("Comment lines", fmt(mt.comment_lines)),
        metric("Attention markers", fmt(mt.todo_count)),
        metric("Complexity", fmt(mt.cyclomatic_complexity)),
        metric("Dependencies", fmt(ecoCount)),
        metric("Modules", fmt(s.modules.length)),
      ], "overview-metrics")}
      <div class="rank-grid">
        <section>
          ${sectionHeading("Lines by top-level dir")}
          ${tops.length ? barRows(tops) : emptyCallout("No directories", "No top-level directories to show.")}
        </section>
        <section>
          ${sectionHeading("Lines by language")}
          ${langs.length
            ? barRows(langs.map(([k, v]) => [k, v.lines]))
            : emptyCallout("No languages", "No languages detected.")}
        </section>
      </div>
    </div>`;
}

async function renderHistory() {
  const hist = await api("/api/section/history");
  const panel = $('.panel[data-panel="history"]');
  const commits = hist.commits || [];
  if (!commits.length) {
    panel.innerHTML = emptyCallout("No history", "No commit history available.");
    return;
  }
  const dirSets = {};
  for (const c of commits.slice(-200)) {
    for (const [d, v] of Object.entries(c.dirs || {})) {
      dirSets[d] = (dirSets[d] || 0) + v;
    }
  }
  const hotDirs = Object.entries(dirSets)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 6);
  const recent = commits.slice().reverse().slice(0, 100);
  panel.innerHTML = `
    <div class="panel-stack">
      ${sectionHeading("Cumulative lines", `${fmt(commits.length)} commits scanned`)}
      <div class="chart-box">
        <div class="chart-frame">
          <div class="chart-surface" tabindex="0" role="img"
            aria-label="Cumulative lines over commit history"></div>
          <div class="chart-tooltip" hidden></div>
        </div>
        <div class="sr-only" aria-live="polite"></div>
      </div>
      ${sectionHeading("Most-changed dirs")}
      ${hotDirs.length ? barRows(hotDirs) : emptyCallout("No directories", "No directory changes to show.")}
      ${sectionHeading("Recent commits")}
      <div class="table-container" role="region" aria-label="Recent commits">
        <table class="data-table" id="history-table">
          <caption class="sr-only">Recent commits in the scanned window</caption>
          <thead><tr>
            ${sortHeader("date", "text")}
            ${sortHeader("sha", "text")}
            ${sortHeader("subject", "text")}
            ${sortHeader("delta", "number", "num")}
            ${sortHeader("total", "number", "num")}
          </tr></thead>
          <tbody>
            ${recent.map(c => `
              <tr>
                <td data-sort-value="${esc(c.date)}">${esc(fmtDate(c.date))}</td>
                <td data-sort-value="${esc(c.sha)}">${shaButton(c.sha)}</td>
                <td class="wrap">${esc(c.subject)}</td>
                <td class="num" data-sort-value="${c.delta}">${c.delta >= 0 ? "+" : ""}${fmt(c.delta)}</td>
                <td class="num">${fmt(c.total)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
  bindHistoryChart(panel.querySelector(".chart-box"), commits);
  makeTableSortable($("#history-table"));
}

async function renderChurn() {
  const [hist, filesData] = await Promise.all([
    api("/api/section/history"),
    api("/api/section/files").catch(() => ({ files: [] })),
  ]);
  const panel = $('.panel[data-panel="churn"]');
  const commits = hist.commits || [];
  const churn = hist.churn || {};
  const hotspots = churn.files || [];
  if (!commits.length) {
    panel.innerHTML = emptyCallout("No history", "No commit history available.");
    return;
  }
  if (!hotspots.length) {
    panel.innerHTML = emptyCallout("No churn",
      "No textual file changes were found in the scanned history.");
    return;
  }

  const trackedPaths = new Set((filesData.files || []).map(f => f.path));
  const topCurrent = hotspots.find(file => trackedPaths.has(file.path));
  const top = topCurrent || hotspots[0];
  const dirs = (churn.dirs || []).slice(0, 10);
  const firstDate = commits[0]?.date;
  const lastDate = commits[commits.length - 1]?.date;
  const windowNote = `${fmtDate(firstDate)} – ${fmtDate(lastDate)}`;

  panel.innerHTML = `
    <div class="panel-stack">
      ${sectionHeading("Churn window", windowNote)}
      ${metricGrid([
        metric("Lines touched", fmt(churn.total)),
        metric("Files changed", fmt(churn.file_count)),
        metric("Highest file churn", fmt(top.churn)),
      ])}
      <section>
        ${sectionHeading("Churn by top-level dir")}
        ${dirs.length
          ? barRows(dirs.map(d => [d.name, d.churn]))
          : emptyCallout("No directories", "No directories to show.")}
      </section>
      ${sectionHeading("Highest-churn files")}
      <div class="filter-row">
        ${searchField("churn-filter", "Filter hotspots")}
        <span class="filter-count" aria-live="polite">${fmt(hotspots.length)} hotspots shown</span>
      </div>
      <div class="table-container" role="region" aria-label="Highest code-churn files">
        <table class="data-table" id="churn-table">
          <caption class="sr-only">Files ranked by additions plus deletions in the scanned commit window</caption>
          <thead><tr>
            ${sortHeader("path", "text")}
            ${sortHeader("commits", "number", "num")}
            ${sortHeader("added", "number", "num")}
            ${sortHeader("deleted", "number", "num")}
            ${sortHeader("churn", "number", "num")}
            ${sortHeader("last change", "text", "num")}
          </tr></thead>
          <tbody>
            ${hotspots.map(file => {
              const tracked = trackedPaths.has(file.path);
              return `<tr class="${tracked ? "" : "is-muted"}" data-path="${esc(file.path)}">
                <td class="wrap" data-sort-value="${esc(file.path)}">${tracked
                  ? `<button type="button" class="table-row-trigger" data-path="${esc(file.path)}"
                       title="Open current file profile">${esc(file.path)}</button>`
                  : `${esc(file.path)} <span class="badge badge-nominal">historical</span>`}</td>
                <td class="num">${fmt(file.commits)}</td>
                <td class="num" data-sort-value="${file.additions}">+${fmt(file.additions)}</td>
                <td class="num" data-sort-value="${file.deletions}">−${fmt(file.deletions)}</td>
                <td class="num">${fmt(file.churn)}</td>
                <td class="num" data-sort-value="${esc(file.last_date)}">${esc(fmtDate(file.last_date))}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
      <div class="callout callout-info" id="churn-filter-empty" hidden>
        <strong>No matches:</strong> no hotspots match this filter.
        <button type="button" class="btn btn-secondary" id="churn-filter-clear">Clear filter</button>
      </div>
    </div>`;

  makeTableSortable($("#churn-table"));
  const input = $("#churn-filter");
  const count = panel.querySelector(".filter-count");
  const empty = $("#churn-filter-empty");
  bindSearch(input, q => {
    const query = q.toLowerCase();
    const rows = $("#churn-table tbody").children;
    let shown = 0;
    for (let i = 0; i < rows.length; i++) {
      const visible = !query || rows[i].textContent.toLowerCase().includes(query);
      rows[i].style.display = visible ? "" : "none";
      if (visible) shown++;
    }
    count.textContent = `${fmt(shown)} hotspots shown`;
    empty.hidden = shown !== 0;
  });
  $("#churn-filter-clear").addEventListener("click", () => {
    input.value = "";
    input.dispatchEvent(new Event("input"));
    input.focus();
  });
  bindRowActivation($("#churn-table"), btn => {
    showPanel("files");
    openFile(btn.dataset.path).catch(console.error);
  });
}

async function renderFiles() {
  const filesData = await api("/api/section/files");
  const panel = $('.panel[data-panel="files"]');
  const files = filesData.files || [];
  const byLines = files.slice().sort((a, b) =>
    (b.lines ?? -1) - (a.lines ?? -1));
  const mt = filesData.metric_totals || {};
  panel.innerHTML = `
    <div class="panel-stack">
      ${metricGrid([
        metric("Tracked files", fmt(filesData.total_files)),
        metric("Physical lines", fmt(filesData.total_lines)),
        metric("Source lines", fmt(mt.code_lines)),
        metric("Files analyzed", fmt(mt.files_analyzed)),
        metric("Functions", fmt(mt.functions)),
        metric("Attention markers", fmt(mt.todo_count)),
      ])}
      <div class="filter-row">
        ${searchField("file-filter", "Filter files")}
        <span class="filter-count" aria-live="polite">${fmt(byLines.length)} files shown</span>
      </div>
      <div class="table-container" role="region" aria-label="Tracked files">
        <table class="data-table" id="file-table">
          <caption class="sr-only">Tracked files with source metrics</caption>
          <thead><tr>
            ${sortHeader("path", "text")}
            ${sortHeader("lang", "text")}
            ${sortHeader("lines", "number", "num")}
            ${sortHeader("code", "number", "num")}
            ${sortHeader("blank", "number", "num")}
            ${sortHeader("comments", "number", "num")}
            ${sortHeader("funcs", "number", "num")}
            ${sortHeader("imports", "number", "num")}
            ${sortHeader("complexity", "number", "num")}
            ${sortHeader("bytes", "number", "num")}
          </tr></thead>
          <tbody>
            ${byLines.map(f => {
              const m = f.metrics || {};
              return `<tr data-path="${esc(f.path)}">
                <td class="wrap" data-sort-value="${esc(f.path)}">
                  <button type="button" class="table-row-trigger" data-path="${esc(f.path)}"
                    title="Open file profile">${esc(f.path)}</button></td>
                <td>${esc(f.lang)}</td>
                <td class="num" data-sort-value="${f.lines ?? ""}">${f.lines == null ? "—" : fmt(f.lines)}</td>
                <td class="num">${fmt(m.code_lines)}</td>
                <td class="num">${fmt(m.blank_lines)}</td>
                <td class="num">${fmt(m.comment_lines)}</td>
                <td class="num">${fmt(m.functions)}</td>
                <td class="num">${fmt(m.imports)}</td>
                <td class="num">${fmt(m.cyclomatic_complexity)}</td>
                <td class="num">${fmt(f.bytes)}</td>
              </tr>`;
            }).join("")}
          </tbody>
        </table>
      </div>
    </div>`;
  makeTableSortable($("#file-table"));
  const input = $("#file-filter");
  const count = panel.querySelector(".filter-count");
  bindSearch(input, q => {
    const query = q.toLowerCase();
    const rows = $("#file-table tbody").children;
    let shown = 0;
    for (let i = 0; i < rows.length; i++) {
      const visible = !query || rows[i].textContent.toLowerCase().includes(query);
      rows[i].style.display = visible ? "" : "none";
      if (visible) shown++;
    }
    count.textContent = `${fmt(shown)} files shown`;
  });
  bindRowActivation($("#file-table"), btn => {
    openFile(btn.dataset.path).catch(console.error);
  });
}

async function openFile(path) {
  const panel = $('.panel[data-panel="files"]');
  panel.innerHTML = `<div class="loading">Loading ${esc(path)}…</div>`;
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
  const fileName = String(d.path || "").split("/").pop();
  const markerSummary = Object.entries(m.todo_markers || {})
    .map(([name, count]) => `${name} ${fmt(count)}`).join(" · ") || "none";
  let code;
  if (d.binary) {
    code = emptyCallout("Binary file", "Content is not shown.");
  } else {
    const lines = (d.content ?? "").split("\n");
    code = `<pre>${lines.map((l, i) =>
      `<span class="ln">${i + 1}</span>${esc(l)}`).join("\n")}</pre>`;
  }
  return `
    <div class="file-toolbar">
      <button type="button" class="btn btn-secondary" id="file-back">Files</button>
      <nav aria-label="Breadcrumb">
        <ol class="breadcrumb-nav">
          <li>Files</li>
          <li class="breadcrumb-sep" aria-hidden="true">/</li>
          <li class="breadcrumb-cur" aria-current="page">${esc(d.path)}</li>
        </ol>
      </nav>
      ${d.truncated ? `<span class="badge badge-caution">Truncated</span>` : ""}
    </div>
    <div class="file-grid">
      <div class="file-code">${code}</div>
      <aside class="file-stats">
        <div class="stats-hero">
          <div class="stats-kicker">File profile</div>
          <div class="stats-title">${esc(fileName || "file")}</div>
          <div class="stats-sub">${esc(d.lang || "unknown")} · ${fmt(d.bytes)} bytes</div>
          <div class="stats-note">${esc(m.analysis || "metrics unavailable")}</div>
        </div>
        ${statGroup("Source composition", statRows([
          ["physical lines", fmt(m.total_lines ?? d.total_lines)],
          ["source lines", fmt(m.code_lines)],
          ["blank lines", fmt(m.blank_lines), pct(m.blank_ratio)],
          ["comment lines", fmt(m.comment_lines), pct(m.comment_ratio)],
          ["comment-only", fmt(m.comment_only_lines)],
          ["inline comments", fmt(m.inline_comment_lines)],
          ["comment blocks", fmt(m.comment_blocks)],
          ["string lines", fmt(m.string_lines)],
        ]))}
        ${statGroup("Structure", statRows([
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
        ]))}
        ${statGroup("Complexity", `
          <div class="complexity-summary">
            <span class="complexity-number">${fmt(m.cyclomatic_complexity)}</span>
            <span class="complexity-label">Cyclomatic</span>
            ${complexityBadge(m)}
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
          <p class="stats-note">Cyclomatic and Halstead values are static estimates; maintainability is a 0–100 heuristic.</p>`)}
        ${statGroup("Text and formatting", statRows([
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
        ]))}
        ${statGroup("Attention signals", `
          ${statRows([
            ["TODO / FIXME / HACK", fmt(m.todo_count), markerSummary],
            ["parse status", m.parse_error ? "fallback heuristic" : "ok"],
          ])}
          ${m.parse_error ? `<div class="callout callout-caution"><strong>Parse fallback:</strong> ${esc(m.parse_error)}</div>` : ""}
          <div class="stats-subhead">Markers</div>
          <div class="marker-list">${esc(markerSummary)}</div>`)}
        ${statGroup("Symbol inventory", `
          <div class="stats-subhead">Functions</div>
          ${symbolList(m.function_names)}
          <div class="stats-subhead">Classes and types</div>
          ${symbolList(m.class_names)}
          <div class="stats-subhead">Imports</div>
          ${symbolList(m.import_names)}
          <div class="stats-subhead">Exports</div>
          ${symbolList(m.export_names)}`)}
        ${statGroup("Git history", `
          ${statRows([
            ["commits", fmt(s.commits)],
            ["lines added", fmt(s.added)],
            ["lines deleted", fmt(s.deleted)],
            ["first commit", s.first_commit_date ? fmtDate(s.first_commit_date) : "—"],
          ])}
          <div class="stats-subhead">Last commit</div>
          ${lc ? `<div class="last-commit">
              <div>${esc(lc.subject)}</div>
              <div class="stats-note">${shaButton(lc.sha)} · ${esc(lc.author)}
                · ${esc(fmtDate(lc.date))}</div>
            </div>` : `<p class="stats-note">—</p>`}
          ${s.authors && s.authors.length ? `
            <div class="stats-subhead">Top authors by commits</div>
            ${barRows(s.authors)}` : ""}
          ${s.blame && s.blame.length ? `
            <div class="stats-subhead">Lines by author (blame)</div>
            ${barRows(s.blame)}` : ""}`)}
      </aside>
    </div>`;
}

async function renderLogs() {
  const data = await api("/api/logs");
  const panel = $('.panel[data-panel="logs"]');
  const lines = data.logs || [];
  panel.innerHTML = `
    <div class="panel-stack">
      ${sectionHeading("Server logs", `${fmt(lines.length)} lines · newest last`)}
      <pre class="log-view">${lines.length ? lines.map(esc).join("\n") : "—"}</pre>
    </div>`;
  const view = panel.querySelector(".log-view");
  view.scrollTop = view.scrollHeight;
}

async function renderDeps() {
  const deps = await api("/api/section/deps");
  const panel = $('.panel[data-panel="deps"]');
  if (!(deps.ecosystems || []).length) {
    panel.innerHTML = emptyCallout("No manifests", "No dependency manifests found in this repo.");
    return;
  }
  panel.innerHTML = `<div class="panel-stack">${deps.ecosystems.map((eco, i) => {
    const declared = eco.declared || [], locked = eco.locked || [];
    const lockedByRoot = {};
    for (const l of locked) {
      lockedByRoot[l.name] = lockedByRoot[l.name] || l.version;
    }
    const lockNote = eco.lockfile ? `lock ${eco.lockfile}` : "no lockfile";
    return `
      <section>
        ${sectionHeading(`${eco.name} · ${eco.manifest}`, lockNote)}
        <div class="table-container" role="region" aria-label="${esc(eco.name)} dependencies">
          <table class="data-table" id="deps-table-${i}">
            <caption class="sr-only">${esc(eco.name)} declared dependencies</caption>
            <thead><tr>
              ${sortHeader("dependency", "text")}
              ${sortHeader("kind", "text")}
              ${sortHeader("declared", "text")}
              ${sortHeader("locked version", "text")}
            </tr></thead>
            <tbody>
              ${declared.map(d => `
                <tr>
                  <td class="wrap">${esc(d.name)}</td>
                  <td>${esc(d.kind)}</td>
                  <td class="wrap">${esc(d.req)}</td>
                  <td>${lockedByRoot[d.name]
                    ? `<span class="meta-tag">${esc(lockedByRoot[d.name])}</span>`
                    : `<span class="badge badge-caution">not in lock</span>`}</td>
                </tr>`).join("") || `<tr><td colspan="4">—</td></tr>`}
            </tbody>
          </table>
        </div>
        ${locked.length > declared.length
          ? `<p class="stats-note">${fmt(locked.length - declared.length)} more locked packages not directly declared.</p>`
          : ""}
      </section>`;
  }).join("")}</div>`;
  deps.ecosystems.forEach((_, i) => {
    const table = $(`#deps-table-${i}`);
    if (table.querySelector("tbody tr td")) makeTableSortable(table);
  });
}

// ------------------------------------------------------------ generation --

async function pollGeneration() {
  try {
    const gen = await api("/api/gen");
    if (state.gen != null && gen.generation !== state.gen) {
      location.reload();
      return;
    }
    state.gen = gen.generation;
    if (!state.scannedAt && gen.rescanned_at) {
      state.scannedAt = new Date(gen.rescanned_at * 1000).toISOString();
      updateScanTime();
    }
    $("#stale-banner").hidden = true;
  } catch {
    $("#stale-banner").hidden = false;
  }
}

// ------------------------------------------------------------- spotlight --

const spotlightTrigger = $("#spotlightTrigger");
const spotlightDialog = $("#spotlightDialog");
const spotlightInput = $("#spotlightInput");
const spotlightInputLabel = $("#spotlightInputLabel");
const spotlightResults = $("#spotlightResults");
const spotlightStatus = $("#spotlightStatus");
const shortcutDialog = $("#shortcutDialog");
let spotlightMode = "commands";
let spotlightOptions = [];
let shortcutReturnFocus = spotlightTrigger;

const shortcutPlatform = navigator.userAgentData?.platform || navigator.platform || "";
const isApple = /mac|iphone|ipad|ipod/i.test(shortcutPlatform);
const commandMod = isApple ? "⌘" : "Ctrl";
const altMod = isApple ? "⌥" : "Alt";

function appendKbd(container, label) {
  const key = document.createElement("kbd");
  key.textContent = label;
  container.appendChild(key);
}

function renderShortcutKeys(container, key, shifted, modifier) {
  container.replaceChildren();
  appendKbd(container, modifier);
  if (shifted) appendKbd(container, isApple ? "⇧" : "Shift");
  appendKbd(container, key);
}

function fillStaticShortcutKeys() {
  $$(".shortcut-keys[data-shortcut-key]").forEach(container => {
    const modifier = container.dataset.shortcutModifier === "command" ? commandMod : altMod;
    renderShortcutKeys(container, container.dataset.shortcutKey,
      container.dataset.shortcutShift === "true", modifier);
    if (container.dataset.controlSpaceAlternative === "true") {
      const sep = document.createElement("span");
      sep.className = "shortcut-alternative";
      sep.setAttribute("aria-hidden", "true");
      sep.textContent = "/";
      container.appendChild(sep);
      appendKbd(container, "Ctrl");
      appendKbd(container, "Space");
    }
  });
}
fillStaticShortcutKeys();

function commandOptions() {
  return [
    { id: "sp-jump", mode: "commands", action: "jump-to", label: "Jump to…",
      search: "jump go navigate tab view", key: "J", code: "KeyJ", shift: false },
    { id: "sp-md", mode: "commands", action: "export-markdown", label: "Export as Markdown",
      search: "export download markdown md", key: "M", code: "KeyM", shift: true },
    { id: "sp-pdf", mode: "commands", action: "export-pdf", label: "Export as PDF",
      search: "export download print pdf", key: "P", code: "KeyP", shift: true },
    { id: "sp-theme", mode: "commands", action: "toggle-theme", label: "Toggle theme",
      search: "toggle switch dark light theme appearance", key: "T", code: "KeyT", shift: true },
    { id: "sp-keys", mode: "commands", action: "shortcuts", label: "Keyboard shortcuts",
      search: "keyboard shortcuts keys commands help", key: "/", code: "Slash", shift: false },
  ];
}

function destinationOptions() {
  const tabs = state.tabs.length
    ? state.tabs
    : CORE_TABS.map(name => ({ name, kind: "core" }));
  return tabs.map((t, i) => {
    const key = tabKey(t);
    const n = i + 1;
    return {
      id: `sp-tab-${key.replace(/[^a-z0-9-]/gi, "-")}`,
      mode: "jump",
      action: "jump",
      target: key,
      label: t.name,
      search: `${t.name} ${t.kind || ""} ${t.description || ""}`,
      key: n <= 9 ? String(n) : "",
      code: n <= 9 ? `Digit${n}` : "",
      shift: false,
    };
  });
}

function rebuildSpotlightDestinations() {
  renderSpotlightOptions();
  setSpotlightMode(spotlightMode, true);
}

function renderSpotlightOptions() {
  const items = [...commandOptions(), ...destinationOptions()];
  spotlightResults.innerHTML = items.map(opt => `
    <li class="spotlight-option" id="${esc(opt.id)}" role="option" aria-selected="false"
      data-spotlight-mode="${esc(opt.mode)}" data-spotlight-action="${esc(opt.action)}"
      data-search="${esc(opt.search)}" data-shortcut-key="${esc(opt.key)}"
      data-shortcut-code="${esc(opt.code)}" data-shortcut-shift="${opt.shift ? "true" : "false"}"
      ${opt.target ? `data-spotlight-target="${esc(opt.target)}"` : ""}
      ${opt.mode === "jump" ? "hidden" : ""}>
      <span class="spotlight-option-label">${esc(opt.label)}</span>
      <span class="spotlight-shortcut" aria-hidden="true"></span>
    </li>`).join("") + `<li class="spotlight-empty" id="spotlightEmpty" hidden>No matching commands</li>`;
  spotlightOptions = $$(".spotlight-option", spotlightResults);
  spotlightOptions.forEach(option => {
    const shifted = option.dataset.shortcutShift === "true";
    const key = option.dataset.shortcutKey;
    if (key) {
      renderShortcutKeys(option.querySelector(".spotlight-shortcut"), key, shifted, altMod);
      option.setAttribute("aria-keyshortcuts",
        (shifted ? "Alt+Shift+" : "Alt+") + key);
    }
  });
}

function visibleSpotlightOptions() {
  return spotlightOptions.filter(o => !o.hidden);
}

function setActiveSpotlightOption(option) {
  spotlightOptions.forEach(candidate => {
    const active = candidate === option;
    candidate.classList.toggle("active", active);
    candidate.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (option) {
    spotlightInput.setAttribute("aria-activedescendant", option.id);
    option.scrollIntoView({ block: "nearest" });
  } else {
    spotlightInput.removeAttribute("aria-activedescendant");
  }
}

function setSpotlightMode(mode, keepQuery = false) {
  spotlightMode = mode;
  const jumping = mode === "jump";
  spotlightDialog.setAttribute("aria-label", jumping ? "Jump to a view" : "Search and commands");
  spotlightInputLabel.textContent = jumping ? "Jump to a view" : "Search commands";
  spotlightInput.placeholder = jumping ? "Jump to a view" : "Search commands";
  spotlightResults.setAttribute("aria-label", jumping ? "Views" : "Commands");
  $("#spotlightEmpty").textContent = jumping ? "No matching views" : "No matching commands";
  if (!keepQuery) spotlightInput.value = "";
  filterSpotlight();
}

function filterSpotlight() {
  const q = spotlightInput.value.trim().toLowerCase();
  let shown = 0;
  spotlightOptions.forEach(option => {
    const matchMode = option.dataset.spotlightMode === spotlightMode;
    const matchQuery = !q || option.dataset.search.toLowerCase().includes(q)
      || option.querySelector(".spotlight-option-label").textContent.toLowerCase().includes(q);
    option.hidden = !(matchMode && matchQuery);
    if (!option.hidden) shown++;
  });
  $("#spotlightEmpty").hidden = shown !== 0;
  const first = visibleSpotlightOptions()[0];
  setActiveSpotlightOption(first || null);
  spotlightStatus.textContent = `${shown} ${spotlightMode === "jump" ? "views" : "commands"}`;
}

function openSpotlight(mode = "commands") {
  if (!spotlightOptions.length) renderSpotlightOptions();
  setSpotlightMode(mode);
  spotlightTrigger.setAttribute("aria-expanded", "true");
  if (!spotlightDialog.open) spotlightDialog.showModal();
  spotlightInput.setAttribute("aria-expanded", "true");
  spotlightInput.focus();
}

function closeSpotlight() {
  if (spotlightDialog.open) spotlightDialog.close();
}

spotlightDialog.addEventListener("close", () => {
  spotlightTrigger.setAttribute("aria-expanded", "false");
  spotlightInput.setAttribute("aria-expanded", "false");
  spotlightTrigger.focus();
});
spotlightDialog.addEventListener("click", e => {
  if (e.target === spotlightDialog) closeSpotlight();
});
spotlightTrigger.addEventListener("click", () => openSpotlight("commands"));
spotlightInput.addEventListener("input", filterSpotlight);
spotlightInput.addEventListener("keydown", e => {
  const visible = visibleSpotlightOptions();
  const current = visible.find(o => o.classList.contains("active"));
  let i = visible.indexOf(current);
  if (e.key === "ArrowDown") {
    e.preventDefault();
    setActiveSpotlightOption(visible[Math.min(visible.length - 1, i + 1)] || visible[0]);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    setActiveSpotlightOption(visible[Math.max(0, i - 1)] || visible[0]);
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (current) runSpotlight(current);
  } else if (e.key === "Escape") {
    closeSpotlight();
  }
});
spotlightResults.addEventListener("click", e => {
  const option = e.target.closest(".spotlight-option");
  if (option) runSpotlight(option);
});

function downloadText(filename, contents, mimeType) {
  const url = URL.createObjectURL(new Blob([contents], { type: mimeType }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportMarkdown() {
  const panel = document.querySelector(".panel:not([hidden])");
  const title = $("#repo-name").textContent.trim();
  const tab = state.active || "overview";
  const body = (panel?.innerText || "").split("\n").map(l => l.trim()).filter(Boolean).join("\n\n");
  downloadText(`${title}-${tab}.md`, `# ${title} · ${tab}\n\n${body}\n`,
    "text/markdown;charset=utf-8");
  spawnToast("Markdown export downloaded");
}

function runSpotlight(option) {
  const action = option.dataset.spotlightAction;
  if (action === "jump-to") {
    setSpotlightMode("jump");
    return;
  }
  closeSpotlight();
  if (action === "toggle-theme") toggleTheme();
  else if (action === "shortcuts") openShortcuts();
  else if (action === "export-markdown") exportMarkdown();
  else if (action === "export-pdf") window.setTimeout(() => window.print(), 0);
  else if (action === "jump") activateTab(option.dataset.spotlightTarget);
}

function openShortcuts() {
  shortcutReturnFocus = document.activeElement;
  if (!shortcutDialog.open) shortcutDialog.showModal();
  $("#btnCloseShortcuts").focus();
}

function closeShortcuts() {
  if (shortcutDialog.open) shortcutDialog.close();
}

$("#btnCloseShortcuts").addEventListener("click", closeShortcuts);
shortcutDialog.addEventListener("click", e => {
  if (e.target === shortcutDialog) closeShortcuts();
});
shortcutDialog.addEventListener("close", () => {
  shortcutReturnFocus?.focus?.();
});

function isEditingTarget(el) {
  if (!el) return false;
  if (el === spotlightInput) return false;
  const tag = el.tagName;
  return el.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

document.addEventListener("keydown", e => {
  const metaK = (e.ctrlKey || e.metaKey) && e.code === "KeyK";
  const ctrlSpace = e.ctrlKey && e.code === "Space";
  if ((metaK || ctrlSpace) && !e.altKey) {
    e.preventDefault();
    openSpotlight("commands");
    return;
  }
  if (isEditingTarget(e.target) && e.target !== spotlightInput) return;
  if (e.altKey && !e.ctrlKey && !e.metaKey) {
    if (e.code === "KeyJ" && !e.shiftKey) {
      e.preventDefault();
      openSpotlight("jump");
    } else if (e.code === "KeyT" && e.shiftKey) {
      e.preventDefault();
      toggleTheme();
    } else if (e.code === "KeyM" && e.shiftKey) {
      e.preventDefault();
      exportMarkdown();
    } else if (e.code === "KeyP" && e.shiftKey) {
      e.preventDefault();
      window.print();
    } else if (e.code === "Slash") {
      e.preventDefault();
      openShortcuts();
    } else if (!e.shiftKey && /^Digit[1-6]$/.test(e.code)) {
      e.preventDefault();
      activateTab(CORE_TABS[Number(e.code.slice(-1)) - 1]);
    }
  }
});

renderSpotlightOptions();

// ------------------------------------------------------------------ init --

(async function init() {
  const hash = decodeURIComponent(location.hash.slice(1) || "");
  try {
    await refresh();
  } catch (err) {
    $(".panel[data-panel='overview']").innerHTML =
      `<div class="callout callout-danger"><strong>Backend error:</strong>
        <pre class="log-view">${esc(String(err))}</pre></div>`;
  }
  const initial = hash && (
    CORE_TABS.includes(hash) || hash.startsWith("m:") ||
    state.tabs.some(t => tabKey(t) === hash)
  ) ? hash : "overview";
  if (initial === "overview") showPanel("overview");
  else activateTab(initial);
  setInterval(refresh, 15000);
  setInterval(pollGeneration, 4000);
  setInterval(updateScanTime, 1000);
  await pollGeneration();

  document.addEventListener("click", ev => {
    const b = ev.target.closest("#tabs button");
    if (!b) return;
    activateTab(b.dataset.tab);
  });

  document.addEventListener("keydown", ev => {
    const current = ev.target.closest("#tabs button");
    if (!current || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(ev.key)) return;
    const tabs = [...document.querySelectorAll("#tabs button")];
    let index = tabs.indexOf(current);
    if (ev.key === "Home") index = 0;
    else if (ev.key === "End") index = tabs.length - 1;
    else index = (index + (ev.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    ev.preventDefault();
    tabs[index].focus();
    tabs[index].click();
  });
})();
