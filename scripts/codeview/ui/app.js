/* codeview frontend: fetches /api/summary + /api/section/*, renders core tabs.
   Module tabs are served at /m/<name>/ and framed on demand. */
"use strict";

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => [...(root || document).querySelectorAll(sel)];
const fmt = n => n == null ? "—" : Number(n).toLocaleString("en-US");
const UI = window.CodeviewUI;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const CORE_TABS = ["overview", "history", "churn", "files", "tree", "deps", "logs"];

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

function currentTheme() {
  return htmlEl.dataset.theme === "light" ? "light" : "dark";
}

// Module tabs are sandboxed without allow-same-origin, so their frames have an
// opaque origin and cannot read the stored theme themselves — localStorage
// throws in there. The host hands the theme over instead: in the frame's src
// for first paint, then by postMessage on every toggle.
function postTheme(frame, theme) {
  try {
    frame.contentWindow.postMessage({ type: "codeview-theme", theme }, "*");
  } catch { /* frame not ready yet; its load handler re-sends */ }
}

function broadcastTheme(theme) {
  document.querySelectorAll("iframe.module-frame")
    .forEach(frame => postTheme(frame, theme));
}

function applyTheme(theme) {
  htmlEl.dataset.theme = theme;
  const light = theme === "light";
  themeIconUse.setAttribute("href", light ? "#lucide-moon" : "#lucide-sun");
  const label = light ? "Switch to dark theme" : "Switch to light theme";
  themeToggle.setAttribute("aria-label", label);
  themeToggle.setAttribute("title", label);
  try { localStorage.setItem("codeview-theme", theme); } catch { /* */ }
  broadcastTheme(theme);
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
  const text = String(value ?? "");
  const trigger = document.activeElement;
  const dialog = trigger?.closest("dialog");
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch { /* selection fallback also works inside native dialogs */ }
  if (!copied) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;opacity:0";
    (dialog || document.body).appendChild(area);
    area.select();
    try { copied = document.execCommand("copy"); } catch { copied = false; }
    finally { area.remove(); trigger?.focus?.({ preventScroll: true }); }
  }
  const message = copied ? "Copied to clipboard" : "Could not copy to clipboard";
  spawnToast(message, !copied);
  if (dialog) {
    let status = dialog.querySelector(".copy-status");
    if (!status) { status = document.createElement("p"); status.className = "stats-note copy-status"; status.setAttribute("role", "status"); dialog.appendChild(status); }
    status.textContent = message;
  }
  return copied;
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
    aria-label="Copy commit hash ${esc(full)}"${short === full ? ` title="${esc(title)}"` : ""}>${esc(short)}</button>`;
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
let hashTooltipTrigger = null;

function hideHashTooltip() {
  window.clearTimeout(hashTooltipTimer);
  if (!hashTooltipEl) return;
  hashTooltipTrigger?.removeAttribute("aria-describedby");
  hashTooltipEl.remove();
  hashTooltipEl = null;
  hashTooltipTrigger = null;
}

function positionHashTooltip() {
  if (!hashTooltipEl) return;
  if (!hashTooltipTrigger?.isConnected) { hideHashTooltip(); return; }
  const bubble = hashTooltipEl;
  const viewport = window.visualViewport;
  const vx = viewport?.offsetLeft || 0, vy = viewport?.offsetTop || 0;
  const width = viewport?.width || window.innerWidth, height = viewport?.height || window.innerHeight;
  bubble.style.maxWidth = `${Math.min(360, width - 24)}px`;
  const rect = hashTooltipTrigger.getBoundingClientRect();
  const tw = bubble.offsetWidth, th = bubble.offsetHeight;
  let left = rect.left + (rect.width - tw) / 2;
  let top = rect.top - th - 8;
  left = Math.min(Math.max(vx + 12, left), vx + width - tw - 12);
  if (top < vy + 12) top = rect.bottom + 8;
  top = Math.max(vy + 12, Math.min(top, vy + height - th - 12));
  bubble.style.left = `${left}px`;
  bubble.style.top = `${top}px`;
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
  (trigger.closest("dialog[open]") || document.body).appendChild(bubble);
  hashTooltipEl = bubble;
  hashTooltipTrigger = trigger;
  trigger.setAttribute("aria-describedby", bubble.id);
  positionHashTooltip();
}

window.addEventListener("scroll", positionHashTooltip, true);
window.addEventListener("resize", positionHashTooltip);
window.visualViewport?.addEventListener("scroll", positionHashTooltip);
window.visualViewport?.addEventListener("resize", positionHashTooltip);

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
    table.dispatchEvent(new Event("codeview:sorted"));
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
  table.addEventListener("codeview:columns", () => {
    sorts = sorts.filter(column => !column.header.hidden);
    columns.forEach(column => { if (column.header.hidden) column.direction = null; });
    apply();
  });
  apply();
  return {
    reset() {
      sorts = [];
      columns.forEach(column => { column.direction = null; });
      apply();
    },
    refresh() {
      original = [...table.querySelectorAll("tbody tr")];
      apply();
    },
  };
}

const rowActivationControllers = new Map();

function bindRowActivation(table, onActivate) {
  rowActivationControllers.get(table.id)?.abort();
  const ac = new AbortController();
  rowActivationControllers.set(table.id, ac);
  const { signal } = ac;
  const rows = () => [...table.querySelectorAll("tbody tr")]
    .filter(r => !r.hidden && r.style.display !== "none");
  const buttons = () => rows().map(r => r.querySelector(".table-row-trigger")).filter(Boolean);

  function setTabStops(active) {
    table.querySelectorAll(".table-row-trigger").forEach(btn => { btn.tabIndex = btn === active ? 0 : -1; });
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
      if (btn) { btn.focus({ preventScroll: true }); onActivate(btn); }
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
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
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
  function clearSelection() {
    table.querySelectorAll("tbody tr").forEach(r => r.classList.remove("is-selected"));
  }
  document.addEventListener("pointerdown", e => {
    if (!table.contains(e.target)) {
      clearSelection();
      if (table.contains(document.activeElement)) document.activeElement.blur();
    }
  }, { signal });
  table.addEventListener("focusout", e => {
    if (!table.contains(e.relatedTarget)) clearSelection();
  }, { signal });
  table.addEventListener("codeview:rows", () => {
    const list = buttons();
    setTabStops(list.find(btn => btn.tabIndex === 0) || list[0]);
  }, { signal });
  return {
    refresh() {
      const list = buttons();
      setTabStops(list.find(btn => btn.tabIndex === 0) || list[0]);
    },
  };
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

function barRows(entries, interactive = false) {
  const max = Math.max(1, ...entries.map(e => Math.abs(e[1])));
  const tag = interactive ? "button" : "div";
  return `<div class="rank-list">${entries.map(([label, value]) => `
    <${tag} class="rank-row"${interactive ? ` type="button" data-rank="${esc(label)}"` : ""}>
      <span class="rank-label" title="${esc(label)}">${esc(label)}</span>
      <span class="rank-track"><span class="rank-fill" style="display:block;
        width:${Math.max(1, (Math.abs(value) / max) * 100)}%"></span></span>
      <span class="rank-value">${fmt(value)}</span>
    </${tag}>`).join("")}</div>`;
}

function metric(label, value, view = "") {
  const tag = view ? "button" : "div";
  return `<${tag} class="metric-card${view ? " metric-action" : ""}"${view ? ` type="button" data-view="${esc(view)}"` : ""}>
    <span class="metric-label">${esc(label)}</span><span class="metric-num">${value}</span></${tag}>`;
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

function statGroup(title, body, expanded = false) {
  return `<details class="stats-group"${expanded ? " open" : ""}>
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
let tabGestures = null;

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
  tabGestures?.reset();
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
  const theme = currentTheme();
  const frame = document.createElement("iframe");
  frame.className = "module-frame";
  frame.title = slug;
  frame.setAttribute("sandbox", "allow-scripts allow-forms");
  // A toggle fired while the frame is still loading lands before the module's
  // listener exists, so re-send the current theme once it is ready.
  frame.addEventListener("load", () => postTheme(frame, currentTheme()));
  frame.src = `/m/${encodeURIComponent(slug)}/?theme=${theme}`;
  panel.replaceChildren(frame);
}

