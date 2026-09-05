(function (reference) {
  var namespace = "http://www.w3.org/2000/svg";
  function node(parent, tag, attributes, text) {
    var element = document.createElementNS(namespace, tag);
    Object.keys(attributes || {}).forEach(function (key) { element.setAttribute(key, attributes[key]); });
    if (text !== undefined) element.textContent = text;
    parent.appendChild(element);
    return element;
  }
  function date(at) { return new Date(at).toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }); }
  function percent(value) { return value === null ? "—" : new Intl.NumberFormat("en", { maximumFractionDigits: 1 }).format(value) + "%"; }

  // Original billing-map camera: the time-zero left edge is near; time recedes right.
  reference.drawBudgetMap = function (svg, model, options) {
    var width = Math.max(240, options.width);
    var scale = Math.min(width - 32, 720) / 305;
    var rise = 94 * 0.82 * scale;
    var height = rise * 2 + 32;
    var origin = { x: (width - 15 * scale) / 2, y: height - 16 };
    var timeVector = { x: 160 * scale, y: -rise };
    var usageVector = { x: -145 * scale, y: -rise };
    function point(t, value) {
      return { x: origin.x + timeVector.x * t + usageVector.x * value / model.maximum,
        y: origin.y + timeVector.y * t + usageVector.y * value / model.maximum };
    }
    function coordinates(p) { return p.x + "," + p.y; }
    function line(parent, a, b, className) {
      return node(parent, "line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: className });
    }
    function marker(parent, p, className, severity, current) {
      if (current || severity === "good") return node(parent, "circle", { cx: p.x, cy: p.y, r: current ? 4 : 5.5, class: className });
      return node(parent, "rect", { x: p.x - 5.5, y: p.y - 5.5, width: 11, height: 11, rx: severity === "caution" ? 4 : 0, class: className });
    }
    function currentMarker(parent, p, item) {
      var logos = options.logos || {};
      var logo = Object.prototype.hasOwnProperty.call(logos, item.logo) ? logos[item.logo] : null;
      if (!logo) return marker(parent, p, "budget-map-current", item.severity, true);
      var size = width < 480 ? 16 : 22;
      var logoWidth = logo.ratio > 1.8 ? size * 1.2 : size;
      var logoHeight = logoWidth / logo.ratio;
      var mark = node(parent, "svg", { x: p.x - logoWidth / 2, y: p.y - logoHeight / 2,
        width: logoWidth, height: logoHeight, viewBox: logo.viewBox, class: "budget-map-current budget-map-logo",
        "data-logo": item.logo, "aria-hidden": "true", focusable: "false" });
      logo.paths.forEach(function (attributes) { node(mark, "path", attributes); });
      return mark;
    }
    svg.replaceChildren();
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    svg.style.height = height + "px";
    var ground = node(svg, "g", { class: "budget-map-ground" });
    node(ground, "polygon", { points: [point(0, 100), point(1, 100), point(1, model.maximum), point(0, model.maximum)].map(coordinates).join(" "), class: "budget-map-overrun" });
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) { line(ground, point(t, 0), point(t, model.maximum), "budget-map-grid"); });
    var ticks = [];
    for (var tick = 0; tick <= model.maximum; tick += 25) ticks.push(tick);
    if (ticks[ticks.length - 1] < model.maximum) ticks.push(model.maximum);
    ticks.forEach(function (value) {
      line(ground, point(0, value), point(1, value), value === 100 ? "budget-map-limit" : "budget-map-grid");
    });
    line(ground, point(0, model.maximum), point(0, 0), "budget-map-near-edge");
    line(ground, point(0, 0), point(1, 100), "budget-map-pace");
    line(ground, point(model.elapsed, 0), point(model.elapsed, model.maximum), "budget-map-now");

    var hits = [], groups = [];
    model.items.forEach(function (item) {
      var group = node(svg, "g", { class: "budget-map-item", "data-budget-id": item.id, "data-severity": item.severity });
      if (item.stale) group.classList.add("is-stale");
      groups.push(group);
      var observations = item.history.concat([{ at: model.now, value: item.current }]);
      var path = "", continuing = false;
      observations.forEach(function (sample) {
        if (sample.value === null) { continuing = false; return; }
        var p = point((sample.at - model.start) / (model.end - model.start), sample.value / item.limit * 100);
        path += (continuing ? " L " : " M ") + coordinates(p);
        continuing = true;
        hits.push({ id: item.id, x: p.x, y: p.y, at: sample.at, value: sample.value, kind: sample.at === model.now ? "Current" : "Observed" });
        node(group, "circle", { cx: p.x, cy: p.y, r: 2, class: "budget-map-history-point" });
      });
      if (path) node(group, "path", { d: path, class: "budget-map-observed" });
      var current = item.currentPercent === null ? null : point(model.elapsed, item.currentPercent);
      var forecast = item.forecastPercent === null ? null : point(1, item.forecastPercent);
      if (current && forecast) {
        line(group, current, forecast, "budget-map-forecast");
        if (item.currentPercent > 100 || item.forecastPercent > 100) {
          var delta = item.forecastPercent - item.currentPercent;
          var crossing = delta ? (100 - item.currentPercent) / delta : 0;
          var boundary = point(model.elapsed + (1 - model.elapsed) * Math.max(0, Math.min(1, crossing)), 100);
          var from = item.currentPercent > 100 ? current : boundary;
          var to = item.forecastPercent > 100 ? forecast : boundary;
          line(group, from, to, "budget-map-forecast budget-map-breach");
        }
      }
      if (current) currentMarker(group, current, item);
      if (forecast) {
        marker(group, forecast, "budget-map-endpoint", item.severity, false);
        hits.push({ id: item.id, x: forecast.x, y: forecast.y, at: model.end, value: item.forecast, kind: "Forecast" });
      }
    });
    if (!model.items.some(function (item) { return item.current !== null || item.forecast !== null; })) {
      node(svg, "text", { x: width / 2, y: height / 2, class: "budget-map-empty", "text-anchor": "middle" }, model.items.length ? "Budget data unavailable" : "No budgets");
    }
    return { hits: hits, groups: groups, width: width, height: height };
  };
  reference.budgetMapFormat = { date: date, percent: percent };
}(window.ComponentReference));
