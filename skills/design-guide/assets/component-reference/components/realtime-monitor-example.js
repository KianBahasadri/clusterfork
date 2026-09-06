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
    { id: "realtimeMonitorServicesExample", channels: serviceChannels, presentation: "sparkline", label: "node-01 services" }
  ];
  var monitors = mounts.map(function (mount) {
    var root = document.getElementById(mount.id);
    return root ? reference.createRealtimeMonitor(root, mount.channels, {
      label: mount.label, simulated: true, presentation: mount.presentation
    }) : null;
  }).filter(Boolean);
  if (!monitors.length) return;
  var origin = Date.now();
  function sample(timestamp) {
    var second = (timestamp - origin) / 1000;
    var phase = ((Math.floor(second) % 60) + 60) % 60;
    return { timestamp: timestamp, values: {
      cpu: Math.min(99, 38 + Math.sin(second * 0.22) * 16 + Math.sin(second * 1.6) * 4 + (phase > 15 && phase < 23 ? 40 : 0)),
      memory: 18.4 + Math.sin(second * 0.07) * 1.4,
      rx: Math.max(0, 6.5 + Math.sin(second * 0.28) * 3.8 + Math.sin(second * 1.1) * 1.8 + (phase > 44 && phase < 52 ? 7 : 0)),
      tx: phase > 32 && phase < 36 ? null : Math.max(0, 2.1 + Math.sin(second * 0.39) * 1.2 + Math.sin(second * 1.4) * 0.6 + (phase > 22 && phase < 28 ? 14.5 : 0)),
      api: phase > 10 && phase < 18 ? "caution" : "good",
      workers: phase > 35 && phase < 41 ? "danger" : phase > 40 && phase < 50 ? "caution" : "good",
      database: phase > 26 && phase < 30 ? null : "good"
    } };
  }
  function pushAll(snapshot) {
    monitors.forEach(function (monitor) { monitor.push(snapshot); });
  }
  for (var offset = -60; offset <= 0; offset++) pushAll(sample(origin + offset * 1000));
  setInterval(function () {
    // A suspended tab leaves a real gap; the demo never fabricates missed samples.
    if (!document.hidden) pushAll(sample(Date.now()));
  }, 1000);
}(window.ComponentReference = window.ComponentReference || {}));