function activateTab(key) {
  showPanel(key);
  const render = { overview: refresh, history: renderHistory, churn: renderChurn,
    files: renderFiles, tree: renderTree, deps: renderDeps, logs: renderLogs }[key];
  if (!render) return;
  const panel = document.querySelector(`.panel[data-panel="${key}"]`);
  panel.setAttribute("aria-busy", "true");
  render().catch(error => {
    panel.innerHTML = `<div class="callout callout-danger"><strong>Could not load ${esc(key)}:</strong>
      ${esc(error.message)} <button type="button" class="btn btn-secondary" data-retry>Retry</button></div>`;
    panel.querySelector("[data-retry]").onclick = () => activateTab(key);
  }).finally(() => panel.removeAttribute("aria-busy"));
}

window.addEventListener("hashchange", () => {
  const key = location.hash.slice(1);
  if (state.tabs.some(tab => tabKey(tab) === key)) activateTab(key);
});

function renderTabs(tabs) {
  tabGestures?.reset();
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
  const [historyData, filesData] = await Promise.all([
    api("/api/section/history").catch(() => ({})),
    api("/api/section/files").catch(() => ({})),
  ]);
  const allFiles = filesData.files || [];
  const tracked = new Set(allFiles.map(file => file.path));
  const hotspots = (historyData.churn?.files || []).filter(file => tracked.has(file.path)).slice(0, 5);
  const attention = allFiles.filter(file => file.metrics?.todo_count > 0)
    .sort((a, b) => b.metrics.todo_count - a.metrics.todo_count).slice(0, 5);
  const recent = (historyData.commits || []).slice(-8).reverse();
  const mt = s.metric_totals || {};
  const langEntries = Object.entries(s.langs || {})
    .sort((a, b) => b[1].lines - a[1].lines);
  const topEntries = Object.entries(s.tops || {})
    .sort((a, b) => b[1].lines - a[1].lines);
  const langs = langEntries.slice(0, 4);
  const tops = topEntries.slice(0, 4).map(([k, v]) => [k === "." ? "(root)" : k, v.lines]);
  const langCount = langEntries.length;
  const ecosystems = s.ecosystems || [];
  const summaries = {
    overview: `${fmt(langCount)} languages · ${fmt(mt.functions)} functions · ${fmt(mt.todo_count)} attention markers`,
    history: `${fmt(s.commits_count)} commits scanned · Cumulative line history`,
    churn: `${fmt(s.churn?.total)} lines changed · ${fmt(s.churn?.file_count)} files`,
    files: `${fmt(s.total_files)} indexed files · ${fmt(s.total_lines)} physical lines`,
    tree: `${fmt(s.indexed_folders)} folders · ${fmt(s.total_files)} indexed files`,
    deps: ecosystems.length
      ? `${fmt(ecosystems.reduce((n, e) => n + (e.declared_count || 0), 0))} declared · ${fmt(ecosystems.reduce((n, e) => n + (e.locked_count || 0), 0))} lock entries · ${ecosystems.map(e => e.name).join(", ")}`
      : "No supported root manifests",
    logs: `${fmt(s.log_lines)} buffered lines · Server activity`,
  };
  const viewSummaries = state.tabs.map(tab => {
    const key = tabKey(tab);
    const summary = tab.kind === "core" ? summaries[tab.name]
      : tab.kind === "broken" ? "Module failed to load · Open for error details"
      : tab.description || "Project dashboard module";
    return `<li><a class="view-summary" href="#${esc(key)}" data-view="${esc(key)}"
        ${key === "overview" ? 'aria-current="page"' : ""}>
      <span class="view-summary-name">${esc(tab.name)}</span>
      <span class="view-summary-copy">${esc(summary || "Repository view")}</span>
    </a></li>`;
  }).join("");
  panel.innerHTML = `
    <div class="panel-stack overview-compact">
      ${metricGrid([
        metric("Source lines", fmt(mt.code_lines ?? s.total_lines), "files"),
        metric("Tracked files", fmt(s.total_files), "tree"),
        metric("Languages", fmt(langCount), "files"),
        metric("Commits scanned", fmt(s.commits_count), "history"),
      ], "overview-metrics")}
      ${s.meta?.empty_repo ? emptyCallout("Empty repository", "No commits yet. Indexed files appear after they are added to git.") : ""}
      <div class="overview-board">
      <div class="rank-grid">
        <section>
          ${sectionHeading("Lines by top-level dir")}
          <div data-overview-directories>${tops.length ? barRows(tops, true) : emptyCallout("No directories", "No top-level directories to show.")}</div>
        </section>
        <section>
          ${sectionHeading("Lines by language")}
          ${langs.length
            ? `<div data-overview-languages>${barRows(langs.map(([k, v]) => [k, v.lines]), true)}</div>`
            : emptyCallout("No languages", "No languages detected.")}
        </section>
      </div>
      <section>
        ${sectionHeading("Churn hotspots")}
        <ul class="compact-list">${hotspots.map(file => `<li><button type="button" class="compact-record" data-file="${esc(file.path)}"
          aria-label="Inspect ${esc(file.path)}"><span class="record-copy" title="${esc(file.path)}">${esc(file.path)}</span>
          <span class="record-meta">${fmt(file.churn)} lines</span></button></li>`).join("") || '<li class="stats-note">No textual file changes.</li>'}</ul>
        <h2 class="stats-subhead">Attention markers</h2>
        <ul class="compact-list">${attention.map(file => `<li><button type="button" class="compact-record" data-file="${esc(file.path)}"
          aria-label="Inspect ${esc(file.path)}"><span class="record-copy" title="${esc(file.path)}">${esc(file.path)}</span>
          <span class="record-meta">${fmt(file.metrics.todo_count)} markers</span></button></li>`).join("") || '<li class="stats-note">No TODO, FIXME, or HACK markers.</li>'}</ul>
      </section>
      <section>
        ${sectionHeading("Recent commits")}
        <ul class="compact-list">${recent.map(commit => `<li><button type="button" class="compact-record" data-commit="${esc(commit.sha)}"
          aria-label="Inspect ${esc(commit.subject)}"><span class="record-copy" title="${esc(commit.subject)}">${esc(commit.subject)}</span>
          <span class="record-meta">${commit.delta >= 0 ? "+" : ""}${fmt(commit.delta)}</span></button></li>`).join("") || '<li class="stats-note">No commits yet.</li>'}</ul>
      </section>
      </div>
      <details class="overview-views"><summary>Repository views · ${state.tabs.length}</summary>
        <nav aria-label="View summaries"><ul class="view-summary-list">${viewSummaries}</ul></nav>
      </details>
    </div>`;
  panel.querySelectorAll("[data-view]").forEach(link => {
    link.addEventListener("click", event => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      activateTab(link.dataset.view);
      [...$("#tabs").children].find(tab => tab.dataset.tab === link.dataset.view)?.focus();
    });
  });
  panel.querySelectorAll("[data-overview-directories] [data-rank]").forEach(button => {
    button.onclick = () => browseFiles("Directory", button.dataset.rank);
  });
  panel.querySelectorAll("[data-overview-languages] [data-rank]").forEach(button => {
    button.onclick = () => browseFiles("Language", button.dataset.rank);
  });
  panel.querySelectorAll("[data-file]").forEach(button => {
    button.onclick = () => openFile(button.dataset.file, button);
  });
  panel.querySelectorAll("[data-commit]").forEach(button => {
    button.onclick = () => inspectCommit(recent.find(commit => commit.sha === button.dataset.commit), button);
  });
}

