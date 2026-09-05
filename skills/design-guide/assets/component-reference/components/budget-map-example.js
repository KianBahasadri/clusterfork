(function (reference) {
  // Fixed illustrative data. Forecasts are explicit inputs, not extrapolated by the component.
  var dates = ["2026-09-01T00:00:00Z", "2026-09-04T00:00:00Z", "2026-09-08T00:00:00Z", "2026-09-12T00:00:00Z"];
  function item(id, label, unit, limit, current, forecast, history) {
    return { id: id, label: label, logo: id, unit: unit, limit: limit, current: current, forecast: forecast,
      history: history.map(function (value, index) { return { at: dates[index], value: value }; }) };
  }
  reference.createBudgetMap(document.getElementById("budgetMapExample"), {
    title: "Monthly infrastructure spend", sample: true,
    start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", asOf: "2026-09-16T00:00:00Z",
    items: [
      item("aws", "AWS", "USD", 10000, 9000, 13200, [180, 2200, 4700, 6900]),
      item("azure", "Azure", "USD", 5000, 3250, 5500, [100, 680, 1520, 2600]),
      item("blacksmith", "Blacksmith", "min", 3000, 1320, 2940, [20, 150, 540, 960]),
      item("github-actions", "GitHub Actions", "min", 3000, 690, 1860, [10, 70, 250, 460]),
      item("openrouter", "OpenRouter", "USD", 100, 4, 36, [0.2, 1.2, 2.4, 3.4])
    ]
  });
}(window.ComponentReference));
