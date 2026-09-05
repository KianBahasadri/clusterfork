(function (reference) {
  var instances = new WeakMap();
  var sequence = 0;
  var stateLabels = { good: "Operational", caution: "Degraded", danger: "Down" };
  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function timeLabel(timestamp) { return new Date(timestamp).toISOString().replace("T", " ").replace("Z", " UTC"); }
  function format(channel, value) {
    if (value === null || value === undefined) return "Unavailable";
    return channel.kind === "status" ? stateLabels[value] : value.toFixed(channel.decimals) + " " + channel.unit;
  }
  function severity(channel, value) {
    if (value === null) return { label: "Unavailable", tone: "nominal" };
    if (channel.kind === "status") return { label: stateLabels[value], tone: value };
    if (Number.isFinite(channel.critical) && value >= channel.critical) return { label: "Critical", tone: "danger" };
    if (Number.isFinite(channel.warning) && value >= channel.warning) return { label: "Elevated", tone: "caution" };
    return { label: value > channel.max ? "Above scale" : "Nominal", tone: "nominal" };
  }

  reference.createRealtimeMonitor = function (root, channels, options) {
    if (instances.has(root)) return instances.get(root);
    options = options || {};
    var model = reference.createRealtimeMonitorModel(channels, options);
    var interactive = options.interactive !== false, selection = null, destroyed = false, frame = null;
    var monitor = element("div", "realtime-monitor");
    monitor.dataset.provenance = options.simulated ? "simulated" : "observed";
    monitor.setAttribute("role", "group");
    monitor.setAttribute("aria-label", (options.label || "System monitor") + (options.simulated ? ", simulated telemetry" : ""));
    var resources = element("div", "monitor-resources"), services = element("div", "monitor-services");
    var tooltip = element("div", "tooltip-bubble monitor-tooltip");
    do { tooltip.id = "monitor-tooltip-" + (++sequence); } while (document.getElementById(tooltip.id));
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    var announcement = element("span", "sr-only");
    announcement.setAttribute("role", "status");
    announcement.setAttribute("aria-atomic", "true");
    monitor.append(resources, services, tooltip, announcement);
    root.appendChild(monitor);

    var rows = model.channels.map(function (channel) {
      var metric = channel.kind === "metric";
      var row = element("div", metric ? "monitor-resource" : "monitor-service");
      row.dataset.channel = channel.id;
      var label = element(metric ? "h3" : "span", metric ? "monitor-symbol" : "monitor-service-name");
      if (metric) {
        var icon = reference.createLucideIcon(channel.icon || "activity");
        icon.setAttribute("focusable", "false");
        label.append(icon, element("span", "sr-only", channel.label));
        label.title = channel.label;
      } else label.textContent = channel.label;
      var value = element("div", "monitor-value"), number = element("span"), unit = element("span", "monitor-unit", channel.unit || "");
      value.append(number, unit);
      var plot = element(interactive ? "button" : "div", "monitor-plot");
      if (interactive) plot.type = "button";
      else plot.setAttribute("role", "img");
      if (interactive) plot.setAttribute("aria-description", "Use Left and Right arrows, Home, or End to pause and inspect samples. Activate again or press Escape to resume the live view.");
      var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "monitor-history");
      svg.setAttribute("aria-hidden", "true");
      svg.setAttribute("focusable", "false");
      plot.appendChild(svg);
      if (metric) {
        row.append(label, value, plot);
        resources.appendChild(row);
      } else {
        plot.prepend(label);
        row.appendChild(plot);
        services.appendChild(row);
      }
      if (interactive) {
        plot.addEventListener("pointermove", function (event) { if (event.pointerType !== "touch") inspect(channel, plot, event); });
        plot.addEventListener("pointerleave", clearHover);
        plot.addEventListener("focus", function () { inspect(channel, plot, null); });
        plot.addEventListener("blur", clearHover);
        plot.addEventListener("click", function (event) {
          if (model.view().paused) { resume(); return; }
          model.pause();
          inspect(channel, plot, event.detail ? event : null);
          announceInspection();
        });
        plot.addEventListener("keydown", function (event) {
          if (event.key === "Escape" && !event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
            event.preventDefault();
            resume();
            return;
          }
          if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          model.pause();
          var samples = model.view().samples;
          if (!samples.length) { render(); return; }
          var index = selection ? samples.findIndex(function (sample) { return sample.timestamp === selection.timestamp; }) : samples.length - 1;
          if (index < 0) index = samples.length - 1;
          if (event.key === "Home") index = 0;
          else if (event.key === "End") index = samples.length - 1;
          else index = Math.max(0, Math.min(samples.length - 1, index + (event.key === "ArrowLeft" ? -1 : 1)));
          selection = { channel: channel, timestamp: samples[index].timestamp };
          render();
          announceInspection();
        });
      }
      return { channel: channel, plot: plot, svg: svg, number: number, unit: unit, value: value };
    });
    resources.hidden = !resources.children.length;
    services.hidden = !services.children.length;

    function inspect(channel, plot, event) {
      var view = model.view();
      if (!view.samples.length) { selection = { channel: channel, timestamp: null }; render(); return; }
      var rect = plot.getBoundingClientRect();
      var timestamp = event ? view.start + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * model.windowMs : selection ? selection.timestamp : view.end;
      var nearest = view.samples.reduce(function (best, sample) { return Math.abs(sample.timestamp - timestamp) < Math.abs(best.timestamp - timestamp) ? sample : best; });
      selection = { channel: channel, timestamp: nearest.timestamp };
      render();
    }
    function clearHover() { if (!model.view().paused) { selection = null; render(); } }
    function announceInspection() { announcement.textContent = tooltip.textContent || "Paused. No samples to inspect."; }
    function positionTooltip() {
      if (destroyed || tooltip.hidden || !selection) return;
      var view = model.view(), viewport = window.visualViewport;
      var left = viewport ? viewport.offsetLeft : 0, top = viewport ? viewport.offsetTop : 0;
      var width = viewport ? viewport.width : document.documentElement.clientWidth;
      var height = viewport ? viewport.height : window.innerHeight;
      tooltip.style.maxWidth = Math.min(360, width - 24) + "px";
      var row = rows.find(function (candidate) { return candidate.channel === selection.channel; });
      var rect = row.svg.getBoundingClientRect(), bubble = tooltip.getBoundingClientRect();
      var fraction = selection.timestamp === null ? 1 : (selection.timestamp - view.start) / model.windowMs;
      var x = rect.left + Math.max(0, Math.min(1, fraction)) * rect.width - bubble.width / 2;
      var y = rect.top - bubble.height - 8;
      if (y < top + 12) y = rect.bottom + 8;
      tooltip.style.left = Math.max(left + 12, Math.min(left + width - bubble.width - 12, x)) + "px";
      tooltip.style.top = Math.max(top + 12, Math.min(top + height - bubble.height - 12, y)) + "px";
    }
    function describeFeed(view) {
      return view.feed + (view.age === null ? "" : " · Last sample " + Math.floor(view.age / 1000) + "s ago");
    }
    function render() {
      if (destroyed) return;
      var view = model.view();
      var inspected = selection && view.samples.find(function (sample) { return sample.timestamp === selection.timestamp; });
      if (selection && selection.timestamp !== null && !inspected) selection = null;
      if (monitor.dataset.feed !== view.feed) announcement.textContent = (options.label || "System monitor") + ": " + view.feed;
      monitor.dataset.feed = view.feed;
      monitor.dataset.paused = String(view.paused);
      rows.forEach(function (row) {
        var channel = row.channel, value = view.latest ? view.latest.values[channel.id] : null;
        var status = severity(channel, value);
        var historic = view.paused || view.feed !== "Live";
        row.number.textContent = value === null ? "—" : channel.kind === "metric" ? value.toFixed(channel.decimals) : "";
        row.unit.hidden = value === null;
        row.value.dataset.tone = status.tone;
        var scale = channel.kind === "metric" ? " Scale 0–" + channel.max + " " + channel.unit + ". " + status.label + (value > channel.max ? ", off scale" : "") + "." : "";
        row.plot.setAttribute("aria-label", channel.label + ": " + (historic ? "last shown " : "") + format(channel, value) + ". " + (options.simulated ? "Simulated. " : "") + describeFeed(view) + ". " + model.windowMs / 1000 + " seconds of history; " + model.interval / 1000 + " second cadence." + scale + (Number.isFinite(channel.warning) ? " Warning at " + channel.warning + " " + channel.unit + "." : "") + (interactive ? view.paused ? " Activate to resume." : " Activate to pause and inspect." : ""));
        if (interactive) row.plot.setAttribute("aria-pressed", String(view.paused));
        if (selection && selection.channel === channel) row.plot.setAttribute("aria-describedby", tooltip.id);
        else row.plot.removeAttribute("aria-describedby");
        reference.drawRealtimeMonitorPlot(row.svg, channel, view, inspected);
      });
      tooltip.hidden = !selection;
      if (selection) {
        var channel = selection.channel, sample = inspected || view.latest;
        var value = sample ? sample.values[channel.id] : null;
        var lines = [channel.label + ": " + format(channel, value), (sample ? timeLabel(sample.timestamp) : "No samples received") + (options.simulated ? " · Simulated" : "")];
        if (channel.kind === "metric") lines.push("0–" + channel.max + " " + channel.unit + " · " + severity(channel, value).label + (value > channel.max ? " · off scale" : "") + (Number.isFinite(channel.warning) ? " · Warning at " + channel.warning + " " + channel.unit : ""));
        lines.push(model.windowMs / 1000 + "s history · " + model.interval / 1000 + "s cadence · " + describeFeed(view));
        if (view.paused) lines.push("Paused · Activate again to resume");
        tooltip.textContent = lines.join("\n");
        positionTooltip();
      }
    }
    function schedule() {
      if (destroyed || frame !== null || document.hidden) return;
      frame = requestAnimationFrame(function () { frame = null; render(); });
    }
    function resume() { model.resume(); selection = null; render(); announcement.textContent = "Live view resumed. " + model.view().feed; }
    var observer = new ResizeObserver(schedule);
    observer.observe(monitor);
    var timer = setInterval(schedule, 1000);
    document.addEventListener("visibilitychange", schedule);
    window.addEventListener("scroll", positionTooltip, true);
    window.addEventListener("resize", positionTooltip);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("scroll", positionTooltip);
      window.visualViewport.addEventListener("resize", positionTooltip);
    }
    var instance = {
      push: function (snapshot) { if (destroyed) return false; var accepted = model.push(snapshot); if (accepted) schedule(); return accepted; },
      setConnection: function (state) { if (!destroyed) { model.setConnection(state); render(); } },
      pause: function () { if (!destroyed) { model.pause(); render(); } },
      resume: function () { if (!destroyed) resume(); },
      destroy: function () {
        destroyed = true;
        clearInterval(timer);
        if (frame !== null) cancelAnimationFrame(frame);
        observer.disconnect();
        document.removeEventListener("visibilitychange", schedule);
        window.removeEventListener("scroll", positionTooltip, true);
        window.removeEventListener("resize", positionTooltip);
        if (window.visualViewport) {
          window.visualViewport.removeEventListener("scroll", positionTooltip);
          window.visualViewport.removeEventListener("resize", positionTooltip);
        }
        monitor.remove();
        instances.delete(root);
      }
    };
    instances.set(root, instance);
    render();
    return instance;
  };
}(window.ComponentReference = window.ComponentReference || {}));