let repositoryTree = null;
async function renderTree() {
  const panel = $('.panel[data-panel="tree"]');
  try {
    const data = await api("/api/section/files");
    if (!repositoryTree) repositoryTree = window.CodeviewTree(panel,
      { esc, fmt, iconUse, searchField, bindSearch, openFile });
    repositoryTree.update(data.files || []);
  } catch (error) {
    if (!repositoryTree) {
      panel.innerHTML = `${emptyCallout("Could not load tree", String(error))}
        <button type="button" class="btn btn-secondary" id="tree-retry">Retry</button>`;
      $("#tree-retry").addEventListener("click", renderTree);
    }
  }
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
  const recent = commits.slice().reverse();
  panel.innerHTML = `
    <div class="analysis-layout history-layout">
    <aside class="analysis-sidebar">
    <section>
      ${sectionHeading("Cumulative lines", `${fmt(commits.length)} commits scanned`)}
      <div class="chart-box history-preview">
        <div class="chart-frame">
          <button type="button" class="chart-surface"
            aria-label="Explore cumulative lines over ${fmt(commits.length)} commits"
            aria-describedby="historyPreviewSummary"></button>
          <div class="chart-tooltip" hidden></div>
          <div class="sr-only" aria-live="polite"></div>
        </div>
        <p class="sr-only" id="historyPreviewSummary">${fmt(commits[0].total)} to ${fmt(commits.at(-1).total)} cumulative lines in commit order, ${esc(fmtDate(commits[0].date))} to ${esc(fmtDate(commits.at(-1).date))}. Open for range controls, exact data, and CSV export.</p>
      </div>
    </section>
    <section>
      ${sectionHeading("Most-changed dirs")}
      ${hotDirs.length ? barRows(hotDirs, true) : emptyCallout("No directories", "No directory changes to show.")}
    </section>
    </aside>
    <section class="analysis-main">
      ${sectionHeading("Recent commits")}
      <div class="collection-toolbar" id="history-toolbar">${searchField("history-filter", "Filter commits")}</div>
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
              <tr data-commit="${esc(c.sha)}">
                <td data-sort-value="${esc(c.date)}">${esc(fmtDate(c.date))}</td>
                <td data-sort-value="${esc(c.sha)}">${shaButton(c.sha)}</td>
                <td class="wrap"><button type="button" class="table-row-trigger" data-commit="${esc(c.sha)}"
                  aria-label="Inspect commit ${esc(c.subject)}">${esc(c.subject)}</button></td>
                <td class="num" data-sort-value="${c.delta}">${c.delta >= 0 ? "+" : ""}${fmt(c.delta)}</td>
                <td class="num">${fmt(c.total)}</td>
              </tr>`).join("")}
          </tbody>
        </table>
      </div>
    </section></div>`;
  window.CodeviewHistoryChart(panel.querySelector(".chart-box"), commits,
    { esc, fmt, fmtDate, sortHeader, makeTableSortable, downloadText, shaButton });
  makeTableSortable($("#history-table"));
  UI.collection($("#history-table"), { toolbar: $("#history-toolbar"), search: $("#history-filter"), label: "commits" });
  bindSearch($("#history-filter"), () => {});
  bindRowActivation($("#history-table"), button => {
    inspectCommit(commits.find(commit => commit.sha === button.dataset.commit), button);
  });
  panel.querySelectorAll("[data-rank]").forEach(button => button.onclick = () => {
    nextChurnDirectory = button.dataset.rank;
    activateTab("churn");
  });
}

function inspectCommit(commit, trigger) {
  UI.inspect(commit.subject, `${statRows([
    ["Date", fmtDate(commit.date)], ["Net lines", fmt(commit.delta)],
    ["Cumulative lines", fmt(commit.total)],
  ])}<p class="inspection-description">${shaButton(commit.sha)}</p>
    <h3 class="stats-subhead">Net change by directory</h3>
    ${Object.keys(commit.dirs || {}).length ? barRows(Object.entries(commit.dirs)) : '<p class="stats-note">No textual directory changes.</p>'}`, trigger);
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
      <div class="analysis-layout">
      <aside class="analysis-sidebar"><section>
        ${sectionHeading("Churn by top-level dir")}
        ${dirs.length
          ? barRows(dirs.map(d => [d.name, d.churn]), true)
          : emptyCallout("No directories", "No directories to show.")}
      </section></aside>
      <section class="analysis-main">
      ${sectionHeading("Highest-churn files")}
      <div class="collection-toolbar" id="churn-toolbar">
        ${searchField("churn-filter", "Filter hotspots")}
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
              return `<tr class="${tracked ? "" : "is-muted"}" data-path="${esc(file.path)}" data-current="${tracked}"
                  data-directory="${esc(file.path.includes("/") ? file.path.split("/")[0] : "(root)")}">
                <td class="wrap" data-sort-value="${esc(file.path)}"><button type="button" class="table-row-trigger" data-path="${esc(file.path)}"
                  aria-label="Inspect ${esc(file.path)}">${esc(file.path)}</button>
                  ${tracked ? "" : '<span class="badge badge-nominal">historical</span>'}</td>
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
      </section></div>
    </div>`;

  makeTableSortable($("#churn-table"));
  const input = $("#churn-filter");
  const controls = UI.collection($("#churn-table"), {
    toolbar: $("#churn-toolbar"), search: input, label: "hotspots", filters: [
      { label: "Directory", all: "All directories", options: (churn.dirs || []).map(dir => dir.name),
        match: (row, value) => row.dataset.directory === value },
      { label: "Files", all: "Current + historical", options: ["Current", "Historical"],
        match: (row, value) => row.dataset.current === String(value === "Current") },
    ],
    onUpdate: (rows, chosen) => panel.querySelectorAll("[data-rank]").forEach(button => {
      button.setAttribute("aria-pressed", String(button.dataset.rank === chosen.get("Directory")));
    }),
  });
  bindSearch(input, () => {});
  bindRowActivation($("#churn-table"), btn => {
    if (trackedPaths.has(btn.dataset.path)) openFile(btn.dataset.path, btn).catch(console.error);
    else {
      const file = hotspots.find(file => file.path === btn.dataset.path);
      UI.inspect(file.path, `<p class="stats-note">Historical file in the scanned commit window.</p>${statRows([
        ["Commits", fmt(file.commits)], ["Lines added", fmt(file.additions)],
        ["Lines deleted", fmt(file.deletions)], ["Churn", fmt(file.churn)],
        ["Last change", fmtDate(file.last_date)],
      ])}`, btn);
    }
  });
  panel.querySelectorAll("[data-rank]").forEach(button => button.onclick = () => {
    controls.setFilter("Directory", button.dataset.rank);
  });
  if (nextChurnDirectory) {
    controls.setFilter("Directory", nextChurnDirectory === "." ? "(root)" : nextChurnDirectory);
    nextChurnDirectory = null;
  }
}

let nextFileFilter = null;
let nextChurnDirectory = null;

function browseFiles(label, value) {
  nextFileFilter = { label, value };
  activateTab("files");
}

async function renderFiles() {
  const filesData = await api("/api/section/files");
  const panel = $('.panel[data-panel="files"]');
  const files = filesData.files || [];
  if (!files.length) {
    panel.innerHTML = emptyCallout("No tracked files", "This repository has no tracked files to inspect.");
    return;
  }
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
      <div class="collection-toolbar" id="file-toolbar">
        ${searchField("file-filter", "Filter files")}
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
              return `<tr data-path="${esc(f.path)}" data-lang="${esc(f.lang)}"
                data-directory="${esc(f.path.includes("/") ? f.path.split("/")[0] : "(root)")}"
                data-markers="${m.todo_count || 0}" data-complexity="${m.cyclomatic_complexity || 0}">
                <td class="wrap" data-sort-value="${esc(f.path)}">
                  <button type="button" class="table-row-trigger" data-path="${esc(f.path)}"
                    aria-label="Open file profile ${esc(f.path)}">${esc(f.path)}</button></td>
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
  bindRowActivation($("#file-table"), btn => {
    openFile(btn.dataset.path, btn).catch(console.error);
  });
  const controls = UI.collection($("#file-table"), {
    toolbar: $("#file-toolbar"), search: input, label: "files", filters: [
      { label: "Language", all: "All languages", options: [...new Set(files.map(file => file.lang))].sort(),
        match: (row, value) => row.dataset.lang === value },
      { label: "Directory", all: "All directories", options: [...new Set(files.map(file =>
        file.path.includes("/") ? file.path.split("/")[0] : "(root)"))].sort(),
        match: (row, value) => row.dataset.directory === value },
      { label: "Attention", all: "All files", options: ["Has markers", "Complexity ≥ 20"],
        match: (row, value) => value === "Has markers" ? Number(row.dataset.markers) > 0 : Number(row.dataset.complexity) >= 20 },
    ], presets: [
      { value: "summary", label: "Summary", columns: [0, 1, 2, 3, 8] },
      { value: "structure", label: "Structure", columns: [0, 1, 6, 7, 8] },
      { value: "composition", label: "Composition", columns: [0, 2, 3, 4, 5, 9] },
      { value: "all", label: "All metrics", columns: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] },
    ],
  });
  bindSearch(input, () => {});
  if (nextFileFilter) {
    controls.setFilter(nextFileFilter.label, nextFileFilter.value);
    nextFileFilter = null;
  }
}

