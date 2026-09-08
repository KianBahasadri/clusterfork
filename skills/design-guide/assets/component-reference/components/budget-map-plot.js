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

  // Low perspective camera: time runs right; the usage plane narrows into the distance.
  reference.drawBudgetMap = function (svg, model, options) {
    options = options || {};
    var width = Math.max(240, options.width);
    var clearance = width < 480 ? 8 : 16;
    var isDynamic = options.dynamicPerspective === true;
    var isDynamicCamera = options.dynamicCamera === true;
    var planeWidth = isDynamic ? width - clearance * 2 : Math.min(width - clearance * 2, 720);
    var depth = isDynamic ? Math.round(planeWidth * 0.90) : planeWidth * 0.34;
    var height = depth + clearance * 2;
    var baseUsage = 0;
    if (isDynamicCamera) {
      var minActive = Infinity;
      model.items.forEach(function (item) {
        if (item.currentPercent !== null) minActive = Math.min(minActive, item.currentPercent);
        if (item.forecastPercent !== null) minActive = Math.min(minActive, item.forecastPercent);
      });
      if (minActive >= 100) {
        baseUsage = 100;
      }
    }
    var overageRatio = Math.max(0, (model.maximum - 100) / 100);
    var tiltIntensity = options.tiltIntensity !== undefined ? options.tiltIntensity : 1;
    var perspective;
    if (options.perspective !== undefined) {
      perspective = options.perspective;
    } else if (isDynamic) {
      if (model.maximum <= 100) {
        perspective = 0;
      } else if (model.maximum <= 132) {
        perspective = (((model.maximum - 100) / 32) * 0.40) * tiltIntensity;
      } else {
        perspective = (0.40 + 0.21 * Math.min(5, (model.maximum - 132) / 100)) * tiltIntensity;
      }
    } else {
      perspective = 0.4;
    }
    var compressionIntensity = options.compressionIntensity !== undefined ? Math.max(0, options.compressionIntensity) : 1;
    var severityIntensity = options.severityIntensity !== undefined ? Math.max(0, options.severityIntensity) : 1;
    var compressNonOverage = options.compressNonOverage !== false;
    var kCompress = isDynamic ? (compressionIntensity + severityIntensity * 0.6 * Math.min(5, overageRatio)) : 0;
    var dNon = Math.max(0, 100 - baseUsage);
    var dOver = Math.max(0, model.maximum - 100);
    var u100 = (dOver > 0 && dNon > 0)
      ? (dNon * (1 + kCompress)) / (dOver + dNon * (1 + kCompress))
      : (model.maximum > baseUsage ? (100 - baseUsage) / (model.maximum - baseUsage) : 1);
    function point(t, value) {
      var progress = (value - baseUsage) / (model.maximum - baseUsage);
      var usage;
      if (isDynamic) {
        if (!compressNonOverage && dOver > 0) {
          if (baseUsage >= 100) {
            usage = progress >= 0 ? (progress * (1 + kCompress)) / (1 + kCompress * progress) : progress;
          } else if (value <= 100) {
            usage = dNon > 0 ? (u100 * (value - baseUsage)) / dNon : progress;
          } else {
            var pOver = (value - 100) / dOver;
            var g = (pOver * (1 + kCompress)) / (1 + kCompress * pOver);
            usage = u100 + (1 - u100) * g;
          }
        } else if (!compressNonOverage && dOver === 0) {
          usage = progress;
        } else {
          usage = progress >= 0 ? (progress * (1 + kCompress)) / (1 + kCompress * progress) : progress;
        }
      } else {
        usage = progress;
      }
      var distance = Math.max(0.1, 1 + perspective * usage);
      return { x: width / 2 + (t - 0.5) * planeWidth / distance,
        y: height - clearance - depth * usage * (1 + perspective) / distance };
    }
    function coordinates(p) { return p.x + "," + p.y; }
    function line(parent, a, b, className) {
      return node(parent, "line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: className });
    }
    function marker(parent, p, className, severity, current) {
      if (current) return node(parent, "circle", { cx: p.x, cy: p.y, r: 4, class: className });
      if (severity === "good") return node(parent, "circle", { cx: p.x, cy: p.y, r: 3.5, class: className });
      return node(parent, "rect", { x: p.x - 3.5, y: p.y - 3.5, width: 7, height: 7, rx: severity === "caution" ? 2 : 0, class: className });
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
    var clipId = "bmap-clip-" + Math.floor(Math.random() * 1000000);
    var defs = node(svg, "defs");
    var clipPath = node(defs, "clipPath", { id: clipId });
    node(clipPath, "polygon", {
      points: [point(0, baseUsage), point(1, baseUsage), point(1, model.maximum), point(0, model.maximum)].map(coordinates).join(" ")
    });
    var ground = node(svg, "g", { class: "budget-map-ground" });
    var groundStart = Math.max(baseUsage, 100);
    if (model.maximum > groundStart) {
      node(ground, "polygon", { points: [point(0, groundStart), point(1, groundStart), point(1, model.maximum), point(0, model.maximum)].map(coordinates).join(" "), class: "budget-map-overrun" });
    }
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) { line(ground, point(t, baseUsage), point(t, model.maximum), "budget-map-grid"); });
    var ticks = [];
    var startTick = Math.ceil(baseUsage / 25) * 25;
    for (var tick = startTick; tick <= model.maximum; tick += 25) ticks.push(tick);
    if (ticks.length === 0 || ticks[0] > baseUsage) ticks.unshift(baseUsage);
    if (ticks[ticks.length - 1] < model.maximum) ticks.push(model.maximum);
    ticks.forEach(function (value) {
      var isRed = value >= 100 && (value - 100) % 100 === 0;
      line(ground, point(0, value), point(1, value), isRed ? "budget-map-limit" : "budget-map-grid");
    });
    if (baseUsage === 0) {
      line(ground, point(0, 0), point(1, 0), "budget-map-near-edge");
    }
    if (baseUsage < 100) line(ground, point(0, baseUsage), point(1, 100), "budget-map-pace");
    line(ground, point(model.elapsed, baseUsage), point(model.elapsed, model.maximum), "budget-map-now");

    var hits = [], groups = [];
    model.items.forEach(function (item) {
      var group = node(svg, "g", { class: "budget-map-item", "data-budget-id": item.id, "data-severity": item.severity });
      if (item.stale) group.classList.add("is-stale");
      groups.push(group);
      var observations = item.history.concat([{ at: model.now, value: item.current }]);
      var path = "", continuing = false;
      observations.forEach(function (sample) {
        if (sample.value === null) { continuing = false; return; }
        var samplePercent = sample.value / item.limit * 100;
        var p = point((sample.at - model.start) / (model.end - model.start), samplePercent);
        path += (continuing ? " L " : " M ") + coordinates(p);
        continuing = true;
        if (samplePercent >= baseUsage) {
          hits.push({ id: item.id, x: p.x, y: p.y, at: sample.at, value: sample.value, kind: sample.at === model.now ? "Current" : "Observed" });
          node(group, "circle", { cx: p.x, cy: p.y, r: 2, class: "budget-map-history-point" });
        }
      });
      if (path) {
        var pathAttrs = { d: path, class: "budget-map-observed" };
        if (baseUsage > 0) pathAttrs["clip-path"] = "url(#" + clipId + ")";
        node(group, "path", pathAttrs);
      }
      var current = item.currentPercent === null ? null : point(model.elapsed, item.currentPercent);
      var showForecast = item.forecastPercent !== null && item.forecastPercent >= 50;
      var forecast = (item.forecastPercent === null || !showForecast) ? null : point(1, item.forecastPercent);
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
    return { hits: hits, groups: groups, width: width, height: height, baseUsage: baseUsage };
  };
  reference.budgetMapFormat = { date: date, percent: percent };
}(window.ComponentReference));
