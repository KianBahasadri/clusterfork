(function (reference) {
  // Fixed illustrative cumulative usage; the supplied forecast is the map's endpoint.
  var dates = ["2026-09-01T00:00:00Z", "2026-09-04T00:00:00Z", "2026-09-08T00:00:00Z", "2026-09-09T00:00:00Z", "2026-09-12T00:00:00Z", "2026-09-13T00:00:00Z"];
  function item(id, label, unit, limit, current, forecast, history) {
    return { id: id, label: label, logo: id, unit: unit, limit: limit, current: current, forecast: forecast,
      history: history.map(function (value, index) { return { at: dates[index], value: value }; }) };
  }
  reference.createBudgetMap(document.getElementById("budgetMapExample"), {
    title: "Monthly infrastructure spend", sample: true,
    start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", asOf: "2026-09-16T00:00:00Z",
    items: [
      item("aws", "AWS", "USD", 10000, 9000, 13200, [0, 2200, 4700, 5100, 6900, 7500]),
      item("azure", "Azure", "USD", 5000, 3250, 5500, [0, 680, 1520, 1900, 2600, 2750]),
      item("blacksmith", "Blacksmith", "min", 3000, 1320, 2940, [0, 150, 540, 600, 960, 1020]),
      item("github-actions", "GitHub Actions", "min", 3000, 690, 1860, [0, 70, 250, 300, 460, 480]),
      item("openrouter", "OpenRouter", "USD", 100, 4, 36, [0, 1.2, 2.4, 2.6, 3.4, 3.6])
    ]
  });
}(window.ComponentReference));
