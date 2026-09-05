(function (chart) {
  function svgNode(name, attributes, text) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attributes).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  chart.createPlot = function (host, actions) {
    var surface = host.querySelector(".chart-surface");
    var svg = host.querySelector("svg");
    var tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.id = svg.id + "-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    host.appendChild(tooltip);
    var announcement = document.createElement("span");
    announcement.className = "sr-only";
    announcement.setAttribute("aria-live", "polite");
    host.appendChild(announcement);
    var help = surface.getAttribute("aria-describedby") || "";
    var view, geometry, cursorLayer;
    var index = 0;
    var active = false;

    function xFor(sampleIndex) {
      var first = Date.parse(chart.samples[view.start].time);
      var last = Date.parse(chart.samples[view.end].time);
      return geometry.left + (Date.parse(chart.samples[sampleIndex].time) - first) / (last - first) * geometry.plotWidth;
    }

    function yFor(value) {
      return geometry.bottom - (value - geometry.scale.min) / (geometry.scale.max - geometry.scale.min) * geometry.plotHeight;
    }

    function sampleAt(clientX) {
      var rect = svg.getBoundingClientRect();
      var x = (clientX - rect.left) / rect.width * geometry.width;
      var nearest = view.start;
      for (var i = view.start + 1; i <= view.end; i++) {
        if (Math.abs(xFor(i) - x) < Math.abs(xFor(nearest) - x)) nearest = i;
      }
      return nearest;
    }

    function marker(sampleIndex, series, radius) {
      var x = xFor(sampleIndex), y = yFor(chart.samples[sampleIndex][series.key]);
      var attributes = { "class": "chart-point", "data-series": series.key };
      if (series.key === "forecast") {
        attributes.d = "M" + x + "," + (y - radius) + "l" + radius + "," + radius + "l" + (-radius) + "," + radius + "l" + (-radius) + "," + (-radius) + "Z";
        return svgNode("path", attributes);
      }
      attributes.cx = x; attributes.cy = y; attributes.r = radius;
      return svgNode("circle", attributes);
    }

    function hide() {
      tooltip.hidden = true;
      if (cursorLayer) cursorLayer.replaceChildren();
      if (help) surface.setAttribute("aria-describedby", help);
      else surface.removeAttribute("aria-describedby");
    }

    function inspect(next, announce) {
      if (!geometry) return;
      index = Math.max(view.start, Math.min(view.end, next));
      actions.onInspect(index);
      var sample = chart.samples[index];
      cursorLayer.replaceChildren(svgNode("line", { x1: xFor(index), x2: xFor(index), y1: geometry.top, y2: geometry.bottom, "class": "chart-crosshair" }));
      tooltip.replaceChildren();
      var timestamp = document.createElement("time");
      timestamp.dateTime = sample.time;
      timestamp.textContent = chart.timeLabel(sample.time, true) + " UTC";
      tooltip.appendChild(timestamp);
      var values = document.createElement("dl");
      var description = [timestamp.textContent];
      function addValue(label, value) {
        var term = document.createElement("dt"), detail = document.createElement("dd");
        term.textContent = label; detail.textContent = chart.valueLabel(value);
        values.append(term, detail);
        description.push(label + " " + detail.textContent);
      }
      chart.series.forEach(function (series) {
        if (!view.visible[series.key]) return;
        addValue(series.label, sample[series.key]);
        if (sample[series.key] !== null) cursorLayer.appendChild(marker(index, series, 4));
      });
      if (view.visible.observed && view.visible.forecast && sample.forecast !== null) addValue("Forecast delta", chart.delta(sample));
      tooltip.appendChild(values);
      tooltip.hidden = false;
      surface.setAttribute("aria-describedby", (help + " " + tooltip.id).trim());
      var bounds = host.getBoundingClientRect();
      var box = tooltip.getBoundingClientRect();
      var x = xFor(index) + 14;
      if (x + box.width > bounds.width - 8) x = xFor(index) - box.width - 14;
      var value = view.visible.observed ? sample.observed : sample.forecast;
      var y = value === null ? geometry.top : yFor(value) - box.height - 12;
      tooltip.style.left = Math.max(8, Math.min(bounds.width - box.width - 8, x)) + "px";
      tooltip.style.top = Math.max(8, Math.min(bounds.height - box.height - 8, y)) + "px";
      if (announce) announcement.textContent = description.join(". ");
    }

    function render(nextView) {
      view = nextView;
      var rect = svg.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      var left = rect.width < 420 ? 48 : 58;
      geometry = { width: rect.width, height: rect.height, left: left, top: 20, bottom: rect.height - 30, plotWidth: rect.width - left - 16, plotHeight: rect.height - 50, scale: chart.scale(view) };
      svg.setAttribute("viewBox", "0 0 " + rect.width + " " + rect.height);
      svg.replaceChildren();
      var axes = svgNode("g", { "aria-hidden": "true" });
      svg.appendChild(axes);
      axes.appendChild(svgNode("text", { x: left - 10, y: 10, "text-anchor": "end", "class": "chart-axis-text" }, "ms"));
      geometry.scale.ticks.forEach(function (tick) {
        var y = yFor(tick);
        if (view.grid) axes.appendChild(svgNode("line", { x1: left, x2: rect.width - 16, y1: y, y2: y, "class": "chart-grid-line" }));
        axes.appendChild(svgNode("text", { x: left - 10, y: y + 4, "text-anchor": "end", "class": "chart-axis-text" }, chart.numbers.format(tick)));
      });
      var maxTicks = Math.min(5, Math.max(2, Math.floor(geometry.plotWidth / 100)));
      var tickStep = Math.ceil((view.end - view.start) / (maxTicks - 1));
      var timeTicks = [];
      for (var tick = view.start; tick < view.end; tick += tickStep) timeTicks.push(tick);
      if (timeTicks.length > 1 && xFor(view.end) - xFor(timeTicks[timeTicks.length - 1]) < 80) timeTicks.pop();
      timeTicks.push(view.end);
      timeTicks.forEach(function (sampleIndex, tickIndex) {
        var time = chart.samples[sampleIndex].time;
        var anchor = tickIndex === 0 ? "start" : tickIndex === timeTicks.length - 1 ? "end" : "middle";
        axes.appendChild(svgNode("text", { x: xFor(sampleIndex), y: rect.height - 7, "text-anchor": anchor, "class": "chart-axis-text" }, chart.timeLabel(time, time.slice(17, 19) !== "00")));
      });
      chart.series.forEach(function (series) {
        if (!view.visible[series.key]) return;
        var segments = [], current = [];
        for (var i = view.start; i <= view.end; i++) {
          if (chart.samples[i][series.key] === null) {
            if (current.length) segments.push(current);
            current = [];
          } else current.push(i);
        }
        if (current.length) segments.push(current);
        segments.forEach(function (segment) {
          var line = segment.map(function (sampleIndex, position) { return (position ? "L" : "M") + xFor(sampleIndex) + "," + yFor(chart.samples[sampleIndex][series.key]); }).join(" ");
          if (view.style === "area") svg.appendChild(svgNode("path", { d: line + "L" + xFor(segment[segment.length - 1]) + "," + geometry.bottom + "L" + xFor(segment[0]) + "," + geometry.bottom + "Z", "class": "chart-area", "data-series": series.key }));
          svg.appendChild(svgNode("path", { d: line, "class": "chart-series", "data-series": series.key }));
          if (view.points || segment.length === 1) segment.forEach(function (sampleIndex) { svg.appendChild(marker(sampleIndex, series, 3)); });
        });
      });
      if (view.target !== null) {
        var y = yFor(view.target);
        svg.appendChild(svgNode("line", { x1: left, x2: rect.width - 16, y1: y, y2: y, "class": "chart-target-line" }));
        svg.appendChild(svgNode("text", { x: rect.width - 16, y: Math.max(12, y - 6), "text-anchor": "end", "class": "chart-target-label" }, "Target " + chart.valueLabel(view.target)));
      }
      if (!geometry.scale.hasValues) svg.appendChild(svgNode("text", { x: left + geometry.plotWidth / 2, y: geometry.top + geometry.plotHeight / 2, "text-anchor": "middle", "class": "chart-empty" }, "No data in this range"));
      cursorLayer = svgNode("g", { "aria-hidden": "true" });
      svg.appendChild(cursorLayer);
      index = Math.max(view.start, Math.min(view.end, view.cursor));
      if (view.pinned || active || document.activeElement === surface) inspect(index, false);
      else hide();
    }

    surface.addEventListener("pointermove", function (event) {
      if (!geometry || event.pointerType === "touch" || view.pinned) return;
      active = true;
      inspect(sampleAt(event.clientX), false);
    });
    surface.addEventListener("pointerleave", function () {
      active = false;
      if (!view.pinned && !surface.matches(":focus-visible")) hide();
    });
    surface.addEventListener("focus", function () { inspect(view.cursor, true); });
    surface.addEventListener("blur", function () { if (!view.pinned) hide(); });
    surface.addEventListener("click", function (event) {
      if (!geometry) return;
      if (event.detail && !view.pinned) index = sampleAt(event.clientX);
      actions.onActivate(index);
    });
    surface.addEventListener("keydown", function (event) {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      var next;
      if (event.key === "ArrowLeft") next = index - 1;
      if (event.key === "ArrowRight") next = index + 1;
      if (event.key === "Home") next = view.start;
      if (event.key === "End") next = view.end;
      if (next !== undefined) {
        event.preventDefault();
        inspect(next, true);
      } else if (surface.tagName !== "BUTTON" && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        actions.onActivate(index);
      } else if (event.key === "Escape" && !surface.closest("dialog")) {
        event.preventDefault();
        active = false;
        hide();
      }
    });
    new ResizeObserver(function () { if (view) render(view); }).observe(svg);
    return { render: render, hide: hide };
  };
}(window.ComponentReference.chart));
