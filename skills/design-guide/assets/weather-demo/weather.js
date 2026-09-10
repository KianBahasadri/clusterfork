(function () {
  "use strict";

  // These are fixed local-time examples, not observations or an astronomy model.
  const sunrise = 6 * 60 + 30;
  const sunset = 19 * 60;
  const presets = {
    sunny: { temperature: 28, condition: "clear", minute: 14 * 60 + 30 },
    cloudy: { temperature: 22, condition: "cloudy", minute: 14 * 60 + 30 },
    rain: { temperature: 15, condition: "rain", minute: 14 * 60 + 30 },
    snow: { temperature: -3, condition: "snow", minute: 9 * 60 },
    night: { temperature: 16, condition: "clear", minute: 22 * 60 }
  };
  let selectedPreset = "cloudy";
  let temperature = presets[selectedPreset].temperature;
  let conditionType = presets[selectedPreset].condition;
  let unit = "C";
  let minute = presets[selectedPreset].minute;
  const timeInputs = Array.from(document.querySelectorAll("[data-time-control]"));
  const reference = window.ComponentReference;
  const relativeWeather = reference.relativeWeather;
  const arc = document.getElementById("daylightArc");
  const plot = document.querySelector(".daylight-plot");
  const timeLabel = document.getElementById("arcTimeLabel");
  const root = document.documentElement;

  const clock = value => `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  const degrees = value => Math.round(unit === "C" ? value : value * 9 / 5 + 32);
  const duration = value => `${Math.floor(value / 60)}h ${String(value % 60).padStart(2, "0")}m`;
  const daylight = () => minute >= sunrise && minute < sunset;
  const fraction = () => Math.max(0, Math.min(1, (minute - sunrise) / (sunset - sunrise)));

  function conditions() {
    const condition = conditionType;
    if (condition === "clear") return daylight() ? ["Sunny", "sun"] : ["Clear night", "moon"];
    if (condition === "cloudy") return ["Partly cloudy", daylight() ? "cloud-sun" : "cloud-moon"];
    return condition === "rain" ? ["Rain", "cloud-rain"] : ["Snow", "snowflake"];
  }

  function nextEvent() {
    if (minute < sunrise) return `Sunrise in ${duration(sunrise - minute)}`;
    if (daylight()) return `Sunset in ${duration(sunset - minute)}`;
    return `Sunrise in ${duration(1440 - minute + sunrise)}`;
  }

  function positionArc() {
    const t = fraction();
    const x = 20 + 360 * t;
    const y = 112 - 352 * t * (1 - t);
    document.getElementById("arcSun").setAttribute("transform", `translate(${x} ${y})`);
    // SVG preserves its aspect ratio. Use its actual transform so the HTML time
    // label stays attached to the sun at every card width and browser zoom.
    const transform = arc.getScreenCTM();
    if (!transform) return;
    const point = new DOMPoint(x, y).matrixTransform(transform);
    const bounds = plot.getBoundingClientRect();
    timeLabel.style.left = `${point.x - bounds.left}px`;
    timeLabel.style.top = `${point.y - bounds.top - 24}px`;

    // Time is linear along the horizontal axis, not along the curve's length.
    // Draw the exact quadratic subcurve so the elapsed stroke ends at the sun.
    document.getElementById("arcElapsed").setAttribute("d", `M20 112 Q${20 + 180 * t} ${112 - 176 * t} ${x} ${y}`);
  }

  function render(announce = false) {
    const [condition, icon] = conditions();
    const currentTime = clock(minute);
    const temperatureText = `${degrees(temperature)}°${unit}`;
    const isDay = daylight();
    const next = nextEvent();

    document.querySelectorAll("[data-temperature]").forEach(element => { element.textContent = temperatureText; });
    document.querySelectorAll("[data-condition]").forEach(element => { element.textContent = condition; });
    document.querySelectorAll("[data-condition-icon]").forEach(element => { element.setAttribute("href", `#lucide-${icon}`); });
    document.querySelectorAll("[data-clock]").forEach(element => {
      element.textContent = currentTime;
      element.dateTime = currentTime;
    });
    document.querySelectorAll("[data-next-event]").forEach(element => { element.textContent = next; });
    document.querySelectorAll("[data-preset]").forEach(button => { button.setAttribute("aria-pressed", String(button.dataset.preset === selectedPreset)); });
    document.querySelectorAll("[data-unit]").forEach(button => { button.setAttribute("aria-pressed", String(button.dataset.unit === unit)); });
    document.querySelectorAll("[data-tick]").forEach(element => { element.textContent = `${degrees(Number(element.dataset.tick))}°`.replace("-", "−"); });

    const unitName = unit === "C" ? "Celsius" : "Fahrenheit";
    document.getElementById("temperatureGauge").setAttribute("aria-label", `Temperature ${degrees(temperature)} degrees ${unitName}, on a scale from ${degrees(-10)} to ${degrees(40)} degrees ${unitName}`);
    // The zero-height -10° tick is at y=172; 40° is at y=12.
    // Add the distance from the tube's bottom to the lowest tick.
    document.getElementById("thermometerFill").style.height = `${10 + (temperature + 10) / 50 * 160}px`;

    timeInputs.forEach(input => {
      input.value = String(minute);
      input.setAttribute("aria-valuetext", currentTime);
      input.style.setProperty("--range-progress", `${minute / Number(input.max) * 100}%`);
    });
    document.querySelectorAll("[data-time-output]").forEach(output => { output.value = currentTime; });
    document.querySelectorAll("[data-sky]").forEach(button => { button.setAttribute("aria-pressed", String(button.dataset.sky === conditionType)); });
    timeLabel.textContent = currentTime;
    timeLabel.hidden = !isDay;
    document.getElementById("arcSun").style.display = isDay ? "" : "none";
    document.getElementById("arcElapsed").style.display = isDay ? "" : "none";
    document.getElementById("daylightFill").style.width = isDay ? `${fraction() * 100}%` : "0%";
    document.getElementById("daylightMarker").hidden = !isDay;
    document.getElementById("daylightMarker").style.left = `${fraction() * 100}%`;
    const daylightDescription = `Daylight from 06:30 to 19:00. Current time ${currentTime}. ${next}.`;
    document.getElementById("daylightArcTitle").textContent = daylightDescription;
    document.getElementById("daylightLine").setAttribute("aria-label", daylightDescription);
    positionArc();
    relativeWeather.render({ temperature, condition, minute, sunrise, sunset });
    if (announce) document.getElementById("previewStatus").textContent = `${temperatureText}, ${relativeWeather.classify(temperature).name} compared with the previous week, ${condition}, ${currentTime}. ${next}.`;
  }

  document.querySelectorAll("[data-preset]").forEach(button => {
    button.addEventListener("click", () => {
      selectedPreset = button.dataset.preset;
      temperature = presets[selectedPreset].temperature;
      conditionType = presets[selectedPreset].condition;
      minute = presets[selectedPreset].minute;
      render(true);
    });
  });
  document.querySelectorAll("[data-unit]").forEach(button => {
    button.addEventListener("click", () => { unit = button.dataset.unit; render(true); });
  });
  timeInputs.forEach(input => {
    input.addEventListener("input", () => { minute = Number(input.value); render(); });
    input.addEventListener("change", () => { render(true); });
  });
  document.querySelectorAll("[data-relative-mode]").forEach(button => {
    button.addEventListener("click", () => {
      selectedPreset = null;
      temperature = relativeWeather.temperatureForMode(Number(button.dataset.relativeMode));
      render(true);
    });
  });
  document.querySelectorAll("[data-sky]").forEach(button => {
    button.addEventListener("click", () => {
      selectedPreset = null;
      conditionType = button.dataset.sky;
      render(true);
    });
  });
  const glyphSize = document.getElementById("glyphSize");
  function resizeGlyphs() {
    const percent = Number(glyphSize.value);
    document.getElementById("glyph-placements").style.setProperty("--weather-glyph-size", `${32 * percent / 100}px`);
    document.getElementById("glyphSizeOutput").value = `${percent}%`;
    glyphSize.setAttribute("aria-valuetext", `${percent} percent`);
    glyphSize.style.setProperty("--range-progress", `${(percent - Number(glyphSize.min)) / (Number(glyphSize.max) - Number(glyphSize.min)) * 100}%`);
  }
  glyphSize.addEventListener("input", resizeGlyphs);
  resizeGlyphs();
  new ResizeObserver(positionArc).observe(plot);

  function toggleTheme() {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    const dark = root.dataset.theme === "dark";
    document.getElementById("themeIconUse").setAttribute("href", dark ? "#lucide-sun" : "#lucide-moon");
    const button = document.getElementById("themeToggleBtn");
    button.setAttribute("aria-label", `Switch to ${dark ? "light" : "dark"} theme`);
    button.title = button.getAttribute("aria-label");
  }
  document.getElementById("themeToggleBtn").addEventListener("click", toggleTheme);

  const sections = Array.from(document.querySelectorAll(".component-section"));
  function currentSection() {
    const destination = sections.find(section => `#${section.id}` === location.hash);
    if (destination) {
      const top = destination.getBoundingClientRect().top;
      if (top >= 0 && top < innerHeight) return destination;
    }
    let current = sections[0];
    let nearest = -Infinity;
    sections.forEach(section => {
      const top = section.getBoundingClientRect().top;
      if (top <= 120 && top > nearest) { current = section; nearest = top; }
    });
    return current;
  }

  function exportMarkdown() {
    const content = `# Weather explorations\n\nSample weather: ${degrees(temperature)}°${unit}, ${conditions()[0]}, ${clock(minute)}.\n\nSunrise 06:30. Sunset 19:00. ${nextEvent()}.\n\nTemperature compared with the previous seven complete days: ${relativeWeather.classify(temperature).name}.\n\n## 01 Compact strip\n\nTemperature, conditions, sunrise, and sunset in one row.\n\n## 02 Daylight arc\n\nCurrent weather above an illustrative sunrise-to-sunset arc.\n\n## 03 Vertical thermometer\n\nA temperature gauge with conditions and a daylight timeline.\n\n## 04 Daylight track\n\nA five-state relative thermometer and weather glyph, with sunrise and sunset positioned along a full-day track.\n\n## 05 Event rings\n\nThe same relative thermometer and weather glyph, with separate rings filling toward the next sunrise and sunset.\n\n## 06 Sun & moon dial\n\nThe same relative thermometer and weather glyph, with a rotating day/night dial and a fixed horizon.\n`;
    const placements = Array.from(document.querySelectorAll(".glyph-placement-section h2"))
      .map(heading => `\n## ${heading.textContent.trim()}\n\nCompact thermometer and weather glyph with a sunrise/sunset strip.\n`)
      .join("");
    const url = URL.createObjectURL(new Blob([content, placements], { type: "text/markdown;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "weather-explorations.md";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    document.getElementById("exportStatus").textContent = "Weather explorations exported as Markdown.";
  }

  Object.assign(reference, {
    toggleTheme, exportMarkdown, currentSection,
    updateContents() {},
    pageOptions: { title: "Weather explorations" }
  });
  render();
}());
