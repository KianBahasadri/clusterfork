(function (reference) {
  function time(value, name) {
    var result = Date.parse(value);
    if (!Number.isFinite(result)) throw new TypeError(name + " must be an ISO timestamp");
    return result;
  }

  function amount(value, name) {
    if (value === null || value === undefined) return null;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
      throw new TypeError(name + " must be a non-negative number or null");
    }
    return value;
  }

  function prepare(data) {
    var start = time(data.start, "start"), end = time(data.end, "end"), now = time(data.asOf, "asOf");
    if (end <= start || now < start || now >= end) throw new RangeError("Use start <= asOf < end");
    var warningAt = data.warningAt === undefined ? 90 : data.warningAt;
    if (!Number.isFinite(warningAt) || warningAt <= 0 || warningAt >= 100) throw new RangeError("warningAt must be between 0 and 100");
    var ids = new Set();
    var items = data.items.map(function (source) {
      if (typeof source.id !== "string" || !source.id || ids.has(source.id)) throw new TypeError("Each item needs a unique string id");
      ids.add(source.id);
      if (typeof source.label !== "string" || !source.label.trim()) throw new TypeError("Each item needs a label");
      if (source.logo !== undefined && typeof source.logo !== "string") throw new TypeError("logo must name an entry in the logo registry");
      var limit = amount(source.limit, "limit");
      if (!limit) throw new RangeError("Each limit must be greater than zero");
      var current = amount(source.current, "current"), forecast = amount(source.forecast, "forecast");
      var dates = new Set();
      var history = (source.history || []).map(function (sample) {
        var at = time(sample.at, "history.at");
        if (at < start || at >= now || dates.has(at)) throw new RangeError("History timestamps must be unique and inside [start, asOf)");
        dates.add(at);
        return { at: at, value: amount(sample.value, "history.value") };
      }).sort(function (a, b) { return a.at - b.at; });
      var currentPercent = current === null ? null : current / limit * 100;
      var forecastPercent = forecast === null ? null : forecast / limit * 100;
      var severity = "neutral", status = "No forecast";
      if (currentPercent !== null && currentPercent > 100) { severity = "danger"; status = "Over limit now"; }
      else if (forecastPercent !== null && forecastPercent > 100) { severity = "danger"; status = "Forecast over limit"; }
      else if (forecastPercent !== null && forecastPercent >= warningAt) { severity = "caution"; status = "Near limit"; }
      else if (forecastPercent !== null) { severity = "good"; status = "Within limit"; }
      else if (current === null) status = "Unavailable";
      return {
        id: source.id, label: source.label, logo: source.logo || null, unit: source.unit || "", limit: limit,
        current: current, forecast: forecast, currentPercent: currentPercent, forecastPercent: forecastPercent,
        history: history, stale: source.stale === true, severity: severity, status: status
      };
    });
    var values = [105];
    items.forEach(function (item) {
      values.push(item.currentPercent || 0, item.forecastPercent || 0);
      item.history.forEach(function (sample) { if (sample.value !== null) values.push(sample.value / item.limit * 100); });
    });
    return {
      title: data.title || "Budget map", start: start, end: end, now: now, warningAt: warningAt,
      sample: data.sample === true, elapsed: (now - start) / (end - start), items: items,
      maximum: Math.max.apply(null, values)
    };
  }

  function records(model) {
    var rows = [];
    model.items.forEach(function (item) {
      item.history.concat([{ at: model.now, value: item.current, kind: "Current" }, { at: model.end, value: item.forecast, kind: "Forecast" }]).forEach(function (sample) {
        rows.push([item.label, new Date(sample.at).toISOString(), sample.kind || "Observed", sample.value,
          item.unit, item.limit, sample.value === null ? null : sample.value / item.limit * 100, item.stale ? "Stale" : "Current"]);
      });
    });
    return rows;
  }

  var columns = ["Item", "Time (UTC)", "Kind", "Value", "Unit", "Limit", "Limit used (%)", "Freshness"];
  function csv(model) {
    return [columns].concat(records(model)).map(function (row) {
      return row.map(function (value) {
        return value === null ? "" : '"' + String(value).replace(/"/g, '""') + '"';
      }).join(",");
    }).join("\r\n") + "\r\n";
  }

  reference.budgetMapModel = { prepare: prepare, records: records, columns: columns, csv: csv };
}(window.ComponentReference = window.ComponentReference || {}));
