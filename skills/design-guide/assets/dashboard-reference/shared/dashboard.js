(function (reference) {
  var table = document.getElementById("sampleTable");
  var rows = Array.from(table.tBodies[0].rows);
  var numbers = new Intl.NumberFormat("en");
  function numeric(cell) { return Number(cell.textContent.replace(/[^\d.\-]/g, "")); }

  var services = rows.map(function (row) {
    var details = reference.serviceDetails[row.dataset.serviceId];
    ["region", "replicas", "version", "team"].forEach(function (key) {
      var cell = row.insertCell();
      cell.textContent = details[key];
      if (key === "replicas") cell.className = "num";
      else if (key !== "team") cell.className = "dashboard-identifier";
    });
    return { details: details, rpm: numeric(row.cells[2]), errorRate: numeric(row.cells[4]), severity: row.cells[1].dataset.sortSeverity || "derived" };
  });
  var requests = services.reduce(function (total, service) { return total + service.rpm; }, 0);
  var errors = services.reduce(function (total, service) { return total + service.rpm * service.errorRate / 100; }, 0);
  document.getElementById("metricRequests").textContent = numbers.format(requests);
  document.getElementById("metricServices").textContent = rows.length;
  document.getElementById("metricAttention").textContent = services.filter(function (service) { return service.severity === "caution" || service.severity === "danger"; }).length;
  document.getElementById("metricReplicas").textContent = services.reduce(function (total, service) { return total + Number(service.details.replicas); }, 0);
  document.getElementById("metricErrors").textContent = requests ? (errors / requests * 100).toFixed(2) + "%" : "—";

  var chart = reference.chart;
  var latest = chart.samples[chart.samples.length - 1];
  var stats = chart.statistics(chart.samples);
  document.getElementById("metricLatency").textContent = chart.valueLabel(latest.observed);
  document.getElementById("latencyMean").textContent = chart.valueLabel(stats.observedMean);
  document.getElementById("latencyPeak").textContent = chart.valueLabel(stats.observedPeak);
  document.getElementById("latencyForecast").textContent = chart.valueLabel(latest.forecast);

  var regions = new Map();
  var severityOrder = ["derived", "good", "caution", "danger"];
  services.forEach(function (service) {
    var region = regions.get(service.details.region) || { name: service.details.region, rpm: 0, replicas: 0, severity: "derived" };
    region.rpm += service.rpm;
    region.replicas += Number(service.details.replicas);
    if (severityOrder.indexOf(service.severity) > severityOrder.indexOf(region.severity)) region.severity = service.severity;
    regions.set(region.name, region);
  });
  var regionTable = document.getElementById("regionTable");
  var regionLabels = { good: "Operational", caution: "Degraded", danger: "Incident", derived: "Simulated" };
  regions.forEach(function (region) {
    var row = regionTable.tBodies[0].insertRow();
    row.insertCell().textContent = region.name;
    var status = row.insertCell();
    status.dataset.sortSeverity = region.severity;
    var badge = document.createElement("span");
    badge.className = "badge badge-" + region.severity;
    badge.textContent = regionLabels[region.severity];
    status.appendChild(badge);
    [region.rpm, region.replicas].forEach(function (value) {
      var cell = row.insertCell(); cell.className = "num"; cell.textContent = numbers.format(value);
    });
  });
  reference.makeTableSortable(regionTable);

  document.getElementById("exportServices").addEventListener("click", function () {
    function csvField(value) { return '"' + String(value).replace(/"/g, '""') + '"'; }
    var records = [["snapshot_utc", "service", "status", "requests_per_minute", "p95_latency_ms", "error_rate_percent", "region", "replicas", "release", "owner"]];
    Array.from(table.tBodies[0].rows).forEach(function (row) {
      var details = reference.serviceDetails[row.dataset.serviceId];
      records.push(["2026-09-04T15:00:00Z", row.cells[0].textContent.trim(), row.cells[1].textContent.trim(), numeric(row.cells[2]), numeric(row.cells[3]), numeric(row.cells[4]), details.region, Number(details.replicas), details.version, details.team]);
    });
    reference.downloadText("services-2026-09-04-150000.csv", records.map(function (record) { return record.map(csvField).join(","); }).join("\r\n") + "\r\n", "text/csv;charset=utf-8");
    reference.spawnToast("Exported " + (records.length - 1) + " services as CSV", false);
  });

  var inspectQueue = document.getElementById("inspectQueue");
  inspectQueue.addEventListener("click", function () {
    document.querySelector('[data-service-id="order-queue"] .table-row-trigger').click();
    document.getElementById("serviceDetailsDialog").addEventListener("close", function () {
      inspectQueue.focus({ preventScroll: true });
    }, { once: true });
  });
}(window.ComponentReference));
