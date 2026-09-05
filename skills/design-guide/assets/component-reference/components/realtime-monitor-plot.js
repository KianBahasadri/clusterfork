(function (reference) {
  var namespace = "http://www.w3.org/2000/svg";
  function svgElement(tag, attributes) {
    var node = document.createElementNS(namespace, tag);
    Object.keys(attributes).forEach(function (key) { node.setAttribute(key, attributes[key]); });
    return node;
  }

  reference.drawRealtimeMonitorPlot = function (svg, channel, view, inspected) {
    var width = Math.max(1, svg.getBoundingClientRect().width);
    var metric = channel.kind === "metric", height = metric ? 64 : 24;
    var x = function (time) { return Math.max(0, Math.min(width, (time - view.start) / (view.end - view.start) * width)); };
    var y = function (value) { return 60 - Math.min(1, value / channel.max) * 56; };
    var nodes = [];
    svg.setAttribute("viewBox", "0 0 " + width + " " + height);
    if (metric) {
      nodes.push(svgElement("path", { d: "M0 60H" + width, class: "monitor-baseline" }));
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
          nodes.push(svgElement("path", { d: line + "L" + points[points.length - 1][0] + " 60L" + points[0][0] + " 60Z", class: "monitor-area" }));
          nodes.push(svgElement("path", { d: line, class: "monitor-trace" }));
        } else {
          nodes.push(svgElement("circle", { cx: points[0][0], cy: points[0][1], r: 2, class: "monitor-point" }));
        }
      });
    } else {
      // Merge adjacent observations into quiet state spans, preserving delivery gaps.
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
          x: x(span.start), y: (height - h) / 2, width: x(span.end) - x(span.start), height: h,
          rx: state === "good" ? 1.5 : state === "caution" ? 1 : 0,
          class: "monitor-state monitor-state-" + (state || "unknown")
        }));
      });
    }
    if (inspected && inspected.timestamp >= view.start && inspected.timestamp <= view.end) {
      nodes.push(svgElement("path", { d: "M" + x(inspected.timestamp) + " 0V" + height, class: "monitor-crosshair" }));
      var value = inspected.values[channel.id];
      if (metric && value !== null) nodes.push(svgElement("circle", { cx: x(inspected.timestamp), cy: y(value), r: 3, class: "monitor-point" }));
    }
    svg.replaceChildren.apply(svg, nodes);
  };
}(window.ComponentReference = window.ComponentReference || {}));