const fileDialog = $("#fileDialog");
let fileReturnFocus = null;
let fileLoad = 0;

async function openFile(path, trigger = document.activeElement) {
  const request = ++fileLoad;
  fileReturnFocus = trigger;
  const body = $("#fileDialogBody");
  $("#fileDialogTitle").textContent = path;
  body.innerHTML = `<div class="loading">Loading ${esc(path)}…</div>`;
  body.setAttribute("aria-busy", "true");
  if (!fileDialog.open) fileDialog.showModal();
  $("#btnCloseFile").focus({ preventScroll: true });
  try {
    const d = await api(`/api/file?path=${encodeURIComponent(path)}`);
    if (request !== fileLoad || !fileDialog.open) return;
    body.innerHTML = fileView(d);
    body.querySelector('[data-source-action="path"]').onclick = () => copyText(d.path);
    body.querySelector('[data-source-action="copy"]').onclick = () => copyText(d.content || "");
    body.querySelector('[data-source-action="download"]').onclick = () => UI.download(d.path.split("/").pop(), d.content || "");
    let matches = [];
    let cursor = -1;
    const search = body.querySelector("#source-filter");
    const status = body.querySelector("#source-matches");
    function findNext(direction) {
      if (!matches.length) return;
      cursor = (cursor + direction + matches.length) % matches.length;
      matches[cursor].scrollIntoView({ block: "center", inline: "nearest" });
      status.textContent = `${cursor + 1} of ${matches.length} matching lines`;
    }
    bindSearch(search, query => {
      matches = [];
      body.querySelectorAll(".source-line").forEach(line => {
        const hit = Boolean(query) && line.querySelector(".source-text").textContent.toLowerCase().includes(query.toLowerCase());
        line.classList.toggle("is-match", hit);
        if (hit) matches.push(line);
      });
      cursor = -1;
      status.textContent = query ? `${matches.length} matching lines` : "";
      body.querySelector('[data-source-action="next"]').disabled = matches.length === 0;
      body.querySelector('[data-source-action="previous"]').disabled = matches.length === 0;
      if (matches.length) findNext(1);
    });
    search.onkeydown = event => {
      if (event.key === "Enter") { event.preventDefault(); findNext(event.shiftKey ? -1 : 1); }
    };
    body.querySelector('[data-source-action="next"]').onclick = () => findNext(1);
    body.querySelector('[data-source-action="previous"]').onclick = () => findNext(-1);
    body.querySelector(".file-code").focus({ preventScroll: true });
  } catch (error) {
    if (request === fileLoad && fileDialog.open) {
      body.innerHTML = emptyCallout("Could not load file", String(error));
    }
  } finally {
    if (request === fileLoad) body.removeAttribute("aria-busy");
  }
}

