(function (reference) {
  var metricChannels = [
    { id: "cpu", kind: "metric", label: "CPU", icon: "cpu", unit: "%", max: 100, warning: 80, critical: 95 },
    { id: "memory", kind: "metric", label: "Memory", icon: "memory-stick", unit: "GiB", max: 32, decimals: 1, warning: 25.6, critical: 30.4 },
    { id: "rx", kind: "metric", label: "Network in", icon: "download", unit: "MiB/s", max: 20, decimals: 1, warning: 16, critical: 19 },
    { id: "tx", kind: "metric", label: "Network out", icon: "upload", unit: "MiB/s", max: 20, decimals: 1, warning: 16, critical: 19 }
  ];
  var serviceChannels = [
    { id: "api", kind: "status", label: "API gateway" },
    { id: "workers", kind: "status", label: "Workers" },
    { id: "database", kind: "status", label: "Database" }
  ];
  var mounts = [
    { id: "realtimeMonitorExample", channels: metricChannels, presentation: "sparkline", label: "node-01" },
    { id: "realtimeMonitorHeatstripExample", channels: metricChannels, presentation: "heatstrip", label: "node-01" },
    { id: "realtimeMonitorArcExample", channels: metricChannels, presentation: "arc", label: "node-01" },
    { id: "realtimeMonitorArcPeakExample", channels: metricChannels, presentation: "arc", label: "node-01", peakHold: true },
    { id: "realtimeMonitorServicesExample", channels: serviceChannels, presentation: "sparkline", label: "node-01 services" }
  ];
  var speedLevels = [
    { label: "0.25x (4.0s)", interval: 4000 },
    { label: "0.5x (2.0s)",  interval: 2000 },
    { label: "1.0x (1.0s)",  interval: 1000 },
    { label: "2.0x (500ms)", interval: 500 },
    { label: "10x (100ms)",  interval: 100 }
  ];
  var currentSpeedLevel = 2;
  var currentInterval = speedLevels[currentSpeedLevel].interval;
  var windowMs = 60000;
  var monitors = mounts.map(function (mount) {
    var root = document.getElementById(mount.id);
    return root ? reference.createRealtimeMonitor(root, mount.channels, {
      label: mount.label, simulated: true, presentation: mount.presentation,
      interval: currentInterval, windowMs: windowMs, maxSamples: 1200,
      peakHold: Boolean(mount.peakHold)
    }) : null;
  }).filter(Boolean);
  if (!monitors.length) return;

  var freqLabels = ["None (nominal)", "Low (rare)", "Normal", "High (frequent)", "Critical (extreme)"];
  var freqPeriods = [0, 120, 60, 30, 18];
  var currentFreqLevel = 2;
  var tickTimer = null;
  var origin = Date.now();

  function sample(timestamp) {
    var second = (timestamp - origin) / 1000;
    var period = freqPeriods[currentFreqLevel];
    var p = period > 0 ? (((Math.floor(second) % period) + period) % period) / period : -1;
    var active = currentFreqLevel > 0;
    var intense = currentFreqLevel >= 4;

    var baseCpu = 38 + Math.sin(second * 0.22) * 16 + Math.sin(second * 1.6) * 4;
    var baseMem = 18.4 + Math.sin(second * 0.07) * 1.4;
    var baseRx = 6.5 + Math.sin(second * 0.28) * 3.8 + Math.sin(second * 1.1) * 1.8;
    var baseTx = 2.1 + Math.sin(second * 0.39) * 1.2 + Math.sin(second * 1.4) * 0.6;

    var cpuSpike = active && p >= 0.25 && p < 0.38 ? (intense ? 45 : 40) : 0;
    var rxSpike = active && p >= 0.73 && p < 0.87 ? 7 : 0;
    var txSpike = active && p >= 0.37 && p < 0.47 ? (intense ? 16 : 14.5) : 0;
    var txDrop = active && p >= 0.53 && p < 0.60;
    var apiCaution = active && p >= 0.17 && p < 0.30;
    var workersDanger = active && p >= 0.58 && p < 0.68;
    var workersCaution = active && p >= 0.67 && p < 0.83;
    var dbDrop = active && p >= 0.43 && p < 0.50;

    return {
      timestamp: timestamp,
      values: {
        cpu: Math.min(99, Math.max(0, baseCpu + cpuSpike)),
        memory: baseMem,
        rx: Math.max(0, baseRx + rxSpike),
        tx: txDrop ? null : Math.max(0, baseTx + txSpike),
        api: apiCaution ? (intense ? "danger" : "caution") : "good",
        workers: workersDanger ? "danger" : workersCaution ? "caution" : "good",
        database: dbDrop ? null : "good"
      }
    };
  }

  function pushAll(snapshot) {
    monitors.forEach(function (monitor) { monitor.push(snapshot); });
  }

  function seedHistory(interval) {
    var now = Date.now();
    var count = Math.min(1000, Math.ceil(windowMs / interval));
    for (var i = -count; i <= 0; i++) {
      pushAll(sample(now + i * interval));
    }
  }

  seedHistory(currentInterval);

  var lastSampleTime = 0;
  function tick() {
    if (!document.hidden) {
      var now = Date.now();
      if (now > lastSampleTime) {
        lastSampleTime = now;
        pushAll(sample(now));
      }
    }
  }

  function restartTimer() {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(tick, currentInterval);
  }
  restartTimer();

  var tickspeedInput = document.getElementById("monitorTickspeed");
  var tickspeedOutput = document.getElementById("monitorTickspeedOutput");
  var eventFreqInput = document.getElementById("monitorEventFreq");
  var eventFreqOutput = document.getElementById("monitorEventFreqOutput");

  function resetControls() {
    currentSpeedLevel = 2;
    currentInterval = speedLevels[2].interval;
    currentFreqLevel = 2;
    if (tickspeedInput) {
      tickspeedInput.value = "2";
      tickspeedInput.setAttribute("aria-valuetext", "1.0x (1.0s)");
    }
    if (tickspeedOutput) {
      tickspeedOutput.textContent = "1.0x (1.0s)";
    }
    if (eventFreqInput) {
      eventFreqInput.value = "2";
      eventFreqInput.setAttribute("aria-valuetext", "Normal");
    }
    if (eventFreqOutput) {
      eventFreqOutput.textContent = "Normal";
    }
  }

  resetControls();
  window.addEventListener("pageshow", resetControls);
  window.addEventListener("load", resetControls);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", resetControls);
  }

  var controlsForm = document.querySelector(".monitor-catalog-controls");
  if (controlsForm) {
    controlsForm.addEventListener("reset", function () {
      setTimeout(resetControls, 0);
    });
  }

  if (tickspeedInput) {
    tickspeedInput.addEventListener("input", function () {
      var level = Math.max(0, Math.min(speedLevels.length - 1, Math.round(Number(tickspeedInput.value) || 0)));
      currentSpeedLevel = level;
      var tier = speedLevels[level];
      currentInterval = tier.interval;
      if (tickspeedOutput) tickspeedOutput.textContent = tier.label;
      tickspeedInput.setAttribute("aria-valuetext", tier.label);
      monitors.forEach(function (monitor) {
        monitor.setInterval(tier.interval);
        monitor.reset();
      });
      seedHistory(tier.interval);
      restartTimer();
    });
  }

  if (eventFreqInput) {
    eventFreqInput.addEventListener("input", function () {
      var level = Math.max(0, Math.min(4, Math.round(Number(eventFreqInput.value) || 0)));
      currentFreqLevel = level;
      var text = freqLabels[level] || "Normal";
      if (eventFreqOutput) eventFreqOutput.textContent = text;
      eventFreqInput.setAttribute("aria-valuetext", text);
      monitors.forEach(function (monitor) {
        monitor.reset();
      });
      seedHistory(currentInterval);
    });
  }
}(window.ComponentReference = window.ComponentReference || {}));
