(function (reference) {
  // Fixed illustrative cumulative usage; the supplied forecast is the map's endpoint.
  var dates = ["2026-09-01T00:00:00Z", "2026-09-04T00:00:00Z", "2026-09-08T00:00:00Z", "2026-09-09T00:00:00Z", "2026-09-12T00:00:00Z", "2026-09-13T00:00:00Z"];
  function item(id, label, unit, limit, current, forecast, history) {
    return { id: id, label: label, logo: id, unit: unit, limit: limit, current: current, forecast: forecast,
      history: history.map(function (value, index) { return { at: dates[index], value: value }; }) };
  }
  function mount(id, title, items, options) {
    var target = document.getElementById(id);
    if (!target) return null;
    return reference.createBudgetMap(target, {
      title: "Monthly infrastructure spend · " + title, sample: true,
      start: "2026-09-01T00:00:00Z", end: "2026-10-01T00:00:00Z", asOf: "2026-09-16T00:00:00Z",
      items: items
    }, options);
  }
  var overruns = [
    item("aws", "AWS", "USD", 10000, 9000, 13200, [0, 2200, 4700, 5100, 6900, 7500]),
    item("azure", "Azure", "USD", 5000, 3250, 5500, [0, 680, 1520, 1900, 2600, 2750]),
    item("blacksmith", "Blacksmith", "min", 3000, 1320, 2940, [0, 150, 540, 600, 960, 1020]),
    item("github-actions", "GitHub Actions", "min", 3000, 690, 1860, [0, 70, 250, 300, 460, 480]),
    item("openrouter", "OpenRouter", "USD", 100, 4, 36, [0, 1.2, 2.4, 2.6, 3.4, 3.6])
  ];
  var withinLimits = [
    item("aws", "AWS", "USD", 10000, 5700, 8800, [0, 1300, 2800, 3200, 4400, 4900]),
    item("azure", "Azure", "USD", 5000, 2350, 4000, [0, 480, 1050, 1280, 1820, 2050]),
    item("blacksmith", "Blacksmith", "min", 3000, 960, 2160, [0, 120, 360, 440, 730, 810]),
    item("github-actions", "GitHub Actions", "min", 3000, 600, 1740, [0, 60, 210, 260, 380, 430]),
    item("openrouter", "OpenRouter", "USD", 100, 4, 36, [0, 1.2, 2.4, 2.6, 3.4, 3.6])
  ];
  var increments = [
    item("aws", "AWS", "USD", 10000, 31000, 60000, [0, 7000, 15000, 17500, 24000, 26500]),
    item("azure", "Azure", "USD", 5000, 2350, 4000, [0, 480, 1050, 1280, 1820, 2050]),
    item("blacksmith", "Blacksmith", "min", 3000, 960, 2160, [0, 120, 360, 440, 730, 810]),
    item("github-actions", "GitHub Actions", "min", 3000, 4800, 9000, [0, 1000, 2200, 2600, 3600, 4000]),
    item("openrouter", "OpenRouter", "USD", 100, 4, 36, [0, 1.2, 2.4, 2.6, 3.4, 3.6])
  ];

  mount("budgetMapWithinLimitExample", "within limits", withinLimits);
  mount("budgetMapExample", "forecast overruns", overruns);
  mount("budgetMapOverrunIncrementsExample", "overrun increments", increments);

  var demoMaps = [
    mount("budgetMapDemoWithinLimitExample", "within limits (dynamic perspective)", withinLimits, { dynamicPerspective: true }),
    mount("budgetMapDemoExample", "forecast overruns (dynamic perspective)", overruns, { dynamicPerspective: true }),
    mount("budgetMapDemoOverrunIncrementsExample", "overrun increments (dynamic perspective)", increments, { dynamicPerspective: true })
  ].filter(Boolean);

  function bindSlider(sliderId, outputId, optionKey, maps) {
    var slider = document.getElementById(sliderId);
    var output = document.getElementById(outputId);
    if (!slider || !output) return;
    slider.addEventListener("input", function () {
      var val = Number(slider.value);
      output.value = val + "%";
      var intensity = val / 100;
      maps.forEach(function (map) {
        map.setOption(optionKey, intensity);
      });
    });
  }

  function bindToggle(toggleId, optionKey, maps) {
    var toggle = document.getElementById(toggleId);
    if (!toggle) return;
    toggle.addEventListener("change", function () {
      var enabled = toggle.checked;
      maps.forEach(function (map) {
        map.setOption(optionKey, enabled);
      });
    });
  }

  bindSlider("tiltSlider1", "tiltVal1", "tiltIntensity", demoMaps);
  bindSlider("compressSlider1", "compressVal1", "compressionIntensity", demoMaps);
  bindSlider("severitySlider1", "severityVal1", "severityIntensity", demoMaps);
  bindToggle("compressNonOverage1", "compressNonOverage", demoMaps);
}(window.ComponentReference));
