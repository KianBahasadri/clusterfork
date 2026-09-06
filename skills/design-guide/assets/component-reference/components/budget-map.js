(function (reference) {
  var sequence = 0;
  var format = reference.budgetMapFormat;
  function element(tag, className, text) {
    var result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }
  function button(text, className) {
    var result = element("button", className, text);
    result.type = "button";
    return result;
  }
  function value(number, unit) {
    return number === null ? "Unavailable" : new Intl.NumberFormat("en", { maximumSignificantDigits: 21 }).format(number) + (unit ? " " + unit : "");
  }
  function estimate(number, unit) {
    return number === null ? "—" : new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(number) + (unit ? " " + unit : "");
  }
  function sortableTable(id, caption, names, numeric) {
    var table = element("table", "data-table");
    table.id = id;
    table.appendChild(element("caption", "sr-only", caption));
    var head = element("thead"), row = element("tr");
    names.forEach(function (name, index) {
      var cell = element("th", numeric.includes(index) ? "num" : "");
      cell.scope = "col";
      var sort = button(name + " ", "sort-th-btn");
      sort.dataset.sort = numeric.includes(index) ? "number" : "text";
      var indicator = element("span", "sort-indicator");
      indicator.setAttribute("aria-hidden", "true");
      indicator.innerHTML = '<svg class="icon"><use href="#lucide-arrow-up"></use></svg><span class="sort-priority"></span>';
      sort.appendChild(indicator);
      cell.appendChild(sort);
      row.appendChild(cell);
    });
    head.appendChild(row);
    table.append(head, element("tbody"));
    return table;
  }

  reference.createBudgetMap = function (root, data, options) {
    options = options || {};
    var interactive = options.interactive !== false;
    var logos = options.logos || reference.budgetMapLogos || {};
    var model = reference.budgetMapModel.prepare(data);
    var prefix;
    do { prefix = "budget-map-" + (++sequence); } while (document.getElementById(prefix + "-help"));
    var activeId = model.items.length ? model.items[0].id : null;
    var hit = null, drawing, destroyed = false, frameRequest = 0;
    var shell = element("div", "budget-map");
    var frame = element("div", "budget-map-frame");
    var surface = interactive ? button("", "budget-map-surface") : element("div", "budget-map-surface");
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("budget-map-svg");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    var tooltip = element("div", "budget-map-tooltip");
    tooltip.hidden = true;
    var help = element("p", "sr-only");
    help.id = prefix + "-help";
    var live = element("span", "sr-only");
    var availability = element("p", "sr-only budget-map-availability");
    live.setAttribute("role", "status");
    surface.setAttribute("aria-describedby", help.id);
    if (!interactive) surface.setAttribute("role", "img");
    surface.appendChild(svg);
    frame.append(surface, tooltip, help, live);
    shell.append(frame, availability);
    root.replaceChildren(shell);

    var dialog, picker, details, table, tbody, tableRegion, dataToggle, feedback, sorter;
    var forecastTable, forecastRegion, forecastSorter, methodNotes;
    function selectedItem() { return model.items.find(function (item) { return item.id === activeId; }); }
    function detailText(item) {
      return item.label + ". " + (item.stale ? "Stale. " : "") + item.status + ". Current " + value(item.current, item.unit)
        + ", " + format.percent(item.currentPercent) + " of limit. Forecast " + value(item.forecast, item.unit)
        + ", " + format.percent(item.forecastPercent) + " of limit. Limit " + value(item.limit, item.unit) + ".";
    }
    function clearInspection() {
      hit = null;
      tooltip.hidden = true;
      if (drawing) drawing.groups.forEach(function (group) { group.classList.remove("is-muted", "is-active"); });
    }
    function inspect(next, announce) {
      if (!next) return;
      hit = next;
      activeId = next.id;
      var item = selectedItem();
      drawing.groups.forEach(function (group) {
        group.classList.toggle("is-muted", group.dataset.budgetId !== activeId);
        group.classList.toggle("is-active", group.dataset.budgetId === activeId);
        if (group.dataset.budgetId === activeId) group.parentElement.appendChild(group);
      });
      var heading = element("strong", "", item.label);
      var time = element("time", "", next.kind + " · " + new Date(next.at).toISOString().replace("T", " ").replace("Z", " UTC"));
      time.dateTime = new Date(next.at).toISOString();
      var amount = element("div", "budget-map-tooltip-value", value(next.value, item.unit) + " · " + format.percent(next.value / item.limit * 100));
      var status = element("div", "budget-map-tooltip-status", (item.stale ? "Stale · " : "") + item.status);
      status.dataset.severity = item.severity;
      tooltip.replaceChildren(heading, time, amount, status);
      tooltip.hidden = false;
      var left = Math.max(8, Math.min(drawing.width - tooltip.offsetWidth - 8, next.x + 16));
      var top = Math.max(8, Math.min(drawing.height - tooltip.offsetHeight - 8, next.y - tooltip.offsetHeight - 12));
      tooltip.style.left = left + "px";
      tooltip.style.top = top + "px";
      if (announce) live.textContent = detailText(item);
    }
    function itemHit(id) {
      var matches = drawing.hits.filter(function (candidate) { return candidate.id === id; });
      return matches.find(function (candidate) { return candidate.kind === "Current"; }) || matches[0];
    }
    function draw() {
      if (destroyed) return;
      frameRequest = 0;
      drawing = reference.drawBudgetMap(svg, model, { width: frame.clientWidth, logos: logos });
      if (hit) inspect(itemHit(activeId), false);
    }
    function renderContext() {
      var unavailable = model.items.filter(function (item) { return item.current === null && item.forecast === null; });
      availability.textContent = unavailable.length ? "Unavailable: " + unavailable.map(function (item) { return item.label; }).join(", ") : "";
      availability.hidden = !unavailable.length;
      surface.setAttribute("aria-label", (interactive ? "Explore " : "") + model.title);
      help.textContent = (model.sample ? "Illustrative sample data. " : "") + "Budgets share a time and percentage-of-limit plane. Time advances from the near left edge to the far right edge. The red boundary is the 100% limit. The plotted range is 0 to " + model.maximum + "% of each limit, from " + new Date(model.start).toISOString() + " to " + new Date(model.end).toISOString() + ". Solid trails are observed; dashed trails end at the period forecast. As of "
        + new Date(model.now).toISOString() + ". " + (model.items.length ? model.items.map(detailText).join(" ") : "No budgets.")
        + (interactive ? " Left and Right inspect budgets; Home and End reach the first and last. Enter or Space opens details and exact data." : "");
    }

    function renderDetails() {
      var item = selectedItem();
      var focused = details.contains(document.activeElement) ? document.activeElement : null;
      details.replaceChildren();
      picker.querySelectorAll("button").forEach(function (control) { control.setAttribute("aria-pressed", String(control.dataset.budgetId === activeId)); });
      if (!item) {
        details.appendChild(element("p", "", "No budgets"));
        if (focused) dialog.querySelector("button").focus({ preventScroll: true });
        return;
      }
      var analysis = reference.budgetMapModel.analyze(model, item);
      var heading = element("h3", "budget-map-detail-title", item.label);
      var status = element("span", "budget-map-detail-status", (item.stale ? "Stale · " : "") + item.status);
      status.dataset.severity = item.severity;
      var context = element("p", "budget-map-context budget-map-period", format.date(model.start) + "–" + format.date(model.end)
        + " UTC · " + estimate(analysis.elapsedDays, "d elapsed") + " · " + estimate(analysis.remainingDays, "d remaining"));
      var columns = element("div", "budget-map-summary");
      function group(title, rows) {
        var section = element("section", "budget-map-summary-group");
        section.appendChild(element("h4", "budget-map-subheading", title));
        var list = element("dl", "budget-map-values");
        rows.forEach(function (pair) {
          var row = element("div"), label = element("dt", "", pair[0]);
          if (pair[2] && pair[1] !== "—" && pair[1] !== "Unavailable") row.dataset.provenance = pair[2];
          if (pair[3]) label.appendChild(element("small", "budget-map-calculation-note", pair[3]));
          row.append(label, element("dd", "", pair[1]));
          list.appendChild(row);
        });
        section.appendChild(list);
        columns.appendChild(section);
      }
      function rateRow(label, rate) {
        return [label, estimate(rate.value, item.unit + "/day"), rate.interpolated ? "forecast" : "observed",
          rate.reason || (rate.interpolated ? "Interpolated window start" : "")];
      }
      group("Budget position", [
        ["Limit", value(item.limit, item.unit)],
        ["Current · " + format.date(model.now), value(item.current, item.unit), "observed"],
        ["Current / limit", format.percent(item.currentPercent), "observed"],
        ["Remaining budget", estimate(analysis.remaining, item.unit), "observed"],
        ["Sustainable rate", estimate(analysis.sustainable, item.unit + "/day"), "forecast"]
      ]);
      group("Usage rates", [
        rateRow("Period average · " + estimate(analysis.elapsedDays, "d"), analysis.average),
        rateRow("Last 7 days", analysis.seven), rateRow("Last 3 days", analysis.three),
        ["3d vs period average", (analysis.change > 0 ? "+" : "") + format.percent(analysis.change), "forecast"],
        ["Required reduction · 3d", format.percent(analysis.reduction), "forecast"]
      ]);
      details.append(heading, status, context, columns);
      renderForecasts(item, analysis);
      if (!methodNotes) {
        methodNotes = element("details", "budget-map-method-notes");
        methodNotes.appendChild(element("summary", "", "Calculation notes"));
        methodNotes.appendChild(element("p", "", "Window rate = (current usage − usage at window start) ÷ elapsed days. Period average uses the observed period-start value. Each calculated scenario = current usage + window rate × remaining days. Days are exact 24-hour UTC intervals; amounts and rates display at most 2 decimals, percentages 1 decimal, and estimated dates to the minute."));
        methodNotes.appendChild(element("p", "", "A missing window boundary is linearly interpolated only between adjacent, available observations and is labelled above. Incomplete windows, explicit gaps, or decreasing usage have no rate. Required reduction compares the 3-day rate with max(0, remaining budget) ÷ remaining days."));
        methodNotes.appendChild(element("p", "", "Limit dates assume a constant rate from the snapshot. The supplied forecast's daily rate and limit date are implied by its straight-line path. These scenarios do not model seasonality or provide confidence intervals. All calculations use the snapshot, including when marked stale; CSV contains the original supplied data."));
      }
      details.appendChild(methodNotes);
      if (focused && details.contains(focused)) focused.focus({ preventScroll: true });
    }
    function renderForecasts(item, analysis) {
      var heading = element("h4", "budget-map-subheading budget-map-forecast-heading", "Forecast comparison");
      heading.appendChild(element("span", "budget-map-context", (item.unit ? "Amounts in " + item.unit + " · " : "") + "Period end " + format.date(model.end) + " UTC"));
      if (!forecastRegion) {
        forecastRegion = element("div", "table-container budget-map-comparison");
        forecastRegion.tabIndex = 0;
        forecastRegion.setAttribute("role", "region");
      }
      forecastRegion.setAttribute("aria-label", "Forecast comparison for " + item.label + (item.unit ? " in " + item.unit : ""));
      if (!forecastTable) forecastTable = sortableTable(prefix + "-forecasts", "Forecast methods and their assumptions", ["Method", "Rate / day", "Period end", "Limit used", "Headroom", "Limit reached"], [1, 2, 3, 4, 5]);
      forecastTable.querySelector("caption").textContent = item.label + " forecast comparison" + (item.unit ? "; amounts in " + item.unit + ", rates in " + item.unit + " per day" : "") + ". Limit dates use UTC.";
      var body = forecastTable.querySelector("tbody");
      body.replaceChildren();
      analysis.forecasts.forEach(function (forecast) {
        var row = element("tr");
        row.dataset.method = forecast.id;
        var method = element("td", "budget-map-method", forecast.label);
        method.dataset.sortValue = forecast.label;
        var rate = analysis[forecast.id];
        var basis = rate && !rate.reason && !rate.interpolated ? format.date(rate.since) + "–" + format.date(model.now) + " UTC" : forecast.basis;
        method.appendChild(element("small", "budget-map-calculation-note", basis));
        row.appendChild(method);
        [forecast.rate, forecast.total, forecast.percent, forecast.headroom].forEach(function (number, index) {
          var cell = element("td", "num", index === 2 ? format.percent(number) : estimate(number, ""));
          cell.dataset.sortValue = number === null ? "" : String(number);
          if (number !== null) cell.dataset.provenance = "forecast";
          row.appendChild(cell);
        });
        var limit = element("td", "num budget-map-limit-date");
        limit.dataset.sortValue = forecast.limitAt === null ? "" : String(forecast.limitAt);
        if (forecast.limitState === "projected") {
          var at = element("time", "", format.date(forecast.limitAt));
          at.dateTime = new Date(forecast.limitAt).toISOString();
          limit.append(at, element("small", "budget-map-calculation-note", new Date(forecast.limitAt).toISOString().slice(11, 16) + " UTC"));
          limit.dataset.provenance = "forecast";
        } else limit.textContent = { reached: "Already reached", "not-reached": "Not reached", "after-period": "After period", unavailable: "—" }[forecast.limitState];
        row.appendChild(limit);
        body.appendChild(row);
      });
      forecastRegion.appendChild(forecastTable);
      details.append(heading, forecastRegion);
      if (forecastSorter) forecastSorter.refresh();
      else forecastSorter = reference.makeTableSortable(forecastTable);
    }
    function renderRecords() {
      tbody.replaceChildren();
      reference.budgetMapModel.records(model).forEach(function (record) {
        var row = element("tr");
        record.forEach(function (entry, index) {
          var cell = element("td", [3, 5, 6].includes(index) ? "num" : "", entry === null ? "—" : entry);
          if ([1, 2, 3, 4, 6].includes(index) && entry !== null) cell.dataset.provenance = record[2] === "Forecast" ? "forecast" : "observed";
          if ([3, 5, 6].includes(index)) {
            cell.dataset.sortValue = entry === null ? "" : String(entry);
            cell.textContent = entry === null ? "—" : value(entry, "");
          }
          row.appendChild(cell);
        });
        tbody.appendChild(row);
      });
      if (sorter) sorter.refresh();
      else sorter = reference.makeTableSortable(table);
    }
    function populatePicker() {
      picker.replaceChildren();
      model.items.forEach(function (item) {
        var control = button(item.label, "budget-map-pick");
        control.dataset.budgetId = item.id;
        control.addEventListener("click", function () { activeId = item.id; renderDetails(); });
        picker.appendChild(control);
      });
    }
    function openDetails() {
      if (dialog.open) return;
      populatePicker();
      renderDetails();
      renderRecords();
      feedback.textContent = "";
      clearInspection();
      dialog.showModal();
      (Array.from(picker.children).find(function (control) { return control.dataset.budgetId === activeId; }) || dialog.querySelector("button")).focus();
    }

    if (interactive) {
      dialog = element("dialog", "modal-dialog budget-map-dialog");
      dialog.id = prefix + "-dialog";
      dialog.setAttribute("aria-labelledby", prefix + "-dialog-title");
      surface.setAttribute("aria-haspopup", "dialog");
      surface.setAttribute("aria-controls", dialog.id);
      var dialogHeader = element("div", "budget-map-dialog-header");
      var dialogTitle = element("h2", "modal-title sr-only", model.title + " · details");
      dialogTitle.id = prefix + "-dialog-title";
      var close = button("", "btn-icon");
      close.setAttribute("aria-label", "Close");
      close.title = "Close";
      close.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#lucide-x"></use></svg>';
      close.addEventListener("click", function () { dialog.close(); });
      var body = element("div", "budget-map-dialog-body");
      picker = element("div", "budget-map-picker");
      picker.setAttribute("role", "group");
      picker.setAttribute("aria-label", "Inspect budget");
      dialogHeader.append(picker, close);
      details = element("div", "budget-map-details");
      tableRegion = element("div", "table-container budget-map-data");
      tableRegion.id = prefix + "-data";
      tableRegion.tabIndex = 0;
      tableRegion.setAttribute("role", "region");
      tableRegion.setAttribute("aria-label", "Exact budget data");
      tableRegion.hidden = true;
      table = sortableTable(prefix + "-table", "All budget observations, current values, and period-end forecasts. Missing values are unavailable, not zero.", reference.budgetMapModel.columns, [3, 5, 6]);
      tbody = table.querySelector("tbody");
      tableRegion.appendChild(table);
      dataToggle = button("Show exact data", "btn btn-secondary");
      dataToggle.setAttribute("aria-expanded", "false");
      dataToggle.setAttribute("aria-controls", tableRegion.id);
      dataToggle.addEventListener("click", function () {
        tableRegion.hidden = !tableRegion.hidden;
        dialog.dataset.exactData = String(!tableRegion.hidden);
        dataToggle.setAttribute("aria-expanded", String(!tableRegion.hidden));
        dataToggle.textContent = tableRegion.hidden ? "Show exact data" : "Hide exact data";
      });
      var actions = element("div", "budget-map-dialog-actions");
      feedback = element("div", "budget-map-context budget-map-feedback");
      feedback.setAttribute("role", "status");
      var exportButton = button("Export all data CSV", "btn btn-secondary");
      exportButton.addEventListener("click", function () {
        var url = URL.createObjectURL(new Blob([reference.budgetMapModel.csv(model)], { type: "text/csv;charset=utf-8" }));
        var link = element("a");
        link.href = url;
        link.download = "budget-map-" + new Date(model.start).toISOString().slice(0, 10) + "_" + new Date(model.end).toISOString().slice(0, 10) + ".csv";
        shell.appendChild(link); link.click(); link.remove();
        window.setTimeout(function () { URL.revokeObjectURL(url); }, 0);
        feedback.textContent = "All budget data exported as CSV";
      });
      actions.append(dataToggle, exportButton);
      body.append(details, actions, feedback, tableRegion);
      dialog.append(dialogTitle, dialogHeader, body);
      shell.appendChild(dialog);
      dialog.addEventListener("click", function (event) {
        var rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
      });
      dialog.addEventListener("keydown", function (event) {
        if (event.key !== "Tab") return;
        var controls = Array.from(dialog.querySelectorAll("button, summary, [tabindex='0']")).filter(function (control) { return !control.disabled && control.getClientRects().length; });
        var first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      });
      dialog.addEventListener("close", function () { if (!destroyed && !dialog.open) surface.focus({ preventScroll: true }); });
      surface.addEventListener("pointermove", function (event) {
        if (event.pointerType === "touch" || !drawing.hits.length) return;
        var rect = surface.getBoundingClientRect();
        var x = event.clientX - rect.left, y = event.clientY - rect.top;
        var nearest = drawing.hits.reduce(function (a, b) {
          return Math.hypot(b.x - x, b.y - y) < Math.hypot(a.x - x, a.y - y) ? b : a;
        });
        inspect(nearest, false);
      });
      surface.addEventListener("pointerleave", clearInspection);
      surface.addEventListener("blur", clearInspection);
      surface.addEventListener("keydown", function (event) {
        if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        if (event.key === "Escape") { clearInspection(); return; }
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        var items = model.items.filter(function (item) { return itemHit(item.id); });
        var index = Math.max(0, items.findIndex(function (item) { return item.id === activeId; }));
        if (event.key === "Home") index = 0;
        else if (event.key === "End") index = items.length - 1;
        else index = Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowRight" ? 1 : -1)));
        if (items[index]) inspect(itemHit(items[index].id), true);
      });
      surface.addEventListener("click", function (event) {
        if (event.detail && drawing.hits.length) {
          var rect = surface.getBoundingClientRect();
          var nearest = drawing.hits.reduce(function (a, b) {
            return Math.hypot(b.x - (event.clientX - rect.left), b.y - (event.clientY - rect.top)) < Math.hypot(a.x - (event.clientX - rect.left), a.y - (event.clientY - rect.top)) ? b : a;
          });
          activeId = nearest.id;
        }
        openDetails();
      });
    }
    var observer = new ResizeObserver(function () {
      if (!frameRequest) frameRequest = requestAnimationFrame(draw);
    });
    observer.observe(frame);
    renderContext(); draw();
    document.fonts.ready.then(draw);
    return {
      update: function (nextData) {
        if (destroyed) throw new Error("This budget map has been destroyed");
        var next = reference.budgetMapModel.prepare(nextData);
        model = next;
        if (!selectedItem()) activeId = model.items.length ? model.items[0].id : null;
        clearInspection(); renderContext(); draw();
        if (dialog) {
          dialog.querySelector(".modal-title").textContent = model.title + " · details";
          if (dialog.open) {
            var focusedId = picker.contains(document.activeElement) ? document.activeElement.dataset.budgetId : null;
            populatePicker(); renderDetails(); renderRecords();
            if (focusedId) {
              var nextFocus = Array.from(picker.children).find(function (control) { return control.dataset.budgetId === focusedId; });
              (nextFocus || picker.firstElementChild || dialog.querySelector("button")).focus();
            }
          }
        }
      },
      destroy: function () {
        destroyed = true;
        observer.disconnect();
        cancelAnimationFrame(frameRequest);
        if (dialog && dialog.open) dialog.close();
        shell.remove();
      }
    };
  };
}(window.ComponentReference));
