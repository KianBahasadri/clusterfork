(function (reference) {
  // Snapshots use one timestamp and explicit missing values across every channel.
  reference.createRealtimeMonitorModel = function (channels, options) {
    options = options || {};
    var clock = options.clock || Date.now;
    var interval = options.interval === undefined ? 1000 : options.interval;
    var windowMs = options.windowMs === undefined ? 60000 : options.windowMs;
    var staleAfter = options.staleAfter === undefined ? interval * 3 : options.staleAfter;
    var maxSamples = options.maxSamples === undefined ? 600 : options.maxSamples;
    if (!([interval, windowMs, staleAfter].every(Number.isFinite) && interval > 0 && windowMs >= interval && staleAfter >= interval && Number.isInteger(maxSamples) && maxSamples >= 2)) {
      throw new Error("Invalid realtime monitoring interval, window, freshness, or capacity");
    }
    var ids = new Set();
    if (!Array.isArray(channels) || !channels.length) throw new Error("At least one monitoring channel is required");
    channels = channels.map(function (channel) {
      if (!channel.id || ids.has(channel.id) || !channel.label || !["metric", "status"].includes(channel.kind)) throw new Error("Invalid or duplicate monitoring channel");
      if (channel.kind === "metric" && (!channel.unit || !Number.isFinite(channel.max) || channel.max <= 0)) throw new Error("Metrics require a unit and positive plot maximum");
      if (channel.decimals !== undefined && (!Number.isInteger(channel.decimals) || channel.decimals < 0 || channel.decimals > 6)) throw new Error("Metric precision must be between zero and six decimals");
      if (["warning", "critical"].some(function (key) { return channel[key] !== undefined && (!Number.isFinite(channel[key]) || channel[key] < 0); }) || (channel.critical !== undefined && channel.warning !== undefined && channel.critical < channel.warning)) throw new Error("Invalid monitoring thresholds");
      ids.add(channel.id);
      return Object.freeze(Object.assign({ decimals: 0 }, channel));
    });
    var samples = [], latest = null, frozen = null, connection = "connecting";

    function push(snapshot) {
      var timestamp = snapshot && snapshot.timestamp;
      if (!Number.isFinite(timestamp) || !Number.isFinite(new Date(timestamp).getTime()) || timestamp > clock() || (latest && timestamp <= latest.timestamp)) return false;
      var values = Object.create(null);
      channels.forEach(function (channel) {
        var value = snapshot.values && snapshot.values[channel.id];
        values[channel.id] = channel.kind === "metric"
          ? (Number.isFinite(value) && value >= 0 ? value : null)
          : (["good", "caution", "danger"].includes(value) ? value : null);
      });
      latest = Object.freeze({ timestamp: timestamp, values: Object.freeze(values) });
      samples.push(latest);
      samples = samples.filter(function (sample) { return sample.timestamp >= timestamp - windowMs; }).slice(-maxSamples);
      connection = "connected";
      return true;
    }

    function view() {
      var now = clock(), end = frozen ? frozen.end : now;
      var age = latest ? Math.max(0, now - latest.timestamp) : null;
      return {
        samples: (frozen ? frozen.samples : samples).filter(function (sample) { return sample.timestamp >= end - windowMs; }),
        latest: frozen ? frozen.latest : latest,
        end: end, start: end - windowMs, interval: interval,
        paused: !!frozen, age: age,
        feed: connection === "disconnected" ? "Disconnected" : !latest ? "Waiting for data" : connection === "connecting" ? "Reconnecting" : age >= staleAfter ? "Stale" : "Live"
      };
    }

    return {
      channels: Object.freeze(channels), interval: interval, windowMs: windowMs,
      push: push, view: view,
      pause: function () { if (!frozen) frozen = { samples: samples.slice(), latest: latest, end: clock() }; },
      resume: function () { frozen = null; },
      setConnection: function (state) {
        if (!["connected", "disconnected", "connecting"].includes(state)) throw new Error("Invalid monitoring connection state");
        connection = state;
      }
    };
  };
}(window.ComponentReference = window.ComponentReference || {}));