$("#btnCloseFile").addEventListener("click", () => fileDialog.close());
fileDialog.addEventListener("click", event => { if (event.target === fileDialog) fileDialog.close(); });
fileDialog.addEventListener("keydown", event => {
  if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); fileDialog.close(); }
});
fileDialog.addEventListener("close", () => {
  fileLoad++;
  $("#fileDialogBody").removeAttribute("aria-busy");
  // Native dialogs normally restore focus immediately. The queued close event
  // must not undo a keyboard move made just after dismissal.
  if (document.activeElement === document.body || fileDialog.contains(document.activeElement)) {
    fileReturnFocus?.focus?.({ preventScroll: true });
  }
});

function fileView(d) {
  const s = d.stats || {};
  const m = d.metrics || {};
  const h = m.halstead || {};
  const lc = s.last_commit;
  const markerSummary = Object.entries(m.todo_markers || {})
    .map(([name, count]) => `${name} ${fmt(count)}`).join(" · ") || "none";
  let code;
  if (d.binary) {
    code = emptyCallout("Binary file", "Content is not shown.");
  } else {
    const lines = (d.content ?? "").split("\n");
    code = `<pre>${lines.map((l, i) =>
      `<span class="source-line" data-line="${i + 1}"><span class="ln">${i + 1}</span><span class="source-text">${esc(l) || " "}</span></span>`).join("")}</pre>`;
  }
  return `
    <div class="file-toolbar">
      <span class="section-note">${esc(d.lang || "unknown")} · ${fmt(d.bytes)} bytes</span>
      ${d.truncated ? `<span class="badge badge-caution">Truncated</span>` : ""}
      <div class="source-actions">
        <button type="button" class="btn btn-quiet" data-source-action="path">Copy path</button>
        <button type="button" class="btn btn-quiet" data-source-action="copy"${d.binary ? " disabled" : ""}>Copy ${d.truncated ? "preview" : "source"}</button>
        <button type="button" class="btn btn-quiet" data-source-action="download"${d.binary ? " disabled" : ""}>Download ${d.truncated ? "preview" : "source"}</button>
      </div>
    </div>
    <div class="collection-toolbar source-find">
      ${searchField("source-filter", "Find in source")}
      <button type="button" class="btn-icon" data-source-action="previous" aria-label="Previous match" title="Previous match" disabled>${UI.icon("left")}</button>
      <button type="button" class="btn-icon" data-source-action="next" aria-label="Next match" title="Next match" disabled>${UI.icon("right")}</button>
      <span class="filter-count" id="source-matches" aria-live="polite"></span>
    </div>
    <div class="file-grid">
      <div class="file-code" tabindex="0" role="region" aria-label="${esc(d.path)} source">${code}</div>
      <aside class="file-stats">
        <div class="stats-hero">
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
        ]), true)}
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

const logState = { follow: true, lines: [], event: "", limit: 500, visible: [] };

async function renderLogs(force = false) {
  const panel = $('.panel[data-panel="logs"]');
  if (!logState.follow && !force && panel.querySelector(".log-lines")) return;
  const data = await api("/api/logs");
  logState.lines = data.logs || [];
  if (!panel.querySelector(".log-lines")) {
    panel.innerHTML = `<div class="panel-stack">
      <div class="collection-toolbar log-toolbar" id="log-toolbar">
        ${searchField("log-filter", "Filter log messages")}
        <button type="button" class="btn btn-secondary" id="log-follow" aria-pressed="true">Pause live</button>
        <button type="button" class="btn btn-quiet" id="log-refresh">Refresh</button>
        <button type="button" class="btn btn-quiet" id="log-copy">Copy visible</button>
        <button type="button" class="btn btn-quiet" id="log-download">Download</button>
      </div>
      <span class="filter-count" id="log-count" aria-live="polite"></span>
      <div class="log-lines" role="region" aria-label="Server log messages" tabindex="0"></div>
    </div>`;
    const toolbar = $("#log-toolbar");
    toolbar.insertBefore(UI.picker("Events", ["All events", "Requests", "Scans", "Watcher", "Errors / failures"], "All events", value => {
      logState.event = value; paintLogs();
    }), $("#log-follow"));
    toolbar.insertBefore(UI.picker("Window", [{ value: 100, label: "Latest 100" }, { value: 500, label: "Latest 500" },
      { value: 5000, label: "All buffered lines" }], logState.limit, value => { logState.limit = value; paintLogs(); }), $("#log-follow"));
    bindSearch($("#log-filter"), paintLogs);
    $("#log-follow").onclick = () => {
      logState.follow = !logState.follow;
      $("#log-follow").textContent = logState.follow ? "Pause live" : "Resume live";
      $("#log-follow").setAttribute("aria-pressed", String(logState.follow));
      if (logState.follow) renderLogs(true);
    };
    $("#log-refresh").onclick = () => renderLogs(true);
    $("#log-copy").onclick = () => copyText(logState.visible.join("\n"));
    $("#log-download").onclick = () => UI.download("codeview-logs.txt", logState.visible.join("\n"));
    panel.querySelector(".log-lines").onclick = event => {
      const button = event.target.closest("[data-log-index]");
      if (!button) return;
      const line = logState.visible[Number(button.dataset.logIndex)];
      const dialog = UI.inspect("Log entry", `<pre class="inspection-code">${esc(line)}</pre>
        <button type="button" class="btn btn-secondary" data-log-copy>Copy entry</button>`, button);
      dialog.querySelector("[data-log-copy]").onclick = () => copyText(line);
    };
    panel.querySelector(".log-lines").onkeydown = event => {
      const item = event.target.closest("[data-log-index]");
      if (!item || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const items = [...panel.querySelectorAll("[data-log-index]")];
      let index = items.indexOf(item);
      index = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
        : Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)));
      items.forEach((button, offset) => { button.tabIndex = offset === index ? 0 : -1; });
      items[index].focus();
    };
  }
  paintLogs();
}

function paintLogs() {
  const panel = $('.panel[data-panel="logs"]');
  const query = $("#log-filter").value.trim().toLowerCase();
  const patterns = { Requests: /\b(GET|POST|PUT|DELETE|PATCH)\b/, Scans: /scan|cache|reload/i,
    Watcher: /watch|restart|generation/i, "Errors / failures": /error|fail|exception|traceback/i };
  const matched = logState.lines.filter(line => (!query || line.toLowerCase().includes(query))
    && (!patterns[logState.event] || patterns[logState.event].test(line)));
  logState.visible = matched.slice(-logState.limit);
  $("#log-count").textContent = `${fmt(logState.visible.length)} of ${fmt(matched.length)} matching lines · newest last`;
  const view = panel.querySelector(".log-lines");
  view.innerHTML = logState.visible.map((line, index) => `<button type="button" class="log-line" data-log-index="${index}" tabindex="${index === 0 ? 0 : -1}"
    data-level="${/error|fail|exception|traceback/i.test(line) ? "error" : /warn/i.test(line) ? "warning" : "info"}"
    aria-label="Inspect log entry ${index + 1}">${esc(line)}</button>`).join("") || '<p class="stats-note">No log messages match these filters.</p>';
  if (logState.follow) view.scrollTop = view.scrollHeight;
}

async function renderDeps() {
  const deps = await api("/api/section/deps");
  const panel = $('.panel[data-panel="deps"]');
  if (!(deps.ecosystems || []).length) {
    panel.innerHTML = emptyCallout("No manifests", "No dependency manifests found in this repo.");
    return;
  }
  const packages = [];
  deps.ecosystems.forEach((eco, ecosystem) => {
    const declared = eco.declared || [];
    const locked = eco.locked || [];
    const names = new Set(declared.map(item => item.name));
    declared.forEach(item => packages.push({ ...item, eco, ecosystem, scope: "Declared",
      versions: [...new Set(locked.filter(entry => entry.name === item.name).map(entry => entry.version))] }));
    locked.filter(item => !names.has(item.name)).forEach(item => packages.push({ ...item, eco, ecosystem,
      scope: "Lock only", kind: "lock entry", req: "—", versions: [item.version] }));
  });
  panel.innerHTML = `<div class="panel-stack dependency-board">
    ${metricGrid([
      metric("Ecosystems", fmt(deps.ecosystems.length)),
      metric("Declared entries", fmt(packages.filter(item => item.scope === "Declared").length)),
      metric("Lock-only entries", fmt(packages.filter(item => item.scope === "Lock only").length)),
    ])}
    <div class="collection-toolbar" id="deps-toolbar">${searchField("deps-filter", "Filter packages")}</div>
    <div class="table-container" role="region" aria-label="Dependency inventory">
      <table class="data-table" id="deps-table"><caption class="sr-only">Declared and scanned lockfile entries</caption>
      <thead><tr>${sortHeader("package", "text")}${sortHeader("ecosystem", "text")}${sortHeader("kind", "text")}
        ${sortHeader("requirement", "text")}${sortHeader("locked version", "text")}</tr></thead>
      <tbody>${packages.map((item, index) => `<tr data-package="${index}" data-ecosystem="${item.ecosystem}"
        data-scope="${item.scope}" data-missing="${Boolean(item.eco.lockfile && !item.versions.length)}">
        <td><button type="button" class="table-row-trigger" data-package="${index}" aria-label="Inspect ${esc(item.name)}">${esc(item.name)}</button></td>
        <td>${esc(item.eco.name)}</td><td>${esc(item.kind)}</td><td class="wrap">${esc(item.req)}</td>
        <td>${item.versions.length ? `<span class="meta-tag">${esc(item.versions.join(", "))}</span>`
          : `<span class="badge badge-${item.eco.lockfile ? "caution" : "nominal"}">${item.eco.lockfile ? "not in scanned lock" : "no lockfile"}</span>`}</td>
      </tr>`).join("")}</tbody></table>
    </div>
    <p class="stats-note">Lock inventories include up to 500 entries per ecosystem. Lock-only entries are not a dependency graph.</p>
  </div>`;
  const table = $("#deps-table");
  makeTableSortable(table);
  UI.collection(table, { toolbar: $("#deps-toolbar"), search: $("#deps-filter"), label: "packages", filters: [
    { label: "Ecosystem", all: "All ecosystems", options: deps.ecosystems.map((eco, index) => ({ value: String(index), label: eco.name })),
      match: (row, value) => row.dataset.ecosystem === value },
    { label: "Scope", all: "Declared + locked", options: ["Declared", "Lock only", "Missing lock entry"],
      match: (row, value) => value === "Missing lock entry" ? row.dataset.missing === "true" : row.dataset.scope === value },
  ] });
  bindSearch($("#deps-filter"), () => {});
  bindRowActivation(table, button => {
    const item = packages[Number(button.dataset.package)];
    const dialog = UI.inspect(item.name, statRows([
      ["Ecosystem", item.eco.name], ["Scope", item.scope], ["Kind", item.kind], ["Requirement", item.req],
      ["Locked versions", item.versions.join(", ") || "unavailable"],
      ["Manifest", item.eco.manifest], ["Lockfile", item.eco.lockfile || "none"],
    ]) + '<button type="button" class="btn btn-secondary" data-package-copy>Copy package name</button>', button);
    dialog.querySelector("[data-package-copy]").onclick = () => copyText(item.name);
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
    const digit = i < 10 ? (i + 1) % 10 : i - 9;
    return {
      id: `sp-tab-${key.replace(/[^a-z0-9-]/gi, "-")}`,
      mode: "jump",
      action: "jump",
      target: key,
      label: t.name,
      search: `${t.name} ${t.kind || ""} ${t.description || ""}`,
      key: i < 16 ? String(digit) : "",
      code: i < 16 ? `Digit${digit}` : "",
      shift: i >= 10,
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

function switchTabFromShortcut(key) {
  const focusedTab = document.activeElement?.closest("#tabs button");
  activateTab(key);
  focusedTab?.blur();
}

document.addEventListener("keydown", e => {
  if (e.isComposing || [...document.querySelectorAll("dialog[open]")]
    .some(dialog => dialog !== spotlightDialog)) return;
  const metaK = (e.ctrlKey || e.metaKey) && e.code === "KeyK";
  const ctrlSpace = e.ctrlKey && e.code === "Space";
  if ((metaK || ctrlSpace) && !e.altKey) {
    e.preventDefault();
    openSpotlight("commands");
    return;
  }
  if (isEditingTarget(e.target) && e.target !== spotlightInput) return;
  if (e.altKey && !e.ctrlKey && !e.metaKey) {
    if (!e.shiftKey && ["ArrowLeft", "ArrowRight"].includes(e.code)) {
      const tabs = [...document.querySelectorAll("#tabs button")];
      if (!tabs.length) return;
      e.preventDefault();
      const index = tabs.findIndex(tab => tab.dataset.tab === state.active);
      const step = e.code === "ArrowRight" ? 1 : -1;
      const next = tabs[(index + step + tabs.length) % tabs.length];
      switchTabFromShortcut(next.dataset.tab);
    } else if (e.code === "KeyJ" && !e.shiftKey) {
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
    } else if (/^Digit[0-9]$/.test(e.code)) {
      const digit = Number(e.code.slice(-1));
      if (e.shiftKey && (digit < 1 || digit > 6)) return;
      const index = e.shiftKey ? digit + 9 : (digit + 9) % 10;
      const tab = state.tabs[index];
      if (tab) { e.preventDefault(); switchTabFromShortcut(tabKey(tab)); }
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

  tabGestures = window.CodeviewTabs($(".tab-group"), activateTab);

  document.addEventListener("keydown", ev => {
    if (ev.defaultPrevented || ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
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
