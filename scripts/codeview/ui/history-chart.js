/* History preview and explorer share the server's actual commit samples. */
"use strict";

window.CodeviewHistoryChart = (() => {
  let dispose = () => {};
  let saved = null;

  return function (wrap, commits, helpers) {
    dispose();
    const { esc, fmt, fmtDate, sortHeader, makeTableSortable, downloadText, shaButton } = helpers;
    const abort = new AbortController();
    const { signal } = abort;
    const listen = (element, event, handler) => element.addEventListener(event, handler, { signal });
    const last = commits.length - 1;
    const signature = commits.map(commit => `${commit.sha}:${commit.total}`).join("|");
    const defaults = () => ({ start: 0, end: last, cursor: last, pinned: false,
      style: "line", scale: "zero", grid: true, points: false, target: null });
    const state = saved?.signature === signature ? saved.state : defaults();
    saved = { signature, state };
    const previewState = defaults();
    const iso = commit => {
      const date = new Date(commit.date);
      return Number.isNaN(date.getTime()) ? String(commit.date) : date.toISOString();
    };
    const timeLabel = commit => {
      const date = new Date(commit.date);
      return Number.isNaN(date.getTime()) ? String(commit.date) : date.toLocaleString("en-US", {
        timeZone: "UTC", month: "short", day: "numeric", year: "numeric",
        hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true,
      }) + " UTC";
    };
    const button = (action, label, quiet = false) => `<button type="button"
      class="btn ${quiet ? "btn-quiet" : "btn-secondary"}" data-chart-action="${action}">${label}</button>`;
    const choice = (name, label, type = "checkbox", value = "", checked = false) =>
      `<label class="history-choice"><input type="${type}" name="history-${name}"
        value="${value}" ${checked ? "checked" : ""}><span class="history-${type}" aria-hidden="true">${type === "checkbox" ? '<svg class="icon"><use href="#lucide-check"></use></svg>' : ""}</span><span>${label}</span></label>`;

    const dialog = document.createElement("dialog");
    dialog.className = "modal-dialog history-dialog";
    dialog.id = "historyExplorer";
    dialog.setAttribute("aria-labelledby", "historyExplorerTitle");
    dialog.innerHTML = `
      <div class="modal-header">
        <div><h2 class="modal-title" id="historyExplorerTitle">History explorer</h2>
          <p class="history-context">Cumulative lines · commit order · UTC</p></div>
        <button class="btn-icon" type="button" data-chart-action="close" aria-label="Close" title="Close">
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#lucide-x"></use></svg></button>
      </div>
      <div class="history-body">
        <div class="history-toolbar">
          <div class="tab-nav" role="tablist" aria-label="History explorer view">
            <button class="tab-btn" type="button" id="historyPlotTab" role="tab" aria-selected="true" aria-controls="historyPlotPanel">Chart</button>
            <button class="tab-btn" type="button" id="historyDataTab" role="tab" aria-selected="false" aria-controls="historyDataPanel" tabindex="-1">Exact data</button>
          </div>${button("reset", "Reset view", true)}
        </div>
        <div class="history-toolbar">
          <div class="history-actions" role="group" aria-label="Commit range">
            ${button("full", "Full range")}${button("recent50", "Last 50 commits")}${button("recent10", "Last 10 commits")}
          </div>
          <div class="history-actions" role="group" aria-label="Zoom and pan">
            ${button("earlier", "Earlier", true)}${button("zoomIn", "Zoom in")}${button("zoomOut", "Zoom out")}${button("later", "Later", true)}
          </div>
        </div>
        <div class="history-workspace">
          <div class="history-main">
            <div id="historyPlotPanel" role="tabpanel" aria-labelledby="historyPlotTab">
              <div class="chart-frame history-detail">
                <div class="chart-surface" tabindex="0" role="group" aria-label="Inspect cumulative lines by commit" aria-describedby="historyPlotHelp"></div>
                <div class="chart-tooltip" hidden></div><div class="sr-only" aria-live="polite"></div>
              </div>
              <p class="sr-only" id="historyPlotHelp">Left and Right inspect commits. Home and End reach the endpoints. Plus and Minus zoom. Shift with Left or Right pans. Enter, Space, or clicking pins a sample.</p>
              <div class="history-sample"><span data-chart-sample></span>${button("unpin", "Unpin sample", true)}</div>
            </div>
            <div id="historyDataPanel" role="tabpanel" aria-labelledby="historyDataTab" hidden>
              <div class="table-container history-data" role="region" aria-label="Exact commit history" tabindex="0">
                <table class="data-table" id="historyExactData"><caption class="sr-only">Actual commit samples in the selected range</caption>
                  <thead><tr>${sortHeader("Time (UTC)", "text", "num")}${sortHeader("Commit", "text")}${sortHeader("Subject", "text")}${sortHeader("Change (lines)", "number", "num")}${sortHeader("Cumulative lines", "number", "num")}</tr></thead><tbody></tbody></table>
              </div>
            </div>
            <div class="history-range-fields">
              ${["start", "end"].map(edge => `<div class="history-range-field">
                <label for="history-${edge}">${edge === "start" ? "From" : "To"} commit <output id="history-${edge}-time" for="history-${edge}"></output></label>
                <div class="history-range-entry"><input id="history-${edge}" type="range" step="1">
                  <label class="sr-only" for="history-${edge}-number">${edge === "start" ? "First" : "Last"} commit number</label>
                  <input class="input-field" id="history-${edge}-number" type="number" step="1" aria-describedby="historyRangeError"></div>
              </div>`).join("")}
            </div>
            <p class="input-error" id="historyRangeError" role="alert" hidden></p>
            <dl class="history-stats" aria-label="Selected range statistics"></dl>
          </div>
          <aside class="history-options" aria-label="Plot controls">
            <fieldset><legend>Series</legend>${choice("series", "Cumulative lines", "checkbox", "", true)}</fieldset>
            <fieldset><legend>Display</legend>${choice("style", "Line", "radio", "line", state.style === "line")}${choice("style", "Area", "radio", "area", state.style === "area")}</fieldset>
            <fieldset><legend>Y axis</legend>${choice("scale", "Start at zero", "radio", "zero", state.scale === "zero")}${choice("scale", "Fit visible values", "radio", "fit", state.scale === "fit")}</fieldset>
            <fieldset><legend>Overlays</legend>${choice("grid", "Gridlines", "checkbox", "", state.grid)}${choice("points", "Sample markers", "checkbox", "", state.points)}${choice("target", "Target line")}
              <label class="form-label history-target" for="history-target-value">Target (lines)<input class="input-field" id="history-target-value" type="number" min="0" step="1" value="${state.target ?? 1000}" disabled aria-describedby="historyTargetError"></label>
              <p class="input-error" id="historyTargetError" role="alert" hidden>Enter a whole number of lines from 0 to 9,007,199,254,740,991</p>
            </fieldset>
          </aside>
        </div>
      </div>
      <div class="history-footer"><span class="history-status" role="status"></span>
        <div class="history-actions">${choice("exportAll", "Full dataset")}${button("export", "Export range CSV")}</div></div>`;
    document.body.appendChild(dialog);
    const $ = selector => dialog.querySelector(selector);
    const action = name => $(`[data-chart-action="${name}"]`);
    const input = name => $(`[name="history-${name}"]`);
    input("series").disabled = true; // The dataset contains one observed series.
    input("target").checked = state.target !== null;
    $("#history-target-value").disabled = state.target === null;
    const table = $("#historyExactData");
    const sorting = makeTableSortable(table);
    const previewSurface = wrap.querySelector(".chart-surface");
    let preview, detail;

    function sampleStatus() {
      $("[data-chart-sample]").textContent = `${state.pinned ? "Pinned · " : ""}${timeLabel(commits[state.cursor])}`;
      action("unpin").hidden = !state.pinned;
    }

    function plot(frame, view, isPreview) {
      const surface = frame.querySelector(".chart-surface");
      const tip = frame.querySelector(".chart-tooltip");
      const live = frame.querySelector("[aria-live]");
      let scale = null;
      let inspecting = false;
      let hovering = false;

      function hide() {
        if (!isPreview && view.pinned) return;
        inspecting = false;
        tip.hidden = true;
        surface.querySelectorAll(".chart-crosshair, .chart-cursor-point").forEach(mark => mark.setAttribute("hidden", ""));
      }

      function inspect(index, announce = false) {
        view.cursor = Math.max(view.start, Math.min(view.end, index));
        const commit = commits[view.cursor];
        if (!isPreview) sampleStatus();
        if (!scale || !Number.isFinite(commit.total)) { hide(); return; }
        inspecting = true;
        const x = scale.x(view.cursor), y = scale.y(commit.total);
        const line = surface.querySelector(".chart-crosshair"), point = surface.querySelector(".chart-cursor-point");
        line.removeAttribute("hidden"); line.setAttribute("x1", x); line.setAttribute("x2", x);
        point.removeAttribute("hidden"); point.setAttribute("cx", x); point.setAttribute("cy", y);
        tip.innerHTML = `<time datetime="${esc(iso(commit))}">${esc(timeLabel(commit))}</time>
          <dl><dt>Commit</dt><dd>${esc(commit.sha.slice(0, 7))}</dd><dt>Cumulative lines</dt><dd>${fmt(commit.total)} lines</dd><dt>Change</dt><dd>${fmt(commit.delta)} lines</dd></dl>`;
        tip.hidden = false;
        tip.style.left = `${Math.max(0, Math.min(scale.width - tip.offsetWidth, x + 12))}px`;
        tip.style.top = `${Math.max(0, Math.min(scale.height - tip.offsetHeight, y - tip.offsetHeight - 8))}px`;
        if (announce) live.textContent = `${timeLabel(commit)}, ${commit.sha.slice(0, 7)}, ${fmt(commit.total)} cumulative lines`;
      }

      function draw() {
        const width = surface.clientWidth, height = surface.clientHeight;
        if (!width || !height) return;
        const values = commits.slice(view.start, view.end + 1).map(c => c.total).filter(Number.isFinite);
        if (!values.length) { surface.innerHTML = '<span class="history-context">No line measurements in this range</span>'; scale = null; hide(); return; }
        let low = view.scale === "zero" ? Math.min(0, ...values) : Math.min(...values);
        let high = Math.max(...values, view.target ?? -Infinity);
        if (view.target !== null) low = Math.min(low, view.target);
        if (high === low) high = low + 1;
        const labelWidth = Math.max(...[low, high].map(value => `${fmt(Math.round(value))} lines`.length * 6.7));
        const left = Math.max(64, labelWidth + 8), right = 12, top = 12, bottom = 32;
        const iw = Math.max(1, width - left - right), ih = Math.max(1, height - top - bottom);
        scale = { width, height, x: i => left + (view.end === view.start ? iw / 2 : (i - view.start) / (view.end - view.start) * iw),
          y: value => top + (high - value) / (high - low) * ih };
        let svg = `<svg class="chart-svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true" focusable="false">`;
        for (let tick = 0; tick <= 4; tick++) {
          const y = top + ih * tick / 4;
          if (view.grid) svg += `<line class="chart-grid-line" x1="${left}" x2="${width - right}" y1="${y}" y2="${y}"></line>`;
          svg += `<text class="chart-axis-text" x="${left - 8}" y="${y + 4}" text-anchor="end">${fmt(Math.round(high - (high - low) * tick / 4))} lines</text>`;
        }
        let segment = [];
        function flush() {
          if (!segment.length) return;
          const points = segment.map(i => `${scale.x(i)},${scale.y(commits[i].total)}`).join(" ");
          if (view.style === "area") svg += `<polygon class="chart-area" points="${scale.x(segment[0])},${scale.y(low)} ${points} ${scale.x(segment.at(-1))},${scale.y(low)}"></polygon>`;
          svg += `<polyline class="chart-series" points="${points}"></polyline>`;
          if (view.points || segment.length === 1) segment.forEach(i => { svg += `<circle class="chart-point" r="3" cx="${scale.x(i)}" cy="${scale.y(commits[i].total)}"></circle>`; });
          segment = [];
        }
        for (let i = view.start; i <= view.end; i++) {
          if (Number.isFinite(commits[i].total)) segment.push(i); else flush();
        }
        flush();
        const ticks = Math.max(1, Math.min(5, Math.floor(iw / 100)));
        for (let tick = 0; tick <= ticks; tick++) {
          const i = Math.round(view.start + (view.end - view.start) * tick / ticks);
          if (view.start === view.end && tick > 0) break;
          svg += `<text class="chart-axis-text" x="${scale.x(i)}" y="${height - 8}" text-anchor="${tick === 0 ? "start" : tick === ticks ? "end" : "middle"}">${esc(fmtDate(iso(commits[i])).split(" ").slice(0, 2).join(" "))}</text>`;
        }
        if (view.target !== null) svg += `<line class="history-target-line" x1="${left}" x2="${width - right}" y1="${scale.y(view.target)}" y2="${scale.y(view.target)}"></line>`;
        surface.innerHTML = svg + `<line class="chart-crosshair" hidden x1="0" x2="0" y1="${top}" y2="${height - bottom}"></line><circle class="chart-point chart-cursor-point" hidden r="4"></circle></svg>`;
        if (inspecting || view.pinned || document.activeElement === surface) inspect(view.cursor);
      }

      function indexAt(event) {
        if (!scale || !event.detail && event.type === "click") return view.cursor;
        const rect = surface.getBoundingClientRect();
        const span = scale.x(view.end) - scale.x(view.start);
        return span ? Math.round(view.start + (event.clientX - rect.left - scale.x(view.start)) / span * (view.end - view.start)) : view.start;
      }
      listen(surface, "mousemove", event => { hovering = true; if (!view.pinned) inspect(indexAt(event)); });
      listen(surface, "mouseleave", () => { hovering = false; hide(); });
      listen(surface, "focus", () => inspect(view.cursor, true));
      listen(surface, "blur", hide);
      listen(surface, "click", event => {
        inspect(indexAt(event));
        if (isPreview) open(view.cursor);
        else { view.pinned = !view.pinned; sampleStatus(); }
      });
      listen(surface, "keydown", event => {
        if (event.altKey || event.ctrlKey || event.metaKey) return;
        let index = view.cursor;
        if (!isPreview && event.shiftKey && ["ArrowLeft", "ArrowRight"].includes(event.key)) { event.preventDefault(); pan(event.key === "ArrowLeft" ? -1 : 1); return; }
        if (!isPreview && ["+", "=", "-"].includes(event.key)) { event.preventDefault(); zoom(event.key === "-" ? 2 : 0.5); return; }
        if (!isPreview && ["Enter", " "].includes(event.key)) { event.preventDefault(); view.pinned = !view.pinned; inspect(index, true); return; }
        if (event.shiftKey) return;
        if (event.key === "ArrowLeft") index--;
        else if (event.key === "ArrowRight") index++;
        else if (event.key === "Home") index = view.start;
        else if (event.key === "End") index = view.end;
        else return;
        event.preventDefault(); inspect(index, true);
      });
      const observer = new ResizeObserver(draw);
      observer.observe(surface);
      signal.addEventListener("abort", () => observer.disconnect());
      draw();
      return { draw, hide, inspect, unpin() { view.pinned = false; if (!hovering) hide(); } };
    }

    function render() {
      const count = state.end - state.start + 1;
      for (const edge of ["start", "end"]) {
        const min = edge === "start" ? 0 : Math.min(last, state.start + 1);
        const max = edge === "end" ? last : Math.max(0, state.end - 1);
        const slider = $(`#history-${edge}`), number = $(`#history-${edge}-number`);
        Object.assign(slider, { min, max, value: state[edge], disabled: last === 0 });
        slider.style.setProperty("--range-progress", `${max > min ? (state[edge] - min) / (max - min) * 100 : 0}%`);
        Object.assign(number, { min: min + 1, max: max + 1, value: state[edge] + 1, disabled: last === 0 });
        number.removeAttribute("aria-invalid");
        const label = timeLabel(commits[state[edge]]);
        $(`#history-${edge}-time`).textContent = label;
        slider.setAttribute("aria-valuetext", `Commit ${state[edge] + 1}, ${label}`);
      }
      $("#historyRangeError").hidden = true;
      action("zoomIn").disabled = count <= 2;
      action("zoomOut").disabled = count === commits.length;
      action("earlier").disabled = state.start === 0;
      action("later").disabled = state.end === last;
      for (const [name, size] of [["full", commits.length], ["recent50", 50], ["recent10", 10]]) {
        action(name).setAttribute("aria-pressed", String(state.end === last && count === Math.min(size, commits.length)));
      }
      const rows = commits.slice(state.start, state.end + 1);
      table.querySelector("tbody").innerHTML = rows.map((c, offset) => `<tr data-sample-index="${state.start + offset}">
        <td class="num" data-sort-value="${esc(iso(c))}"><time datetime="${esc(iso(c))}">${esc(timeLabel(c))}</time></td>
        <td data-sort-value="${esc(c.sha)}">${shaButton(c.sha)}</td><td class="wrap">${esc(c.subject)}</td>
        <td class="num" data-sort-value="${c.delta ?? ""}">${fmt(c.delta)}</td><td class="num" data-sort-value="${c.total ?? ""}">${fmt(c.total)}</td></tr>`).join("");
      sorting.refresh();
      const values = rows.map(c => c.total).filter(Number.isFinite);
      const stats = [["Mean cumulative lines", values.length ? `${fmt(Math.round(values.reduce((a, b) => a + b, 0) / values.length))} lines` : "—"],
        ["Peak cumulative lines", values.length ? `${fmt(Math.max(...values))} lines` : "—"],
        ["Net line change", Number.isFinite(rows[0].total) && Number.isFinite(rows.at(-1).total) ? `${fmt(rows.at(-1).total - rows[0].total)} lines` : "—"], ["Commits", fmt(count)]];
      $(".history-stats").innerHTML = stats.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("");
      $(".history-status").textContent = `${fmt(count)} commits · ${fmtDate(iso(rows[0]))} – ${fmtDate(iso(rows.at(-1)))} UTC`;
      sampleStatus(); detail.draw();
    }

    function setRange(start, end) {
      state.start = Math.max(0, Math.min(last, start));
      state.end = Math.max(state.start, Math.min(last, end));
      if (state.cursor < state.start || state.cursor > state.end) {
        state.cursor = Math.max(state.start, Math.min(state.end, state.cursor)); state.pinned = false;
      }
      render();
    }
    function zoom(factor) {
      const span = Math.min(last, Math.max(1, Math.round((state.end - state.start) * factor)));
      const start = Math.max(0, Math.min(last - span, state.cursor - Math.floor(span / 2)));
      setRange(start, start + span);
    }
    function pan(direction) {
      const span = state.end - state.start;
      const start = Math.max(0, Math.min(last - span, state.start + direction * Math.max(1, Math.round(span / 2))));
      setRange(start, start + span);
    }
    function selectView(tab) {
      dialog.querySelectorAll('[role="tab"]').forEach(candidate => {
        const active = candidate === tab;
        candidate.setAttribute("aria-selected", String(active)); candidate.tabIndex = active ? 0 : -1;
        $(`#${candidate.getAttribute("aria-controls")}`).hidden = !active;
      });
      $(".history-options").hidden = tab.id === "historyDataTab";
      $(".history-workspace").classList.toggle("is-data-view", tab.id === "historyDataTab");
      detail.draw();
    }
    function open(index) {
      state.cursor = index; state.pinned = false;
      if (index < state.start || index > state.end) {
        const span = state.end - state.start;
        state.start = Math.max(0, Math.min(last - span, index - Math.floor(span / 2)));
        state.end = state.start + span;
      }
      preview.hide();
      dialog.showModal(); selectView($("#historyPlotTab")); render();
      $(".history-detail .chart-surface").focus({ preventScroll: true });
    }

    preview = plot(wrap.querySelector(".chart-frame"), previewState, true);
    detail = plot($(".history-detail"), state, false);
    for (const tab of dialog.querySelectorAll('[role="tab"]')) {
      listen(tab, "click", () => selectView(tab));
      listen(tab, "keydown", event => {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...dialog.querySelectorAll('[role="tab"]')];
        const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[1] : tabs[1 - tabs.indexOf(tab)];
        selectView(next); next.focus();
      });
    }
    const actions = {
      close: () => dialog.close(), full: () => setRange(0, last),
      recent50: () => setRange(Math.max(0, last - 49), last), recent10: () => setRange(Math.max(0, last - 9), last),
      earlier: () => pan(-1), later: () => pan(1), zoomIn: () => zoom(0.5), zoomOut: () => zoom(2),
      unpin: () => { detail.unpin(); sampleStatus(); $(".history-detail .chart-surface").focus({ preventScroll: true }); },
      reset: () => {
        Object.assign(state, defaults());
        for (const name of ["style", "scale"]) dialog.querySelectorAll(`[name="history-${name}"]`).forEach(control => { control.checked = control.value === state[name]; });
        for (const name of ["grid", "points"]) input(name).checked = state[name];
        input("target").checked = false; $("#history-target-value").disabled = true;
        $("#history-target-value").removeAttribute("aria-invalid"); $("#historyTargetError").hidden = true;
        sorting.reset(); render();
      },
      export: () => {
        const all = input("exportAll").checked;
        const indices = all ? commits.map((_, i) => i) : [...table.querySelectorAll("tbody tr")].map(row => Number(row.dataset.sampleIndex));
        const rows = indices.map(i => commits[i]);
        const csvCell = value => `"${String(value ?? "").replace(/"/g, '""')}"`;
        const csv = [["time_utc", "commit", "subject", "change_lines", "cumulative_lines"], ...rows.map(c => [iso(c), c.sha, c.subject, c.delta, c.total])].map(row => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
        const first = commits[all ? 0 : state.start], end = commits[all ? last : state.end];
        downloadText(`history-${iso(first).replace(/[:.]/g, "-")}-${iso(end).replace(/[:.]/g, "-")}.csv`, csv, "text/csv;charset=utf-8");
        $(".history-status").textContent = `Exported ${fmt(rows.length)} commits as CSV`;
      },
    };
    for (const [name, handler] of Object.entries(actions)) listen(action(name), "click", handler);
    for (const edge of ["start", "end"]) {
      listen($(`#history-${edge}`), "input", event => setRange(edge === "start" ? Number(event.target.value) : state.start, edge === "end" ? Number(event.target.value) : state.end));
      listen($(`#history-${edge}-number`), "change", event => {
        const field = event.target, value = field.valueAsNumber;
        if (!Number.isInteger(value) || value < Number(field.min) || value > Number(field.max)) {
          field.setAttribute("aria-invalid", "true");
          $("#historyRangeError").textContent = `Enter a commit number from ${field.min} to ${field.max}`;
          $("#historyRangeError").hidden = false; return;
        }
        setRange(edge === "start" ? value - 1 : state.start, edge === "end" ? value - 1 : state.end);
      });
    }
    for (const name of ["style", "scale"]) dialog.querySelectorAll(`[name="history-${name}"]`).forEach(control => listen(control, "change", () => { state[name] = control.value; detail.draw(); }));
    for (const name of ["grid", "points"]) listen(input(name), "change", () => { state[name] = input(name).checked; detail.draw(); });
    function targetChanged() {
      const field = $("#history-target-value"), enabled = input("target").checked;
      field.disabled = !enabled;
      const valid = Number.isSafeInteger(field.valueAsNumber) && field.valueAsNumber >= 0;
      field.setAttribute("aria-invalid", String(enabled && !valid)); $("#historyTargetError").hidden = !enabled || valid;
      state.target = enabled && valid ? field.valueAsNumber : null; detail.draw();
    }
    listen(input("target"), "change", targetChanged);
    listen($("#history-target-value"), "input", targetChanged);
    listen(input("exportAll"), "change", () => { action("export").textContent = input("exportAll").checked ? "Export full CSV" : "Export range CSV"; });
    listen(dialog, "click", event => { if (event.target === dialog) dialog.close(); });
    listen(dialog, "close", () => { detail.unpin(); previewSurface.focus({ preventScroll: true }); });
    dispose = () => { abort.abort(); dialog.remove(); };
  };
})();
