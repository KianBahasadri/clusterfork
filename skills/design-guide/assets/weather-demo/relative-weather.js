(function (reference) {
  "use strict";

  // One representative temperature for each of the previous seven complete
  // days, oldest first. Today is deliberately excluded from the baseline.
  const previousWeekCelsius = Object.freeze([18, 20, 22, 21, 23, 24, 19]);
  const average = previousWeekCelsius.reduce((sum, value) => sum + value, 0) / previousWeekCelsius.length;
  const modeNames = ["Far below average", "Below average", "Around average", "Above average", "Far above average"];
  const sampleOffsets = [-8, -4, 0, 4, 8];
  const dayLength = 24 * 60;

  function classify(temperature) {
    const difference = temperature - average;
    const level = difference <= -6 ? 0 : difference < -2 ? 1 : difference <= 2 ? 2 : difference < 6 ? 3 : 4;
    return { level, name: modeNames[level] };
  }

  function temperatureForMode(level) {
    return average + sampleOffsets[level];
  }

  function timeUntil(minute, event) {
    return (event - minute + dayLength) % dayLength;
  }

  function eventDescription(name, minutes) {
    if (!minutes) return `${name} now`;
    return `${name} in ${Math.floor(minutes / 60)} hours and ${minutes % 60} minutes`;
  }

  function render({ temperature, condition, minute, sunrise, sunset }) {
    const { level, name } = classify(temperature);
    // The visible stem runs from y=12 to the bulb at y=148. Its five
    // levels are empty, quarter, half, three-quarters, and full.
    const top = level === 0 ? 160 : 148 - level / 4 * (148 - 12);
    const reading = `${name} compared with the average temperature of the previous seven days. ${condition}.`;
    document.querySelectorAll("[data-relative-reading]").forEach(element => {
      element.setAttribute("aria-label", reading);
      element.setAttribute("title", reading);
      element.dataset.level = String(level);
    });
    document.querySelectorAll("[data-relative-fill]").forEach(element => {
      element.setAttribute("y", String(top));
      element.setAttribute("height", String(160 - top));
    });
    document.querySelectorAll("[data-relative-mode]").forEach(button => { button.setAttribute("aria-pressed", String(Number(button.dataset.relativeMode) === level)); });

    const isDay = minute >= sunrise && minute < sunset;
    const nextSunrise = eventDescription("Sunrise", timeUntil(minute, sunrise));
    const nextSunset = eventDescription("Sunset", timeUntil(minute, sunset));
    const daylightDescription = `${isDay ? "Daytime" : "Nighttime"}. ${nextSunrise}. ${nextSunset}.`;
    document.querySelectorAll("[data-relative-daylight]").forEach(element => {
      element.setAttribute("aria-label", daylightDescription);
      element.setAttribute("title", daylightDescription);
      element.dataset.daylight = String(isDay);
    });

    const daylightPosition = 4 + Math.max(0, Math.min(1, (minute - sunrise) / (sunset - sunrise))) * 64;
    document.querySelectorAll("[data-placement-daylight-fill]").forEach(element => {
      element.setAttribute("d", `M4 12H${daylightPosition}`);
      element.style.display = isDay ? "" : "none";
    });
    document.querySelectorAll("[data-placement-daylight-marker]").forEach(element => {
      element.setAttribute("cx", String(daylightPosition));
      element.style.display = isDay ? "" : "none";
    });
    document.querySelectorAll("[data-placement-night]").forEach(element => { element.style.display = isDay ? "none" : ""; });

    // This track spans the entire local day, including both night portions.
    const xAtMinute = value => 20 + value / dayLength * 240;
    const dawnX = xAtMinute(sunrise);
    const duskX = xAtMinute(sunset);
    document.getElementById("relativeDayBand").setAttribute("x", String(dawnX));
    document.getElementById("relativeDayBand").setAttribute("width", String(duskX - dawnX));
    document.getElementById("relativeDawnMarker").setAttribute("transform", `translate(${dawnX} 0)`);
    document.getElementById("relativeDuskMarker").setAttribute("transform", `translate(${duskX} 0)`);
    document.getElementById("relativeNowMarker").setAttribute("transform", `translate(${xAtMinute(minute)} 58)`);
    document.getElementById("relativeNowIcon").setAttribute("href", `#lucide-${isDay ? "sun" : "moon"}`);

    // Each ring fills over 24 hours toward its next named event, and resets
    // when that event occurs. Sunrise and sunset recur in this fixed sample.
    for (const [id, event, label] of [["sunriseRing", sunrise, nextSunrise], ["sunsetRing", sunset, nextSunset]]) {
      const elapsed = (minute - event + dayLength) % dayLength / dayLength;
      const ring = document.getElementById(id);
      ring.querySelector(".event-ring-progress").setAttribute("stroke-dasharray", `${elapsed} 1`);
      const radians = elapsed * 2 * Math.PI - Math.PI / 2;
      ring.querySelector(".event-ring-marker").setAttribute("cx", String(44 + 32 * Math.cos(radians)));
      ring.querySelector(".event-ring-marker").setAttribute("cy", String(44 + 32 * Math.sin(radians)));
      ring.setAttribute("aria-label", label);
      ring.setAttribute("title", label);
    }

    // Sun and moon are opposite each other. Scale the two half-turns to the
    // actual daylight and night durations so horizon crossings match the
    // supplied sunrise and sunset rather than assuming a twelve-hour day.
    const nightLength = dayLength - (sunset - sunrise);
    const turn = isDay
      ? (minute - sunrise) / (sunset - sunrise) * 180
      : 180 + (minute - sunset + dayLength) % dayLength / nightLength * 180;
    document.getElementById("dayNightRotor").setAttribute("transform", `rotate(${turn})`);
    document.querySelectorAll("[data-dial-upright]").forEach(element => { element.setAttribute("transform", `rotate(${-turn})`); });
    document.getElementById("dayNightDial").dataset.phase = isDay ? "day" : "night";
  }

  reference.relativeWeather = { classify, temperatureForMode, render };
}(window.ComponentReference = window.ComponentReference || {}));
