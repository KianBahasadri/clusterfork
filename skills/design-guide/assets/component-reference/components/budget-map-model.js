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

  var day = 86400000;
  function finite(value) { return Number.isFinite(value) ? value : null; }
  function windowRate(model, item, since) {
    var result = { value: null, since: since, interpolated: false, reason: "" };
    if (since < model.start || since >= model.now) { result.reason = "Window not complete"; return result; }
    if (item.current === null) { result.reason = "Current usage unavailable"; return result; }
    var samples = item.history.concat([{ at: model.now, value: item.current }]);
    var index = samples.findIndex(function (sample) { return sample.at >= since; });
    if (samples[index].at !== since) index -= 1;
    if (index < 0) { result.reason = "Missing starting observation"; return result; }
    var window = samples.slice(index);
    if (window.some(function (sample) { return sample.value === null; })) { result.reason = "History contains a gap"; return result; }
    if (window.some(function (sample, i) { return i && sample.value < window[i - 1].value; })) {
      result.reason = "Usage decreased in window"; return result;
    }
    var baseline = window[0].value;
    if (window[0].at !== since) {
      result.interpolated = true;
      baseline += (window[1].value - baseline) * ((since - window[0].at) / (window[1].at - window[0].at));
    }
    var rate = (item.current - baseline) / ((model.now - since) / day);
    if (Number.isFinite(rate)) result.value = rate;
    else result.reason = "Rate exceeds numeric range";
    return result;
  }

  // Analysis is separate from the supplied forecast used by the map and raw export.
  function analyze(model, item) {
    var elapsedDays = (model.now - model.start) / day, remainingDays = (model.end - model.now) / day;
    var remaining = item.current === null ? null : item.limit - item.current;
    var sustainable = remaining === null ? null : finite(Math.max(0, remaining) / remainingDays);
    var average = windowRate(model, item, model.start);
    var seven = windowRate(model, item, model.now - 7 * day);
    var three = windowRate(model, item, model.now - 3 * day);
    function scenario(id, label, rate, total, basis) {
      if (!Number.isFinite(total)) total = null;
      if (!Number.isFinite(rate)) rate = null;
      var limitAt = null, limitState = "unavailable";
      if (item.current !== null && item.current >= item.limit) { limitAt = model.now; limitState = "reached"; }
      else if (item.current !== null && total !== null && rate !== null) {
        if (rate <= 0) limitState = "not-reached";
        else if (total < item.limit) limitState = "after-period";
        else { limitAt = Math.min(model.end, model.now + (remaining / rate) * day); limitState = "projected"; }
      }
      return { id: id, label: label, rate: rate, total: total, basis: basis,
        percent: total === null ? null : finite(total / item.limit * 100),
        headroom: total === null ? null : item.limit - total, limitAt: limitAt, limitState: limitState };
    }
    var suppliedRate = item.current === null || item.forecast === null ? null : (item.forecast - item.current) / remainingDays;
    var forecasts = [scenario("supplied", "Supplied forecast", suppliedRate, item.forecast, "Plotted on map · implied rate")];
    [["average", "Period average", average], ["seven", "7-day average", seven], ["three", "3-day average", three]].forEach(function (entry) {
      var rate = entry[2];
      var total = rate.value === null ? null : item.current + rate.value * remainingDays;
      forecasts.push(scenario(entry[0], entry[1], rate.value, total,
        rate.reason || (rate.interpolated ? "Interpolated window start" : "Observed window rate")));
    });
    return { elapsedDays: elapsedDays, remainingDays: remainingDays, remaining: remaining, sustainable: sustainable,
      average: average, seven: seven, three: three, forecasts: forecasts,
      change: average.value > 0 && three.value !== null ? finite((three.value / average.value - 1) * 100) : null,
      reduction: sustainable === null || three.value === null ? null : three.value === 0 ? 0 : Math.max(0, 1 - sustainable / three.value) * 100 };
  }

  var columns = ["Item", "Time (UTC)", "Kind", "Value", "Unit", "Limit", "Limit used (%)", "Freshness"];
  function csv(model) {
    return [columns].concat(records(model)).map(function (row) {
      return row.map(function (value) {
        return value === null ? "" : '"' + String(value).replace(/"/g, '""') + '"';
      }).join(",");
    }).join("\r\n") + "\r\n";
  }

  reference.budgetMapModel = { prepare: prepare, analyze: analyze, records: records, columns: columns, csv: csv };
}(window.ComponentReference = window.ComponentReference || {}));
