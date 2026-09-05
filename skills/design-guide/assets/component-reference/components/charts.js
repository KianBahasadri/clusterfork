(function (reference) {
  var chart = reference.chart;
  var last = chart.samples.length - 1;
  var dialog = document.getElementById("chartExplorerDialog");
  var previewSurface = document.getElementById("btnExploreChart");
  var detailSurface = document.getElementById("chartDetailSurface");
  var dataTable = document.getElementById("chartDataTable");
  var sorting = reference.makeTableSortable(dataTable);
  var controls = {};
  ["RangeStart", "RangeEnd", "ShowObserved", "ShowForecast", "ShowGrid", "ShowPoints", "ShowTarget", "TargetValue", "TargetError", "ZoomIn", "ZoomOut", "PanEarlier", "PanLater", "Unpin", "ExportAll", "ExportCsv"].forEach(function (name) {
    controls[name] = document.getElementById("chart" + name);
  });
  var tabs = Array.from(dialog.querySelectorAll(".chart-tab"));

  function defaults() {
    return { start: 0, end: last, visible: { observed: true, forecast: true }, style: "line", scale: "zero", grid: true, points: false, target: null, cursor: 4, pinned: false };
  }
  var state = defaults();
  var previewState = defaults();
  previewState.cursor = 0;
  var preview = chart.createPlot(document.getElementById("chartPreview"), {
    onInspect: function (index) { previewState.cursor = index; },
    onActivate: openExplorer
  });
  var detail = chart.createPlot(document.getElementById("chartDetail"), {
    onInspect: function (index) { state.cursor = index; updateSampleStatus(); },
    onActivate: function (index) {
      state.cursor = index;
      state.pinned = !state.pinned;
      controls.Unpin.hidden = !state.pinned;
      detail.render(state);
      updateSampleStatus();
    }
  });

  function updateSampleStatus() {
    document.getElementById("chartSampleStatus").textContent = (state.pinned ? "Pinned · " : "") + chart.timeLabel(chart.samples[state.cursor].time, true) + " UTC";
  }

  function renderData() {
    var fragment = document.createDocumentFragment();
    chart.samples.slice(state.start, state.end + 1).forEach(function (sample, offset) {
      var row = document.createElement("tr");
      row.dataset.sampleIndex = state.start + offset;
      [sample.time, sample.observed, sample.forecast, chart.delta(sample)].forEach(function (value, column) {
        var cell = document.createElement("td");
        cell.dataset.sortValue = value === null ? "" : String(value);
        if (column === 0) {
          var time = document.createElement("time");
          time.dateTime = value; time.textContent = chart.timeLabel(value, true);
          cell.appendChild(time);
        } else {
          cell.className = "num";
          cell.textContent = value === null ? "—" : chart.numbers.format(value);
          if (value === null) cell.setAttribute("aria-label", "Not available");
        }
        row.appendChild(cell);
      });
      fragment.appendChild(row);
    });
    dataTable.tBodies[0].replaceChildren(fragment);
    sorting.refresh();
  }

  function render() {
    var span = state.end - state.start;
    controls.RangeStart.max = state.end - 1;
    controls.RangeEnd.min = state.start + 1;
    controls.RangeStart.value = state.start;
    controls.RangeEnd.value = state.end;
    ["Start", "End"].forEach(function (edge) {
      var text = chart.timeLabel(chart.samples[state[edge.toLowerCase()]].time, true);
      document.getElementById("chartRange" + edge + "Label").textContent = text;
      controls["Range" + edge].setAttribute("aria-valuetext", text + " UTC");
    });
    controls.ZoomIn.disabled = span <= 1;
    controls.ZoomOut.disabled = span === last;
    controls.PanEarlier.disabled = state.start === 0;
    controls.PanLater.disabled = state.end === last;
    controls.ShowObserved.disabled = !state.visible.forecast;
    controls.ShowForecast.disabled = !state.visible.observed;
    controls.TargetValue.disabled = !controls.ShowTarget.checked;
    controls.Unpin.hidden = !state.pinned;
    dialog.querySelectorAll("[data-chart-window]").forEach(function (button) {
      button.setAttribute("aria-pressed", state.end === last && span === Number(button.dataset.chartWindow) ? "true" : "false");
    });
    var rows = chart.samples.slice(state.start, state.end + 1);
    var stats = chart.statistics(rows);
    Object.keys(stats).forEach(function (key) {
      var name = key[0].toUpperCase() + key.slice(1);
      document.getElementById("chart" + name).textContent = stats[key] === null ? "—" : chart.valueLabel(stats[key]);
    });
    var range = rows.length + " samples · " + chart.timeLabel(rows[0].time, true) + "–" + chart.timeLabel(rows[rows.length - 1].time, true) + " UTC";
    var status = document.getElementById("chartRangeStatus");
    if (status.textContent !== range) status.textContent = range;
    renderData();
    detail.render(state);
    updateSampleStatus();
  }

  function setWindow(first, end) {
    state.start = first; state.end = end;
    if (state.cursor < first || state.cursor > end) {
      state.cursor = Math.max(first, Math.min(end, state.cursor));
      state.pinned = false;
    }
    render();
  }

  function zoom(factor) {
    var range = chart.windowAround(state.cursor, (state.end - state.start) * factor);
    setWindow(range.start, range.end);
  }

  function pan(direction) {
    var span = state.end - state.start;
    var first = Math.max(0, Math.min(last - span, state.start + direction * Math.max(1, Math.round(span / 2))));
    setWindow(first, first + span);
  }

  function selectTab(tab) {
    tabs.forEach(function (candidate) {
      var active = candidate === tab;
      candidate.setAttribute("aria-selected", active ? "true" : "false");
      candidate.tabIndex = active ? 0 : -1;
      document.getElementById(candidate.getAttribute("aria-controls")).hidden = !active;
    });
    var dataView = tab.id === "chartDataTab";
    document.getElementById("chartPlotOptions").hidden = dataView;
    dialog.querySelector(".chart-workspace").classList.toggle("is-data-view", dataView);
    if (dataView) detail.hide();
    else detail.render(state);
  }

  function openExplorer(index) {
    state.cursor = index;
    state.pinned = false;
    if (index < state.start || index > state.end) {
      var range = chart.windowAround(index, state.end - state.start);
      state.start = range.start; state.end = range.end;
    }
    preview.hide();
    previewSurface.focus({ preventScroll: true });
    dialog.showModal();
    selectTab(tabs[0]);
    render();
    detailSurface.focus({ preventScroll: true });
  }

  tabs.forEach(function (tab, index) {
    tab.addEventListener("click", function () { selectTab(tab); });
    tab.addEventListener("keydown", function (event) {
      var next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
      if (event.key === "Home") next = 0;
      if (event.key === "End") next = tabs.length - 1;
      if (next === undefined) return;
      event.preventDefault(); selectTab(tabs[next]); tabs[next].focus();
    });
  });
  dialog.querySelectorAll("[data-chart-window]").forEach(function (button) {
    button.addEventListener("click", function () { setWindow(last - Number(button.dataset.chartWindow), last); });
  });
  controls.RangeStart.addEventListener("input", function () { setWindow(Number(this.value), state.end); });
  controls.RangeEnd.addEventListener("input", function () { setWindow(state.start, Number(this.value)); });
  controls.ZoomIn.addEventListener("click", function () { zoom(0.5); });
  controls.ZoomOut.addEventListener("click", function () { zoom(2); });
  controls.PanEarlier.addEventListener("click", function () { pan(-1); });
  controls.PanLater.addEventListener("click", function () { pan(1); });
  controls.Unpin.addEventListener("click", function () {
    state.pinned = false; render(); detailSurface.focus({ preventScroll: true });
  });
  ["Observed", "Forecast"].forEach(function (name) {
    controls["Show" + name].addEventListener("change", function () { state.visible[name.toLowerCase()] = this.checked; render(); });
  });
  ["Grid", "Points"].forEach(function (name) {
    controls["Show" + name].addEventListener("change", function () { state[name.toLowerCase()] = this.checked; render(); });
  });
  ["Style", "Scale"].forEach(function (name) {
    dialog.querySelectorAll('input[name="chart' + name + '"]').forEach(function (input) {
      input.addEventListener("change", function () { if (this.checked) { state[name.toLowerCase()] = this.value; render(); } });
    });
  });

  function updateTarget() {
    var target = Number(controls.TargetValue.value);
    var valid = controls.TargetValue.value !== "" && Number.isFinite(target) && target >= 0 && target <= 60000;
    controls.TargetError.hidden = valid || !controls.ShowTarget.checked;
    controls.TargetValue.classList.toggle("is-invalid", !controls.TargetError.hidden);
    if (controls.TargetError.hidden) controls.TargetValue.removeAttribute("aria-invalid");
    else controls.TargetValue.setAttribute("aria-invalid", "true");
    state.target = controls.ShowTarget.checked && valid ? target : null;
    render();
  }
  controls.ShowTarget.addEventListener("change", updateTarget);
  controls.TargetValue.addEventListener("input", updateTarget);
  controls.ExportAll.addEventListener("change", function () { controls.ExportCsv.textContent = this.checked ? "Export full CSV" : "Export range CSV"; });
  controls.ExportCsv.addEventListener("click", function () {
    var rows = controls.ExportAll.checked ? chart.samples : Array.from(dataTable.tBodies[0].rows, function (row) { return chart.samples[Number(row.dataset.sampleIndex)]; });
    var first = chart.samples[controls.ExportAll.checked ? 0 : state.start].time;
    var end = chart.samples[controls.ExportAll.checked ? last : state.end].time;
    var filename = "latency-" + first.slice(0, 10) + "-" + chart.timeLabel(first, true).replace(/:/g, "") + "-" + chart.timeLabel(end, true).replace(/:/g, "") + ".csv";
    reference.downloadText(filename, chart.csv(rows), "text/csv;charset=utf-8");
    document.getElementById("chartRangeStatus").textContent = "Exported " + rows.length + " samples as CSV";
  });
  document.getElementById("chartReset").addEventListener("click", function () {
    state = defaults();
    ["Observed", "Forecast", "Grid"].forEach(function (name) { controls["Show" + name].checked = true; });
    ["Points", "Target"].forEach(function (name) { controls["Show" + name].checked = false; });
    controls.TargetValue.value = "1000";
    controls.TargetValue.classList.remove("is-invalid");
    controls.TargetValue.removeAttribute("aria-invalid");
    controls.TargetError.hidden = true;
    controls.ExportAll.checked = false;
    controls.ExportCsv.textContent = "Export range CSV";
    dialog.querySelector('input[name="chartStyle"][value="line"]').checked = true;
    dialog.querySelector('input[name="chartScale"][value="zero"]').checked = true;
    sorting.reset(); render();
  });

  detailSurface.addEventListener("keydown", function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "+" || event.key === "=") { event.preventDefault(); zoom(0.5); }
    if (event.key === "-" || event.key === "_") { event.preventDefault(); zoom(2); }
    if (event.shiftKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
      event.preventDefault(); pan(event.key === "ArrowLeft" ? -1 : 1);
    }
  });
  document.getElementById("btnCloseChartExplorer").addEventListener("click", function () { dialog.close(); });
  dialog.addEventListener("click", function (event) {
    if (event.target !== dialog) return;
    var bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
  dialog.addEventListener("keydown", function (event) {
    if (event.key !== "Tab") return;
    var focusable = Array.from(dialog.querySelectorAll("button, input, [tabindex]")).filter(function (element) {
      return !element.disabled && element.tabIndex >= 0 && element.getClientRects().length;
    });
    var next = focusable.indexOf(document.activeElement) + (event.shiftKey ? -1 : 1);
    if (next < 0 || next >= focusable.length) {
      event.preventDefault(); focusable[next < 0 ? focusable.length - 1 : 0].focus();
    }
  });
  dialog.addEventListener("close", function () { detail.hide(); });
  preview.render(previewState);
  render();
}(window.ComponentReference));
