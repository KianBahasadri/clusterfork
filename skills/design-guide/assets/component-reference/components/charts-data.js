(function (reference) {
  var observed = [180, 210, 260, 610, 1050, 980, 920, 940, 880];
  var forecast = [null, null, null, null, 1050, 800, 620, 480, 350];
  var start = Date.UTC(2026, 8, 4, 14);
  var samples = observed.map(function (value, index) {
    return { time: new Date(start + index * 450000).toISOString(), observed: value, forecast: forecast[index] };
  });
  var series = [{ key: "observed", label: "Observed" }, { key: "forecast", label: "Forecast" }];
  var numbers = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

  function timeLabel(time, seconds) {
    return time.slice(11, seconds ? 19 : 16);
  }

  function valueLabel(value) {
    return value === null ? "Not available" : numbers.format(value) + " ms";
  }

  function delta(sample) {
    return sample.forecast === null ? null : sample.forecast - sample.observed;
  }

  function mean(values) {
    return values.length ? values.reduce(function (sum, value) { return sum + value; }, 0) / values.length : null;
  }

  function statistics(rows) {
    var actual = rows.map(function (row) { return row.observed; });
    var projected = rows.map(function (row) { return row.forecast; }).filter(function (value) { return value !== null; });
    var differences = rows.map(delta).filter(function (value) { return value !== null; });
    return { observedMean: mean(actual), observedPeak: actual.length ? Math.max.apply(null, actual) : null, forecastMean: mean(projected), meanDelta: mean(differences) };
  }

  function csv(rows) {
    function field(value) {
      var text = value === null ? "" : String(value);
      return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
    }
    var records = [["time_utc", "observed_ms", "forecast_ms", "delta_ms"]];
    rows.forEach(function (row) { records.push([row.time, row.observed, row.forecast, delta(row)]); });
    return records.map(function (record) { return record.map(field).join(","); }).join("\r\n") + "\r\n";
  }

  function windowAround(center, span) {
    span = Math.max(1, Math.min(samples.length - 1, Math.round(span)));
    var first = Math.max(0, Math.min(samples.length - 1 - span, Math.round(center - span / 2)));
    return { start: first, end: first + span };
  }

  function scale(view) {
    var values = [];
    samples.slice(view.start, view.end + 1).forEach(function (row) {
      series.forEach(function (item) {
        if (view.visible[item.key] && row[item.key] !== null) values.push(row[item.key]);
      });
    });
    var hasValues = values.length > 0;
    if (view.target !== null) values.push(view.target);
    var low = values.length ? Math.min.apply(null, values) : 0;
    var high = values.length ? Math.max.apply(null, values) : 1000;
    var padding = Math.max((high - low) * 0.1, high * 0.05, 1);
    low = view.scale === "fit" ? Math.max(0, low - padding) : 0;
    high += padding;
    var rough = (high - low) / 4;
    var power = Math.pow(10, Math.floor(Math.log10(rough)));
    var step = [1, 2, 5, 10].find(function (factor) { return factor * power >= rough; }) * power;
    low = Math.floor(low / step) * step;
    high = Math.ceil(high / step) * step;
    var ticks = [];
    for (var tick = low; tick <= high + step / 2; tick += step) ticks.push(tick);
    return { min: low, max: high, ticks: ticks, hasValues: hasValues };
  }

  reference.chart = { samples: samples, series: series, numbers: numbers, timeLabel: timeLabel, valueLabel: valueLabel, delta: delta, statistics: statistics, csv: csv, windowAround: windowAround, scale: scale };
}(window.ComponentReference));
