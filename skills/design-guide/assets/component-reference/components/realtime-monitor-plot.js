(function (reference) {
  var namespace = "http://www.w3.org/2000/svg";
  var ARC_SIZE = 132, ARC_CX = 66, ARC_CY = 66, ARC_R = 52, ARC_START = 135, ARC_SWEEP = 270;

  function svgElement(tag, attributes) {
    var node = document.createElementNS(namespace, tag);
    Object.keys(attributes).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    return node;
  }

  function xAt(view, width, time) {
    return Math.max(0, Math.min(width, (time - view.start) / (view.end - view.start) * width));
  }

  reference.realtimeMonitorZone = function (channel, value) {
    if (channel.kind === "status") return value || "unknown";
    if (value === null || value === undefined || !Number.isFinite(value)) return "unknown";
    if (Number.isFinite(channel.critical) && value >= channel.critical) return "danger";
    if (Number.isFinite(channel.warning) && value >= channel.warning) return "caution";
    if (Number.isFinite(channel.warning) || Number.isFinite(channel.critical)) return "good";
    return "nominal";
  };

  function heatClass(zone) {
    if (zone === "danger") return "monitor-heat-danger";
    if (zone === "caution") return "monitor-heat-caution";
    if (zone === "good") return "monitor-heat-good";
    if (zone === "nominal") return "monitor-heat-accent";
    return null;
  }

  function drawSparkline(nodes, channel, view, width, height, x) {
    var bottom = height - 4, top = 4;
    var y = function (value) { return bottom - Math.min(1, value / channel.max) * (bottom - top); };
    nodes.push(svgElement("path", { d: "M0 " + bottom + "H" + width, class: "monitor-baseline" }));
    if (Number.isFinite(channel.warning) && channel.warning <= channel.max) {
      nodes.push(svgElement("path", { d: "M0 " + y(channel.warning) + "H" + width, class: "monitor-threshold" }));
    }
    var segments = [], segment = [], previous = null;
    view.samples.forEach(function (sample) {
      var value = sample.values[channel.id];
      if (value === null || (previous !== null && sample.timestamp - previous > view.interval * 1.5)) {
        if (segment.length) segments.push(segment);
        segment = [];
      }
      if (value !== null) segment.push([x(sample.timestamp), y(value)]);
      previous = sample.timestamp;
    });
    if (segment.length) segments.push(segment);
    segments.forEach(function (points) {
      var line = points.map(function (point, index) { return (index ? "L" : "M") + point.join(" "); }).join(" ");
      if (points.length > 1) {
        nodes.push(svgElement("path", { d: line + "L" + points[points.length - 1][0] + " " + bottom + "L" + points[0][0] + " " + bottom + "Z", class: "monitor-area" }));
        nodes.push(svgElement("path", { d: line, class: "monitor-trace" }));
      } else {
        nodes.push(svgElement("circle", { cx: points[0][0], cy: points[0][1], r: 2, class: "monitor-point" }));
      }
    });
    return y;
  }

  function drawHeatstrip(nodes, channel, view, width, height, x) {
    var metric = channel.kind === "metric";
    var cellH = metric ? 18 : 18;
    var y = (height - cellH) / 2;
    var slot = Math.max(1, view.interval / (view.end - view.start) * width);
    var gap = slot >= 3 ? 1 : 0;
    var cellW = Math.max(0.5, slot - gap);
    view.samples.forEach(function (sample) {
      var value = sample.values[channel.id];
      if (metric && value === null) return;
      var zone = reference.realtimeMonitorZone(channel, value);
      var cls = metric ? heatClass(zone) : "monitor-state monitor-state-" + (value || "unknown");
      if (!cls) return;
      var t = metric && Number.isFinite(value) ? Math.max(0, Math.min(1, value / channel.max)) : 1;
      var opacity = metric && (zone === "good" || zone === "nominal") ? (0.22 + 0.78 * t).toFixed(2) : "1";
      var rx = !metric && value === "good" ? 1.5 : !metric && value === "caution" ? 1 : 0;
      nodes.push(svgElement("rect", {
        x: x(sample.timestamp), y: y, width: cellW, height: cellH, rx: rx,
        class: cls, "fill-opacity": opacity
      }));
    });
  }

  function drawStatusSpans(nodes, channel, view, height, x) {
    var spans = [];
    view.samples.forEach(function (sample, index) {
      var state = sample.values[channel.id];
      var next = view.samples[index + 1];
      var contiguous = next && next.timestamp - sample.timestamp <= view.interval * 1.5;
      var end = Math.min(view.end, contiguous ? next.timestamp : sample.timestamp + view.interval);
      if (end <= sample.timestamp) return;
      var previous = spans[spans.length - 1];
      if (previous && previous.state === state && previous.end >= sample.timestamp) previous.end = end;
      else spans.push({ start: sample.timestamp, end: end, state: state });
    });
    spans.forEach(function (span) {
      var state = span.state;
      var h = state === "danger" ? 8 : state === "caution" ? 6 : state === "good" ? 3 : 1;
      nodes.push(svgElement("rect", {
        x: x(span.start), y: (height - h) / 2, width: Math.max(0, x(span.end) - x(span.start)), height: h,
        rx: state === "good" ? 1.5 : state === "caution" ? 1 : 0,
        class: "monitor-state monitor-state-" + (state || "unknown")
      }));
    });
  }

  function polar(deg, radius) {
    var rad = deg * Math.PI / 180;
    return { x: ARC_CX + radius * Math.cos(rad), y: ARC_CY + radius * Math.sin(rad) };
  }

  function arcPath(fromDeg, toDeg, radius) {
    var span = toDeg - fromDeg;
    if (span < 0.4) {
      var point = polar(fromDeg, radius);
      return "M" + point.x.toFixed(2) + " " + point.y.toFixed(2);
    }
    var start = polar(fromDeg, radius), end = polar(toDeg, radius);
    return "M" + start.x.toFixed(2) + " " + start.y.toFixed(2) + " A" + radius + " " + radius + " 0 " + (span > 180 ? 1 : 0) + " 1 " + end.x.toFixed(2) + " " + end.y.toFixed(2);
  }

  function toDeg(value, max) {
    var t = max > 0 && Number.isFinite(value) ? value / max : 0;
    return ARC_START + Math.max(0, Math.min(1, t)) * ARC_SWEEP;
  }

  reference.drawRealtimeMonitorArc = function (svg, channel, value) {
    var nodes = [];
    var zone = reference.realtimeMonitorZone(channel, value);
    var hasBands = Number.isFinite(channel.warning) || Number.isFinite(channel.critical);
    svg.setAttribute("viewBox", "0 0 " + ARC_SIZE + " " + ARC_SIZE);
    nodes.push(svgElement("path", { d: arcPath(ARC_START, ARC_START + ARC_SWEEP, ARC_R), class: "monitor-arc-track" }));
    if (hasBands) {
      var warn = Number.isFinite(channel.warning) ? channel.warning : (Number.isFinite(channel.critical) ? channel.critical : channel.max);
      var crit = Number.isFinite(channel.critical) ? channel.critical : channel.max;
      if (warn > 0) nodes.push(svgElement("path", { d: arcPath(toDeg(0, channel.max), toDeg(warn, channel.max), ARC_R), class: "monitor-arc-band monitor-arc-band-good" }));
      if (crit > warn) nodes.push(svgElement("path", { d: arcPath(toDeg(warn, channel.max), toDeg(crit, channel.max), ARC_R), class: "monitor-arc-band monitor-arc-band-caution" }));
      if (channel.max > crit) nodes.push(svgElement("path", { d: arcPath(toDeg(crit, channel.max), toDeg(channel.max, channel.max), ARC_R), class: "monitor-arc-band monitor-arc-band-danger" }));
    }
    if (Number.isFinite(channel.warning) && channel.warning <= channel.max) {
      var tick = toDeg(channel.warning, channel.max);
      var inner = polar(tick, ARC_R - 7), outer = polar(tick, ARC_R + 7);
      nodes.push(svgElement("line", { x1: inner.x.toFixed(2), y1: inner.y.toFixed(2), x2: outer.x.toFixed(2), y2: outer.y.toFixed(2), class: "monitor-arc-tick" }));
    }
    if (value !== null && Number.isFinite(value)) {
      var fillClass = "monitor-arc-fill monitor-arc-fill-" + (zone === "unknown" ? "nominal" : zone);
      nodes.push(svgElement("path", { d: arcPath(ARC_START, toDeg(value, channel.max), ARC_R), class: fillClass }));
      var knob = polar(toDeg(value, channel.max), ARC_R);
      nodes.push(svgElement("circle", { cx: knob.x.toFixed(2), cy: knob.y.toFixed(2), r: 4, class: "monitor-arc-knob monitor-arc-fill-" + (zone === "unknown" ? "nominal" : zone) }));
    }
    svg.replaceChildren.apply(svg, nodes);
  };

  reference.drawRealtimeMonitorPlot = function (svg, channel, view, inspected, presentation) {
    presentation = presentation || "sparkline";
    var width = Math.max(1, svg.getBoundingClientRect().width);
    var metric = channel.kind === "metric";
    var height = metric && presentation === "sparkline" ? 64 : 24;
    var x = function (time) { return xAt(view, width, time); };
    var nodes = [];
    var y = null;
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    if (metric && presentation === "heatstrip") drawHeatstrip(nodes, channel, view, width, height, x);
    else if (metric) y = drawSparkline(nodes, channel, view, width, height, x);
    else if (presentation === "heatstrip") drawHeatstrip(nodes, channel, view, width, height, x);
    else drawStatusSpans(nodes, channel, view, height, x);
    if (inspected && inspected.timestamp >= view.start && inspected.timestamp <= view.end) {
      nodes.push(svgElement("path", { d: "M" + x(inspected.timestamp) + " 0V" + height, class: "monitor-crosshair" }));
      var value = inspected.values[channel.id];
      if (metric && presentation !== "heatstrip" && value !== null && y) {
        nodes.push(svgElement("circle", { cx: x(inspected.timestamp), cy: y(value), r: 3, class: "monitor-point" }));
      }
    }
    svg.replaceChildren.apply(svg, nodes);
  };
}(window.ComponentReference = window.ComponentReference || {}));
