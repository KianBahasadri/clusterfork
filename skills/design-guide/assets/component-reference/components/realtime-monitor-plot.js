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
      if (value !== null) {
        var sampleZone = reference.realtimeMonitorZone(channel, value);
        var suffix = sampleZone === "unknown" ? "nominal" : (sampleZone || "nominal");
        segment.push({ x: x(sample.timestamp), y: y(value), zone: suffix });
      }
      previous = sample.timestamp;
    });
    if (segment.length) segments.push(segment);
    segments.forEach(function (points) {
      var n = points.length;
      if (n === 1) {
        var p0 = points[0];
        nodes.push(svgElement("circle", { cx: p0.x.toFixed(2), cy: p0.y.toFixed(2), r: 2, class: "monitor-point monitor-point-" + p0.zone }));
        return;
      }
      var i = 0;
      while (i < n) {
        var currentZone = points[i].zone;
        var runStart = i;
        while (i + 1 < n && points[i + 1].zone === currentZone) {
          i++;
        }
        var runEnd = i;
        var polyline = [];
        if (runStart > 0) {
          var prev = points[runStart - 1], curr = points[runStart];
          polyline.push([(prev.x + curr.x) / 2, (prev.y + curr.y) / 2]);
        }
        for (var k = runStart; k <= runEnd; k++) {
          polyline.push([points[k].x, points[k].y]);
        }
        if (runEnd < n - 1) {
          var last = points[runEnd], next = points[runEnd + 1];
          polyline.push([(last.x + next.x) / 2, (last.y + next.y) / 2]);
        }
        var lineD = polyline.map(function (pt, idx) {
          return (idx ? "L" : "M") + pt[0].toFixed(2) + " " + pt[1].toFixed(2);
        }).join(" ");
        var areaD = lineD + "L" + polyline[polyline.length - 1][0].toFixed(2) + " " + bottom + "L" + polyline[0][0].toFixed(2) + " " + bottom + "Z";
        nodes.push(svgElement("path", { d: areaD, class: "monitor-area monitor-area-" + currentZone }));
        nodes.push(svgElement("path", { d: lineD, class: "monitor-trace monitor-trace-" + currentZone }));
        i++;
      }
    });
    return y;
  }

  function drawHeatstrip(nodes, channel, view, width, height) {
    var metric = channel.kind === "metric";
    var cellH = 18;
    var y = (height - cellH) / 2;
    var numBars = 40;
    var slot = width / numBars;
    var gap = slot >= 3 ? 1 : 0;
    var cellW = Math.max(0.5, slot - gap);
    var windowSpan = view.end - view.start;
    var bucketDuration = windowSpan / numBars;

    for (var i = 0; i < numBars; i++) {
      var barStart = view.start + i * bucketDuration;
      var barEnd = barStart + bucketDuration;
      var barMid = (barStart + barEnd) / 2;

      var matchingSamples = [];
      for (var s = 0; s < view.samples.length; s++) {
        var sample = view.samples[s];
        var isLast = (i === numBars - 1);
        if (sample.timestamp >= barStart && (isLast ? sample.timestamp <= barEnd : sample.timestamp < barEnd)) {
          matchingSamples.push(sample);
        }
      }

      var chosenValue = null;
      if (matchingSamples.length > 0) {
        if (metric) {
          var worstZone = "good";
          var maxVal = null;
          for (var m = 0; m < matchingSamples.length; m++) {
            var v = matchingSamples[m].values[channel.id];
            if (v !== null && Number.isFinite(v)) {
              var z = reference.realtimeMonitorZone(channel, v);
              if (z === "danger") { worstZone = "danger"; maxVal = v; }
              else if (z === "caution" && worstZone !== "danger") { worstZone = "caution"; maxVal = v; }
              else if (maxVal === null || v > maxVal) { maxVal = v; }
            }
          }
          chosenValue = maxVal;
        } else {
          var worstState = null;
          for (var m = 0; m < matchingSamples.length; m++) {
            var st = matchingSamples[m].values[channel.id];
            if (st === "danger") worstState = "danger";
            else if (st === "caution" && worstState !== "danger") worstState = "caution";
            else if (st === "good" && !worstState) worstState = "good";
          }
          chosenValue = worstState;
        }
      } else {
        var nearest = null;
        var minDist = Infinity;
        for (var s = 0; s < view.samples.length; s++) {
          var sample = view.samples[s];
          var dist = Math.abs(sample.timestamp - barMid);
          if (dist < minDist) {
            minDist = dist;
            nearest = sample;
          }
        }
        var maxTolerance = Math.max(bucketDuration, view.interval) * 1.5;
        if (nearest && minDist <= maxTolerance) {
          chosenValue = nearest.values[channel.id];
        }
      }

      if (chosenValue === null || chosenValue === undefined) continue;

      var zone = reference.realtimeMonitorZone(channel, chosenValue);
      var cls = metric ? heatClass(zone) : "monitor-state monitor-state-" + (chosenValue || "unknown");
      if (!cls) continue;

      var t = metric && Number.isFinite(chosenValue) ? Math.max(0, Math.min(1, chosenValue / channel.max)) : 1;
      var opacity = metric && (zone === "good" || zone === "nominal") ? (0.22 + 0.78 * t).toFixed(2) : "1";
      var rx = !metric && chosenValue === "good" ? 1.5 : !metric && chosenValue === "caution" ? 1 : 0;
      var barX = i * slot;

      nodes.push(svgElement("rect", {
        x: barX.toFixed(2), y: y, width: cellW.toFixed(2), height: cellH, rx: rx,
        class: cls, "fill-opacity": opacity
      }));
    }
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

  reference.drawRealtimeMonitorArc = function (svg, channel, value, peakValue) {
    var nodes = [];
    var zone = reference.realtimeMonitorZone(channel, value);
    var hasBands = Number.isFinite(channel.warning) || Number.isFinite(channel.critical);
    var hasFill = value !== null && Number.isFinite(value) && value > 0;
    var warn = Number.isFinite(channel.warning) ? channel.warning : (Number.isFinite(channel.critical) ? channel.critical : channel.max);
    var crit = Number.isFinite(channel.critical) ? channel.critical : channel.max;
    var trackStart = hasFill ? toDeg(value, channel.max) : ARC_START;
    var trackEnd = hasBands ? toDeg(warn, channel.max) : ARC_START + ARC_SWEEP;
    svg.setAttribute("viewBox", "0 0 " + ARC_SIZE + " " + ARC_SIZE);
    if (trackEnd - trackStart >= 0.4) {
      nodes.push(svgElement("path", { d: arcPath(trackStart, trackEnd, ARC_R), class: "monitor-arc-track" }));
    }
    if (!hasFill) {
      var startPt = polar(ARC_START, ARC_R);
      nodes.push(svgElement("circle", { cx: startPt.x.toFixed(2), cy: startPt.y.toFixed(2), r: 4, class: "monitor-arc-cap monitor-arc-cap-track" }));
    }
    if (!hasBands) {
      var endPt = polar(ARC_START + ARC_SWEEP, ARC_R);
      nodes.push(svgElement("circle", { cx: endPt.x.toFixed(2), cy: endPt.y.toFixed(2), r: 4, class: "monitor-arc-cap monitor-arc-cap-track" }));
    }
    if (hasBands) {
      if (crit > warn) nodes.push(svgElement("path", { d: arcPath(toDeg(warn, channel.max), toDeg(crit, channel.max), ARC_R), class: "monitor-arc-band monitor-arc-band-caution" }));
      if (channel.max > crit) nodes.push(svgElement("path", { d: arcPath(toDeg(crit, channel.max), toDeg(channel.max, channel.max), ARC_R), class: "monitor-arc-band monitor-arc-band-danger" }));
      var terminalZone = channel.max > crit ? "danger" : (crit > warn ? "caution" : null);
      if (terminalZone) {
        var terminalPt = polar(ARC_START + ARC_SWEEP, ARC_R);
        nodes.push(svgElement("circle", { cx: terminalPt.x.toFixed(2), cy: terminalPt.y.toFixed(2), r: 4, class: "monitor-arc-cap monitor-arc-cap-" + terminalZone }));
      }
    }
    if (hasFill) {
      var isPeakGauge = (peakValue !== undefined && peakValue !== null);
      var fillDeg = isPeakGauge ? Math.max(ARC_START, toDeg(value, channel.max) - 4.41) : toDeg(value, channel.max);
      var fillClass = "monitor-arc-fill monitor-arc-fill-" + (zone === "unknown" ? "nominal" : zone);
      nodes.push(svgElement("path", { d: arcPath(ARC_START, fillDeg, ARC_R), class: fillClass }));
    }
    if (peakValue !== undefined && peakValue !== null && Number.isFinite(peakValue) && peakValue > 0) {
      var peakDeg = toDeg(peakValue, channel.max);
      var inner = polar(peakDeg, ARC_R - 7), outer = polar(peakDeg, ARC_R + 7);
      nodes.push(svgElement("line", {
        x1: inner.x.toFixed(2), y1: inner.y.toFixed(2),
        x2: outer.x.toFixed(2), y2: outer.y.toFixed(2),
        class: "monitor-arc-tick"
      }));
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
    var activeSample = inspected || view.latest;
    var activeValue = activeSample ? activeSample.values[channel.id] : null;
    var zone = reference.realtimeMonitorZone(channel, activeValue);
    if (metric && presentation === "heatstrip") drawHeatstrip(nodes, channel, view, width, height, x);
    else if (metric && presentation === "sparkline") y = drawSparkline(nodes, channel, view, width, height, x);
    else if (presentation === "heatstrip") drawHeatstrip(nodes, channel, view, width, height, x);
    else if (!metric) drawStatusSpans(nodes, channel, view, height, x);
    if (inspected && inspected.timestamp >= view.start && inspected.timestamp <= view.end) {
      nodes.push(svgElement("path", { d: "M" + x(inspected.timestamp) + " 0V" + height, class: "monitor-crosshair" }));
      var value = inspected.values[channel.id];
      if (metric && presentation === "sparkline" && value !== null && y) {
        var inspectZone = reference.realtimeMonitorZone(channel, value);
        var inspectSuffix = inspectZone === "unknown" ? "nominal" : inspectZone;
        nodes.push(svgElement("circle", { cx: x(inspected.timestamp), cy: y(value), r: 3, class: "monitor-point monitor-point-" + inspectSuffix }));
      }
    }
    svg.replaceChildren.apply(svg, nodes);
  };
}(window.ComponentReference = window.ComponentReference || {}));
